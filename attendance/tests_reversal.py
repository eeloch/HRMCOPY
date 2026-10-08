from datetime import date, datetime, timedelta
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from audit.models import AuditEvent
from employees.models import Employee
from payroll.models import EmployeePayroll, PayrollLineItem, PayrollPeriod
from payroll.services.attendance import sync_attendance_deductions_for_period

from .models import AttendanceException, DailyAttendance, EmployeeRosterDay, Shift
from .services.reversal import change_decision


class ChangeAttendanceChargeTests(TestCase):
    """An employee contests a charge on their statement: it is checked and, if wrong, reversed - or put back."""

    def setUp(self):
        self.employee = Employee.objects.create(employee_id="REV-001", first_name="Contest", last_name="Ed", basic_salary=Decimal("150000.00"))
        self.shift = Shift.objects.create(name="Reversal Shift", start_time="07:00", end_time="19:00")
        self.period = PayrollPeriod.objects.create(year=2032, month=6)
        self.payroll = EmployeePayroll.objects.create(payroll_period=self.period, employee=self.employee, basic_salary=Decimal("150000.00"), gross_earnings=Decimal("150000.00"), net_pay=Decimal("150000.00"))
        for day in range(1, 31):
            EmployeeRosterDay.objects.get_or_create(employee=self.employee, date=date(2032, 6, day), defaults={"status": "work" if day <= 27 else "rest", "shift": self.shift if day <= 27 else None, "source": "manual"})
        self.actor = get_user_model().objects.create_user(username="reverser", password="pw", first_name="Rita", last_name="Reviewer")
        self.actor.user_permissions.add(Permission.objects.get(codename="reverse_attendance_charge"))

    def case(self, day=3, kind="late", minutes=40, status="approved"):
        attendance = DailyAttendance.objects.create(employee=self.employee, date=date(2032, 6, day), shift=self.shift, status="late" if kind == "late" else "absent", late_minutes=minutes if kind == "late" else 0)
        return AttendanceException.objects.create(attendance=attendance, exception_type=kind, minutes_affected=minutes, status=status, reviewed_by="Gift_HR", reviewed_at=timezone.now(), admin_comment="Late by more than 30 minutes.")

    def lines(self):
        return PayrollLineItem.objects.filter(payroll=self.payroll, is_system_generated=True)

    def test_reversing_an_approved_lateness_charge_takes_it_out_of_the_payroll(self):
        exception = self.case()
        sync_attendance_deductions_for_period(self.period)
        self.assertEqual([line.amount for line in self.lines()], [Decimal("700.00")])

        change_decision(exception, "waived", "Checked the terminal: clocked in at 07:05, the first scan was a misread.", self.actor)

        exception.refresh_from_db()
        self.assertEqual(exception.status, "waived")
        self.assertEqual(exception.reviewed_by, "Rita Reviewer")
        self.assertIn("misread", exception.admin_comment)
        self.assertIn("Earlier: approved by Gift_HR", exception.admin_comment)  # the history is kept
        self.assertFalse(self.lines().exists())
        self.payroll.refresh_from_db()
        self.assertEqual(self.payroll.total_deductions, Decimal("0.00"))
        self.assertTrue(AuditEvent.objects.filter(event_type="attendance.exception_changed", employee=self.employee).exists())

    def test_a_reversal_made_by_mistake_can_be_put_back(self):
        exception = self.case(status="waived")
        change_decision(exception, "approved", "The employee's claim was not borne out by the punches.", self.actor)
        exception.refresh_from_db()
        self.assertEqual((exception.status, exception.proposed_deduction), ("approved", Decimal("700.00")))
        sync_attendance_deductions_for_period(self.period)
        self.assertEqual([line.amount for line in self.lines()], [Decimal("700.00")])

    def test_refusals(self):
        pending, decided = self.case(day=3, status="pending"), self.case(day=4)
        with self.assertRaisesMessage(ValueError, "not been decided"):
            change_decision(pending, "waived", "x", self.actor)
        with self.assertRaisesMessage(ValueError, "already charged"):
            change_decision(decided, "approved", "x", self.actor)
        with self.assertRaisesMessage(ValueError, "Say why"):
            change_decision(decided, "waived", "  ", self.actor)
        punch = self.case(day=5, kind="late")
        AttendanceException.objects.filter(pk=punch.pk).update(exception_type="missing_clock_out")
        with self.assertRaisesMessage(ValueError, "Only lateness"):
            change_decision(punch, "waived", "x", self.actor)

    def test_an_approved_payroll_cannot_be_changed(self):
        exception = self.case()
        PayrollPeriod.objects.filter(pk=self.period.pk).update(status="approved")
        with self.assertRaisesMessage(ValueError, "already approved"):
            change_decision(exception, "waived", "Contested", self.actor)
        exception.refresh_from_db()
        self.assertEqual(exception.status, "approved")

    def test_reversing_an_absence_refreshes_the_meal_penalty(self):
        exception = self.case(day=6, kind="absence", minutes=0)
        with mock.patch("meals.services.MealService.sync_absence_penalties") as sync:
            change_decision(exception, "waived", "Was on approved leave that day.", self.actor)
        sync.assert_called_once()

    def test_the_endpoint_needs_the_permission(self):
        exception = self.case()
        client = APIClient()
        client.force_authenticate(get_user_model().objects.create_user(username="nobody-rev", password="pw"))
        self.assertEqual(client.post(f"/api/attendance/exceptions/{exception.pk}/change-decision/", {"decision": "waived", "reason": "x"}, format="json").status_code, 403)
        client.force_authenticate(self.actor)
        response = client.post(f"/api/attendance/exceptions/{exception.pk}/change-decision/", {"decision": "waived", "reason": "Checked the punches"}, format="json")
        self.assertEqual((response.status_code, response.json()["exception"]["status"]), (200, "waived"))
        self.assertEqual(client.post(f"/api/attendance/exceptions/{exception.pk}/change-decision/", {"decision": "waived", "reason": "again"}, format="json").status_code, 400)


class StatementShowsChargesToCheckTests(TestCase):
    """The statement lists each lateness / early-departure / absence case with its punches, and offers the change
    only to people allowed to make it."""

    def setUp(self):
        today = timezone.localdate()
        self.day = today.replace(day=1)
        if self.day == today:  # first of the month: use the same day, the statement shows up to today
            self.day = today
        self.employee = Employee.objects.create(employee_id="REV-002", first_name="State", last_name="Ment", basic_salary=Decimal("90000.00"))
        shift = Shift.objects.create(name="Statement Rev Shift", start_time="07:00", end_time="19:00")
        start = timezone.make_aware(datetime.combine(self.day, datetime.min.time().replace(hour=7)))
        attendance = DailyAttendance.objects.create(employee=self.employee, date=self.day, shift=shift, status="late", late_minutes=40, scheduled_start=start, scheduled_end=start + timedelta(hours=12), actual_clock_in=start + timedelta(minutes=40), actual_clock_out=start + timedelta(hours=12))
        self.case = AttendanceException.objects.create(attendance=attendance, exception_type="late", minutes_affected=40, proposed_deduction=Decimal("700"), status="approved", reviewed_by="Gift_HR", reviewed_at=timezone.now())
        self.viewer = get_user_model().objects.create_user(username="stmt-viewer", password="pw")
        self.viewer.user_permissions.add(Permission.objects.get(codename="view_employee_statement"))
        self.client = APIClient()

    def statement(self, user):
        self.client.force_authenticate(user)
        return self.client.get(f"/api/reports/employee-statement/?employee={self.employee.pk}&year={self.day.year}&month={self.day.month}").json()

    def test_the_case_is_listed_with_its_punches_and_no_amount(self):
        data = self.statement(self.viewer)
        case = data["attendance_cases"][0]
        self.assertEqual((case["kind"], case["minutes"], case["clock_in"], case["status_label"]), ("Late arrival", 40, "07:40", "Charged"))
        self.assertEqual(case["scheduled"], "07:00 - 19:00")
        self.assertNotIn("amount", case)
        self.assertNotIn("proposed_deduction", case)
        self.assertFalse(case["can_change"])
        self.assertFalse(data["can_change_cases"])

    def test_only_someone_allowed_can_change_it(self):
        changer = get_user_model().objects.create_user(username="stmt-changer", password="pw")
        changer.user_permissions.add(Permission.objects.get(codename="view_employee_statement"), Permission.objects.get(codename="reverse_attendance_charge"))
        data = self.statement(changer)
        self.assertTrue(data["can_change_cases"])
        self.assertTrue(data["attendance_cases"][0]["can_change"])

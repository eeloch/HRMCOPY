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
        AttendanceException.objects.filter(pk=punch.pk).update(exception_type="hostel_violation")
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
        self.assertIsNone(case["amount"])  # an amount is shown only for a missing clock-in/out fine, and only to someone who can change it
        self.assertNotIn("proposed_deduction", case)
        self.assertFalse(case["can_change"])
        self.assertFalse(data["can_change_cases"])

    def test_only_someone_allowed_can_change_it(self):
        changer = get_user_model().objects.create_user(username="stmt-changer", password="pw")
        changer.user_permissions.add(Permission.objects.get(codename="view_employee_statement"), Permission.objects.get(codename="reverse_attendance_charge"))
        data = self.statement(changer)
        self.assertTrue(data["can_change_cases"])
        self.assertTrue(data["attendance_cases"][0]["can_change"])


class LeaveClearsAbsenceTests(TestCase):
    """Leave approved after the absences were decided: the days become leave and everything they caused is undone."""

    def setUp(self):
        from leave.models import LeaveRequest, LeaveStatus, LeaveType
        from meals.models import MealAbsencePenalty  # noqa: F401

        self.employee = Employee.objects.create(employee_id="LVC-001", first_name="Sick", last_name="Day", basic_salary=Decimal("150000.00"))
        self.shift = Shift.objects.create(name="Leave Cover Shift", start_time="07:00", end_time="19:00")
        self.period = PayrollPeriod.objects.create(year=2032, month=6)
        self.payroll = EmployeePayroll.objects.create(payroll_period=self.period, employee=self.employee, basic_salary=Decimal("150000.00"), gross_earnings=Decimal("150000.00"), net_pay=Decimal("150000.00"))
        for day in range(1, 31):
            EmployeeRosterDay.objects.get_or_create(employee=self.employee, date=date(2032, 6, day), defaults={"status": "work", "shift": self.shift, "source": "manual"})
        self.actor = get_user_model().objects.create_user(username="leaver", password="pw")
        leave_type = LeaveType.objects.create(name="Sick Cover", code="SICK-COVER")
        self.leave = LeaveRequest.objects.create(request_number="LV-COVER-1", employee=self.employee, leave_type=leave_type, start_date=date(2032, 6, 3), end_date=date(2032, 6, 4), total_days=2, reason="Sick", status=LeaveStatus.PENDING)
        self.days = {}
        for day in (2, 3, 4, 8):
            attendance = DailyAttendance.objects.create(employee=self.employee, date=date(2032, 6, day), shift=self.shift, status="absent")
            AttendanceException.objects.create(attendance=attendance, exception_type="absence", minutes_affected=720, status="approved", reviewed_by="HR", reviewed_at=timezone.now())
            self.days[day] = attendance

    def approve_leave(self):
        from leave.models import LeaveStatus
        from leave.services.approval import LeaveApprovalService

        LeaveApprovalService._apply_decision(self.leave, self.actor, status=LeaveStatus.APPROVED, approved_start_date=date(2032, 6, 3), approved_end_date=date(2032, 6, 4), approved_days=2)

    def penalties(self):
        from meals.models import MealAbsencePenalty, MealAbsencePenaltyStatus

        return MealAbsencePenalty.objects.filter(employee=self.employee, status=MealAbsencePenaltyStatus.ACTIVE)

    def test_approving_leave_turns_covered_absences_into_leave_and_undoes_their_meal_penalty(self):
        from meals.services import MealService

        MealService.sync_absence_penalties(self.employee, date(2032, 6, 8))
        self.assertTrue(self.penalties().filter(penalty_type="two_consecutive").exists())

        self.approve_leave()

        for day in (3, 4):
            self.days[day].refresh_from_db()
            self.assertEqual(self.days[day].status, "leave")
            exception = self.days[day].exceptions.get()
            self.assertEqual(exception.status, "waived")
            self.assertIn("LV-COVER-1", exception.admin_comment)
        for day in (2, 8):
            self.days[day].refresh_from_db()
            self.assertEqual(self.days[day].status, "absent")
            self.assertEqual(self.days[day].exceptions.get().status, "approved")
        # Only the two uncovered absences (2 and 8 June) still count; the leave pair and the monthly trigger are gone.
        remaining = list(self.penalties())
        self.assertEqual([p.penalty_type for p in remaining], ["single_absence", "single_absence"])
        self.assertEqual(sorted(sum((p.source_absence_dates for p in remaining), [])), ["2032-06-02", "2032-06-08"])

    def test_a_pending_absence_inside_leave_is_dropped_and_the_day_becomes_leave(self):
        AttendanceException.objects.filter(attendance=self.days[3]).update(status="pending")
        self.approve_leave()
        self.days[3].refresh_from_db()
        self.assertEqual(self.days[3].status, "leave")
        self.assertFalse(self.days[3].exceptions.exists())

    def test_a_day_with_a_punch_is_left_alone(self):
        DailyAttendance.objects.filter(pk=self.days[3].pk).update(status="incomplete")
        self.approve_leave()
        self.days[3].refresh_from_db()
        self.assertEqual(self.days[3].status, "incomplete")

    def test_a_day_in_an_approved_payroll_is_left_as_it_is(self):
        PayrollPeriod.objects.filter(pk=self.period.pk).update(status="approved")
        self.approve_leave()
        self.days[3].refresh_from_db()
        self.assertEqual(self.days[3].status, "absent")
        self.assertEqual(self.days[3].exceptions.get().status, "approved")

    def test_processing_a_leave_day_clears_a_leftover_absence(self):
        from leave.models import LeaveStatus

        type(self.leave).objects.filter(pk=self.leave.pk).update(status=LeaveStatus.APPROVED, approved_start_date=date(2032, 6, 3), approved_end_date=date(2032, 6, 4), approved_days=2)
        from attendance.services.processing import process_employee_attendance

        process_employee_attendance(self.employee, date(2032, 6, 3), now=timezone.make_aware(datetime(2032, 6, 20, 12)))
        self.days[3].refresh_from_db()
        self.assertEqual(self.days[3].status, "leave")
        self.assertEqual(self.days[3].exceptions.get().status, "waived")

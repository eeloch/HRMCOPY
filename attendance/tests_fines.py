from datetime import date, datetime, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from employees.models import Employee
from payroll.models import EmployeePayroll, PayrollLineItem, PayrollPeriod
from payroll.services.attendance import sync_attendance_deductions_for_period

from .models import AttendanceEvent, AttendanceException, DailyAttendance, EmployeeRosterDay, Shift
from .services.processing import process_employee_attendance
from .services.reversal import change_decision, change_fine_amount


class MissingPunchFineAndAutoAbsenceTests(TestCase):
    """A missing clock-in / clock-out is fined N1,000 at least once approved (the amount can be raised); an absence is approved
    the moment it is recorded."""

    def setUp(self):
        self.employee = Employee.objects.create(employee_id="FIN-001", first_name="Fine", last_name="Test", basic_salary=Decimal("150000.00"))
        self.shift = Shift.objects.create(name="Fine Shift", start_time="07:00", end_time="19:00")
        self.period = PayrollPeriod.objects.create(year=2032, month=6)
        self.payroll = EmployeePayroll.objects.create(payroll_period=self.period, employee=self.employee, basic_salary=Decimal("150000.00"), gross_earnings=Decimal("150000.00"), net_pay=Decimal("150000.00"))
        for day in range(1, 31):
            EmployeeRosterDay.objects.get_or_create(employee=self.employee, date=date(2032, 6, day), defaults={"status": "work", "shift": self.shift, "source": "manual"})
        self.after = timezone.make_aware(datetime(2032, 6, 20, 12, 0))
        self.actor = get_user_model().objects.create_user(username="fine-reviewer", password="pw", first_name="Fin", last_name="Reviewer")
        self.actor.user_permissions.add(Permission.objects.get(codename="review_attendanceexception"), Permission.objects.get(codename="reverse_attendance_charge"))

    def punch(self, day, hour, minute, tag):
        AttendanceEvent.objects.create(employee=self.employee, timestamp=timezone.make_aware(datetime(2032, 6, day, hour, minute)), external_event_id=f"fin-{day}-{tag}")

    def lone_clock_in(self, day=3):
        self.punch(day, 7, 0, "in")  # in on time, never clocked out
        process_employee_attendance(self.employee, date(2032, 6, day), now=self.after)
        return AttendanceException.objects.get(attendance__employee=self.employee, attendance__date=date(2032, 6, day), exception_type="missing_clock_out")

    def lines(self):
        return PayrollLineItem.objects.filter(payroll=self.payroll, is_system_generated=True)

    def decide(self, exception, **body):
        client = APIClient()
        client.force_authenticate(self.actor)
        return client.post(f"/api/attendance/exceptions/{exception.pk}/decision/", {"decision": "approved", "comment": "", **body}, format="json")

    def test_a_missing_clock_out_proposes_the_minimum_fine(self):
        exception = self.lone_clock_in()
        self.assertEqual((exception.status, exception.proposed_deduction), ("pending", Decimal("1000.00")))

    def test_approving_it_puts_the_fine_in_payroll(self):
        exception = self.lone_clock_in()
        self.assertEqual(self.decide(exception).status_code, 200)
        sync_attendance_deductions_for_period(self.period)
        self.assertEqual([line.amount for line in self.lines()], [Decimal("1000.00")])
        self.assertEqual(self.lines().get().code, "ATTENDANCE_MISSING_CLOCK_OUT")

    def test_the_fine_can_be_raised_when_approving_but_never_below_the_minimum(self):
        exception = self.lone_clock_in()
        self.assertEqual(self.decide(exception, amount="900").status_code, 400)
        self.assertEqual(self.decide(exception, amount="3500").status_code, 200)
        sync_attendance_deductions_for_period(self.period)
        self.assertEqual([line.amount for line in self.lines()], [Decimal("3500.00")])

    def test_an_amount_is_refused_on_other_kinds_of_case(self):
        self.punch(4, 7, 40, "late-in")
        self.punch(4, 19, 0, "late-out")
        process_employee_attendance(self.employee, date(2032, 6, 4), now=self.after)
        late = AttendanceException.objects.get(attendance__employee=self.employee, exception_type="late")
        self.assertEqual(self.decide(late, amount="2000").status_code, 400)

    def test_an_approved_fine_can_be_raised_later_with_a_reason_and_reversed(self):
        exception = self.lone_clock_in()
        self.decide(exception)
        sync_attendance_deductions_for_period(self.period)
        with self.assertRaisesMessage(ValueError, "at least"):
            change_fine_amount(exception, "500", "x", self.actor)
        with self.assertRaisesMessage(ValueError, "Say why"):
            change_fine_amount(exception, "2500", " ", self.actor)
        change_fine_amount(exception, "2500", "He actually left at 10am.", self.actor)
        self.assertEqual([line.amount for line in self.lines()], [Decimal("2500.00")])
        change_decision(exception, "waived", "Forgot the scan; supervisor confirms he stayed.", self.actor)
        self.assertFalse(self.lines().exists())

    def test_a_missing_punch_on_a_flexible_shift_is_not_fined(self):
        flexible = Shift.objects.create(name="Fine Flex", start_time="00:00", end_time="23:59:59", is_flexible=True)
        EmployeeRosterDay.objects.filter(employee=self.employee, date=date(2032, 6, 8)).update(shift=flexible)
        self.punch(8, 9, 0, "flex")
        process_employee_attendance(self.employee, date(2032, 6, 8), now=self.after)
        for exception in AttendanceException.objects.filter(attendance__employee=self.employee, attendance__date=date(2032, 6, 8), exception_type__startswith="missing"):
            self.assertEqual(exception.proposed_deduction, Decimal("0.00"))

    def test_an_absence_is_approved_the_moment_it_is_recorded_and_can_be_reviewed_later(self):
        attendance = process_employee_attendance(self.employee, date(2032, 6, 5), now=self.after)
        exception = attendance.exceptions.get(exception_type="absence")
        self.assertEqual((attendance.status, exception.status, exception.reviewed_by), ("absent", "approved", "System (automatic)"))
        from meals.models import MealAbsencePenalty

        self.assertTrue(MealAbsencePenalty.objects.filter(employee=self.employee, source_absence_dates=["2032-06-05"]).exists())
        sync_attendance_deductions_for_period(self.period)
        self.assertEqual(self.lines().get().code, "ATTENDANCE_ABSENCE")
        change_decision(exception, "waived", "Was on an approved errand.", self.actor)  # reviewed later
        self.assertFalse(self.lines().exists())

    def test_a_proposed_absence_from_lateness_still_waits_for_a_person(self):
        self.punch(6, 14, 0, "very-late")
        self.punch(6, 19, 0, "out")
        process_employee_attendance(self.employee, date(2032, 6, 6), now=self.after)
        proposed = AttendanceException.objects.get(attendance__employee=self.employee, attendance__date=date(2032, 6, 6), exception_type="absence")
        self.assertEqual(proposed.status, "pending")

    def test_punches_that_arrive_later_withdraw_the_automatic_absence(self):
        attendance = process_employee_attendance(self.employee, date(2032, 6, 7), now=self.after)
        self.assertEqual(attendance.exceptions.get(exception_type="absence").status, "approved")
        self.punch(7, 7, 2, "later-in")  # a terminal reconnects and its stored scans arrive
        self.punch(7, 19, 1, "later-out")
        attendance = process_employee_attendance(self.employee, date(2032, 6, 7), now=self.after)
        self.assertEqual(attendance.status, "present")
        self.assertEqual(attendance.exceptions.get(exception_type="absence").status, "waived")


class AttendanceHistoryShowsChargesTests(MissingPunchFineAndAutoAbsenceTests):
    """The history says what each day's case amounted to - for people who may see money only."""

    def history(self, user):
        client = APIClient()
        client.force_authenticate(user)
        return client.get(f"/api/attendance/records/?employee={self.employee.pk}").json()["results"]

    def test_a_charge_shows_its_state_and_amount_to_someone_who_may_see_money(self):
        exception = self.lone_clock_in()
        row = next(item for item in self.history(self.actor) if item["date"] == "2032-06-03")
        self.assertEqual([(c["label"], c["state"], c["amount"]) for c in row["charges"]], [("No clock-out", "Proposed", "1000.00")])
        self.decide(exception, amount="2500")
        sync_attendance_deductions_for_period(self.period)
        row = next(item for item in self.history(self.actor) if item["date"] == "2032-06-03")
        self.assertEqual([(c["state"], c["amount"]) for c in row["charges"]], [("Charged", "2500.00")])  # the payroll deduction itself
        change_decision(exception, "waived", "Forgot the scan.", self.actor)
        row = next(item for item in self.history(self.actor) if item["date"] == "2032-06-03")
        self.assertEqual([(c["state"], c["amount"]) for c in row["charges"]], [("Waived", "0.00")])

    def test_someone_without_money_access_sees_the_state_but_no_amount(self):
        self.lone_clock_in()
        plain = get_user_model().objects.create_user(username="plain-viewer", password="pw")
        plain.user_permissions.add(Permission.objects.get(codename="manage_devices"))  # may open attendance pages, not see money
        row = next(item for item in self.history(plain) if item["date"] == "2032-06-03")
        self.assertEqual([(c["state"], c["amount"]) for c in row["charges"]], [("Proposed", None)])

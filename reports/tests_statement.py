import json
from datetime import date, datetime
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from advances.models import AdvanceStatus, SalaryAdvance
from attendance.models import DailyAttendance, EmployeeRosterDay, RosterDayStatus, Shift
from audit.models import AuditEvent
from bonuses.models import Bonus, BonusKind, BonusStatus
from employees.models import Department, Employee
from meals.models import MealExcessException, MealExcessStatus
from offences.models import EmployeeOffence, EmployeeOffenceStatus, OffenceType
from payroll.models import EmployeePayroll, PayrollLineItem, PayrollPeriod

from .statement import build_statement


class EmployeeStatementTests(TestCase):
    """What HR can show an employee: attendance and charges for a month - and never any pay."""

    TODAY = date(2026, 10, 20)

    def setUp(self):
        self.dept = Department.objects.create(name="Packaging")
        self.employee = Employee.objects.create(employee_id="ST-001", first_name="Stella", last_name="Obi", department=self.dept, basic_salary=Decimal("187654.32"))
        self.shift = Shift.objects.create(name="Day", start_time="07:00", end_time="19:00", is_overnight=False)
        stamp = lambda day, hour, minute: timezone.make_aware(datetime(2026, 9, day, hour, minute))  # noqa: E731
        rows = [
            (1, "present", stamp(1, 6, 55), stamp(1, 19, 5), 0, 600),
            (2, "late", stamp(2, 7, 20), stamp(2, 19, 0), 20, 580),
            (3, "absent", None, None, 0, 0),
            (4, "leave", None, None, 0, 0),
        ]
        for day, status, clock_in, clock_out, late, minutes in rows:
            DailyAttendance.objects.create(employee=self.employee, date=date(2026, 9, day), status=status, shift=self.shift, actual_clock_in=clock_in, actual_clock_out=clock_out, late_minutes=late, worked_minutes=minutes)
        EmployeeRosterDay.objects.create(employee=self.employee, date=date(2026, 9, 5), status=RosterDayStatus.REST)
        for day in (1, 2, 3):
            EmployeeRosterDay.objects.create(employee=self.employee, date=date(2026, 9, day), status=RosterDayStatus.WORK, shift=self.shift)

        kind = OffenceType.objects.create(category="Attendance", name="Late arrival after 7:00", penalty_first="₦500")
        EmployeeOffence.objects.create(employee=self.employee, offence_type=kind, amount=Decimal("500.00"), occurrence=1, incident_date=date(2026, 9, 2), status=EmployeeOffenceStatus.APPROVED)
        EmployeeOffence.objects.create(employee=self.employee, offence_type=kind, amount=Decimal("800.00"), occurrence=2, incident_date=date(2026, 9, 9), status=EmployeeOffenceStatus.PENDING)
        EmployeeOffence.objects.create(employee=self.employee, offence_type=kind, amount=Decimal("999.00"), occurrence=3, incident_date=date(2026, 9, 10), status=EmployeeOffenceStatus.REJECTED)
        MealExcessException.objects.create(employee=self.employee, work_date=date(2026, 9, 3), entitlement_snapshot=1, collected_quantity=2, excess_quantity=1, rate_snapshot=Decimal("700.00"), proposed_deduction=Decimal("700.00"), status=MealExcessStatus.APPROVED)
        MealExcessException.objects.create(employee=self.employee, work_date=date(2026, 9, 4), entitlement_snapshot=1, collected_quantity=2, excess_quantity=1, rate_snapshot=Decimal("700.00"), proposed_deduction=Decimal("700.00"), status=MealExcessStatus.DECLINED)
        Bonus.objects.create(employee=self.employee, kind=BonusKind.PERFORMANCE, amount=Decimal("1000.00"), reason="No absence", performance_year=2026, performance_month=9, pay_year=2026, pay_month=9, status=BonusStatus.PROPOSED)
        # Payroll's own absence deduction (daily rate times days) - this one would give the salary away.
        period = PayrollPeriod.objects.create(year=2026, month=9)
        payroll = EmployeePayroll.objects.create(payroll_period=period, employee=self.employee, basic_salary=Decimal("187654.32"), gross_earnings=Decimal("187654.32"), net_pay=Decimal("187654.32"))
        PayrollLineItem.objects.create(payroll=payroll, item_type="deduction", code="ATTENDANCE_ABSENCE", description="Unauthorized Absence - 2026-09-03", amount=Decimal("6255.14"), source_type="attendance_exception", source_reference="1", metadata={"daily_rate": "6255.14"})

    def statement(self):
        return build_statement(self.employee, 2026, 9, today=self.TODAY)

    def test_the_attendance_record_and_totals(self):
        result = self.statement()
        summary = result["attendance_summary"]
        self.assertEqual((summary["present"], summary["late"], summary["absent"], summary["leave"], summary["rest_days"]), (1, 1, 1, 1, 1))
        self.assertEqual((summary["days_worked"], summary["late_minutes"], summary["hours_worked"], summary["scheduled_work_days"]), (2, 20, 19.67, 3))
        self.assertEqual(len(result["days"]), 30)
        first = result["days"][0]
        self.assertEqual((first["status"], first["clock_in"], first["clock_out"], first["shift"]), ("present", "06:55", "19:05", "Day"))
        self.assertEqual(result["absent_dates"], ["2026-09-03"])

    def test_charges_show_what_is_coming_and_leave_out_rejected_or_declined_ones(self):
        result = self.statement()
        got = {(item["kind"], item["amount"], item["status"]) for item in result["charges"]}
        self.assertEqual(got, {("Offence", "500.00", "approved"), ("Offence", "800.00", "pending"), ("Extra meal", "700.00", "approved")})
        self.assertEqual(result["charges_total"], "1200.00")
        self.assertEqual(result["charges_pending_total"], "800.00")

    def test_rewards_are_listed(self):
        result = self.statement()
        self.assertEqual([(r["description"], r["amount"], r["status_label"]) for r in result["rewards"]], [("Performance bonus", "1000.00", "Awaiting approval")])

    def test_a_penalty_that_is_not_money_is_still_listed(self):
        kind = OffenceType.objects.create(name="Sleeping at work", penalty_first="Verbal Warning")
        EmployeeOffence.objects.create(employee=self.employee, offence_type=kind, amount=Decimal("0.00"), occurrence=1, penalty_text="Verbal Warning", incident_date=date(2026, 9, 12), status=EmployeeOffenceStatus.APPROVED)
        warning = next(item for item in self.statement()["charges"] if "Sleeping" in item["description"])
        self.assertIn("Verbal Warning", warning["description"])
        self.assertEqual(warning["amount"], "0.00")

    def test_an_advance_instalment_is_expected_until_it_is_taken(self):
        SalaryAdvance.objects.create(employee=self.employee, amount=Decimal("30000.00"), repayment_months=3, deduct_from_year=2026, deduct_from_month=9, status=AdvanceStatus.PAID)
        advance = next(item for item in self.statement()["charges"] if item["kind"] == "Salary advance")
        self.assertEqual((advance["amount"], advance["status"]), ("10000.00", "expected"))

    def test_no_pay_information_appears_anywhere_in_the_statement(self):
        text = json.dumps(self.statement())
        for secret in ("187654", "187,654", "6255", "daily_rate", "basic_salary", "gross", "net_pay", "salary"):
            self.assertNotIn(secret, text.replace("Salary advance", "").replace("salary advance", ""), secret)
        # The absence is mentioned in days, with a note that payroll deducts it - but no amount.
        self.assertIn("amount is not shown", self.statement()["attendance_deduction_note"])

    def test_the_current_month_is_provisional_and_a_future_month_is_refused(self):
        self.assertTrue(build_statement(self.employee, 2026, 10, today=self.TODAY)["period"]["provisional"])
        self.assertFalse(self.statement()["period"]["provisional"])
        with self.assertRaisesMessage(ValueError, "not started"):
            build_statement(self.employee, 2026, 11, today=self.TODAY)


class EmployeeStatementAccessTests(TestCase):
    def setUp(self):
        self.employee = Employee.objects.create(employee_id="ST-002", first_name="Ada", last_name="Eze", basic_salary=Decimal("90000.00"))
        self.hr = get_user_model().objects.create_user(username="statement-hr", password="pw")
        self.hr.user_permissions.add(Permission.objects.get(codename="view_employee_statement"))
        self.other = get_user_model().objects.create_user(username="statement-other", password="pw")
        self.url = f"/api/reports/employee-statement/?employee={self.employee.pk}&year={timezone.localdate().year}&month={timezone.localdate().month}"

    def get(self, user, url=None):
        client = APIClient()
        client.force_authenticate(user)
        return client.get(url or self.url)

    def test_hr_with_the_permission_gets_the_statement_and_it_is_audited(self):
        response = self.get(self.hr)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("90000", json.dumps(response.json()))
        event = AuditEvent.objects.get(event_type="reports.employee_statement_generated")
        self.assertEqual((event.actor, event.employee), (self.hr, self.employee))

    def test_someone_without_the_permission_is_refused(self):
        self.assertEqual(self.get(self.other).status_code, 403)
        self.assertFalse(AuditEvent.objects.filter(event_type="reports.employee_statement_generated").exists())

    def test_the_statement_permission_does_not_open_up_salary(self):
        client = APIClient()
        client.force_authenticate(self.hr)
        self.assertNotIn("basic_salary", client.get(f"/api/employees/{self.employee.pk}/").json())

    def test_bad_input_is_a_clear_error(self):
        self.assertEqual(self.get(self.hr, "/api/reports/employee-statement/").status_code, 400)
        self.assertEqual(self.get(self.hr, f"/api/reports/employee-statement/?employee={self.employee.pk}&year=2099&month=1").status_code, 400)
        self.assertEqual(self.get(self.hr, "/api/reports/employee-statement/?employee=999999").status_code, 404)

    def test_the_permission_is_offered_in_settings(self):
        from core.permissions_registry import MANAGED_PERMISSION_CODENAMES

        self.assertIn("view_employee_statement", MANAGED_PERMISSION_CODENAMES)

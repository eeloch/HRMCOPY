from decimal import Decimal

from django.test import TestCase

from employees.models import Employee
from payroll.models import EmployeePayroll, PayrollLineItem, PayrollPeriod
from payroll.serializers import EmployeePayrollListSerializer
from payroll.services.breakdown import breakdown


class DeductionBreakdownTests(TestCase):
    def setUp(self):
        self.employee = Employee.objects.create(employee_id="BRK-001", first_name="Break", last_name="Down", basic_salary=Decimal("100000.00"))
        self.period = PayrollPeriod.objects.create(year=2033, month=1)
        self.payroll = EmployeePayroll.objects.create(payroll_period=self.period, employee=self.employee, basic_salary=Decimal("100000.00"), gross_earnings=Decimal("100000.00"), net_pay=Decimal("100000.00"))

    def line(self, code, amount, source_type="", reference="", item_type="deduction"):
        return PayrollLineItem.objects.create(payroll=self.payroll, item_type=item_type, code=code, description=code, amount=Decimal(amount), source_type=source_type, source_reference=reference or code, is_system_generated=bool(source_type))

    def test_deductions_are_grouped_by_kind_in_display_order(self):
        self.line("DEFERRED_FUND", "5000", "deferred_fund")
        self.line("ATTENDANCE_ABSENCE", "3000", "attendance_exception", "1")
        self.line("ATTENDANCE_ABSENCE", "3000", "attendance_exception", "2")
        self.line("ATTENDANCE_LATE", "500", "attendance_exception", "3")
        self.line("MEAL_EXCESS", "700", "meal_excess", "4")
        self.line("MEAL_CHARGEBACK", "1400", "meal_chargeback", "5")
        self.line("TEST_BOOT", "2500", "ppe_issue", "6")
        self.line("MANUAL", "100")
        self.line("BONUS", "9999", item_type="earning")  # earnings are never part of it
        result = breakdown(self.payroll.line_items.all())
        self.assertEqual([(part["key"], part["amount"], part["count"]) for part in result], [
            ("absence", "6000.00", 2), ("late", "500.00", 1), ("meals", "2100.00", 2), ("ppe", "2500.00", 1), ("deferred_fund", "5000.00", 1), ("other", "100.00", 1),
        ])

    def test_the_breakdown_adds_up_to_the_deductions_and_is_on_the_payroll_list(self):
        self.line("ATTENDANCE_LATE", "300", "attendance_exception", "1")
        self.line("SALARY_ADVANCE", "10000", "salary_advance", "2")
        data = EmployeePayrollListSerializer(EmployeePayroll.objects.prefetch_related("line_items").get(pk=self.payroll.pk)).data
        self.assertEqual(sum(Decimal(part["amount"]) for part in data["deduction_breakdown"]), Decimal("10300.00"))
        self.assertEqual([part["label"] for part in data["deduction_breakdown"]], ["Lateness", "Salary advance"])

    def test_the_period_summary_can_list_every_kind_even_at_zero(self):
        self.line("ATTENDANCE_LATE", "300", "attendance_exception", "1")
        everything = breakdown(self.payroll.line_items.all(), include_empty=True)
        self.assertEqual([part["key"] for part in everything], ["absence", "late", "early_departure", "missing_punch", "unpaid_leave", "meals", "ppe", "salary_advance", "deferred_fund", "offences", "other"])
        self.assertEqual({part["key"]: (part["amount"], part["count"]) for part in everything}["ppe"], ("0.00", 0))
        self.assertEqual([part["key"] for part in breakdown(self.payroll.line_items.all())], ["late"])  # an employee's own list stays short


class SalaryEnteredAfterPayrollWasGeneratedTests(TestCase):
    """Someone added to a draft payroll before their salary was entered: Generate picks the salary up; no deductions on N0."""

    def test_generate_brings_a_draft_record_up_to_the_salary_now_and_sync_refuses_a_record_with_no_salary(self):
        from datetime import date as _date

        from payroll.services import generate_payroll_for_period
        from payroll.services.attendance import PayrollAttendanceSyncSummary, _sync_employee_payroll

        employee = Employee.objects.create(employee_id="LATE-SAL", first_name="Late", last_name="Salary", basic_salary=Decimal("0.00"), employment_date=_date(2033, 2, 1), status="active")
        period = PayrollPeriod.objects.create(year=2033, month=2)
        generate_payroll_for_period(period)
        record = EmployeePayroll.objects.get(payroll_period=period, employee=employee)
        self.assertEqual(record.basic_salary, Decimal("0.00"))
        summary = PayrollAttendanceSyncSummary()
        _sync_employee_payroll(record, summary)
        self.assertTrue(any("No salary" in error["detail"] for error in summary.errors))

        employee.basic_salary = Decimal("70000.00")
        employee.save()
        result = generate_payroll_for_period(period)
        record.refresh_from_db()
        self.assertEqual((record.basic_salary, record.net_pay, result.salary_refreshed), (Decimal("70000.00"), Decimal("70000.00"), 1))

import tempfile
from datetime import date
from decimal import Decimal
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from openpyxl import Workbook
from rest_framework.test import APIClient

from employees.models import Employee
from payroll.models import EmployeePayroll, PayrollLineItem, PayrollPeriod

from .models import DeferredFundAccount, DeferredFundEntry
from .services import DeferredFundService


def sheet(rows):
    workbook = Workbook()
    ws = workbook.active
    ws.append(["Employee ID", "Name", "Current Balance", "Contribution %", None])
    for row in rows:
        ws.append(list(row))
    handle = tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False)
    workbook.save(handle.name)
    return handle.name


class ImportDeferredFundSheetTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_user(username="admin", password="pw")
        self.perm = Employee.objects.create(employee_id="000001", first_name="Obi", last_name="Chidimma", employment_type="permanent", status="active")
        self.casual = Employee.objects.create(employee_id="000002", first_name="Egbunu", last_name="Jonathan", employment_type="casual", status="active")
        self.contract = Employee.objects.create(employee_id="000004", first_name="Job", last_name="Godfrey", employment_type="contract", status="active")
        self.left = Employee.objects.create(employee_id="000005", first_name="Left", last_name="Already", employment_type="permanent", status="inactive")
        self.typo = Employee.objects.create(employee_id="000033", first_name="Tyochi", last_name="Simeon", employment_type="permanent", status="active")

    def run_command(self, rows, *flags):
        out = StringIO()
        call_command("import_deferred_fund_sheet", sheet(rows), *flags, stdout=out)
        return out.getvalue()

    ROWS = [(1, "Obi Chidimma", 1200000.0, 0.15), (2, "Egbunu Jonathan", 492000.5, 0.1), (4, "Job Godfrey", 450000.0, 0.15), (5, "Left Already", 100000.0, 0.15), (32, "Tyochi Simeon", 297000.0, 0.09)]

    def test_a_dry_run_changes_nothing_and_says_what_it_would_do(self):
        text = self.run_command(self.ROWS)
        self.assertIn("DRY RUN", text)
        self.assertIn("5 to load", text)
        self.assertEqual((DeferredFundAccount.objects.count(), Employee.objects.get(pk=self.perm.pk).employment_type), (0, "permanent"))

    def test_applying_retypes_enrols_and_loads_balances(self):
        self.run_command(self.ROWS, "--apply")
        self.perm.refresh_from_db(); self.casual.refresh_from_db(); self.contract.refresh_from_db()
        self.assertEqual({self.perm.employment_type, self.casual.employment_type, self.contract.employment_type}, {"contract"})
        account = self.perm.deferred_fund
        self.assertEqual((account.percent, account.balance, account.active, account.contributions_from), (Decimal("15.00"), Decimal("1200000.00"), True, date(2026, 10, 1)))
        self.assertEqual((self.casual.deferred_fund.percent, self.casual.deferred_fund.balance), (Decimal("10.00"), Decimal("492000.50")))
        self.assertEqual(self.typo.deferred_fund.percent, Decimal("9.00"))  # 0.09 means 9%

    def test_someone_who_has_left_keeps_the_balance_but_no_monthly_deduction(self):
        self.run_command(self.ROWS, "--apply")
        account = self.left.deferred_fund
        self.assertEqual((account.active, account.balance), (False, Decimal("100000.00")))

    def test_a_wrong_staff_number_is_matched_by_the_unique_name(self):
        text = self.run_command(self.ROWS, "--apply")
        self.assertIn("Matched Tyochi Simeon to 000033 by name", text)
        self.assertTrue(DeferredFundAccount.objects.filter(employee=self.typo).exists())

    def test_problems_are_reported_and_existing_accounts_are_left_alone(self):
        DeferredFundService.enrol(employee=self.contract, percent=5, opening_balance=1000, actor=self.admin)
        text = self.run_command([(4, "Job Godfrey", 450000.0, 0.15), (999, "Nobody Known", 10.0, 0.1)], "--apply")
        self.assertIn("already has a deferred fund account", text)
        self.assertIn("no unique match", text)
        self.assertEqual(self.contract.deferred_fund.balance, Decimal("1000.00"))

    def test_running_it_again_does_not_duplicate(self):
        self.run_command(self.ROWS, "--apply")
        self.run_command(self.ROWS, "--apply")
        self.assertEqual(DeferredFundEntry.objects.filter(entry_type="opening").count(), 5)


class ContributionRulesTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_user(username="df-admin", password="pw")
        self.employee = Employee.objects.create(employee_id="000010", first_name="Con", last_name="Tract", employment_type="contract", status="active", basic_salary=Decimal("100000.00"))
        self.account = DeferredFundService.enrol(employee=self.employee, percent=15, opening_balance=50000, contributions_from=date(2026, 10, 1), actor=self.admin)

    def payroll(self, month, salary=Decimal("100000.00")):
        period = PayrollPeriod.objects.create(year=2026, month=month)
        record = EmployeePayroll.objects.create(payroll_period=period, employee=self.employee, basic_salary=salary, gross_earnings=salary, net_pay=salary)
        return period, record

    def test_a_month_before_the_start_date_is_not_deducted(self):
        september, record = self.payroll(9)
        self.assertEqual(DeferredFundService.apply_for_period(september), 0)
        self.assertFalse(PayrollLineItem.objects.filter(payroll=record).exists())

    def test_the_month_from_the_start_date_is_deducted_and_shown(self):
        october, record = self.payroll(10)
        self.assertEqual(DeferredFundService.apply_for_period(october), 1)
        line = PayrollLineItem.objects.get(payroll=record, code="DEFERRED_FUND")
        self.assertEqual((line.amount, line.item_type), (Decimal("15000.00"), "deduction"))
        self.assertEqual(self.account.balance, Decimal("65000.00"))
        DeferredFundService.apply_for_period(october)  # repeating changes nothing
        self.assertEqual(PayrollLineItem.objects.filter(payroll=record, code="DEFERRED_FUND").count(), 1)

    def test_with_no_salary_the_line_is_still_shown_at_zero_with_the_reason(self):
        october, record = self.payroll(10, salary=Decimal("0.00"))
        DeferredFundService.apply_for_period(october)
        line = PayrollLineItem.objects.get(payroll=record, code="DEFERRED_FUND")
        self.assertEqual(line.amount, Decimal("0.00"))
        self.assertIn("no salary on file", line.description)
        self.assertEqual(self.account.balance, Decimal("50000.00"))  # nothing was added to the ledger
        DeferredFundService.apply_for_period(october)
        self.assertEqual(PayrollLineItem.objects.filter(payroll=record, code="DEFERRED_FUND").count(), 1)


class ContractWithoutFundNeedsAttentionTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="dir-viewer", password="pw")
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        base = dict(status="active", basic_salary=Decimal("90000"), bank_name="GTB", account_number="0123456789", bank_code="058", biometric_user_id="B")
        self.without = Employee.objects.create(employee_id="000020", first_name="No", last_name="Fund", employment_type="contract", **base)
        self.with_fund = Employee.objects.create(employee_id="000021", first_name="Has", last_name="Fund", employment_type="contract", **base)
        self.casual = Employee.objects.create(employee_id="000022", first_name="Plain", last_name="Casual", employment_type="casual", **base)
        DeferredFundService.enrol(employee=self.with_fund, percent=10, actor=self.user)
        from attendance.models import Shift, ShiftPlan
        from attendance.services.shift_plans import assign_plan

        shift = Shift.objects.create(name="DF Day", start_time="07:00", end_time="19:00")
        plan = ShiftPlan.objects.create(name="DF Perm", kind="fixed", shift=shift, working_weekdays=[0, 1, 2, 3, 4, 5])
        assign_plan([self.without, self.with_fund, self.casual], plan, start_date=date(2026, 1, 1))  # so a missing plan is not what flags them

    def reasons(self):
        rows = self.client.get("/api/employees/?status=active").json()["results"]
        return {row["employee_id"]: row["attention_reasons"] for row in rows}

    def test_only_contract_staff_without_an_active_fund_are_flagged(self):
        reasons = self.reasons()
        self.assertIn("Contract staff not on the deferred fund list", reasons["000020"])
        self.assertNotIn("Contract staff not on the deferred fund list", reasons["000021"])
        self.assertEqual(reasons["000022"], [])

    def test_the_filter_and_the_count_include_them(self):
        rows = {row["employee_id"] for row in self.client.get("/api/employees/?needs_attention=1&status=active").json()["results"] if True}
        self.assertEqual(rows, {"000020"})
        summary = self.client.get("/api/employees/summary/").json()
        self.assertEqual((summary["needs_attention"], summary["contract_without_deferred_fund"]), (1, 1))

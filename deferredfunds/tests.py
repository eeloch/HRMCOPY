from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from rest_framework.test import APIClient

from employees.models import Employee
from payroll.models import EmployeePayroll, PayrollPeriod
from payroll.services import generate_payroll_for_period

from .models import DeferredFundAccount, EntryType
from .services import DeferredFundService


def user_with(name, *codenames):
    user = get_user_model().objects.create_user(name, password="pw")
    user.user_permissions.add(*Permission.objects.filter(codename__in=codenames))
    return user


class DeferredFundTests(TestCase):
    def setUp(self):
        self.hr = user_with("hr", "manage_deferred_funds", "view_deferred_funds")
        self.boss = user_with("boss", "approve_deferred_withdrawal")
        self.finance = user_with("fin", "pay_deferred_withdrawal")
        self.staff = Employee.objects.create(employee_id="C001", first_name="Con", last_name="Tract", employment_type="contract", basic_salary=Decimal("200000.00"))
        self.permanent = Employee.objects.create(employee_id="P001", first_name="Per", last_name="Manent", employment_type="permanent", basic_salary=Decimal("200000.00"))
        self.sept = PayrollPeriod.objects.create(year=2026, month=9)
        self.oct = PayrollPeriod.objects.create(year=2026, month=10)

    def enrol(self, percent="15", opening="0"):
        return DeferredFundService.enrol(employee=self.staff, percent=Decimal(percent), opening_balance=Decimal(opening), actor=self.hr)

    def generate(self, period):
        generate_payroll_for_period(period, actor=self.hr)
        return EmployeePayroll.objects.get(payroll_period=period, employee=self.staff)

    def test_only_contract_staff_can_be_enrolled(self):
        with self.assertRaises(ValueError):
            DeferredFundService.enrol(employee=self.permanent, percent=Decimal("15"), actor=self.hr)

    def test_each_person_has_their_own_percentage_taken_from_basic_salary(self):
        self.enrol("10")
        payroll = self.generate(self.sept)
        self.assertEqual(payroll.total_deductions, Decimal("20000.00"))
        self.assertEqual(payroll.net_pay, Decimal("180000.00"))
        self.assertEqual(payroll.line_items.get(code="DEFERRED_FUND").description, "Deferred fund contribution (10%)")

    def test_balance_grows_each_month_and_regenerating_never_double_counts(self):
        account = self.enrol("15")
        self.generate(self.sept)
        self.generate(self.sept)
        self.generate(self.oct)
        self.assertEqual(account.balance, Decimal("60000.00"))

    def test_opening_balance_is_counted(self):
        account = self.enrol("15", opening="120000")
        self.generate(self.sept)
        self.assertEqual(account.balance, Decimal("150000.00"))
        self.assertTrue(account.entries.filter(entry_type=EntryType.OPENING).exists())

    def test_changing_the_percentage_applies_from_the_next_payroll(self):
        account = self.enrol("15")
        self.generate(self.sept)
        DeferredFundService.set_percent(account, Decimal("5"), actor=self.hr)
        self.generate(self.oct)
        self.assertEqual(account.balance, Decimal("40000.00"))

    def test_not_enrolled_and_stopped_accounts_have_no_deduction(self):
        self.assertEqual(self.generate(self.sept).total_deductions, Decimal("0.00"))

    def test_partial_withdrawal_needs_approval_then_payment_and_cannot_exceed_balance(self):
        account = self.enrol("15", opening="100000")
        with self.assertRaises(ValueError):
            DeferredFundService.request_withdrawal(account, kind="partial", amount=Decimal("100001"), actor=self.hr)
        first = DeferredFundService.request_withdrawal(account, kind="partial", amount=Decimal("60000"), actor=self.hr)
        with self.assertRaises(ValueError):  # 60k already in progress, only 40k left to ask for
            DeferredFundService.request_withdrawal(account, kind="partial", amount=Decimal("50000"), actor=self.hr)
        with self.assertRaises(ValueError):
            DeferredFundService.pay(first, actor=self.finance)
        DeferredFundService.approve(first, actor=self.boss)
        DeferredFundService.pay(first, actor=self.finance, reference="TRF1")
        self.assertEqual(account.balance, Decimal("40000.00"))
        self.assertTrue(account.active)

    def test_full_release_pays_the_whole_balance_and_stops_contributions(self):
        account = self.enrol("15", opening="50000")
        self.generate(self.sept)  # +30000
        release = DeferredFundService.request_withdrawal(account, kind="final", reason="Resigned with notice", actor=self.hr)
        DeferredFundService.approve(release, actor=self.boss)
        release.refresh_from_db()
        DeferredFundService.pay(release, actor=self.finance)
        account.refresh_from_db()
        release.refresh_from_db()
        self.assertEqual(release.amount, Decimal("80000.00"))
        self.assertEqual(account.balance, Decimal("0.00"))
        self.assertFalse(account.active)
        self.assertEqual(self.generate(self.oct).total_deductions, Decimal("0.00"))

    def test_decline_needs_a_reason(self):
        account = self.enrol("15", opening="1000")
        request = DeferredFundService.request_withdrawal(account, kind="partial", amount=Decimal("500"), actor=self.hr)
        with self.assertRaises(ValueError):
            DeferredFundService.decline(request, actor=self.boss, comment="")

    def test_adjustment_needs_a_reason_and_cannot_go_negative(self):
        account = self.enrol("15", opening="1000")
        with self.assertRaises(ValueError):
            DeferredFundService.adjust(account, amount=Decimal("-2000"), note="fix", actor=self.hr)
        with self.assertRaises(ValueError):
            DeferredFundService.adjust(account, amount=Decimal("100"), note=" ", actor=self.hr)

    def test_api_roles_and_overview(self):
        client = APIClient()
        client.force_authenticate(self.hr)
        created = client.post("/api/deferred-funds/accounts/", {"employee": self.staff.pk, "percent": "15", "opening_balance": "5000"}, format="json")
        self.assertEqual(created.status_code, 201)
        overview = client.get("/api/deferred-funds/").json()
        self.assertEqual(overview["total_held"], "5000.00")
        self.assertEqual(overview["accounts"][0]["monthly_amount"], "30000.00")
        self.assertEqual(client.post("/api/deferred-funds/accounts/", {"employee": self.permanent.pk, "percent": "15"}, format="json").status_code, 400)
        wid = client.post("/api/deferred-funds/withdrawals/", {"account": created.json()["id"], "kind": "partial", "amount": "1000"}, format="json").json()["id"]
        self.assertEqual(client.post(f"/api/deferred-funds/withdrawals/{wid}/approve/").status_code, 403)
        client.force_authenticate(self.boss)
        self.assertEqual(client.post(f"/api/deferred-funds/withdrawals/{wid}/approve/").status_code, 200)
        self.assertEqual(client.post(f"/api/deferred-funds/withdrawals/{wid}/pay/").status_code, 403)
        client.force_authenticate(self.finance)
        self.assertEqual(client.post(f"/api/deferred-funds/withdrawals/{wid}/pay/").json()["status"], "paid")
        client.force_authenticate(get_user_model().objects.create_user("nobody", password="pw"))
        self.assertEqual(client.get("/api/deferred-funds/").status_code, 403)


class WithdrawalBankFileTests(TestCase):
    def setUp(self):
        self.hr = user_with("hr", "manage_deferred_funds")
        self.boss = user_with("boss", "approve_deferred_withdrawal")
        self.finance = user_with("fin", "pay_deferred_withdrawal")
        self.client = APIClient()
        self.client.force_authenticate(self.finance)

    def approved(self, staff_id, opening, kind="partial", amount=None, account="2252037955", code="000015"):
        employee = Employee.objects.create(employee_id=staff_id, first_name="C", last_name=staff_id, employment_type="contract", basic_salary=Decimal("100000"), bank_name="Zenith", account_number=account, bank_code=code)
        acct = DeferredFundService.enrol(employee=employee, percent=Decimal("10"), opening_balance=Decimal(opening), actor=self.hr)
        withdrawal = DeferredFundService.request_withdrawal(acct, kind=kind, amount=amount, actor=self.hr)
        return DeferredFundService.approve(withdrawal, actor=self.boss)

    def test_file_has_the_payroll_layout_and_full_release_uses_the_whole_balance(self):
        import io
        from openpyxl import load_workbook
        part = self.approved("C1", "100000", amount=Decimal("25000"), code="14")
        full = self.approved("C2", "80000", kind="final")
        response = self.client.post("/api/deferred-funds/withdrawals/bank-upload/", {"ids": [part.pk, full.pk]}, format="json")
        self.assertEqual(response.status_code, 200)
        sheet = load_workbook(io.BytesIO(response.content)).active
        self.assertEqual([c.value for c in sheet[1]], ["Account_Number", "Amount", "Bank_Codes", "Narration"])
        self.assertEqual([c.value for c in sheet[2]], [2252037955, 25000, "000014", "Hello"])
        self.assertEqual([c.value for c in sheet[3]], [2252037955, 80000, "000015", "Hello"])

    def test_mark_paid_only_covers_people_in_the_file(self):
        good = self.approved("C1", "50000", amount=Decimal("1000"))
        bad = self.approved("C2", "50000", amount=Decimal("1000"), account="", code="")
        self.assertEqual(self.client.post("/api/deferred-funds/withdrawals/mark-paid/", {"ids": [good.pk, bad.pk], "reference": "B1"}, format="json").json(), {"paid": 1})
        good.refresh_from_db(); bad.refresh_from_db()
        self.assertEqual((good.status, bad.status), ("paid", "approved"))
        self.assertEqual(good.account.balance, Decimal("49000.00"))

    def test_needs_the_pay_permission(self):
        item = self.approved("C1", "50000", amount=Decimal("1000"))
        self.client.force_authenticate(self.boss)
        self.assertEqual(self.client.post("/api/deferred-funds/withdrawals/bank-upload/", {"ids": [item.pk]}, format="json").status_code, 403)

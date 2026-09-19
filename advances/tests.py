from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from rest_framework.test import APIClient

from employees.models import Employee
from payroll.models import EmployeePayroll, PayrollPeriod
from payroll.services import generate_payroll_for_period

from .models import AdvanceStatus, SalaryAdvance
from .services import AdvanceService


def user_with(name, *codenames):
    user = get_user_model().objects.create_user(name, password="pw")
    user.user_permissions.add(*Permission.objects.filter(codename__in=codenames))
    return user


class AdvanceTests(TestCase):
    def setUp(self):
        self.hr = user_with("hr", "record_salary_advance")
        self.boss = user_with("boss", "approve_salary_advance")
        self.finance = user_with("finance", "pay_salary_advance")
        self.employee = Employee.objects.create(employee_id="A001", first_name="Ada", last_name="Obi", basic_salary=Decimal("100000.00"))
        self.sept = PayrollPeriod.objects.create(year=2026, month=9)
        self.oct = PayrollPeriod.objects.create(year=2026, month=10)
        self.nov = PayrollPeriod.objects.create(year=2026, month=11)

    def advance(self, amount="30000", months=3, deduct_from=(2026, 9)):
        return AdvanceService.record(employee=self.employee, amount=Decimal(amount), reason="School fees", repayment_months=months, deduct_from=deduct_from, actor=self.hr)

    def paid(self, **kw):
        advance = self.advance(**kw)
        AdvanceService.approve(advance, actor=self.boss)
        return AdvanceService.pay(advance, actor=self.finance)

    def generate(self, period):
        generate_payroll_for_period(period, actor=self.hr)
        return EmployeePayroll.objects.get(payroll_period=period, employee=self.employee)

    def test_full_workflow_deducts_each_month_and_clears(self):
        advance = self.paid()
        self.assertEqual(advance.status, AdvanceStatus.PAID)
        for period in (self.sept, self.oct):
            payroll = self.generate(period)
            self.assertEqual(payroll.total_deductions, Decimal("10000.00"))
            self.assertEqual(payroll.net_pay, Decimal("90000.00"))
        self.assertEqual(SalaryAdvance.objects.get().status, AdvanceStatus.PAID)
        self.generate(self.nov)
        advance.refresh_from_db()
        self.assertEqual(advance.status, AdvanceStatus.REPAID)
        self.assertEqual(advance.balance, Decimal("0.00"))

    def test_no_deduction_before_payment_or_before_the_start_month(self):
        self.advance()
        self.assertEqual(self.generate(self.sept).total_deductions, Decimal("0.00"))
        later = self.paid(deduct_from=(2026, 11))
        self.assertEqual(self.generate(self.oct).total_deductions, Decimal("0.00"))
        self.assertEqual(self.generate(self.nov).total_deductions, Decimal("10000.00"))
        self.assertEqual(later.repayments.count(), 1)

    def test_regenerating_the_same_month_does_not_deduct_twice(self):
        self.paid()
        self.generate(self.sept)
        payroll = self.generate(self.sept)
        self.assertEqual(payroll.total_deductions, Decimal("10000.00"))

    def test_paying_takes_the_instalment_at_once_when_that_payroll_already_exists(self):
        payroll = self.generate(self.sept)
        self.paid(months=1)
        payroll.refresh_from_db()
        self.assertEqual(payroll.total_deductions, Decimal("30000.00"))

    def test_uneven_amount_clears_exactly(self):
        advance = self.paid(amount="10000.00", months=3)
        for period in (self.sept, self.oct, self.nov):
            self.generate(period)
        advance.refresh_from_db()
        self.assertEqual(advance.amount_repaid, Decimal("10000.00"))
        self.assertEqual(advance.status, AdvanceStatus.REPAID)

    def test_never_takes_more_than_the_month_pays_and_carries_the_rest(self):
        advance = self.paid(amount="250000", months=1)
        self.assertEqual(self.generate(self.sept).net_pay, Decimal("0.00"))
        advance.refresh_from_db()
        self.assertEqual(advance.balance, Decimal("150000.00"))
        self.assertEqual(advance.status, AdvanceStatus.PAID)

    def test_steps_must_happen_in_order(self):
        advance = self.advance()
        with self.assertRaises(ValueError):
            AdvanceService.pay(advance, actor=self.finance)
        AdvanceService.approve(advance, actor=self.boss)
        with self.assertRaises(ValueError):
            AdvanceService.approve(advance, actor=self.boss)
        AdvanceService.pay(advance, actor=self.finance)
        with self.assertRaises(ValueError):
            AdvanceService.cancel(advance, actor=self.hr)

    def test_decline_needs_a_reason_and_stops_the_advance(self):
        advance = self.advance()
        with self.assertRaises(ValueError):
            AdvanceService.decline(advance, actor=self.boss, comment=" ")
        AdvanceService.decline(advance, actor=self.boss, comment="Already owes")
        with self.assertRaises(ValueError):
            AdvanceService.pay(advance, actor=self.finance)

    def test_inactive_employee_and_bad_amounts_are_refused(self):
        self.employee.status = "terminated"
        self.employee.save()
        with self.assertRaises(ValueError):
            self.advance()

    def test_api_permissions_follow_the_three_roles(self):
        client = APIClient()
        client.force_authenticate(self.hr)
        created = client.post("/api/advances/", {"employee": self.employee.pk, "amount": "20000", "repayment_months": 2}, format="json")
        self.assertEqual(created.status_code, 201)
        pk = created.json()["id"]
        self.assertEqual(client.post(f"/api/advances/{pk}/approve/").status_code, 403)
        client.force_authenticate(self.boss)
        self.assertEqual(client.post("/api/advances/", {"employee": self.employee.pk, "amount": "1"}, format="json").status_code, 403)
        self.assertEqual(client.post(f"/api/advances/{pk}/pay/").status_code, 403)
        self.assertEqual(client.post(f"/api/advances/{pk}/approve/").status_code, 200)
        client.force_authenticate(self.finance)
        self.assertEqual(client.post(f"/api/advances/{pk}/pay/", {"reference": "TRF123"}, format="json").json()["status"], "paid")
        outsider = get_user_model().objects.create_user("nobody", password="pw")
        client.force_authenticate(outsider)
        self.assertEqual(client.get("/api/advances/").status_code, 403)

    def test_salary_is_only_shown_to_people_who_may_see_it(self):
        self.advance()
        client = APIClient()
        client.force_authenticate(self.boss)
        self.assertIsNone(client.get("/api/advances/").json()["results"][0]["employee_basic_salary"])
        self.boss.user_permissions.add(Permission.objects.get(codename="view_salary"))
        client.force_authenticate(get_user_model().objects.get(pk=self.boss.pk))
        self.assertEqual(client.get("/api/advances/").json()["results"][0]["employee_basic_salary"], "100000.00")


class AdvanceBankFileTests(TestCase):
    def setUp(self):
        self.finance = user_with("fin", "pay_salary_advance")
        self.hr = user_with("hr2", "record_salary_advance")
        self.boss = user_with("boss2", "approve_salary_advance")
        self.client = APIClient()
        self.client.force_authenticate(self.finance)

    def approved(self, staff_id, amount, account="2252037955", code="000015"):
        employee = Employee.objects.create(employee_id=staff_id, first_name="Ada", last_name=staff_id, basic_salary=Decimal("100000"), bank_name="Zenith", account_number=account, bank_code=code)
        advance = AdvanceService.record(employee=employee, amount=Decimal(amount), repayment_months=1, actor=self.hr)
        return AdvanceService.approve(advance, actor=self.boss)

    def test_file_has_the_payroll_layout_and_only_approved_advances(self):
        import io
        from openpyxl import load_workbook
        first, second = self.approved("000001", "20000", code="14"), self.approved("000002", "35000.50")
        waiting = AdvanceService.record(employee=Employee.objects.create(employee_id="000003", first_name="X", last_name="Y", bank_name="B", account_number="2252037955", bank_code="000015"), amount=Decimal("500"), actor=self.hr)
        response = self.client.post("/api/advances/bank-upload/", {"ids": [first.pk, second.pk, waiting.pk]}, format="json")
        self.assertEqual(response.status_code, 200)
        sheet = load_workbook(io.BytesIO(response.content)).active
        self.assertEqual([c.value for c in sheet[1]], ["Account_Number", "Amount", "Bank_Codes", "Narration"])
        self.assertEqual([c.value for c in sheet[2]], [2252037955, 20000, "000014", "Hello"])
        self.assertEqual([c.value for c in sheet[3]], [2252037955, 35000.5, "000015", "Hello"])
        self.assertEqual(sheet.max_row, 3)

    def test_people_with_bad_bank_details_are_listed_not_silently_dropped(self):
        good, bad = self.approved("000001", "1000"), self.approved("000002", "2000", account="", code="")
        data = self.client.post("/api/advances/bank-upload/preview/", {"ids": [good.pk, bad.pk]}, format="json").json()
        self.assertEqual(data["ready_count"], 1)
        self.assertEqual([i["employee_id"] for i in data["issues"]], ["000002"])

    def test_bulk_pay_marks_them_paid_and_needs_the_pay_permission(self):
        one, two = self.approved("000001", "1000"), self.approved("000002", "2000")
        self.client.force_authenticate(self.hr)
        self.assertEqual(self.client.post("/api/advances/mark-paid/", {"ids": [one.pk]}, format="json").status_code, 403)
        self.client.force_authenticate(self.finance)
        self.assertEqual(self.client.post("/api/advances/mark-paid/", {"ids": [one.pk, two.pk], "reference": "BATCH1"}, format="json").json(), {"paid": 2})
        one.refresh_from_db()
        self.assertEqual((one.status, one.payment_reference), ("paid", "BATCH1"))
        self.assertEqual(self.client.post("/api/advances/bank-upload/", {"ids": [one.pk]}, format="json").status_code, 400)

    def test_bulk_pay_skips_people_who_were_not_in_the_bank_file(self):
        good, bad = self.approved("000001", "1000"), self.approved("000002", "2000", account="", code="")
        self.assertEqual(self.client.post("/api/advances/mark-paid/", {"ids": [good.pk, bad.pk]}, format="json").json(), {"paid": 1})
        bad.refresh_from_db()
        self.assertEqual(bad.status, "approved")

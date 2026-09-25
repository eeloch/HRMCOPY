from datetime import date
from decimal import Decimal

from employees.admin_testing import AdminTestCase
from payroll.models import EmployeePayroll, PayrollLineItem, PayrollPeriod

from .models import EmployeePPEIssue, PPEDeductionStatus, PPEType
from .services import PPEDeductionService


class PPEAdminTests(AdminTestCase):
    def setUp(self):
        super().setUp()
        self.ada = self.make_employee(middle="Grace", basic_salary=Decimal("50000.00"))
        self.bola = self.make_employee("000685", "Bola", "Adeyemi", basic_salary=Decimal("50000.00"))
        self.boots = PPEType.objects.create(name="Safety boots", code="ADM_BOOT", potentially_employee_deductible=True)
        self.helmet = PPEType.objects.create(name="Helmet", code="ADM_HELMET", potentially_employee_deductible=False)
        self.period = PayrollPeriod.objects.create(year=2026, month=9)
        EmployeePayroll.objects.create(payroll_period=self.period, employee=self.ada, basic_salary=Decimal("50000.00"), gross_earnings=Decimal("50000.00"), net_pay=Decimal("50000.00"))
        self.one = self.issue(self.ada, self.boots)
        self.two = self.issue(self.bola, self.helmet)

    def issue(self, employee, ppe_type, quantity=2):
        return PPEDeductionService.issue(employee=employee, ppe_type=ppe_type, quantity=quantity, issue_date=date(2026, 9, 1), unit_cost=Decimal("10000.00"), notes="", actor=self.admin_user)

    def test_changelists_load(self):
        self.get_list(PPEType)
        self.get_list(EmployeePPEIssue)
        for params in ({"deduction_status__exact": "pending"}, {"employee_deductible__exact": "1"}, {"ppe_type__id__exact": self.boots.pk},
                       {"employee__department__id__exact": self.department.pk}, {"issue_date__gte": "2026-09-01"}):
            self.get_list(EmployeePPEIssue, **params)

    def test_search(self):
        self.assert_search(EmployeePPEIssue, "000684", [self.one.pk])
        self.assert_search(EmployeePPEIssue, "Grace", [self.one.pk])
        self.assert_search(EmployeePPEIssue, "Adeyemi", [self.two.pk])
        self.assert_search(EmployeePPEIssue, "Helmet", [self.two.pk])

    def test_type_list_shows_issue_and_unit_counts(self):
        page = self.get_list(PPEType).content.decode()
        self.assertIn("Issues recorded", page)
        self.assertIn("Units issued", page)

    def test_no_n_plus_one(self):
        counter = iter(range(10, 200))

        def more():
            for _ in range(4):
                n = next(counter)
                self.issue(self.make_employee(f"0008{n}", "Extra", f"Person{n}"), self.boots)

        self.assert_same_query_count(EmployeePPEIssue, more)
        self.assert_same_query_count(PPEType, lambda: PPEType.objects.create(name=f"Gloves {next(counter)}", code=f"G{next(counter)}"))

    def test_str(self):
        self.assertEqual(str(self.one), "000684 Ada Grace Okafor - Safety boots x2 - 01 Sep 2026")

    def test_no_bulk_delete(self):
        response = self.get_list(EmployeePPEIssue)
        self.assertNotIn("delete_selected", [name for name, _ in response.context["action_form"].fields["action"].choices])

    def test_add_goes_through_the_issue_service(self):
        data = {"employee": self.bola.pk, "ppe_type": self.boots.pk, "quantity": "3", "issue_date": "2026-09-12", "unit_cost": "5000.00", "notes": ""}
        response = self.client.post(self.url(EmployeePPEIssue, "add"), data)
        self.assertEqual(response.status_code, 302, getattr(response, "context", None) and response.context["adminform"].form.errors)
        created = EmployeePPEIssue.objects.get(issue_date=date(2026, 9, 12))
        self.assertEqual(created.total_cost, Decimal("15000.00"))
        self.assertEqual((created.employee_deductible, created.deduction_status), (True, PPEDeductionStatus.PENDING))
        self.assertEqual(created.issued_by, self.admin_user)
        self.assertEqual(self.audit("ppe.issued", object_id=created.pk).count(), 1)

    def test_approve_uses_the_service_skips_non_deductible_and_audits(self):
        first, second = self.run_action(EmployeePPEIssue, "approve_deductions", [self.one.pk, self.two.pk], period=self.period.pk)
        self.assertContains(first, "Approve PPE deductions")
        self.one.refresh_from_db()
        self.two.refresh_from_db()
        self.assertEqual(self.one.deduction_status, PPEDeductionStatus.DEDUCTED)
        self.assertEqual(PayrollLineItem.objects.filter(source_type="ppe_issue").count(), 1)
        self.assertEqual(self.two.deduction_status, PPEDeductionStatus.NOT_DEDUCTIBLE)
        self.assertContains(second, "not employee-deductible")
        self.assertEqual(self.audit("ppe.deduction_approved", employee=self.ada).count(), 1)
        self.assertEqual(self.one.reviewed_by, self.admin_user)

    def test_confirmation_changes_nothing(self):
        self.run_action(EmployeePPEIssue, "approve_deductions", [self.one.pk], confirm=False)
        self.one.refresh_from_db()
        self.assertEqual(self.one.deduction_status, PPEDeductionStatus.PENDING)

    def test_hold_needs_a_reason(self):
        _, second = self.run_action(EmployeePPEIssue, "hold_deductions", [self.one.pk], follow=False)
        self.assertContains(second, "This is required.")
        self.run_action(EmployeePPEIssue, "hold_deductions", [self.one.pk], comment="Waiting for HR")
        self.one.refresh_from_db()
        self.assertEqual((self.one.deduction_status, self.one.review_comment), (PPEDeductionStatus.HELD, "Waiting for HR"))
        self.assertEqual(self.audit("ppe.deduction_held", employee=self.ada).count(), 1)

    def test_defer_to_a_month(self):
        later = PayrollPeriod.objects.create(year=2026, month=10)
        self.run_action(EmployeePPEIssue, "defer_deductions", [self.one.pk], period=later.pk)
        self.one.refresh_from_db()
        self.assertEqual((self.one.deduction_status, self.one.target_payroll_period), (PPEDeductionStatus.DEFERRED, later))
        self.assertEqual(self.audit("ppe.deduction_deferred", employee=self.ada).count(), 1)

    def test_deducted_issue_is_locked_and_cannot_be_reviewed_again(self):
        self.run_action(EmployeePPEIssue, "approve_deductions", [self.one.pk], period=self.period.pk)
        self.assertEqual(self.client.get(self.url(EmployeePPEIssue, "delete", self.one.pk)).status_code, 403)
        _, second = self.run_action(EmployeePPEIssue, "hold_deductions", [self.one.pk], comment="again")
        self.one.refresh_from_db()
        self.assertEqual(self.one.deduction_status, PPEDeductionStatus.DEDUCTED)
        self.assertContains(second, "already deducted")

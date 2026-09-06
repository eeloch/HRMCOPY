from datetime import date
from decimal import Decimal

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied
from django.test import TestCase
from django.test.client import RequestFactory
from rest_framework.test import APIClient, APIRequestFactory, force_authenticate

from audit.models import AuditEvent
from employees.models import Employee
from payroll.models import EmployeePayroll, EmployeePayrollStatus, PayrollLineItem, PayrollLineItemType, PayrollPeriod, PayrollPeriodStatus

from .admin import EmployeePPEIssueAdmin
from .models import EmployeePPEIssue, PPEDeductionStatus, PPEType
from .services import PPEDeductionService


class PPEWorkflowTests(TestCase):
    def setUp(self):
        self.actor = get_user_model().objects.create_user("ppe-manager", password="password")
        self.employee = Employee.objects.create(employee_id="PPE001", first_name="PPE", last_name="Employee", basic_salary=Decimal("50000.00"))
        self.boots = PPEType.objects.create(name="Safety Boots", code="TEST_BOOT", potentially_employee_deductible=True)
        self.helmet = PPEType.objects.create(name="Helmet", code="TEST_HELMET", potentially_employee_deductible=False)
        self.period = PayrollPeriod.objects.create(year=2026, month=9, status=PayrollPeriodStatus.DRAFT)
        self.payroll = EmployeePayroll.objects.create(payroll_period=self.period, employee=self.employee, basic_salary=Decimal("50000.00"), gross_earnings=Decimal("50000.00"), net_pay=Decimal("50000.00"))

    def issue(self, ppe_type=None, unit_cost=Decimal("10000.00"), quantity=2):
        return PPEDeductionService.issue(employee=self.employee, ppe_type=ppe_type or self.boots, quantity=quantity, issue_date=date(2026, 9, 1), unit_cost=unit_cost, notes="", actor=self.actor)

    def test_issue_calculates_authoritative_total_and_starts_pending(self):
        issue = self.issue()
        self.assertEqual(issue.total_cost, Decimal("20000.00"))
        self.assertEqual(issue.deduction_status, PPEDeductionStatus.PENDING)
        self.assertFalse(PayrollLineItem.objects.exists())

    def test_non_deductible_issue_has_no_deduction_workflow(self):
        issue = self.issue(self.helmet)
        self.assertFalse(issue.employee_deductible)
        self.assertEqual(issue.deduction_status, PPEDeductionStatus.NOT_DEDUCTIBLE)

    def test_issue_api_ignores_client_supplied_total_cost(self):
        self.actor.user_permissions.add(Permission.objects.get(codename="record_ppe_issue"))
        self.actor = get_user_model().objects.get(pk=self.actor.pk)
        client = APIClient(); client.force_authenticate(user=self.actor)
        response = client.post("/api/ppe/issues/", {"employee": self.employee.pk, "ppe_type": self.boots.pk, "quantity": 2, "issue_date": "2026-09-01", "unit_cost": "10000.00", "total_cost": "1.00"}, format="json")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["total_cost"], "20000.00")

    def test_approve_creates_one_full_system_deduction_and_recalculates(self):
        issue = self.issue()
        result = PPEDeductionService.approve(issue, payroll_period=self.period, actor=self.actor)
        line = PayrollLineItem.objects.get(payroll=self.payroll, source_type="ppe_issue", source_reference=str(issue.pk))
        self.payroll.refresh_from_db()
        self.assertEqual(result.deduction_status, PPEDeductionStatus.DEDUCTED)
        self.assertEqual(line.amount, Decimal("20000.00"))
        self.assertEqual(self.payroll.net_pay, Decimal("30000.00"))
        self.assertTrue(AuditEvent.objects.filter(event_type="ppe.deduction_applied").exists())

    def test_repeated_approval_cannot_duplicate_a_deduction(self):
        issue = self.issue()
        PPEDeductionService.approve(issue, payroll_period=self.period, actor=self.actor)
        with self.assertRaisesMessage(ValueError, "already been deducted"):
            PPEDeductionService.approve(issue, payroll_period=self.period, actor=self.actor)
        self.assertEqual(PayrollLineItem.objects.filter(source_type="ppe_issue").count(), 1)

    def test_low_pay_approval_posts_full_deduction_and_allows_negative_net_pay(self):
        self.payroll.basic_salary = Decimal("20000.00")
        self.payroll.gross_earnings = Decimal("20000.00")
        self.payroll.net_pay = Decimal("20000.00")
        self.payroll.save(update_fields=["basic_salary", "gross_earnings", "net_pay"])
        issue = self.issue(unit_cost=Decimal("25000.00"), quantity=1)

        result = PPEDeductionService.approve(issue, payroll_period=self.period, actor=self.actor)

        self.payroll.refresh_from_db()
        line = PayrollLineItem.objects.get(payroll=self.payroll, source_type="ppe_issue", source_reference=str(issue.pk))
        self.assertEqual(line.item_type, PayrollLineItemType.DEDUCTION)
        self.assertEqual(line.amount, Decimal("25000.00"))
        self.assertTrue(line.is_system_generated)
        self.assertEqual(self.payroll.total_deductions, Decimal("25000.00"))
        self.assertEqual(self.payroll.net_pay, Decimal("-5000.00"))
        self.assertEqual(result.deduction_status, PPEDeductionStatus.DEDUCTED)
        with self.assertRaisesMessage(ValueError, "already been deducted"):
            PPEDeductionService.approve(issue, payroll_period=self.period, actor=self.actor)
        self.assertEqual(PayrollLineItem.objects.filter(payroll=self.payroll, source_type="ppe_issue").count(), 1)

    def test_hold_and_defer_preserve_full_issue_cost_without_line_items(self):
        issue = self.issue()
        held = PPEDeductionService.hold(issue, actor=self.actor, comment="Awaiting HR confirmation.")
        self.assertEqual(held.deduction_status, PPEDeductionStatus.HELD)
        deferred = PPEDeductionService.defer(held, payroll_period=self.period, actor=self.actor, comment="Next month.")
        self.assertEqual(deferred.deduction_status, PPEDeductionStatus.DEFERRED)
        self.assertEqual(deferred.total_cost, Decimal("20000.00"))
        self.assertFalse(PayrollLineItem.objects.exists())

    def test_immutable_period_cannot_receive_ppe_deduction(self):
        for status in (PayrollPeriodStatus.APPROVED, PayrollPeriodStatus.PAID, PayrollPeriodStatus.CLOSED):
            with self.subTest(status=status):
                self.period.status = status
                self.period.save(update_fields=["status"])
                issue = self.issue()
                with self.assertRaisesMessage(ValueError, "can no longer accept"):
                    PPEDeductionService.approve(issue, payroll_period=self.period, actor=self.actor)
                issue.refresh_from_db()
                self.payroll.refresh_from_db()
                self.assertEqual(issue.deduction_status, PPEDeductionStatus.PENDING)
                self.assertIsNone(issue.payroll_line_item)
                self.assertEqual(self.payroll.total_deductions, Decimal("0.00"))
                self.assertEqual(PayrollLineItem.objects.filter(source_type="ppe_issue", source_reference=str(issue.pk)).count(), 0)
                self.period.status = PayrollPeriodStatus.DRAFT
                self.period.save(update_fields=["status"])

    def test_immutable_employee_payroll_cannot_receive_ppe_deduction(self):
        for status in (EmployeePayrollStatus.APPROVED, EmployeePayrollStatus.PAID):
            with self.subTest(status=status):
                self.payroll.status = status
                self.payroll.save(update_fields=["status"])
                issue = self.issue()
                with self.assertRaisesMessage(ValueError, "employee payroll record can no longer accept"):
                    PPEDeductionService.approve(issue, payroll_period=self.period, actor=self.actor)
                issue.refresh_from_db()
                self.payroll.refresh_from_db()
                self.assertEqual(issue.deduction_status, PPEDeductionStatus.PENDING)
                self.assertIsNone(issue.payroll_line_item)
                self.assertEqual(self.payroll.total_deductions, Decimal("0.00"))
                self.assertEqual(PayrollLineItem.objects.filter(source_type="ppe_issue", source_reference=str(issue.pk)).count(), 0)
                self.payroll.status = EmployeePayrollStatus.DRAFT
                self.payroll.save(update_fields=["status"])

    def test_missing_employee_payroll_leaves_issue_pending_without_a_line_item(self):
        self.payroll.delete()
        issue = self.issue()

        with self.assertRaisesMessage(ValueError, "Generate this employee's payroll record"):
            PPEDeductionService.approve(issue, payroll_period=self.period, actor=self.actor)

        issue.refresh_from_db()
        self.assertEqual(issue.deduction_status, PPEDeductionStatus.PENDING)
        self.assertIsNone(issue.target_payroll_period)
        self.assertIsNone(issue.payroll_line_item)
        self.assertFalse(PayrollLineItem.objects.filter(source_type="ppe_issue", source_reference=str(issue.pk)).exists())

    def test_existing_system_line_is_recovered_without_creating_a_duplicate(self):
        issue = self.issue()
        line = PayrollLineItem.objects.create(
            payroll=self.payroll,
            item_type=PayrollLineItemType.DEDUCTION,
            code=f"PPE_{self.boots.code}",
            description="Recovered PPE deduction",
            amount=issue.total_cost,
            source_type="ppe_issue",
            source_reference=str(issue.pk),
            is_system_generated=True,
        )

        result = PPEDeductionService.approve(issue, payroll_period=self.period, actor=self.actor)

        result.refresh_from_db()
        self.assertEqual(result.deduction_status, PPEDeductionStatus.DEDUCTED)
        self.assertEqual(result.payroll_line_item, line)
        self.assertEqual(result.deducted_payroll, self.payroll)
        self.assertEqual(PayrollLineItem.objects.filter(payroll=self.payroll, source_type="ppe_issue", source_reference=str(issue.pk)).count(), 1)

    def test_admin_protects_deducted_issues_from_changes_and_bulk_deletion(self):
        issue = self.issue()
        PPEDeductionService.approve(issue, payroll_period=self.period, actor=self.actor)
        issue.refresh_from_db()
        model_admin = EmployeePPEIssueAdmin(EmployeePPEIssue, admin.site)
        request = RequestFactory().get("/")
        request.user = self.actor

        self.assertEqual(
            set(model_admin.get_readonly_fields(request, issue)),
            {field.name for field in issue._meta.fields},
        )
        self.assertFalse(model_admin.has_delete_permission(request, issue))
        with self.assertRaises(PermissionDenied):
            model_admin.delete_queryset(request, EmployeePPEIssue.objects.filter(pk=issue.pk))
        with self.assertRaises(PermissionDenied):
            model_admin.save_model(request, issue, form=None, change=True)
        self.assertTrue(EmployeePPEIssue.objects.filter(pk=issue.pk).exists())

    def test_history_is_scoped_to_employee_and_review_permission_is_required(self):
        issue = self.issue()
        other = Employee.objects.create(employee_id="PPE002", first_name="Other", last_name="Employee")
        PPEDeductionService.issue(employee=other, ppe_type=self.boots, quantity=1, issue_date=date(2026, 9, 1), unit_cost=Decimal("1000.00"), notes="", actor=self.actor)
        from ppe.views import PPEIssueListCreateAPIView

        request = APIRequestFactory().get(f"/api/ppe/issues/?employee={self.employee.pk}")
        force_authenticate(request, user=self.actor)
        response = PPEIssueListCreateAPIView.as_view()(request)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 1)
        client = APIClient(); client.force_authenticate(user=self.actor)
        response = client.post(f"/api/ppe/issues/{issue.pk}/hold/", {"comment": "No permission"}, format="json")
        self.assertEqual(response.status_code, 403)
        self.actor.user_permissions.add(Permission.objects.get(codename="review_ppe_deduction"))
        self.actor = get_user_model().objects.get(pk=self.actor.pk)
        client.force_authenticate(user=self.actor)
        response = client.post(f"/api/ppe/issues/{issue.pk}/hold/", {"comment": "Reviewed"}, format="json")
        self.assertEqual(response.status_code, 200)

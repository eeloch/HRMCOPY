from django.db import transaction
from django.utils import timezone

from audit.models import AuditSeverity
from audit.services import AuditService
from payroll.models import EmployeePayrollStatus, PayrollLineItem, PayrollLineItemType, PayrollPeriodStatus
from payroll.services import recalculate_employee_payroll

from .models import EmployeePPEIssue, PPEDeductionStatus


class PPEDeductionService:
    @staticmethod
    def issue(*, employee, ppe_type, quantity, issue_date, unit_cost, notes="", actor):
        deductible = ppe_type.potentially_employee_deductible
        issue = EmployeePPEIssue.objects.create(
            employee=employee, ppe_type=ppe_type, quantity=quantity, issue_date=issue_date,
            unit_cost=unit_cost, notes=notes, issued_by=actor, employee_deductible=deductible,
            deduction_status=PPEDeductionStatus.PENDING if deductible else PPEDeductionStatus.NOT_DEDUCTIBLE,
        )
        AuditService.log(event_type="ppe.issued", module="ppe", employee=employee, actor=actor, object=issue, severity=AuditSeverity.SUCCESS, title="PPE issued", description=f"{ppe_type.name} was issued to {employee.full_name}.", metadata={"ppe_issue_id": issue.pk, "ppe_type": ppe_type.code, "quantity": quantity, "total_cost": str(issue.total_cost)})
        return issue

    @staticmethod
    def hold(issue, *, actor, comment):
        if not comment.strip():
            raise ValueError("A reason is required to hold a PPE deduction.")
        PPEDeductionService._ensure_open(issue)
        issue.deduction_status = PPEDeductionStatus.HELD
        PPEDeductionService._review(issue, actor, comment)
        AuditService.log(event_type="ppe.deduction_held", module="ppe", employee=issue.employee, actor=actor, object=issue, severity=AuditSeverity.WARNING, title="PPE deduction held", description=f"{issue.ppe_type.name} deduction was held.", metadata=PPEDeductionService._metadata(issue))
        return issue

    @staticmethod
    def defer(issue, *, payroll_period, actor, comment=""):
        PPEDeductionService._ensure_open(issue)
        issue.deduction_status = PPEDeductionStatus.DEFERRED
        issue.target_payroll_period = payroll_period
        PPEDeductionService._review(issue, actor, comment)
        AuditService.log(event_type="ppe.deduction_deferred", module="ppe", employee=issue.employee, actor=actor, object=issue, severity=AuditSeverity.SUCCESS, title="PPE deduction deferred", description=f"{issue.ppe_type.name} deduction was deferred to {payroll_period.display_name}.", metadata=PPEDeductionService._metadata(issue))
        return issue

    @staticmethod
    def approve(issue, *, payroll_period, actor, comment=""):
        with transaction.atomic():
            issue = EmployeePPEIssue.objects.select_for_update().select_related("ppe_type", "employee").get(pk=issue.pk)
            PPEDeductionService._ensure_open(issue)
            if payroll_period.status in {PayrollPeriodStatus.APPROVED, PayrollPeriodStatus.PAID, PayrollPeriodStatus.CLOSED}:
                raise ValueError("This payroll period can no longer accept PPE deductions.")
            payroll = payroll_period.employee_payrolls.select_for_update().filter(employee=issue.employee).first()
            if payroll is None:
                raise ValueError("Generate this employee's payroll record before approving the PPE deduction.")
            existing = PayrollLineItem.objects.filter(payroll=payroll, source_type="ppe_issue", source_reference=str(issue.pk), is_system_generated=True).first()
            if existing:
                issue.payroll_line_item, issue.deducted_payroll, issue.target_payroll_period = existing, payroll, payroll_period
                issue.deduction_status = PPEDeductionStatus.DEDUCTED
                PPEDeductionService._review(issue, actor, comment)
                issue.save(update_fields=["payroll_line_item", "deducted_payroll", "updated_at"])
                return issue
            if payroll.status in {EmployeePayrollStatus.APPROVED, EmployeePayrollStatus.PAID}:
                raise ValueError("This employee payroll record can no longer accept PPE deductions.")
            issue.deduction_status = PPEDeductionStatus.APPROVED
            issue.target_payroll_period = payroll_period
            PPEDeductionService._review(issue, actor, comment)
            line_item = PayrollLineItem.objects.create(payroll=payroll, item_type=PayrollLineItemType.DEDUCTION, code=f"PPE_{issue.ppe_type.code}", description=f"{issue.ppe_type.name} issued {issue.issue_date}", amount=issue.total_cost, source_type="ppe_issue", source_reference=str(issue.pk), metadata={"ppe_issue_id": issue.pk, "ppe_type": issue.ppe_type.code, "issue_date": issue.issue_date.isoformat(), "quantity": issue.quantity, "total_cost": str(issue.total_cost)}, is_system_generated=True)
            recalculate_employee_payroll(payroll)
            issue.payroll_line_item, issue.deducted_payroll, issue.deduction_status = line_item, payroll, PPEDeductionStatus.DEDUCTED
            issue.save(update_fields=["payroll_line_item", "deducted_payroll", "deduction_status", "updated_at"])
            AuditService.log(event_type="ppe.deduction_approved", module="ppe", employee=issue.employee, actor=actor, object=issue, severity=AuditSeverity.SUCCESS, title="PPE deduction approved", description=f"{issue.ppe_type.name} deduction was approved.", metadata=PPEDeductionService._metadata(issue))
            AuditService.log(event_type="ppe.deduction_applied", module="ppe", employee=issue.employee, actor=actor, object=line_item, severity=AuditSeverity.SUCCESS, title="PPE deduction applied to payroll", description=f"{issue.ppe_type.name} deduction was added to {payroll_period.display_name}.", metadata=PPEDeductionService._metadata(issue))
            return issue

    @staticmethod
    def _ensure_open(issue):
        if not issue.employee_deductible:
            raise ValueError("This PPE issue is not employee-deductible.")
        if issue.deduction_status == PPEDeductionStatus.DEDUCTED:
            raise ValueError("This PPE issue has already been deducted.")

    @staticmethod
    def _review(issue, actor, comment):
        issue.reviewed_by, issue.reviewed_at, issue.review_comment = actor, timezone.now(), comment.strip()
        issue.save(update_fields=["deduction_status", "target_payroll_period", "reviewed_by", "reviewed_at", "review_comment", "updated_at"])

    @staticmethod
    def _metadata(issue):
        return {"ppe_issue_id": issue.pk, "ppe_type": issue.ppe_type.code, "amount": str(issue.total_cost), "issue_date": issue.issue_date.isoformat(), "target_payroll": issue.target_payroll_period.display_name if issue.target_payroll_period else None, "status": issue.deduction_status}

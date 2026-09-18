from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from audit.models import AuditSeverity
from audit.services import AuditService
from payroll.models import EmployeePayrollStatus, PayrollLineItem, PayrollLineItemType, PayrollPeriod, PayrollPeriodStatus
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
    def approve(issue, *, payroll_period=None, actor, comment=""):
        """Approve a PPE deduction: it comes out of that month's salary.

        Works at any time. The month is the payroll period given, else the one an
        earlier Defer chose, else the month the PPE was issued. If the employee's
        payroll record in that period already exists it is deducted immediately;
        otherwise the issue is held as approved (even if no period exists yet) and
        deducted automatically when that month's payroll is generated (see
        apply_approved_for_period). A payroll period or record that is already
        approved/paid is never altered.
        """
        with transaction.atomic():
            issue = EmployeePPEIssue.objects.select_for_update().select_related("ppe_type", "employee").get(pk=issue.pk)
            PPEDeductionService._ensure_open(issue)
            period = payroll_period or issue.target_payroll_period or PayrollPeriod.objects.filter(year=issue.issue_date.year, month=issue.issue_date.month).first()
            payroll = None
            if period is not None:
                if period.status in {PayrollPeriodStatus.APPROVED, PayrollPeriodStatus.PAID, PayrollPeriodStatus.CLOSED}:
                    raise ValueError("This payroll period can no longer accept PPE deductions.")
                payroll = period.employee_payrolls.select_for_update().filter(employee=issue.employee).first()
                if payroll is not None and payroll.status in {EmployeePayrollStatus.APPROVED, EmployeePayrollStatus.PAID}:
                    already_there = PayrollLineItem.objects.filter(payroll=payroll, source_type="ppe_issue", source_reference=str(issue.pk), is_system_generated=True).exists()
                    if not already_there:
                        raise ValueError("This employee payroll record can no longer accept PPE deductions.")
            issue.deduction_status = PPEDeductionStatus.APPROVED
            issue.target_payroll_period = period
            PPEDeductionService._review(issue, actor, comment)
            AuditService.log(event_type="ppe.deduction_approved", module="ppe", employee=issue.employee, actor=actor, object=issue, severity=AuditSeverity.SUCCESS, title="PPE deduction approved", description=f"{issue.ppe_type.name} deduction was approved.", metadata=PPEDeductionService._metadata(issue))
            if payroll is not None:
                PPEDeductionService._apply(issue, payroll, actor)
            return issue

    @staticmethod
    def _apply(issue, payroll, actor):
        """Add an approved PPE deduction to an employee's payroll (idempotent)."""
        line_item = PayrollLineItem.objects.filter(payroll=payroll, source_type="ppe_issue", source_reference=str(issue.pk), is_system_generated=True).first()
        created = line_item is None
        if created:
            line_item = PayrollLineItem.objects.create(payroll=payroll, item_type=PayrollLineItemType.DEDUCTION, code=f"PPE_{issue.ppe_type.code}", description=f"{issue.ppe_type.name} issued {issue.issue_date}", amount=issue.total_cost, source_type="ppe_issue", source_reference=str(issue.pk), metadata={"ppe_issue_id": issue.pk, "ppe_type": issue.ppe_type.code, "issue_date": issue.issue_date.isoformat(), "quantity": issue.quantity, "total_cost": str(issue.total_cost)}, is_system_generated=True)
            recalculate_employee_payroll(payroll)
        issue.payroll_line_item, issue.deducted_payroll, issue.target_payroll_period, issue.deduction_status = line_item, payroll, payroll.payroll_period, PPEDeductionStatus.DEDUCTED
        issue.save(update_fields=["payroll_line_item", "deducted_payroll", "target_payroll_period", "deduction_status", "updated_at"])
        if created:
            AuditService.log(event_type="ppe.deduction_applied", module="ppe", employee=issue.employee, actor=actor, object=line_item, severity=AuditSeverity.SUCCESS, title="PPE deduction applied to payroll", description=f"{issue.ppe_type.name} deduction was added to {payroll.payroll_period.display_name}.", metadata=PPEDeductionService._metadata(issue))
        return line_item

    @staticmethod
    def apply_approved_for_period(period):
        """Deduct every approved-but-not-yet-deducted PPE issue aimed at this period
        (or, with no period chosen, issued in its month) from the matching payroll
        records. Called when payroll is generated. Deferred issues are untouched:
        Defer means postponed until HR approves them."""
        applied = 0
        awaiting = EmployeePPEIssue.objects.filter(deduction_status=PPEDeductionStatus.APPROVED).filter(
            Q(target_payroll_period=period)
            | Q(target_payroll_period__isnull=True, issue_date__year=period.year, issue_date__month=period.month)
        ).select_related("ppe_type", "employee")
        for issue in awaiting:
            payroll = period.employee_payrolls.filter(employee=issue.employee).exclude(status__in=[EmployeePayrollStatus.APPROVED, EmployeePayrollStatus.PAID]).first()
            if payroll is not None:
                PPEDeductionService._apply(issue, payroll, None)
                applied += 1
        return applied

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

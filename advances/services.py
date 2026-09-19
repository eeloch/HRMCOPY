from datetime import date
from decimal import Decimal

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from audit.models import AuditSeverity
from audit.services import AuditService
from payroll.models import EmployeePayrollStatus, PayrollLineItem, PayrollLineItemType, PayrollPeriod, PayrollPeriodStatus
from payroll.services import recalculate_employee_payroll

from .models import AdvanceRepayment, AdvanceStatus, SalaryAdvance

LOCKED_PERIOD = {PayrollPeriodStatus.APPROVED, PayrollPeriodStatus.PAID, PayrollPeriodStatus.CLOSED}
LOCKED_RECORD = {EmployeePayrollStatus.APPROVED, EmployeePayrollStatus.PAID}
MAX_MONTHS = 24


def next_month(today=None):
    today = today or timezone.localdate()
    return (today.year + 1, 1) if today.month == 12 else (today.year, today.month + 1)


class AdvanceService:
    @staticmethod
    def record(*, employee, amount, reason="", repayment_months=1, deduct_from=None, actor):
        amount = Decimal(amount)
        if amount <= 0:
            raise ValueError("The advance amount must be more than zero.")
        if not 1 <= repayment_months <= MAX_MONTHS:
            raise ValueError(f"Repayment must be between 1 and {MAX_MONTHS} months.")
        if employee.status != "active":
            raise ValueError("Advances can only be recorded for active employees.")
        year, month = deduct_from or next_month()
        if not 1 <= month <= 12:
            raise ValueError("Choose a valid first deduction month.")
        advance = SalaryAdvance.objects.create(employee=employee, amount=amount, reason=reason.strip(), repayment_months=repayment_months, deduct_from_year=year, deduct_from_month=month, recorded_by=actor)
        AdvanceService._log("advance.recorded", advance, actor, AuditSeverity.INFO, "Salary advance recorded", f"A salary advance of {amount:,.2f} was recorded for {employee.full_name}.")
        return advance

    @staticmethod
    def approve(advance, *, actor, comment=""):
        return AdvanceService._decide(advance, AdvanceStatus.APPROVED, actor, comment, "advance.approved", AuditSeverity.SUCCESS, "Salary advance approved")

    @staticmethod
    def decline(advance, *, actor, comment):
        if not comment.strip():
            raise ValueError("Give a reason for declining the advance.")
        return AdvanceService._decide(advance, AdvanceStatus.DECLINED, actor, comment, "advance.declined", AuditSeverity.WARNING, "Salary advance declined")

    @staticmethod
    def _decide(advance, new_status, actor, comment, event, severity, title):
        with transaction.atomic():
            advance = SalaryAdvance.objects.select_for_update().select_related("employee").get(pk=advance.pk)
            if advance.status != AdvanceStatus.REQUESTED:
                raise ValueError("Only a request that is awaiting approval can be approved or declined.")
            advance.status, advance.decided_by, advance.decided_at, advance.decision_comment = new_status, actor, timezone.now(), comment.strip()
            advance.save(update_fields=["status", "decided_by", "decided_at", "decision_comment", "updated_at"])
            AdvanceService._log(event, advance, actor, severity, title, f"{title}: {advance.employee.full_name}, {advance.amount:,.2f}.")
        return advance

    @staticmethod
    def cancel(advance, *, actor, comment=""):
        with transaction.atomic():
            advance = SalaryAdvance.objects.select_for_update().select_related("employee").get(pk=advance.pk)
            if advance.status not in {AdvanceStatus.REQUESTED, AdvanceStatus.APPROVED}:
                raise ValueError("Only an advance that has not been paid yet can be cancelled.")
            advance.status, advance.decision_comment = AdvanceStatus.CANCELLED, comment.strip() or advance.decision_comment
            advance.save(update_fields=["status", "decision_comment", "updated_at"])
            AdvanceService._log("advance.cancelled", advance, actor, AuditSeverity.WARNING, "Salary advance cancelled", f"Advance for {advance.employee.full_name} ({advance.amount:,.2f}) was cancelled.")
        return advance

    @staticmethod
    def pay(advance, *, actor, paid_on=None, reference=""):
        with transaction.atomic():
            advance = SalaryAdvance.objects.select_for_update().select_related("employee").get(pk=advance.pk)
            if advance.status != AdvanceStatus.APPROVED:
                raise ValueError("Only an approved advance can be paid out.")
            advance.status, advance.paid_by, advance.paid_on, advance.payment_reference = AdvanceStatus.PAID, actor, paid_on or timezone.localdate(), reference.strip()
            advance.save(update_fields=["status", "paid_by", "paid_on", "payment_reference", "updated_at"])
            AdvanceService._log("advance.paid", advance, actor, AuditSeverity.SUCCESS, "Salary advance paid", f"{advance.amount:,.2f} was paid to {advance.employee.full_name}.")
            # If the first deduction month's payroll already exists and is still open, take the instalment now.
            period = PayrollPeriod.objects.filter(year=advance.deduct_from_year, month=advance.deduct_from_month).exclude(status__in=LOCKED_PERIOD).first()
            if period is not None:
                AdvanceService._deduct(advance, period, actor)
        return advance

    @staticmethod
    def apply_for_period(period):
        """Take this month's instalment for every paid advance. Called when payroll
        is generated; safe to repeat (one instalment per advance per month)."""
        if period.status in LOCKED_PERIOD:
            return 0
        applied = 0
        due = SalaryAdvance.objects.filter(status=AdvanceStatus.PAID).filter(
            Q(deduct_from_year__lt=period.year) | Q(deduct_from_year=period.year, deduct_from_month__lte=period.month)
        )
        for advance in due.select_related("employee"):
            if AdvanceService._deduct(advance, period, None):
                applied += 1
        return applied

    @staticmethod
    def _deduct(advance, period, actor):
        payroll = period.employee_payrolls.select_for_update().filter(employee=advance.employee).exclude(status__in=LOCKED_RECORD).first()
        if payroll is None or AdvanceRepayment.objects.filter(advance=advance, payroll_period=period).exists():
            return False
        balance = advance.balance
        if balance <= 0:
            return False
        # Never take more than the instalment, what is still owed, or what the person has earned this month.
        available = max(payroll.net_pay, Decimal("0.00"))
        instalment = balance if balance - advance.instalment < Decimal("0.01") or advance.repayments.count() + 1 >= advance.repayment_months else advance.instalment
        amount = min(instalment, balance, available)
        if amount <= 0:
            return False
        number = advance.repayments.count() + 1
        line_item = PayrollLineItem.objects.create(
            payroll=payroll, item_type=PayrollLineItemType.DEDUCTION, code="SALARY_ADVANCE",
            description=f"Salary advance repayment ({number} of {advance.repayment_months})", amount=amount,
            source_type="salary_advance", source_reference=f"{advance.pk}:{period.pk}",
            metadata={"advance_id": advance.pk, "advance_amount": str(advance.amount), "balance_before": str(balance)}, is_system_generated=True,
        )
        AdvanceRepayment.objects.create(advance=advance, payroll_period=period, payroll=payroll, line_item=line_item, amount=amount)
        recalculate_employee_payroll(payroll)
        if advance.balance <= 0:
            advance.status = AdvanceStatus.REPAID
            advance.save(update_fields=["status", "updated_at"])
        AdvanceService._log("advance.repayment_applied", advance, actor, AuditSeverity.SUCCESS, "Salary advance repayment applied", f"{amount:,.2f} was taken from {advance.employee.full_name}'s {period.display_name} payroll.")
        return True

    @staticmethod
    def outstanding_for(employee, exclude=None):
        rows = SalaryAdvance.objects.filter(employee=employee, status__in=[AdvanceStatus.PAID, AdvanceStatus.APPROVED, AdvanceStatus.REQUESTED])
        if exclude is not None:
            rows = rows.exclude(pk=exclude)
        return sum((row.balance if row.status == AdvanceStatus.PAID else row.amount for row in rows), Decimal("0.00"))

    @staticmethod
    def _log(event, advance, actor, severity, title, description):
        AuditService.log(event_type=event, module="advances", employee=advance.employee, actor=actor, object=advance, severity=severity, title=title, description=description, metadata={"advance_id": advance.pk, "amount": str(advance.amount), "status": advance.status})

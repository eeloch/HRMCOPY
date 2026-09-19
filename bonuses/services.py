from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from audit.models import AuditSeverity
from audit.services import AuditService
from payroll.models import EmployeePayrollStatus, PayrollLineItem, PayrollLineItemType, PayrollPeriod, PayrollPeriodStatus
from payroll.services import recalculate_employee_payroll

from .models import Bonus, BonusKind, BonusStatus, EmployeeOfTheMonth, EotmStatus

LOCKED_PERIOD = {PayrollPeriodStatus.APPROVED, PayrollPeriodStatus.PAID, PayrollPeriodStatus.CLOSED}
LOCKED_RECORD = {EmployeePayrollStatus.APPROVED, EmployeePayrollStatus.PAID}
MONTHS = ["", "January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]


def month_label(year, month):
    return f"{MONTHS[month]} {year}"


def _check_month(year, month, what):
    if not (isinstance(year, int) and isinstance(month, int) and 2000 <= year <= 2100 and 1 <= month <= 12):
        raise ValueError(f"Choose a valid {what}.")


class BonusService:
    @staticmethod
    def record(*, employee, amount, reason, performance_year, performance_month, pay_year=None, pay_month=None, kind=BonusKind.PERFORMANCE, actor):
        amount = Decimal(amount)
        if amount <= 0:
            raise ValueError("The bonus amount must be more than zero.")
        if not str(reason or "").strip():
            raise ValueError("Say what this person did that went beyond the call of duty.")
        if employee.status != "active":
            raise ValueError("Bonuses can only be recorded for active employees.")
        pay_year, pay_month = pay_year or performance_year, pay_month or performance_month
        _check_month(performance_year, performance_month, "month for the good work")
        _check_month(pay_year, pay_month, "payroll month")
        bonus = Bonus.objects.create(employee=employee, kind=kind, amount=amount, reason=reason.strip(), performance_year=performance_year, performance_month=performance_month, pay_year=pay_year, pay_month=pay_month, recorded_by=actor)
        BonusService._log("bonus.recorded", bonus, actor, AuditSeverity.INFO, "Bonus recorded", f"A bonus of {amount:,.2f} was recorded for {employee.full_name} for {month_label(performance_year, performance_month)}.")
        return bonus

    @staticmethod
    def approve(bonus, *, actor, comment="", pay_year=None, pay_month=None):
        with transaction.atomic():
            bonus = Bonus.objects.select_for_update().select_related("employee").get(pk=bonus.pk)
            if bonus.status != BonusStatus.PROPOSED:
                raise ValueError("Only a bonus that is awaiting approval can be approved.")
            if pay_year and pay_month:
                _check_month(pay_year, pay_month, "payroll month")
                bonus.pay_year, bonus.pay_month = pay_year, pay_month
            period = PayrollPeriod.objects.filter(year=bonus.pay_year, month=bonus.pay_month).first()
            if period is not None and period.status in LOCKED_PERIOD:
                raise ValueError(f"{month_label(bonus.pay_year, bonus.pay_month)} payroll is already approved, so it cannot take a new bonus. Choose a later payroll month.")
            bonus.status, bonus.decided_by, bonus.decided_at, bonus.decision_comment = BonusStatus.APPROVED, actor, timezone.now(), comment.strip()
            bonus.save()
            BonusService._log("bonus.approved", bonus, actor, AuditSeverity.SUCCESS, "Bonus approved", f"{bonus.employee.full_name}'s bonus of {bonus.amount:,.2f} was approved for the {month_label(bonus.pay_year, bonus.pay_month)} payroll.")
            # If that month's payroll already exists and is still open, add it now; otherwise it is added when payroll is generated.
            if period is not None:
                BonusService._apply(bonus, period, actor)
        return bonus

    @staticmethod
    def decline(bonus, *, actor, comment):
        if not str(comment or "").strip():
            raise ValueError("Give a reason for declining the bonus.")
        with transaction.atomic():
            bonus = Bonus.objects.select_for_update().select_related("employee").get(pk=bonus.pk)
            if bonus.status != BonusStatus.PROPOSED:
                raise ValueError("Only a bonus that is awaiting approval can be declined.")
            bonus.status, bonus.decided_by, bonus.decided_at, bonus.decision_comment = BonusStatus.DECLINED, actor, timezone.now(), comment.strip()
            bonus.save()
            BonusService._log("bonus.declined", bonus, actor, AuditSeverity.WARNING, "Bonus declined", f"{bonus.employee.full_name}'s bonus of {bonus.amount:,.2f} was declined.")
        return bonus

    @staticmethod
    def cancel(bonus, *, actor):
        with transaction.atomic():
            bonus = Bonus.objects.select_for_update().select_related("employee").get(pk=bonus.pk)
            if bonus.status not in {BonusStatus.PROPOSED, BonusStatus.APPROVED}:
                raise ValueError("Only a bonus that is not yet in payroll can be cancelled.")
            bonus.status = BonusStatus.CANCELLED
            bonus.save()
            BonusService._log("bonus.cancelled", bonus, actor, AuditSeverity.WARNING, "Bonus cancelled", f"{bonus.employee.full_name}'s bonus of {bonus.amount:,.2f} was cancelled.")
        return bonus

    @staticmethod
    def apply_for_period(period):
        """Add every approved bonus for this payroll month to that person's payroll as an earning.
        Called when payroll is generated; safe to repeat."""
        if period.status in LOCKED_PERIOD:
            return 0
        applied = 0
        for bonus in Bonus.objects.filter(status=BonusStatus.APPROVED, pay_year=period.year, pay_month=period.month).select_related("employee"):
            if BonusService._apply(bonus, period, None):
                applied += 1
        return applied

    @staticmethod
    def _apply(bonus, period, actor):
        payroll = period.employee_payrolls.select_for_update().filter(employee=bonus.employee).exclude(status__in=LOCKED_RECORD).first()
        if payroll is None or bonus.status != BonusStatus.APPROVED:
            return False
        if bonus.kind == BonusKind.EMPLOYEE_OF_MONTH:
            eotm = EmployeeOfTheMonth.objects.filter(bonus=bonus).select_related("department").first()
            title = f"Employee of the Month - {eotm.department.name}" if eotm else "Employee of the Month"
        else:
            title = "Performance bonus" if bonus.kind == BonusKind.PERFORMANCE else "Bonus"
        line = PayrollLineItem.objects.create(
            payroll=payroll, item_type=PayrollLineItemType.EARNING, code="BONUS" if bonus.kind != BonusKind.EMPLOYEE_OF_MONTH else "EOTM",
            description=f"{title} ({month_label(bonus.performance_year, bonus.performance_month)})", amount=bonus.amount,
            source_type="bonus", source_reference=str(bonus.pk), metadata={"bonus_id": bonus.pk, "reason": bonus.reason[:300]}, is_system_generated=True,
        )
        recalculate_employee_payroll(payroll)
        bonus.status, bonus.payroll, bonus.payroll_line_item = BonusStatus.PAID, payroll, line
        bonus.save(update_fields=["status", "payroll", "payroll_line_item", "updated_at"])
        BonusService._log("bonus.applied", bonus, actor, AuditSeverity.SUCCESS, "Bonus added to payroll", f"{bonus.amount:,.2f} was added to {bonus.employee.full_name}'s {period.display_name} payroll.")
        return True

    @staticmethod
    def _log(event, bonus, actor, severity, title, description):
        AuditService.log(event_type=event, module="bonuses", employee=bonus.employee, actor=actor, object=bonus, severity=severity, title=title, description=description, metadata={"bonus_id": bonus.pk, "amount": str(bonus.amount), "status": bonus.status})


class EmployeeOfTheMonthService:
    @staticmethod
    def propose(*, department, year, month, employee, reason, reward_amount=None, actor):
        _check_month(year, month, "month")
        if not str(reason or "").strip():
            raise ValueError("Say why this person is the Employee of the Month.")
        if employee.status != "active":
            raise ValueError("Only an active employee can be Employee of the Month.")
        if employee.department_id != department.pk:
            raise ValueError(f"{employee.full_name} is not in {department.name}.")
        if reward_amount is not None and Decimal(reward_amount) < 0:
            raise ValueError("The reward cannot be negative.")
        if EmployeeOfTheMonth.objects.filter(department=department, year=year, month=month, status__in=[EotmStatus.PROPOSED, EotmStatus.APPROVED]).exists():
            raise ValueError(f"{department.name} already has an Employee of the Month for {month_label(year, month)}. Cancel or decline it first.")
        reward = Decimal(reward_amount) if reward_amount else None
        entry = EmployeeOfTheMonth.objects.create(department=department, year=year, month=month, employee=employee, reason=reason.strip(), reward_amount=reward if reward and reward > 0 else None, recorded_by=actor)
        EmployeeOfTheMonthService._log("eotm.proposed", entry, actor, AuditSeverity.INFO, "Employee of the Month proposed", f"{employee.full_name} was proposed as {department.name} Employee of the Month for {month_label(year, month)}.")
        return entry

    @staticmethod
    def approve(entry, *, actor, comment="", pay_year=None, pay_month=None):
        with transaction.atomic():
            entry = EmployeeOfTheMonth.objects.select_for_update().select_related("employee", "department").get(pk=entry.pk)
            if entry.status != EotmStatus.PROPOSED:
                raise ValueError("Only a proposal that is awaiting approval can be approved.")
            bonus = None
            if entry.reward_amount:
                bonus = Bonus.objects.create(employee=entry.employee, kind=BonusKind.EMPLOYEE_OF_MONTH, amount=entry.reward_amount, reason=entry.reason, performance_year=entry.year, performance_month=entry.month, pay_year=pay_year or entry.year, pay_month=pay_month or entry.month, recorded_by=entry.recorded_by)
                entry.bonus = bonus
            entry.status, entry.decided_by, entry.decided_at, entry.decision_comment = EotmStatus.APPROVED, actor, timezone.now(), comment.strip()
            entry.save()
            if bonus is not None:
                entry_link = entry  # the bonus needs the award linked before it can be titled in payroll
                BonusService.approve(bonus, actor=actor, comment=f"Employee of the Month: {entry_link.department.name}")
            EmployeeOfTheMonthService._log("eotm.approved", entry, actor, AuditSeverity.SUCCESS, "Employee of the Month approved", f"{entry.employee.full_name} is {entry.department.name} Employee of the Month for {month_label(entry.year, entry.month)}.")
        return entry

    @staticmethod
    def decline(entry, *, actor, comment):
        if not str(comment or "").strip():
            raise ValueError("Give a reason for declining.")
        with transaction.atomic():
            entry = EmployeeOfTheMonth.objects.select_for_update().select_related("employee", "department").get(pk=entry.pk)
            if entry.status != EotmStatus.PROPOSED:
                raise ValueError("Only a proposal that is awaiting approval can be declined.")
            entry.status, entry.decided_by, entry.decided_at, entry.decision_comment = EotmStatus.DECLINED, actor, timezone.now(), comment.strip()
            entry.save()
            EmployeeOfTheMonthService._log("eotm.declined", entry, actor, AuditSeverity.WARNING, "Employee of the Month declined", f"{entry.employee.full_name}'s proposal for {entry.department.name} was declined.")
        return entry

    @staticmethod
    def cancel(entry, *, actor):
        with transaction.atomic():
            entry = EmployeeOfTheMonth.objects.select_for_update().select_related("employee", "department", "bonus").get(pk=entry.pk)
            if entry.status not in {EotmStatus.PROPOSED, EotmStatus.APPROVED}:
                raise ValueError("This entry is already closed.")
            if entry.bonus_id and entry.bonus.status == BonusStatus.PAID:
                raise ValueError("The reward is already in payroll. Reverse it in payroll before cancelling.")
            if entry.bonus_id:
                BonusService.cancel(entry.bonus, actor=actor)
            entry.status = EotmStatus.CANCELLED
            entry.save()
            EmployeeOfTheMonthService._log("eotm.cancelled", entry, actor, AuditSeverity.WARNING, "Employee of the Month cancelled", f"{entry.department.name}'s {month_label(entry.year, entry.month)} entry for {entry.employee.full_name} was cancelled.")
        return entry

    @staticmethod
    def _log(event, entry, actor, severity, title, description):
        AuditService.log(event_type=event, module="bonuses", employee=entry.employee, actor=actor, object=entry, severity=severity, title=title, description=description, metadata={"eotm_id": entry.pk, "department": entry.department.name, "status": entry.status})

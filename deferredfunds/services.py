from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction
from django.utils import timezone

from audit.models import AuditSeverity
from audit.services import AuditService
from employees.models import EmploymentType
from payroll.models import EmployeePayrollStatus, PayrollLineItem, PayrollLineItemType, PayrollPeriodStatus
from payroll.services import recalculate_employee_payroll

from .models import DeferredFundAccount, DeferredFundEntry, DeferredFundWithdrawal, EntryType, WithdrawalKind, WithdrawalStatus

LOCKED_PERIOD = {PayrollPeriodStatus.APPROVED, PayrollPeriodStatus.PAID, PayrollPeriodStatus.CLOSED}
LOCKED_RECORD = {EmployeePayrollStatus.APPROVED, EmployeePayrollStatus.PAID}
TWO_PLACES = Decimal("0.01")

# Company policy. Requests outside these are still recorded, flagged as exceptions,
# and can only be approved by management with a reason.
MIN_MONTHS_FOR_PARTIAL = 18
PARTIAL_LIMIT_PERCENT = Decimal("30")
MIN_NOTICE_MONTHS = 2


def add_months(day, months):
    index = day.month - 1 + months
    year, month = day.year + index // 12, index % 12 + 1
    last = (date(year + (month == 12), month % 12 + 1, 1) - timedelta(days=1)).day
    return date(year, month, min(day.day, last))


def partial_eligible_from(account):
    """18 months after the later of: started saving, started work (when known)."""
    start = account.saving_start
    if account.employee.employment_date and account.employee.employment_date > start:
        start = account.employee.employment_date
    return add_months(start, MIN_MONTHS_FOR_PARTIAL)


def partial_limit(account):
    """What the 30% policy still allows: 30% of everything accumulated, less partials already taken or pending."""
    room = account.accumulated * PARTIAL_LIMIT_PERCENT / 100 - account.partial_withdrawn - account.pending_withdrawals
    return max(money(room), Decimal("0.00"))


def money(value):
    return Decimal(value).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


class DeferredFundService:
    # ---- accounts -------------------------------------------------------------------
    @staticmethod
    def enrol(*, employee, percent, opening_balance=0, saving_since=None, actor):
        percent = Decimal(percent)
        if not Decimal("0") < percent <= Decimal("100"):
            raise ValueError("The percentage must be more than 0 and at most 100.")
        if employee.employment_type != EmploymentType.CONTRACT:
            raise ValueError("Only contract staff have a deferred fund.")
        opening = money(opening_balance or 0)
        if opening < 0:
            raise ValueError("The opening balance cannot be negative.")
        with transaction.atomic():
            account, created = DeferredFundAccount.objects.get_or_create(employee=employee, defaults={"percent": percent, "created_by": actor, "saving_since": saving_since})
            if not created:
                if account.active:
                    raise ValueError("This person is already enrolled.")
                account.active, account.percent = True, percent
                if saving_since:
                    account.saving_since = saving_since
                account.save(update_fields=["active", "percent", "saving_since", "updated_at"])
            if opening > 0:
                DeferredFundEntry.objects.create(account=account, entry_type=EntryType.OPENING, amount=opening, entry_date=timezone.localdate(), note="Balance already held before this system", created_by=actor)
            DeferredFundService._log("deferred_fund.enrolled", account, actor, AuditSeverity.SUCCESS, "Enrolled in deferred fund", f"{employee.full_name} was enrolled at {percent}%" + (f" with an opening balance of {opening:,.2f}." if opening > 0 else "."))
        return account

    @staticmethod
    def set_percent(account, percent, *, actor):
        percent = Decimal(percent)
        if not Decimal("0") < percent <= Decimal("100"):
            raise ValueError("The percentage must be more than 0 and at most 100.")
        old, account.percent = account.percent, percent
        account.save(update_fields=["percent", "updated_at"])
        DeferredFundService._log("deferred_fund.percent_changed", account, actor, AuditSeverity.INFO, "Deferred fund percentage changed", f"{account.employee.full_name}: {old}% to {percent}%. Applies from the next payroll.")
        return account

    @staticmethod
    def adjust(account, *, amount, note, actor):
        amount = money(amount)
        if amount == 0:
            raise ValueError("The adjustment cannot be zero.")
        if not note.strip():
            raise ValueError("Say why the balance is being adjusted.")
        with transaction.atomic():
            account = DeferredFundAccount.objects.select_for_update().get(pk=account.pk)
            if account.balance + amount < 0:
                raise ValueError("That would make the balance negative.")
            entry = DeferredFundEntry.objects.create(account=account, entry_type=EntryType.ADJUSTMENT, amount=amount, entry_date=timezone.localdate(), note=note.strip(), created_by=actor)
            DeferredFundService._log("deferred_fund.adjusted", account, actor, AuditSeverity.WARNING, "Deferred fund balance adjusted", f"{account.employee.full_name}: {amount:+,.2f} ({note.strip()}).")
        return entry

    # ---- payroll --------------------------------------------------------------------
    @staticmethod
    def apply_for_period(period):
        """Take each enrolled contract employee's monthly contribution from this period's
        payroll. Called when payroll is generated; safe to repeat (once per person per month)."""
        if period.status in LOCKED_PERIOD:
            return 0
        applied = 0
        for account in DeferredFundAccount.objects.filter(active=True).select_related("employee"):
            payroll = period.employee_payrolls.select_for_update().filter(employee=account.employee).exclude(status__in=LOCKED_RECORD).first()
            if payroll is None or DeferredFundEntry.objects.filter(account=account, payroll_period=period, entry_type=EntryType.CONTRIBUTION).exists():
                continue
            # A percentage of basic salary, never more than the person actually earns this month.
            amount = min(money(payroll.basic_salary * account.percent / 100), max(payroll.net_pay, Decimal("0.00")))
            if amount <= 0:
                continue
            line_item = PayrollLineItem.objects.create(
                payroll=payroll, item_type=PayrollLineItemType.DEDUCTION, code="DEFERRED_FUND",
                description=f"Deferred fund contribution ({account.percent.normalize():f}%)", amount=amount,
                source_type="deferred_fund", source_reference=f"{account.pk}:{period.pk}", metadata={"percent": str(account.percent)}, is_system_generated=True,
            )
            DeferredFundEntry.objects.create(account=account, entry_type=EntryType.CONTRIBUTION, amount=amount, entry_date=timezone.localdate(), payroll_period=period, line_item=line_item, note=f"{period.display_name} at {account.percent.normalize():f}%")
            recalculate_employee_payroll(payroll)
            applied += 1
        return applied

    # ---- withdrawals ----------------------------------------------------------------
    @staticmethod
    def request_withdrawal(account, *, kind, amount=None, reason="", notice_given_on=None, leaving_on=None, actor):
        exceptions = []
        today = timezone.localdate()
        with transaction.atomic():
            account = DeferredFundAccount.objects.select_for_update().select_related("employee").get(pk=account.pk)
            if account.balance <= 0:
                raise ValueError("There is no money held for this person.")
            if account.withdrawals.filter(kind=WithdrawalKind.FINAL, status__in=[WithdrawalStatus.REQUESTED, WithdrawalStatus.APPROVED]).exists():
                raise ValueError("A full release is already in progress for this person.")
            if kind == WithdrawalKind.PARTIAL:
                amount = money(amount or 0)
                if amount <= 0:
                    raise ValueError("Enter the amount to withdraw.")
                if amount > account.available:
                    raise ValueError(f"Only {account.available:,.2f} can still be requested (balance {account.balance:,.2f}, less requests already in progress).")
                eligible = partial_eligible_from(account)
                if today < eligible:
                    exceptions.append(f"Under {MIN_MONTHS_FOR_PARTIAL} months of service and saving (eligible from {eligible:%d %b %Y}).")
                limit = partial_limit(account)
                if amount > limit:
                    exceptions.append(f"Above the {PARTIAL_LIMIT_PERCENT.normalize():f}% limit: {limit:,.2f} is allowed, {amount:,.2f} requested.")
            else:
                amount = None
                if not notice_given_on or not leaving_on:
                    raise ValueError("Enter the date notice was given and the last working day.")
                if notice_given_on > today:
                    raise ValueError("The notice date cannot be in the future.")
                if leaving_on < notice_given_on:
                    raise ValueError("The last working day cannot be before the notice date.")
                if leaving_on < add_months(notice_given_on, MIN_NOTICE_MONTHS):
                    exceptions.append(f"Less than {MIN_NOTICE_MONTHS} months' notice (notice {notice_given_on:%d %b %Y}, leaving {leaving_on:%d %b %Y}).")
            withdrawal = DeferredFundWithdrawal.objects.create(account=account, kind=kind, amount=amount, reason=reason.strip(), notice_given_on=notice_given_on, leaving_on=leaving_on, rule_exceptions="\n".join(exceptions), recorded_by=actor)
            DeferredFundService._log("deferred_fund.withdrawal_requested", account, actor, AuditSeverity.WARNING if exceptions else AuditSeverity.INFO, "Deferred fund withdrawal requested", f"{account.employee.full_name}: " + (f"{amount:,.2f}" if amount else "full release") + (" (outside policy: " + "; ".join(exceptions) + ")." if exceptions else "."))
        return withdrawal

    @staticmethod
    def forfeit(account, *, actor, reason):
        """Dismissal for theft or serious misconduct: the company keeps the whole fund."""
        if not reason.strip():
            raise ValueError("Give the reason the fund is being forfeited.")
        with transaction.atomic():
            account = DeferredFundAccount.objects.select_for_update().select_related("employee").get(pk=account.pk)
            balance = account.balance
            if balance <= 0:
                raise ValueError("There is no money held for this person.")
            account.withdrawals.filter(status__in=[WithdrawalStatus.REQUESTED, WithdrawalStatus.APPROVED]).update(status=WithdrawalStatus.CANCELLED)
            DeferredFundEntry.objects.create(account=account, entry_type=EntryType.FORFEITURE, amount=-balance, entry_date=timezone.localdate(), note=reason.strip(), created_by=actor)
            account.active = False
            account.save(update_fields=["active", "updated_at"])
            DeferredFundService._log("deferred_fund.forfeited", account, actor, AuditSeverity.WARNING, "Deferred fund forfeited", f"{account.employee.full_name} forfeited {balance:,.2f}: {reason.strip()}")
        return balance

    @staticmethod
    def approve(withdrawal, *, actor, comment=""):
        if withdrawal.rule_exceptions and not comment.strip():
            raise ValueError("This request is outside policy. Give your reason for approving it anyway.")
        return DeferredFundService._decide(withdrawal, WithdrawalStatus.APPROVED, actor, comment)

    @staticmethod
    def decline(withdrawal, *, actor, comment):
        if not comment.strip():
            raise ValueError("Give a reason for declining.")
        return DeferredFundService._decide(withdrawal, WithdrawalStatus.DECLINED, actor, comment)

    @staticmethod
    def _decide(withdrawal, new_status, actor, comment):
        with transaction.atomic():
            withdrawal = DeferredFundWithdrawal.objects.select_for_update().select_related("account__employee").get(pk=withdrawal.pk)
            if withdrawal.status != WithdrawalStatus.REQUESTED:
                raise ValueError("Only a request awaiting approval can be approved or declined.")
            withdrawal.status, withdrawal.decided_by, withdrawal.decided_at, withdrawal.decision_comment = new_status, actor, timezone.now(), comment.strip()
            withdrawal.save(update_fields=["status", "decided_by", "decided_at", "decision_comment", "updated_at"])
            DeferredFundService._log(f"deferred_fund.withdrawal_{new_status}", withdrawal.account, actor, AuditSeverity.SUCCESS if new_status == WithdrawalStatus.APPROVED else AuditSeverity.WARNING, f"Deferred fund withdrawal {new_status}", f"{withdrawal.account.employee.full_name}'s withdrawal was {new_status}.")
        return withdrawal

    @staticmethod
    def cancel(withdrawal, *, actor):
        with transaction.atomic():
            withdrawal = DeferredFundWithdrawal.objects.select_for_update().select_related("account__employee").get(pk=withdrawal.pk)
            if withdrawal.status not in {WithdrawalStatus.REQUESTED, WithdrawalStatus.APPROVED}:
                raise ValueError("Only a withdrawal that has not been paid can be cancelled.")
            withdrawal.status = WithdrawalStatus.CANCELLED
            withdrawal.save(update_fields=["status", "updated_at"])
            DeferredFundService._log("deferred_fund.withdrawal_cancelled", withdrawal.account, actor, AuditSeverity.WARNING, "Deferred fund withdrawal cancelled", f"{withdrawal.account.employee.full_name}'s withdrawal was cancelled.")
        return withdrawal

    @staticmethod
    def pay(withdrawal, *, actor, paid_on=None, reference=""):
        with transaction.atomic():
            withdrawal = DeferredFundWithdrawal.objects.select_for_update().select_related("account__employee").get(pk=withdrawal.pk)
            if withdrawal.status != WithdrawalStatus.APPROVED:
                raise ValueError("Only an approved withdrawal can be paid out.")
            account = DeferredFundAccount.objects.select_for_update().get(pk=withdrawal.account_id)
            balance = account.balance
            final = withdrawal.kind == WithdrawalKind.FINAL
            amount = balance if final else withdrawal.amount
            if amount is None or amount <= 0 or amount > balance:
                raise ValueError(f"The balance is now {balance:,.2f}, which no longer covers this withdrawal.")
            today = paid_on or timezone.localdate()
            if final and withdrawal.leaving_on and today < withdrawal.leaving_on:
                raise ValueError(f"A full release is paid on or after the last working day ({withdrawal.leaving_on:%d %b %Y}).")
            DeferredFundEntry.objects.create(account=account, entry_type=EntryType.RELEASE if final else EntryType.WITHDRAWAL, amount=-amount, entry_date=today, note=withdrawal.reason or ("Full release on exit" if final else "Withdrawal"), created_by=actor)
            withdrawal.amount, withdrawal.status, withdrawal.paid_by, withdrawal.paid_on, withdrawal.payment_reference = amount, WithdrawalStatus.PAID, actor, today, reference.strip()
            withdrawal.save(update_fields=["amount", "status", "paid_by", "paid_on", "payment_reference", "updated_at"])
            if final:
                account.active = False  # nothing left to hold; contributions stop
                account.save(update_fields=["active", "updated_at"])
            DeferredFundService._log("deferred_fund.withdrawal_paid", account, actor, AuditSeverity.SUCCESS, "Deferred fund paid out", f"{amount:,.2f} was paid to {account.employee.full_name}" + (" as a full release." if final else "."))
        return withdrawal

    @staticmethod
    def _log(event, account, actor, severity, title, description):
        AuditService.log(event_type=event, module="deferred_funds", employee=account.employee, actor=actor, object=account, severity=severity, title=title, description=description, metadata={"account_id": account.pk, "percent": str(account.percent)})

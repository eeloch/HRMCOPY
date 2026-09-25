from decimal import Decimal

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Sum

from employees.models import Employee
from payroll.models import PayrollLineItem, PayrollPeriod


class DeferredFundAccount(models.Model):
    """The money a contract employee sets aside with the company each month.

    The company holds it as protection against abrupt resignation. It is paid
    back in part on request, or in full when the person leaves with proper notice.
    """

    employee = models.OneToOneField(Employee, on_delete=models.PROTECT, related_name="deferred_fund")
    percent = models.DecimalField(max_digits=5, decimal_places=2, validators=[MinValueValidator(Decimal("0.01")), MaxValueValidator(Decimal("100"))])
    active = models.BooleanField(default=True)
    enrolled_on = models.DateField(auto_now_add=True)
    # When they started saving with the company (earlier than enrolment for people who saved before this system).
    saving_since = models.DateField(null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["employee__employee_id"]
        permissions = [
            ("view_deferred_funds", "Can view deferred funds"),
            ("manage_deferred_funds", "Can enrol staff, set percentages and record withdrawals"),
            ("approve_deferred_withdrawal", "Can approve or decline deferred fund withdrawals"),
            ("pay_deferred_withdrawal", "Can pay out deferred fund withdrawals"),
        ]

    @property
    def balance(self):
        return self.entries.aggregate(total=Sum("amount"))["total"] or Decimal("0.00")

    @property
    def pending_withdrawals(self):
        total = self.withdrawals.filter(kind="partial", status__in=["requested", "approved"]).aggregate(total=Sum("amount"))["total"]
        return total or Decimal("0.00")

    @property
    def saving_start(self):
        return self.saving_since or self.enrolled_on

    @property
    def partial_withdrawn(self):
        """Paid out so far as partial withdrawals (a positive number)."""
        return -(self.entries.filter(entry_type="withdrawal").aggregate(total=Sum("amount"))["total"] or Decimal("0.00"))

    @property
    def accumulated(self):
        """Everything ever saved and still not forfeited or released: what the 30% policy is measured on."""
        return self.balance + self.partial_withdrawn

    @property
    def available(self):
        """What can still be asked for: the balance less requests already in progress."""
        return self.balance - self.pending_withdrawals

    def __str__(self):
        return f"{self.employee.employee_id} {self.employee.full_name} - deferred fund ({self.percent.normalize():f}%)"


class EntryType(models.TextChoices):
    CONTRIBUTION = "contribution", "Monthly contribution"
    WITHDRAWAL = "withdrawal", "Withdrawal"
    RELEASE = "release", "Full release on exit"
    FORFEITURE = "forfeiture", "Forfeited to the company"
    ADJUSTMENT = "adjustment", "Adjustment"
    OPENING = "opening", "Opening balance"


class DeferredFundEntry(models.Model):
    """One line of the ledger. `amount` is signed: money in is positive, money out negative."""

    account = models.ForeignKey(DeferredFundAccount, on_delete=models.CASCADE, related_name="entries")
    entry_type = models.CharField(max_length=20, choices=EntryType.choices)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    entry_date = models.DateField()
    note = models.CharField(max_length=255, blank=True)
    payroll_period = models.ForeignKey(PayrollPeriod, on_delete=models.CASCADE, null=True, blank=True, related_name="deferred_fund_entries")
    # Deleting the payroll line (e.g. payroll re-generated) removes the contribution, so the balance always matches payroll.
    line_item = models.OneToOneField(PayrollLineItem, on_delete=models.CASCADE, null=True, blank=True, related_name="deferred_fund_entry")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["entry_date", "id"]
        constraints = [
            models.UniqueConstraint(fields=["account", "payroll_period"], condition=models.Q(entry_type="contribution"), name="one_contribution_per_period"),
        ]

    def __str__(self):
        employee = self.account.employee
        sign = "-" if self.amount < 0 else "+"
        return f"{employee.employee_id} {employee.full_name} - {self.get_entry_type_display()} {sign}N {abs(self.amount):,.2f} ({self.entry_date})"


class WithdrawalKind(models.TextChoices):
    PARTIAL = "partial", "Partial withdrawal"
    FINAL = "final", "Full release (leaving with notice)"


class WithdrawalStatus(models.TextChoices):
    REQUESTED = "requested", "Awaiting management approval"
    APPROVED = "approved", "Approved - awaiting payment"
    DECLINED = "declined", "Declined"
    PAID = "paid", "Paid"
    CANCELLED = "cancelled", "Cancelled"


class DeferredFundWithdrawal(models.Model):
    account = models.ForeignKey(DeferredFundAccount, on_delete=models.PROTECT, related_name="withdrawals")
    kind = models.CharField(max_length=10, choices=WithdrawalKind.choices, default=WithdrawalKind.PARTIAL)
    # Partial: what was asked for. Final: left empty until paid, then the whole balance at that moment.
    amount = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    reason = models.TextField(blank=True)
    # Full release: when notice was given and the last working day.
    notice_given_on = models.DateField(null=True, blank=True)
    leaving_on = models.DateField(null=True, blank=True)
    # Policy checks this request does not meet, one per line. Management must give a reason to approve it anyway.
    rule_exceptions = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=WithdrawalStatus.choices, default=WithdrawalStatus.REQUESTED)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_comment = models.TextField(blank=True)
    paid_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    paid_on = models.DateField(null=True, blank=True)
    payment_reference = models.CharField(max_length=120, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        employee = self.account.employee
        amount = f"N {self.amount:,.2f}" if self.amount is not None else "whole balance"
        return f"{employee.employee_id} {employee.full_name} - {self.get_kind_display()} {amount} ({self.get_status_display()})"

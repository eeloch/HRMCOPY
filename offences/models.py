from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models

from employees.models import Employee
from payroll.models import EmployeePayroll, PayrollLineItem, PayrollPeriod


def penalty_for_occurrence(texts, amounts, occurrence):
    """The penalty text and money amount for the Nth time someone does this (3rd and later use the 3rd).
    A tier the policy leaves blank or marks NIL falls back to the last tier that says something - e.g. a second
    "Fighting" is still "Dismissal"."""
    index = min(max(occurrence, 1), len(texts)) - 1
    while index > 0 and (not texts[index].strip() or texts[index].strip().upper() in {"NIL", "-"}):
        index -= 1
    return texts[index].strip(), amounts[index]


class OffenceType(models.Model):
    """One line of the Disciplinary Action Policy: what the offence is and what happens the 1st, 2nd and 3rd time.

    The penalty text is shown as written; the amount is only filled in where the penalty is a plain fixed sum.
    Warnings, suspensions, dismissal and penalties that depend on a cost, a salary or a team have no amount and
    are decided case by case."""

    category = models.CharField(max_length=100, blank=True)
    name = models.CharField(max_length=200)
    default_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"), validators=[MinValueValidator(Decimal("0.00"))])
    description = models.CharField(max_length=255, blank=True)
    penalty_first = models.TextField(blank=True)
    penalty_second = models.TextField(blank=True)
    penalty_third = models.TextField(blank=True)
    amount_first = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True, validators=[MinValueValidator(Decimal("0.00"))])
    amount_second = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True, validators=[MinValueValidator(Decimal("0.00"))])
    amount_third = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True, validators=[MinValueValidator(Decimal("0.00"))])
    sort_order = models.PositiveIntegerField(default=0)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["sort_order", "category", "name"]
        constraints = [models.UniqueConstraint(fields=["category", "name"], name="unique_offence_type_per_category")]

    def __str__(self):
        return f"{self.category} - {self.name}" if self.category else self.name

    def penalty_for(self, occurrence):
        """(text, amount or None) for the Nth offence of this kind. Types set up before tiers existed
        (no tier text) use their single standard amount every time."""
        if not (self.penalty_first or self.penalty_second or self.penalty_third or self.amount_first is not None):
            return "", (self.default_amount or None)
        return penalty_for_occurrence(
            [self.penalty_first, self.penalty_second, self.penalty_third],
            [self.amount_first, self.amount_second, self.amount_third],
            occurrence,
        )


class RewardAutoRule(models.TextChoices):
    NONE = "", "Recorded by hand"
    NO_ABSENCE = "no_absence", "Proposed automatically: no absence in the month"
    NO_LATENESS = "no_lateness", "Proposed automatically: no lateness in the month"


class RewardType(models.Model):
    """One line of the policy's reward sheet. Money rewards are proposed through Bonuses (and approved there);
    the rest (a gift, meals, an increment...) are listed here as the policy states them."""

    category = models.CharField(max_length=100, blank=True)
    name = models.CharField(max_length=200)
    reward_first = models.TextField(blank=True)
    reward_second = models.TextField(blank=True)
    amount_first = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True, validators=[MinValueValidator(Decimal("0.00"))])
    amount_second = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True, validators=[MinValueValidator(Decimal("0.00"))])
    auto_rule = models.CharField(max_length=20, choices=RewardAutoRule.choices, blank=True, default="")
    sort_order = models.PositiveIntegerField(default=0)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["sort_order", "category", "name"]
        constraints = [models.UniqueConstraint(fields=["category", "name"], name="unique_reward_type_per_category")]

    def __str__(self):
        return f"{self.category} - {self.name}" if self.category else self.name

    def reward_for(self, occurrence):
        """(text, amount or None) for the Nth time someone earns this (2nd and later use the 2nd)."""
        return penalty_for_occurrence([self.reward_first, self.reward_second], [self.amount_first, self.amount_second], occurrence)


class EmployeeOffenceStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    APPROVED = "approved", "Approved"
    REJECTED = "rejected", "Rejected"
    DEDUCTED = "deducted", "Deducted"


class EmployeeOffence(models.Model):
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="offences")
    offence_type = models.ForeignKey(OffenceType, on_delete=models.PROTECT, related_name="offences")
    # 0 when the penalty is not money (a warning, a suspension, dismissal...): it is still approved and recorded,
    # but nothing is taken from pay.
    amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"), validators=[MinValueValidator(Decimal("0.00"))])
    # Which time this is for this person and offence (1st, 2nd, 3rd...), and what the policy says should happen.
    occurrence = models.PositiveSmallIntegerField(default=1)
    penalty_text = models.TextField(blank=True)
    incident_date = models.DateField()
    notes = models.TextField(blank=True)

    status = models.CharField(max_length=20, choices=EmployeeOffenceStatus.choices, default=EmployeeOffenceStatus.PENDING)

    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="offences_recorded")
    reviewer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="offences_reviewed")
    reviewed_at = models.DateTimeField(null=True, blank=True)
    comment = models.TextField(blank=True)

    payroll_period = models.ForeignKey(PayrollPeriod, on_delete=models.SET_NULL, null=True, blank=True, related_name="employee_offences")
    payroll = models.ForeignKey(EmployeePayroll, on_delete=models.SET_NULL, null=True, blank=True, related_name="offences")
    payroll_line_item = models.OneToOneField(PayrollLineItem, on_delete=models.SET_NULL, null=True, blank=True, related_name="offence")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-incident_date", "-id"]
        permissions = [
            ("record_employee_offences", "Can record employee offences"),
            ("review_employee_offences", "Can review and approve employee offences"),
            ("manage_offence_configuration", "Can manage offence types"),
        ]

    def __str__(self):
        # "000684 Ada Okafor - Late arrival - 24 Sep 2026"
        return f"{self.employee.employee_id} {self.employee.full_name} - {self.offence_type.name} - {self.incident_date:%d %b %Y}"

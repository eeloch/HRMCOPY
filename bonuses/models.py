from decimal import Decimal

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from employees.models import Department, Employee
from payroll.models import EmployeePayroll, PayrollLineItem


class BonusKind(models.TextChoices):
    PERFORMANCE = "performance", "Performance bonus"
    EMPLOYEE_OF_MONTH = "employee_of_month", "Employee of the Month"
    POLICY_REWARD = "policy_reward", "Policy reward"
    OTHER = "other", "Other bonus"


class BonusStatus(models.TextChoices):
    PROPOSED = "proposed", "Awaiting management approval"
    APPROVED = "approved", "Approved - goes into payroll"
    PAID = "paid", "In payroll"
    DECLINED = "declined", "Declined"
    CANCELLED = "cancelled", "Cancelled"


class Bonus(models.Model):
    """Extra pay for someone who went beyond the call of duty.

    HR records it with the reason, management approves it, and payroll adds it to that
    month's pay as an earning when the payroll is generated.
    """

    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="bonuses")
    kind = models.CharField(max_length=30, choices=BonusKind.choices, default=BonusKind.PERFORMANCE)
    amount = models.DecimalField(max_digits=14, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))])
    reason = models.TextField()
    # The month the good work was done, and the payroll month it is paid with (usually the same).
    performance_year = models.PositiveSmallIntegerField()
    performance_month = models.PositiveSmallIntegerField(validators=[MinValueValidator(1), MaxValueValidator(12)])
    pay_year = models.PositiveSmallIntegerField()
    pay_month = models.PositiveSmallIntegerField(validators=[MinValueValidator(1), MaxValueValidator(12)])
    status = models.CharField(max_length=20, choices=BonusStatus.choices, default=BonusStatus.PROPOSED)
    # Set when this is a reward from the Disciplinary Action Policy (e.g. "No absence"), and which time the person
    # has earned it - that decides the first-time or second-time amount.
    reward_type = models.ForeignKey("offences.RewardType", on_delete=models.SET_NULL, null=True, blank=True, related_name="bonuses")
    occurrence = models.PositiveSmallIntegerField(null=True, blank=True)

    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_comment = models.TextField(blank=True)
    payroll = models.ForeignKey(EmployeePayroll, on_delete=models.SET_NULL, null=True, blank=True, related_name="bonuses")
    payroll_line_item = models.OneToOneField(PayrollLineItem, on_delete=models.SET_NULL, null=True, blank=True, related_name="bonus")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-performance_year", "-performance_month", "-created_at"]
        constraints = [
            # A policy reward is earned once per person per month: re-running the automatic check can never double up.
            models.UniqueConstraint(
                fields=["employee", "reward_type", "performance_year", "performance_month"],
                condition=models.Q(reward_type__isnull=False, status__in=["proposed", "approved", "paid"]),
                name="one_policy_reward_per_person_per_month",
            ),
        ]
        permissions = [
            ("view_bonuses", "Can view bonuses and employees of the month"),
            ("record_bonus", "Can record bonuses and propose employees of the month"),
            ("approve_bonus", "Can approve or decline bonuses and employees of the month"),
        ]

    def __str__(self):
        return f"{self.employee.employee_id} {self.employee.full_name} - {self.get_kind_display()} N {self.amount:,.2f} ({self.performance_month:02d}/{self.performance_year})"


class EotmStatus(models.TextChoices):
    PROPOSED = "proposed", "Awaiting management approval"
    APPROVED = "approved", "Approved"
    DECLINED = "declined", "Declined"
    CANCELLED = "cancelled", "Cancelled"


class EmployeeOfTheMonth(models.Model):
    """One winner per department per month."""

    department = models.ForeignKey(Department, on_delete=models.PROTECT, related_name="employees_of_the_month")
    year = models.PositiveSmallIntegerField()
    month = models.PositiveSmallIntegerField(validators=[MinValueValidator(1), MaxValueValidator(12)])
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="employee_of_the_month_awards")
    reason = models.TextField()
    # An optional reward. When approved it becomes an approved bonus in that month's payroll.
    reward_amount = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    status = models.CharField(max_length=20, choices=EotmStatus.choices, default=EotmStatus.PROPOSED)
    bonus = models.OneToOneField(Bonus, on_delete=models.SET_NULL, null=True, blank=True, related_name="employee_of_the_month")

    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_comment = models.TextField(blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-year", "-month", "department__name"]
        constraints = [
            models.UniqueConstraint(fields=["department", "year", "month"], condition=models.Q(status__in=["proposed", "approved"]), name="one_employee_of_the_month_per_department"),
        ]

    def __str__(self):
        return f"{self.department.name} {self.month:02d}/{self.year} - {self.employee.employee_id} {self.employee.full_name}"

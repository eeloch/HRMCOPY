from django.conf import settings
from django.db import models
from django.utils import timezone

from employees.models import (
    Employee,
    EmploymentType,
)


def current_year():
    return timezone.localdate().year


class LeaveType(models.Model):
    name = models.CharField(max_length=120)
    code = models.CharField(max_length=30, unique=True)
    description = models.TextField(blank=True)
    default_days = models.DecimalField(max_digits=6, decimal_places=2, default=0)
    requires_approval = models.BooleanField(default=True)
    is_paid = models.BooleanField(default=True)
    color = models.CharField(max_length=7, default="#2563EB")
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.code})"


class LeavePolicy(models.Model):
    leave_type = models.ForeignKey(
        LeaveType,
        on_delete=models.PROTECT,
        related_name="policies",
    )

    employment_type = models.CharField(
        max_length=20,
        choices=EmploymentType.choices,
        null=True,
        blank=False,
    )

    allocated_days = models.DecimalField(max_digits=6, decimal_places=2, default=0)
    requires_approval = models.BooleanField(default=True)
    is_paid = models.BooleanField(default=True)

    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = [
            "leave_type__name",
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["leave_type", "employment_type"],
                name="unique_leave_policy_per_type",
            )
        ]

    def __str__(self):
        return f"{self.leave_type.name} policy - {self.get_employment_type_display()}"


class LeaveBalance(models.Model):
    employee = models.ForeignKey(
        Employee,
        on_delete=models.CASCADE,
        related_name="leave_balances",
    )
    leave_type = models.ForeignKey(
        LeaveType,
        on_delete=models.PROTECT,
        related_name="balances",
    )
    year = models.PositiveIntegerField(default=current_year)
    allocated_days = models.DecimalField(max_digits=6, decimal_places=2, default=0)
    used_days = models.DecimalField(max_digits=6, decimal_places=2, default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["employee", "leave_type", "year"],
                name="unique_leave_balance_per_year",
            )
        ]

    @property
    def remaining_days(self):
        return self.allocated_days - self.used_days

    def __str__(self):
        return f"{self.employee} - {self.leave_type} ({self.year})"


class LeaveStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    APPROVED = "approved", "Approved"
    PARTIALLY_APPROVED = "partially_approved", "Partially Approved"
    REJECTED = "rejected", "Rejected"
    CANCELLED = "cancelled", "Cancelled"


class LeaveDuration(models.TextChoices):
    FULL_DAY = "full_day", "Full Day"
    FIRST_HALF = "first_half", "First Half"
    SECOND_HALF = "second_half", "Second Half"


class LeaveRequest(models.Model):
    request_number = models.CharField(
        max_length=20,
        unique=True,
        blank=True,
    )
    employee = models.ForeignKey(
        Employee,
        on_delete=models.CASCADE,
        related_name="leave_requests",
    )
    leave_type = models.ForeignKey(
        LeaveType,
        on_delete=models.PROTECT,
        related_name="requests",
    )
    start_date = models.DateField()
    end_date = models.DateField()
    return_date = models.DateField(null=True, blank=True)
    duration_type = models.CharField(
        max_length=20,
        choices=LeaveDuration.choices,
        default=LeaveDuration.FULL_DAY,
    )
    total_days = models.DecimalField(max_digits=6, decimal_places=2, default=0)
    reason = models.TextField()
    status = models.CharField(
        max_length=20,
        choices=LeaveStatus.choices,
        default=LeaveStatus.PENDING,
    )
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="requested_leave_requests",
    )
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="approved_leave_requests",
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    approved_start_date = models.DateField(null=True, blank=True)
    approved_end_date = models.DateField(null=True, blank=True)
    approved_days = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        null=True,
        blank=True,
    )
    rejection_reason = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        permissions = [
            ("approve_leave", "Can approve leave requests"),
        ]

    def __str__(self):
        return f"{self.employee} - {self.leave_type} ({self.start_date})"

import calendar
from datetime import date
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q

from employees.models import Employee


class PayrollPeriodStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    PROCESSING = "processing", "Processing"
    REVIEW = "review", "Review"
    APPROVED = "approved", "Approved"
    PAID = "paid", "Paid"
    CLOSED = "closed", "Closed"


class EmployeePayrollStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    REVIEW = "review", "Review"
    APPROVED = "approved", "Approved"
    PAID = "paid", "Paid"


class PayrollPeriod(models.Model):
    year = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    month = models.PositiveSmallIntegerField(validators=[MinValueValidator(1), MaxValueValidator(12)])
    status = models.CharField(max_length=20, choices=PayrollPeriodStatus.choices, default=PayrollPeriodStatus.DRAFT)
    created_at = models.DateTimeField(auto_now_add=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="created_payroll_periods")
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="approved_payroll_periods")
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-year", "-month"]
        permissions = [
            ("view_payroll", "Can view payroll"),
            ("manage_payroll", "Can manage payroll periods and records"),
        ]
        constraints = [
            models.UniqueConstraint(fields=["year", "month"], name="unique_payroll_period_month"),
            models.CheckConstraint(condition=Q(month__gte=1) & Q(month__lte=12), name="payroll_period_valid_month"),
        ]

    @property
    def display_name(self):
        return f"{calendar.month_name[self.month]} {self.year}"

    @property
    def start_date(self):
        return date(self.year, self.month, 1)

    @property
    def end_date(self):
        return date(self.year, self.month, calendar.monthrange(self.year, self.month)[1])

    def __str__(self):
        return self.display_name


class EmployeePayroll(models.Model):
    payroll_period = models.ForeignKey(PayrollPeriod, on_delete=models.PROTECT, related_name="employee_payrolls")
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="payroll_records")
    basic_salary = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    gross_earnings = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    total_deductions = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    net_pay = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    status = models.CharField(max_length=20, choices=EmployeePayrollStatus.choices, default=EmployeePayrollStatus.DRAFT)
    generated_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["payroll_period", "employee__last_name", "employee__first_name"]
        constraints = [
            models.UniqueConstraint(fields=["payroll_period", "employee"], name="unique_employee_payroll_per_period")
        ]

    def __str__(self):
        return f"{self.employee.employee_id} - {self.payroll_period}"


class PayrollLineItemType(models.TextChoices):
    EARNING = "earning", "Earning"
    DEDUCTION = "deduction", "Deduction"


class PayrollLineItem(models.Model):
    payroll = models.ForeignKey(EmployeePayroll, on_delete=models.CASCADE, related_name="line_items")
    item_type = models.CharField(max_length=20, choices=PayrollLineItemType.choices)
    code = models.CharField(max_length=50)
    description = models.CharField(max_length=255)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    source_type = models.CharField(max_length=50, blank=True)
    source_reference = models.CharField(max_length=150, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    is_system_generated = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["item_type", "code", "id"]
        constraints = [
            models.CheckConstraint(condition=Q(amount__gte=0), name="payroll_line_item_nonnegative_amount"),
            models.UniqueConstraint(
                fields=["payroll", "source_type", "source_reference"],
                condition=Q(is_system_generated=True),
                name="unique_system_payroll_line_source",
            ),
        ]

    def clean(self):
        if self.amount is not None and self.amount < 0:
            raise ValidationError({"amount": "Payroll line item amounts cannot be negative."})

    def __str__(self):
        return f"{self.payroll} - {self.code}"


class PayrollSetting(models.Model):
    """Inactive configuration storage for future payroll rules and thresholds."""

    key = models.CharField(max_length=100, unique=True)
    value = models.JSONField(default=dict)
    description = models.TextField(blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["key"]

    def __str__(self):
        return self.key

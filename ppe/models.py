from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models

from employees.models import Employee
from payroll.models import EmployeePayroll, PayrollLineItem, PayrollPeriod


class PPEType(models.Model):
    name = models.CharField(max_length=120)
    code = models.CharField(max_length=40, unique=True)
    description = models.TextField(blank=True)
    potentially_employee_deductible = models.BooleanField(default=False)
    default_cost = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class PPEDeductionStatus(models.TextChoices):
    NOT_DEDUCTIBLE = "not_deductible", "Not deductible"
    PENDING = "pending", "Pending HR review"
    APPROVED = "approved", "Approved"
    HELD = "held", "Held"
    DEFERRED = "deferred", "Deferred"
    DEDUCTED = "deducted", "Deducted"


class EmployeePPEIssue(models.Model):
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="ppe_issues")
    ppe_type = models.ForeignKey(PPEType, on_delete=models.PROTECT, related_name="issues")
    quantity = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    issue_date = models.DateField()
    unit_cost = models.DecimalField(max_digits=14, decimal_places=2, validators=[MinValueValidator(Decimal("0.00"))])
    total_cost = models.DecimalField(max_digits=14, decimal_places=2, editable=False)
    issued_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="ppe_issues_recorded")
    notes = models.TextField(blank=True)
    employee_deductible = models.BooleanField(default=False)
    deduction_status = models.CharField(max_length=20, choices=PPEDeductionStatus.choices, default=PPEDeductionStatus.NOT_DEDUCTIBLE)
    target_payroll_period = models.ForeignKey(PayrollPeriod, on_delete=models.SET_NULL, null=True, blank=True, related_name="ppe_deduction_issues")
    deducted_payroll = models.ForeignKey(EmployeePayroll, on_delete=models.SET_NULL, null=True, blank=True, related_name="ppe_deduction_issues")
    payroll_line_item = models.OneToOneField(PayrollLineItem, on_delete=models.SET_NULL, null=True, blank=True, related_name="ppe_issue")
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="ppe_issues_reviewed")
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_comment = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-issue_date", "-id"]
        permissions = [
            ("record_ppe_issue", "Can record PPE issues"),
            ("review_ppe_deduction", "Can review PPE deductions"),
        ]

    def save(self, *args, **kwargs):
        self.total_cost = Decimal(self.quantity) * self.unit_cost
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.employee.employee_id} - {self.ppe_type.code} ({self.issue_date})"

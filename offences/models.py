from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models

from employees.models import Employee
from payroll.models import EmployeePayroll, PayrollLineItem, PayrollPeriod


class OffenceType(models.Model):
    name = models.CharField(max_length=150, unique=True)
    default_amount = models.DecimalField(max_digits=12, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))])
    description = models.CharField(max_length=255, blank=True)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class EmployeeOffenceStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    APPROVED = "approved", "Approved"
    REJECTED = "rejected", "Rejected"
    DEDUCTED = "deducted", "Deducted"


class EmployeeOffence(models.Model):
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="offences")
    offence_type = models.ForeignKey(OffenceType, on_delete=models.PROTECT, related_name="offences")
    amount = models.DecimalField(max_digits=12, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))])
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

from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Sum

from employees.models import Employee
from payroll.models import EmployeePayroll, PayrollLineItem, PayrollPeriod


class AdvanceStatus(models.TextChoices):
    REQUESTED = "requested", "Awaiting management approval"
    APPROVED = "approved", "Approved - awaiting payment"
    DECLINED = "declined", "Declined"
    PAID = "paid", "Paid - being repaid"
    REPAID = "repaid", "Fully repaid"
    CANCELLED = "cancelled", "Cancelled"


class SalaryAdvance(models.Model):
    """Money paid to an employee before payday, taken back from later payroll.

    HR records it, management approves it, finance pays it out. Once paid, each
    month's payroll takes one instalment automatically until it is cleared.
    """

    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="salary_advances")
    amount = models.DecimalField(max_digits=14, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))])
    reason = models.TextField(blank=True)
    repayment_months = models.PositiveSmallIntegerField(default=1)
    deduct_from_year = models.PositiveSmallIntegerField()
    deduct_from_month = models.PositiveSmallIntegerField()
    status = models.CharField(max_length=20, choices=AdvanceStatus.choices, default=AdvanceStatus.REQUESTED)

    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="advances_recorded")
    created_at = models.DateTimeField(auto_now_add=True)
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="advances_decided")
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_comment = models.TextField(blank=True)
    paid_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="advances_paid")
    paid_on = models.DateField(null=True, blank=True)
    payment_reference = models.CharField(max_length=120, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        permissions = [
            ("record_salary_advance", "Can record salary advance requests"),
            ("approve_salary_advance", "Can approve or decline salary advances"),
            ("pay_salary_advance", "Can pay out approved salary advances"),
        ]

    @property
    def amount_repaid(self):
        return self.repayments.aggregate(total=Sum("amount"))["total"] or Decimal("0.00")

    @property
    def balance(self):
        return self.amount - self.amount_repaid

    @property
    def instalment(self):
        return (self.amount / self.repayment_months).quantize(Decimal("0.01"))

    def __str__(self):
        return f"{self.employee.employee_id} {self.employee.full_name} - advance N {self.amount:,.2f} ({self.get_status_display()})"


class AdvanceRepayment(models.Model):
    """One instalment taken from one month's payroll.

    Deleting the payroll line item deletes the repayment, so the balance owed
    always matches what payroll actually took.
    """

    advance = models.ForeignKey(SalaryAdvance, on_delete=models.CASCADE, related_name="repayments")
    payroll_period = models.ForeignKey(PayrollPeriod, on_delete=models.CASCADE, related_name="advance_repayments")
    payroll = models.ForeignKey(EmployeePayroll, on_delete=models.CASCADE, related_name="advance_repayments")
    line_item = models.OneToOneField(PayrollLineItem, on_delete=models.CASCADE, related_name="advance_repayment")
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["payroll_period__year", "payroll_period__month", "id"]
        constraints = [models.UniqueConstraint(fields=["advance", "payroll_period"], name="one_advance_repayment_per_period")]

    def __str__(self):
        employee = self.advance.employee
        return f"{employee.employee_id} {employee.full_name} - repayment N {self.amount:,.2f} ({self.payroll_period.start_date:%b %Y})"

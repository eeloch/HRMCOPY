from dataclasses import dataclass
from decimal import Decimal

from django.db import transaction

from audit.models import AuditSeverity
from audit.services import AuditService
from employees.models import Employee
from payroll.models import EmployeePayroll, PayrollLineItemType


@dataclass
class PayrollGenerationSummary:
    created: int = 0
    existing: int = 0
    skipped: int = 0


def generate_payroll_for_period(period, *, actor=None):
    """Create missing monthly payroll snapshots for active employees only."""
    summary = PayrollGenerationSummary()
    employees = Employee.objects.filter(status="active").order_by("id")

    with transaction.atomic():
        for employee in employees:
            _, created = EmployeePayroll.objects.get_or_create(
                payroll_period=period,
                employee=employee,
                defaults={
                    "basic_salary": employee.basic_salary,
                    "gross_earnings": employee.basic_salary,
                    "total_deductions": Decimal("0.00"),
                    "net_pay": employee.basic_salary,
                },
            )
            if created:
                summary.created += 1
            else:
                summary.existing += 1

        if summary.created:
            AuditService.log(
                event_type="payroll.generated",
                module="payroll",
                actor=actor,
                object=period,
                severity=AuditSeverity.SUCCESS,
                title="Payroll records generated",
                description=f"Generated {summary.created} payroll record(s) for {period.display_name}.",
                metadata={
                    "period": period.display_name,
                    "created": summary.created,
                    "existing": summary.existing,
                    "skipped": summary.skipped,
                },
            )

    return summary


def recalculate_employee_payroll(payroll):
    """Apply explicit generic line items without any attendance deduction rules."""
    earnings = Decimal("0.00")
    deductions = Decimal("0.00")
    for line_item in payroll.line_items.all():
        if line_item.item_type == PayrollLineItemType.EARNING:
            earnings += line_item.amount
        else:
            deductions += line_item.amount

    payroll.gross_earnings = payroll.basic_salary + earnings
    payroll.total_deductions = deductions
    payroll.net_pay = payroll.gross_earnings - deductions
    payroll.save(update_fields=["gross_earnings", "total_deductions", "net_pay", "updated_at"])
    return payroll

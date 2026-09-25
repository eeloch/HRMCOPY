from dataclasses import dataclass
from decimal import Decimal

from django.db import transaction
from django.db.models import Q

from audit.models import AuditSeverity
from audit.services import AuditService
from employees.models import Employee
from payroll.models import EmployeePayroll, PayrollLineItemType


@dataclass
class PayrollGenerationSummary:
    created: int = 0
    existing: int = 0
    skipped: int = 0
    deductions_applied: int = 0


def employees_for_period(period):
    """Use dated employment boundaries; retain the active fallback for undated staff."""
    return Employee.objects.filter(
        Q(employment_date__isnull=True) | Q(employment_date__lte=period.end_date),
        Q(exit_date__gte=period.start_date) | Q(exit_date__isnull=True, status="active"),
    )


def generate_payroll_for_period(period, *, actor=None):
    """Create missing monthly salary snapshots for staff employed in this period."""
    summary = PayrollGenerationSummary()
    employees = employees_for_period(period).order_by("id")
    eligible_ids = set(employees.values_list("id", flat=True))
    existing_ids = set(period.employee_payrolls.values_list("employee_id", flat=True))
    if existing_ids - eligible_ids:
        raise ValueError("This payroll contains employees who were not employed during the period. Review the existing records before regenerating.")

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

        # Deductions accepted/approved before this month's payroll existed (meal
        # excess, offence punishments, PPE) land here, in that month's salary. Deferred
        # imports: meals and offences import payroll at module load.
        from advances.services import AdvanceService
        from bonuses.services import BonusService
        from deferredfunds.services import DeferredFundService
        from meals.services import MealService
        from offences.services import OffenceService
        from ppe.services import PPEDeductionService

        summary.deductions_applied = (
            MealService.apply_accepted_excess_for_period(period)
            + OffenceService.apply_approved_offences_for_period(period)
            + PPEDeductionService.apply_approved_for_period(period)
            + AdvanceService.apply_for_period(period)
            + BonusService.apply_for_period(period)
            + DeferredFundService.apply_for_period(period)
        )

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
                    "deductions_applied": summary.deductions_applied,
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

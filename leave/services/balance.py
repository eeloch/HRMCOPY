from decimal import Decimal

from django.db.models import DecimalField, F, Sum
from django.db.models.functions import Coalesce
from django.utils import timezone

from leave.models import LeavePolicy, LeaveRequest, LeaveStatus


class LeaveBalanceService:
    """Resolve leave entitlements without creating or updating model records."""

    @classmethod
    def resolve_policy(cls, employee, leave_type, as_of=None):
        """Return the active policy assigned to the employee's employment type."""
        return LeavePolicy.objects.filter(
            leave_type=leave_type,
            employment_type=employee.employment_type,
            is_active=True,
        ).first()

    @staticmethod
    def calculate_used_days(employee, leave_type, year):
        """Return approved leave totals for the requested calendar year."""
        approved_amount = Coalesce(
            "approved_days",
            F("total_days"),
            output_field=DecimalField(max_digits=6, decimal_places=2),
        )
        used_days = (
            LeaveRequest.objects.filter(
                employee=employee,
                leave_type=leave_type,
                status__in=[
                    LeaveStatus.APPROVED,
                    LeaveStatus.PARTIALLY_APPROVED,
                ],
                start_date__year=year,
            ).aggregate(total=Sum(approved_amount))["total"]
            or Decimal("0")
        )

        return used_days

    @classmethod
    def get_balance(cls, employee, leave_type, year=None, as_of=None):
        """Return the calculated allocation, usage, remaining balance, and policy."""
        as_of = as_of or timezone.localdate()
        year = year or as_of.year
        matched_policy = cls.resolve_policy(employee, leave_type, as_of)
        allocated_days = (
            matched_policy.allocated_days
            if matched_policy
            else Decimal("0")
        )
        used_days = cls.calculate_used_days(employee, leave_type, year)

        return {
            "allocated_days": allocated_days,
            "used_days": used_days,
            "remaining_days": allocated_days - used_days,
            "matched_policy": matched_policy,
        }

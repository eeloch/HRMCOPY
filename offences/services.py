from django.db import transaction
from django.utils import timezone

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.db.models import Q

from audit.models import AuditSeverity
from audit.services import AuditService
from notifications.models import NotificationSeverity
from notifications.services import NotificationService
from payroll.models import (
    EmployeePayrollStatus,
    PayrollLineItem,
    PayrollLineItemType,
    PayrollPeriod,
    PayrollPeriodStatus,
)
from payroll.services import recalculate_employee_payroll

from .models import EmployeeOffenceStatus


class OffenceService:
    @staticmethod
    @transaction.atomic
    def approve(offence, actor, comment=""):
        """Approve a punishment: it is deducted from the salary in payroll.

        Works at any time. If the employee's payroll record in the open period
        already exists it is deducted immediately; otherwise the offence is held as
        approved and deducted automatically when that period's payroll is generated
        (see apply_approved_offences_for_period). A payroll period or record that is
        already approved/paid is never altered.
        """
        if offence.status != EmployeeOffenceStatus.PENDING:
            raise ValueError("This offence has already been decided.")

        period = (
            PayrollPeriod.objects
            .exclude(status__in=[
                PayrollPeriodStatus.APPROVED,
                PayrollPeriodStatus.PAID,
                PayrollPeriodStatus.CLOSED,
            ])
            .order_by("-year", "-month")
            .first()
        )
        payroll = period.employee_payrolls.filter(employee=offence.employee).first() if period else None
        if payroll is not None and payroll.status in {EmployeePayrollStatus.APPROVED, EmployeePayrollStatus.PAID}:
            raise ValueError("This employee payroll record cannot accept offence deductions.")

        offence.status = EmployeeOffenceStatus.APPROVED
        offence.reviewer = actor
        offence.reviewed_at = timezone.now()
        offence.comment = comment.strip()
        offence.payroll_period = period
        offence.save()

        if payroll is not None:
            OffenceService.apply_to_payroll(offence, payroll)

        AuditService.log(
            event_type="offences.approved",
            module="offences",
            employee=offence.employee,
            actor=actor,
            object=offence,
            severity=AuditSeverity.SUCCESS,
            title="Employee offence approved",
            description=(
                "Offence deduction approved and applied to payroll."
                if payroll is not None
                else "Offence deduction approved; it will be deducted when payroll is generated."
            ),
            metadata={"offence": offence.pk, "amount": str(offence.amount), "deducted_now": payroll is not None},
        )

        if offence.recorded_by_id:
            NotificationService.create(
                recipient=offence.recorded_by,
                event_type="offences.approved",
                title="Offence approved",
                message=(
                    f"{offence.employee.full_name}: {offence.offence_type.name} ({offence.amount}) was approved and deducted from payroll."
                    if payroll is not None
                    else f"{offence.employee.full_name}: {offence.offence_type.name} ({offence.amount}) was approved and will be deducted when payroll is generated."
                ),
                severity=NotificationSeverity.SUCCESS,
                employee=offence.employee,
                related_url="/offences",
            )

        return offence

    @staticmethod
    def apply_to_payroll(offence, payroll):
        """Add an approved offence to an employee's payroll as a deduction (idempotent)."""
        line, _ = PayrollLineItem.objects.get_or_create(
            payroll=payroll,
            source_type="employee_offence",
            source_reference=str(offence.pk),
            is_system_generated=True,
            defaults={
                "item_type": PayrollLineItemType.DEDUCTION,
                "code": "OFFENCE",
                "description": f"{offence.offence_type.name} - {offence.incident_date}",
                "amount": offence.amount,
                "metadata": {
                    "offence_id": offence.pk,
                    "offence_type": offence.offence_type.name,
                    "incident_date": offence.incident_date.isoformat(),
                },
            },
        )
        recalculate_employee_payroll(payroll)
        offence.payroll, offence.payroll_line_item, offence.payroll_period = payroll, line, payroll.payroll_period
        offence.status = EmployeeOffenceStatus.DEDUCTED
        offence.save()
        return line

    @staticmethod
    def apply_approved_offences_for_period(period):
        """Deduct every approved-but-not-yet-deducted offence aimed at this period
        (or, if none was open when it was approved, from the incident's month) from
        the matching payroll records. Called when payroll is generated."""
        from .models import EmployeeOffence

        applied = 0
        awaiting = EmployeeOffence.objects.filter(status=EmployeeOffenceStatus.APPROVED).filter(
            Q(payroll_period=period)
            | Q(payroll_period__isnull=True, incident_date__year=period.year, incident_date__month=period.month)
        ).select_related("employee", "offence_type")
        for offence in awaiting:
            payroll = period.employee_payrolls.filter(employee=offence.employee).exclude(status__in=[EmployeePayrollStatus.APPROVED, EmployeePayrollStatus.PAID]).first()
            if payroll is not None:
                OffenceService.apply_to_payroll(offence, payroll)
                applied += 1
        return applied

    @staticmethod
    def reject(offence, actor, comment):
        if offence.status != EmployeeOffenceStatus.PENDING:
            raise ValueError("This offence has already been decided.")
        if not comment.strip():
            raise ValueError("A rejection reason is required.")

        offence.status = EmployeeOffenceStatus.REJECTED
        offence.reviewer = actor
        offence.reviewed_at = timezone.now()
        offence.comment = comment.strip()
        offence.save()

        AuditService.log(
            event_type="offences.rejected",
            module="offences",
            employee=offence.employee,
            actor=actor,
            object=offence,
            severity=AuditSeverity.WARNING,
            title="Employee offence rejected",
            description="Offence deduction rejected.",
            metadata={"offence": offence.pk, "reason": comment},
        )

        if offence.recorded_by_id:
            NotificationService.create(
                recipient=offence.recorded_by,
                event_type="offences.rejected",
                title="Offence rejected",
                message=f"{offence.employee.full_name}: {comment.strip()}",
                severity=NotificationSeverity.WARNING,
                employee=offence.employee,
                related_url="/offences",
            )

        return offence

    @staticmethod
    def notify_reviewers_of_pending_offence(offence):
        User = get_user_model()
        permission = Permission.objects.filter(
            content_type__app_label="offences", codename="review_employee_offences"
        ).first()
        reviewers = User.objects.filter(
            Q(is_superuser=True)
            | Q(user_permissions=permission)
            | Q(groups__permissions=permission)
        ).distinct()
        for user in reviewers:
            NotificationService.create(
                recipient=user,
                event_type="offences.pending",
                title="Offence pending review",
                message=f"{offence.employee.full_name}: {offence.offence_type.name} ({offence.amount}) awaits review.",
                severity=NotificationSeverity.INFO,
                employee=offence.employee,
                related_url="/offences",
            )

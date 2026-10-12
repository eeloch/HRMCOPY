"""Changing an attendance charge after it was decided - for when an employee contests their statement and, once the
punches have been checked, the charge turns out wrong (or a reversal turns out to have been a mistake).

A pending case is decided in Attendance > Exceptions as usual; this is for cases already approved, waived or held. The
change is recorded with the reason and what it was before, the employee's payroll for that month is brought in line
(the deduction is taken out, or put in) while that payroll is still open, and an absence also refreshes the meal-ticket
penalty it can cause. An approved payroll is never altered."""

from django.db import transaction
from django.utils import timezone

from audit.models import AuditSeverity
from audit.services import AuditService

CHANGEABLE_TYPES = ("late", "early_departure", "absence", "missing_clock_in", "missing_clock_out")
DECISIONS = {"waived": "Waived - no charge", "approved": "Charged"}


@transaction.atomic
def change_decision(exception, decision, reason, actor):
    from attendance.models import AttendanceException
    from attendance.services.penalties import estimated_daily_rate, penalty_for
    from meals.services import MealService
    from payroll.models import EmployeePayrollStatus, PayrollLineItem, PayrollPeriod
    from payroll.services import recalculate_employee_payroll
    from payroll.services.attendance import LOCKED_PERIOD_STATUSES, PayrollAttendanceSyncSummary, _sync_employee_payroll

    exception = AttendanceException.objects.select_for_update().select_related("attendance", "attendance__employee").get(pk=exception.pk)
    reason = (reason or "").strip()
    if exception.exception_type not in CHANGEABLE_TYPES:
        raise ValueError("Only lateness, early departure, absence and missing clock-in / clock-out charges can be changed here.")
    if decision not in DECISIONS:
        raise ValueError("Choose to waive the charge or to charge it.")
    if exception.status == "pending":
        raise ValueError("This case has not been decided yet - decide it in Attendance > Exceptions.")
    if exception.status == decision:
        raise ValueError(f"This case is already {'waived' if decision == 'waived' else 'charged'}.")
    if not reason:
        raise ValueError("Say why the charge is being changed (what was checked).")

    employee, day = exception.attendance.employee, exception.attendance.date
    period = PayrollPeriod.objects.filter(year=day.year, month=day.month).first()
    payroll = period.employee_payrolls.filter(employee=employee).first() if period else None
    if payroll is not None and (period.status in LOCKED_PERIOD_STATUSES or payroll.status in {EmployeePayrollStatus.APPROVED, EmployeePayrollStatus.PAID}):
        raise ValueError(f"The {day:%B %Y} payroll for this employee is already approved, so this charge can no longer be changed here.")

    previous = f"{exception.status} by {exception.reviewed_by or 'nobody'}" + (f" on {exception.reviewed_at:%d %b %Y}" if exception.reviewed_at else "")
    earlier = f" [Earlier: {previous}{'; ' + exception.admin_comment if exception.admin_comment else ''}]"
    before = exception.status
    exception.status, exception.reviewed_at = decision, timezone.now()
    exception.reviewed_by = (actor.get_full_name() or actor.get_username()) if actor else "System"
    exception.admin_comment = reason + earlier
    if decision == "approved" and exception.exception_type in ("late", "early_departure"):
        result = penalty_for(exception.exception_type, exception.minutes_affected, estimated_daily_rate(employee, day))
        exception.proposed_deduction = result[0] if result else 0
    exception.save()

    marked = exception.attendance
    if decision == "waived" and exception.exception_type == "absence" and marked.absence_marked_by:
        # Someone had marked this day absent on a no-attendance shift: taking it back makes it a normal present day again.
        marked.status, marked.absence_marked_by = "present", ""
        marked.save(update_fields=["status", "absence_marked_by"])
    if exception.exception_type == "absence":
        MealService.sync_absence_penalties(employee, day, actor=actor)
    if payroll is not None:
        if decision == "waived":  # take the deduction out directly - it must go even when the month's roster is incomplete
            lines = PayrollLineItem.objects.filter(payroll=payroll, is_system_generated=True, source_type="attendance_exception", source_reference=str(exception.pk))
            if lines.exists():
                lines.delete()
                recalculate_employee_payroll(payroll)
        else:
            _sync_employee_payroll(payroll, PayrollAttendanceSyncSummary())

    AuditService.log(
        event_type="attendance.exception_changed", module="attendance", employee=employee, actor=actor, object=exception,
        severity=AuditSeverity.WARNING, title=f"Attendance charge {'reversed' if decision == 'waived' else 'reinstated'}",
        description=f"{exception.get_exception_type_display()} on {day} changed from {before} to {decision}: {reason}",
        metadata={"exception_id": exception.pk, "from": before, "to": decision, "reason": reason, "attendance_date": day.isoformat()},
    )
    return exception


@transaction.atomic
def change_fine_amount(exception, amount, reason, actor):
    """Raise (or restore) the fine on a missing clock-in / clock-out - for someone who really left work very early. Never
    below the minimum. The employee's payroll for that month follows while it is still open."""
    from decimal import Decimal, InvalidOperation

    from attendance.models import AttendanceException
    from attendance.services.penalties import MISSING_PUNCH_FINE, MISSING_PUNCH_TYPES
    from payroll.models import EmployeePayrollStatus, PayrollPeriod
    from payroll.services.attendance import LOCKED_PERIOD_STATUSES, PayrollAttendanceSyncSummary, _sync_employee_payroll

    exception = AttendanceException.objects.select_for_update().select_related("attendance", "attendance__employee").get(pk=exception.pk)
    reason = (reason or "").strip()
    if exception.exception_type not in MISSING_PUNCH_TYPES:
        raise ValueError("Only a missing clock-in or clock-out fine has an amount that can be changed.")
    try:
        amount = Decimal(str(amount)).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        raise ValueError("Enter the fine as a number.")
    if amount < MISSING_PUNCH_FINE:
        raise ValueError(f"The fine is at least N{MISSING_PUNCH_FINE:,.0f}.")
    if exception.status in ("waived",):
        raise ValueError("This charge was waived - charge it first, then change the amount.")
    if not reason:
        raise ValueError("Say why the fine is being changed (for example, the person really left very early).")

    employee, day = exception.attendance.employee, exception.attendance.date
    period = PayrollPeriod.objects.filter(year=day.year, month=day.month).first()
    payroll = period.employee_payrolls.filter(employee=employee).first() if period else None
    if payroll is not None and (period.status in LOCKED_PERIOD_STATUSES or payroll.status in {EmployeePayrollStatus.APPROVED, EmployeePayrollStatus.PAID}):
        raise ValueError(f"The {day:%B %Y} payroll for this employee is already approved, so this fine can no longer be changed.")

    before = exception.proposed_deduction
    exception.proposed_deduction = amount
    exception.admin_comment = (exception.admin_comment + " | " if exception.admin_comment else "") + f"Fine changed from N{before:,.0f} to N{amount:,.0f}: {reason}"
    exception.save(update_fields=["proposed_deduction", "admin_comment"])
    if exception.status == "approved" and payroll is not None:
        _sync_employee_payroll(payroll, PayrollAttendanceSyncSummary())
    AuditService.log(
        event_type="attendance.fine_changed", module="attendance", employee=employee, actor=actor, object=exception,
        severity=AuditSeverity.WARNING, title="Attendance fine changed",
        description=f"{exception.get_exception_type_display()} on {day}: fine changed from N{before:,.0f} to N{amount:,.0f}: {reason}",
        metadata={"exception_id": exception.pk, "from": str(before), "to": str(amount), "reason": reason, "attendance_date": day.isoformat()},
    )
    return exception

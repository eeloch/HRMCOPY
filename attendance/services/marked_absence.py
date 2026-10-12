"""On a shift where attendance is not required (management who need not come in, drivers working out and about) nobody
is ever absent by default. A day only counts as not worked when someone specifically says so: this records that, as a
decided absence with the reason and who said it, so it follows the normal absence rules - the day's pay deduction and
the meal-ticket penalty. Taking it back is "Reverse charge" on the case in the employee's statement."""

from django.db import transaction
from django.utils import timezone

from audit.models import AuditSeverity
from audit.services import AuditService

ABSENT_DAY_MINUTES = 720


@transaction.atomic
def mark_absent(employee, day, reason, actor):
    from attendance.models import AttendanceException, DailyAttendance, EmployeeRosterDay
    from attendance.services.processing import calculate_proposed_deduction, shift_schedule
    from meals.services import MealService
    from payroll.models import EmployeePayrollStatus, PayrollPeriod
    from payroll.services.attendance import LOCKED_PERIOD_STATUSES, PayrollAttendanceSyncSummary, _sync_employee_payroll

    reason = (reason or "").strip()
    if not reason:
        raise ValueError("Say who reported that this person did not work, and how it is known.")
    if day > timezone.localdate():
        raise ValueError("That day has not happened yet.")
    roster = EmployeeRosterDay.objects.select_related("shift").filter(employee=employee, date=day).first()
    if roster is None or roster.status != "work" or roster.shift is None or not roster.shift.attendance_not_required:
        raise ValueError("This person is not on a no-attendance shift that day - decide a normal absence in Attendance > Exceptions.")
    period = PayrollPeriod.objects.filter(year=day.year, month=day.month).first()
    payroll = period.employee_payrolls.filter(employee=employee).first() if period else None
    if payroll is not None and (period.status in LOCKED_PERIOD_STATUSES or payroll.status in {EmployeePayrollStatus.APPROVED, EmployeePayrollStatus.PAID}):
        raise ValueError(f"The {day:%B %Y} payroll for this employee is already approved, so the day can no longer be marked absent.")
    existing = DailyAttendance.objects.filter(employee=employee, date=day).first()
    if existing is not None and existing.status == "leave":
        raise ValueError("This person is on approved leave that day.")
    if existing is not None and existing.absence_marked_by:
        raise ValueError("This day is already marked absent.")

    who = (actor.get_full_name() or actor.get_username()) if actor else "System"
    start, end = shift_schedule(roster.shift, day)
    attendance, _ = DailyAttendance.objects.get_or_create(employee=employee, date=day, defaults={"shift": roster.shift, "scheduled_start": start, "scheduled_end": end})
    attendance.status, attendance.absence_marked_by = "absent", who
    attendance.save(update_fields=["status", "absence_marked_by"])
    exception, _ = AttendanceException.objects.get_or_create(attendance=attendance, exception_type="absence", defaults={"minutes_affected": ABSENT_DAY_MINUTES})
    exception.minutes_affected = ABSENT_DAY_MINUTES
    exception.proposed_deduction = calculate_proposed_deduction(employee, ABSENT_DAY_MINUTES)
    exception.status, exception.reviewed_by, exception.reviewed_at, exception.admin_comment = "approved", who, timezone.now(), f"Marked absent: {reason}"
    exception.save()
    MealService.sync_absence_penalties(employee, day, actor=actor)
    if payroll is not None:
        _sync_employee_payroll(payroll, PayrollAttendanceSyncSummary())
    AuditService.log(
        event_type="attendance.marked_absent", module="attendance", employee=employee, actor=actor, object=attendance,
        severity=AuditSeverity.WARNING, title="Marked absent (no-attendance shift)",
        description=f"{employee.full_name} marked absent on {day}: {reason}",
        metadata={"date": day.isoformat(), "reason": reason},
    )
    return exception

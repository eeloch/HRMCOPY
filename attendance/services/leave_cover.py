"""Approved leave clears the absences it covers.

A day inside approved leave is "On leave", not "Absent": the attendance row is switched over, an absence case on it is
dropped (if still pending) or waived with the leave named as the reason (if it was already decided, which also takes
the deduction and the meal-ticket penalty it caused back out). Runs when the leave is approved - however late - and
whenever attendance is processed for a leave day. A day that cannot be changed (its payroll is already approved) is
left as it is and reported."""

from django.db import transaction

from attendance.models import AttendanceException, DailyAttendance


def _reason(leave_ref):
    return f"Covered by approved leave{' ' + leave_ref if leave_ref else ''}"


def cover_day(attendance, *, leave_ref="", actor=None):
    """Turn one no-punch absent day into leave. Returns True when the day was changed."""
    from attendance.services.reversal import change_decision

    if attendance.status != "absent" or attendance.actual_clock_in or attendance.actual_clock_out:
        return False
    reason = _reason(leave_ref)
    with transaction.atomic():
        for exception in AttendanceException.objects.filter(attendance=attendance, exception_type="absence"):
            if exception.status == "pending":
                exception.delete()
            elif exception.status == "approved":
                change_decision(exception, "waived", reason, actor)  # raises ValueError when payroll is approved
            elif exception.status == "held":
                exception.status = "waived"
                exception.admin_comment = (reason + (f" [Earlier: held; {exception.admin_comment}]" if exception.admin_comment else " [Earlier: held]"))
                exception.save(update_fields=["status", "admin_comment"])
        attendance.status = "leave"
        attendance.save(update_fields=["status"])
    return True


def cover_leave_days(employee, start, end, *, leave_ref="", actor=None):
    """Cover every no-punch absent day of the employee between start and end. Returns (covered dates, skipped [(date, why)])."""
    from meals.services import MealService

    covered, skipped = [], []
    rows = DailyAttendance.objects.filter(employee=employee, date__range=(start, end), status="absent").order_by("date")
    for attendance in rows:
        try:
            if cover_day(attendance, leave_ref=leave_ref, actor=actor):
                covered.append(attendance.date)
        except ValueError as error:
            skipped.append((attendance.date, str(error)))
    if covered:
        MealService.sync_absence_penalties(employee, max(covered), actor=actor)
    return covered, skipped

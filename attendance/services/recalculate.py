"""Re-read people's attendance from their punches after their roster changed for days already worked.

Saving a shift plan with a start date in the past (or a day off from a past date) rewrites the roster for those days; the
attendance already recorded was judged on the old roster, so it is read again - on the new one - without anyone having to
ask. Decisions already taken are never touched (an approved or waived case stays as decided); what changes is the record
itself (shift, status, minutes) and the cases still waiting for a decision. A day that became a rest day loses a record
nobody worked."""

import logging
import threading
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)

INLINE_LIMIT = getattr(settings, "ATTENDANCE_RECALC_INLINE_LIMIT", 400)  # person-days worked out inside the request; more runs in the background


def _remove_unworked_rest_day_records(employees, start, end, actor):
    """A rest day cannot hold a Night-shift record nobody worked. Only records with no punches at all are removed."""
    from attendance.models import DailyAttendance, EmployeeRosterDay, RosterDayStatus
    from audit.models import AuditSeverity
    from audit.services import AuditService

    removed = []
    for row in EmployeeRosterDay.objects.filter(employee__in=employees, date__range=(start, end), status=RosterDayStatus.REST).select_related("employee"):
        attendance = DailyAttendance.objects.filter(employee=row.employee, date=row.date, actual_clock_in__isnull=True, actual_clock_out__isnull=True).exclude(status="leave").first()
        if attendance is None:
            continue
        AuditService.log(
            event_type="attendance.record_removed", module="attendance", employee=row.employee, actor=None, object=attendance, severity=AuditSeverity.WARNING,
            title="Attendance record removed - rest day",
            description=f"{row.date} became a rest day after a shift change; the leftover record (no punches that day) was removed.",
            metadata={"date": row.date.isoformat(), "set_by": str(actor)},
        )
        attendance.delete()
        removed.append((row.employee.employee_id, row.date))
    return removed


def recalculate_attendance_for(employees, start, end=None, *, actor=""):
    """Work these people's attendance out again for every day from `start` to `end` (default today). Returns the number of
    person-days processed."""
    from attendance.services.processing import process_employee_attendance
    from attendance.services.shift_plans import clear_false_absences_on_rest_days

    employees = list(employees)
    end = end or timezone.localdate()
    if not employees or start > end:
        return 0
    clear_false_absences_on_rest_days(employees, start, end, actor=actor)
    _remove_unworked_rest_day_records(employees, start, end, actor)
    processed, day = 0, start
    while day <= end:
        for employee in employees:
            process_employee_attendance(employee, day)
            processed += 1
        day += timedelta(days=1)
    return processed


def recalculate_after_roster_change(employees, start, *, actor=""):
    """Called when a roster was rewritten from a past date. Never raises: a problem here must not undo the plan change.
    Small jobs run now; big ones (a whole roster upload) run in the background so the request returns."""
    if not getattr(settings, "ATTENDANCE_RECALC_ON_PLAN_CHANGE", True):
        return 0
    today = timezone.localdate()
    employees = list(employees)
    if not employees or start > today:
        return 0
    size = len(employees) * ((today - start).days + 1)

    def run():
        try:
            return recalculate_attendance_for(employees, start, today, actor=actor)
        except Exception:  # noqa: BLE001
            logger.exception("Recalculating attendance after a roster change failed")
            return 0

    if size <= INLINE_LIMIT:
        return run()
    threading.Thread(target=run, daemon=True).start()
    return 0

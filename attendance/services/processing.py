"""Idempotent conversion of biometric events into daily attendance facts."""

from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from attendance.models import AttendanceEvent, AttendanceException, DailyAttendance, EmployeeRosterDay, ShiftAssignment
from attendance.services.leave import approved_leave_employee_ids
from attendance.services.roster import get_employee_roster_day
from audit.models import AuditSeverity
from audit.services import AuditService


CAPTURE_WINDOW_HOURS = 3
PENDING_EXCEPTION_STATUS = "pending"


def get_active_shift_assignment(employee, work_date):
    return (
        ShiftAssignment.objects.filter(employee=employee, start_date__lte=work_date)
        .filter(Q(end_date__isnull=True) | Q(end_date__gte=work_date))
        .select_related("shift")
        .order_by("-start_date")
        .first()
    )


def combine_date_and_time(work_date, shift_time):
    return timezone.make_aware(
        datetime.combine(work_date, shift_time),
        timezone.get_current_timezone(),
    )


def calculate_minute_rate(employee, working_days=26):
    if employee.basic_salary <= 0:
        return Decimal("0.00")

    return (employee.basic_salary / Decimal(working_days) / Decimal(720)).quantize(
        Decimal("0.01"),
        rounding=ROUND_HALF_UP,
    )


def calculate_proposed_deduction(employee, minutes):
    return (calculate_minute_rate(employee) * Decimal(minutes)).quantize(
        Decimal("0.01"),
        rounding=ROUND_HALF_UP,
    )


def shift_schedule(shift, work_date):
    """Return the scheduled bounds, including the next-day end for overnight shifts."""
    scheduled_start = combine_date_and_time(work_date, shift.start_time)
    end_date = work_date + timedelta(days=1) if shift.is_overnight else work_date
    scheduled_end = combine_date_and_time(end_date, shift.end_time)
    return scheduled_start, scheduled_end


def _sync_exception(attendance, exception_type, *, applies, minutes=0, monetary=True):
    """Update pending exceptions only; HR-reviewed decisions are immutable here."""
    exceptions = AttendanceException.objects.filter(
        attendance=attendance,
        exception_type=exception_type,
    ).order_by("id")
    exception = exceptions.first()

    if not applies:
        exceptions.filter(status=PENDING_EXCEPTION_STATUS).delete()
        return

    deduction = calculate_proposed_deduction(attendance.employee, minutes) if monetary else Decimal("0.00")
    if exception is None:
        AttendanceException.objects.create(
            attendance=attendance,
            exception_type=exception_type,
            minutes_affected=minutes,
            proposed_deduction=deduction,
        )
    elif exception.status == PENDING_EXCEPTION_STATUS:
        exception.minutes_affected = minutes
        exception.proposed_deduction = deduction
        exception.save(update_fields=["minutes_affected", "proposed_deduction"])


def _log_attendance_changes(attendance, previous):
    """Emit audit events only when a newly processed value changes."""
    employee = attendance.employee
    metadata = {"attendance_date": attendance.date.isoformat()}

    if attendance.actual_clock_in and previous["clock_in"] != attendance.actual_clock_in:
        AuditService.log(
            event_type="attendance.clock_in",
            module="attendance",
            employee=employee,
            object=attendance,
            severity=AuditSeverity.SUCCESS,
            title="Clock in recorded",
            description=f"Clock in recorded for {employee.full_name}.",
            metadata={**metadata, "clock_in": attendance.actual_clock_in.isoformat()},
        )

    if attendance.actual_clock_out and previous["clock_out"] != attendance.actual_clock_out:
        AuditService.log(
            event_type="attendance.clock_out",
            module="attendance",
            employee=employee,
            object=attendance,
            severity=AuditSeverity.SUCCESS,
            title="Clock out recorded",
            description=f"Clock out recorded for {employee.full_name}.",
            metadata={**metadata, "clock_out": attendance.actual_clock_out.isoformat()},
        )

    if attendance.late_minutes and (
        previous["status"] != "late" or previous["late_minutes"] != attendance.late_minutes
    ):
        AuditService.log(
            event_type="attendance.late",
            module="attendance",
            employee=employee,
            object=attendance,
            severity=AuditSeverity.WARNING,
            title="Late arrival recorded",
            description=f"{employee.full_name} arrived late.",
            metadata={**metadata, "late_minutes": attendance.late_minutes},
        )


def process_employee_attendance(employee, work_date, *, now=None, leave_ids=None):
    """Process one employee's assigned shift date without duplicating facts.

    While the shift is still open (until the punch-capture window after it ends) nobody is marked absent and no
    missing-punch or early-departure exception is raised: a person who has punched is simply present or late.
    Once the shift and its capture window are over the day is judged in full. Running it again is always safe.
    """
    now = now or timezone.now()
    roster_day = get_employee_roster_day(employee, work_date)
    existing_attendance = DailyAttendance.objects.filter(employee=employee, date=work_date).first()
    # A date-level rest day prevents automated absence creation. Raw punches are
    # intentionally left untouched for a future rest-day/overtime policy.
    if roster_day and roster_day.status == "rest":
        return existing_attendance
    assignment = get_active_shift_assignment(employee, work_date)
    shift = roster_day.shift if roster_day else (assignment.shift if assignment else None)
    if not shift:
        return None

    scheduled_start, scheduled_end = shift_schedule(shift, work_date)
    events = AttendanceEvent.objects.filter(
        employee=employee,
        timestamp__gte=scheduled_start - timedelta(hours=CAPTURE_WINDOW_HOURS),
        timestamp__lte=scheduled_end + timedelta(hours=CAPTURE_WINDOW_HOURS),
    ).order_by("timestamp")

    has_leave = employee.pk in (leave_ids if leave_ids is not None else set(approved_leave_employee_ids(work_date)))
    shift_open = now < scheduled_end + timedelta(hours=CAPTURE_WINDOW_HOURS)

    # Leave stays an operational overlay. Do not create an absence row or punch
    # facts for a leave-only shift; existing facts remain untouched when no events exist.
    if not events.exists() and has_leave:
        return existing_attendance

    # Shift still open and nobody has punched yet: there is nothing to record (and nobody to call absent yet).
    if shift_open and not events.exists():
        if existing_attendance is not None and existing_attendance.status == "absent":
            existing_attendance.delete()  # an absence written by an earlier run, before the shift had finished
            return None
        return existing_attendance

    with transaction.atomic():
        attendance, _ = DailyAttendance.objects.select_for_update().get_or_create(
            employee=employee,
            date=work_date,
            defaults={
                "shift": shift,
                "scheduled_start": scheduled_start,
                "scheduled_end": scheduled_end,
            },
        )
        previous = {
            "clock_in": attendance.actual_clock_in,
            "clock_out": attendance.actual_clock_out,
            "status": attendance.status,
            "late_minutes": attendance.late_minutes,
        }

        attendance.shift = shift
        attendance.scheduled_start = scheduled_start
        attendance.scheduled_end = scheduled_end
        event_count = events.count()

        if shift_open:
            # Punched during the shift: in from their first punch. The clock-out waits for the end of the shift.
            first_punch = events.first().timestamp
            attendance.actual_clock_in = first_punch
            attendance.actual_clock_out = None
            missing_clock_in = missing_clock_out = False
            attendance.late_minutes = max(0, int((first_punch - scheduled_start).total_seconds() // 60))
            attendance.early_departure_minutes = 0
            attendance.worked_minutes = 0
            attendance.overtime_minutes = 0
            attendance.status = "late" if attendance.late_minutes else "present"
        elif event_count == 0:
            attendance.actual_clock_in = None
            attendance.actual_clock_out = None
            attendance.late_minutes = 0
            attendance.early_departure_minutes = 0
            attendance.worked_minutes = 0
            attendance.overtime_minutes = 0
            attendance.status = "absent"
        elif event_count == 1:
            punch = events.first().timestamp
            # A lone punch at/after shift end is treated as a clock-out; otherwise
            # it is a clock-in. This preserves the real event without inventing one.
            if punch >= scheduled_end:
                attendance.actual_clock_in = None
                attendance.actual_clock_out = punch
                missing_clock_in = True
                missing_clock_out = False
            else:
                attendance.actual_clock_in = punch
                attendance.actual_clock_out = None
                missing_clock_in = False
                missing_clock_out = True
            attendance.late_minutes = max(
                0,
                int((punch - scheduled_start).total_seconds() // 60),
            ) if attendance.actual_clock_in else 0
            attendance.early_departure_minutes = 0
            attendance.worked_minutes = 0
            attendance.overtime_minutes = 0
            attendance.status = "incomplete"
        else:
            attendance.actual_clock_in = events.first().timestamp
            attendance.actual_clock_out = events.last().timestamp
            attendance.late_minutes = max(
                0,
                int((attendance.actual_clock_in - scheduled_start).total_seconds() // 60),
            )
            attendance.early_departure_minutes = max(
                0,
                int((scheduled_end - attendance.actual_clock_out).total_seconds() // 60),
            )
            attendance.worked_minutes = max(
                0,
                int((attendance.actual_clock_out - attendance.actual_clock_in).total_seconds() // 60),
            )
            attendance.overtime_minutes = max(
                0,
                int((attendance.actual_clock_out - scheduled_end).total_seconds() // 60),
            )
            attendance.status = "late" if attendance.late_minutes else "present"
            missing_clock_in = False
            missing_clock_out = False

        attendance.save()

        _sync_exception(
            attendance,
            "absence",
            applies=attendance.status == "absent",
            minutes=int((scheduled_end - scheduled_start).total_seconds() // 60),
        )
        _sync_exception(
            attendance,
            "late",
            applies=attendance.late_minutes > 0,
            minutes=attendance.late_minutes,
        )
        _sync_exception(
            attendance,
            "early_departure",
            applies=attendance.early_departure_minutes > 0,
            minutes=attendance.early_departure_minutes,
        )
        _sync_exception(
            attendance,
            "missing_clock_in",
            applies=event_count == 1 and missing_clock_in,
        )
        _sync_exception(
            attendance,
            "missing_clock_out",
            applies=event_count == 1 and missing_clock_out,
        )
        _sync_exception(attendance, "leave_punch_conflict", applies=has_leave and event_count > 0, monetary=False)
        _log_attendance_changes(attendance, previous)

    return attendance


def process_attendance_for_date(work_date, employee=None, *, now=None):
    """Process every employee expected on a date (rostered to work, or on an active shift assignment), or one employee."""
    now = now or timezone.now()
    if employee is not None:
        employees = [employee] if (get_active_shift_assignment(employee, work_date) or get_employee_roster_day(employee, work_date)) else []
    else:
        by_id = {}
        for row in EmployeeRosterDay.objects.filter(date=work_date, status="work", employee__status="active").select_related("employee", "shift"):
            by_id[row.employee_id] = row.employee
        for assignment in (
            ShiftAssignment.objects.filter(start_date__lte=work_date)
            .filter(Q(end_date__isnull=True) | Q(end_date__gte=work_date))
            .select_related("employee", "shift")
            .order_by("employee_id", "-start_date")
        ):
            by_id.setdefault(assignment.employee_id, assignment.employee)
        employees = list(by_id.values())

    leave_ids = set(approved_leave_employee_ids(work_date))
    results = []
    for assigned_employee in employees:
        attendance = process_employee_attendance(assigned_employee, work_date, now=now, leave_ids=leave_ids)
        if attendance is not None:
            results.append(attendance)
    return results

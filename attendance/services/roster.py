from dataclasses import asdict, dataclass, field
from datetime import timedelta

from django.db import transaction

from attendance.models import EmployeeRosterDay, RosterDaySource, RosterDayStatus
from audit.models import AuditSeverity
from audit.services import AuditService


class IncompleteRosterError(ValueError):
    def __init__(self, missing_dates):
        self.missing_dates = missing_dates
        super().__init__(f"Roster is incomplete: {len(missing_dates)} date(s) are not configured.")


class RosterGenerationConflict(ValueError):
    def __init__(self, conflicts):
        self.conflicts = conflicts
        super().__init__(f"Roster generation conflicts with {len(conflicts)} existing date(s).")


@dataclass
class RosterGenerationSummary:
    created: int = 0
    updated: int = 0
    skipped: int = 0
    conflicts: list = field(default_factory=list)

    def as_dict(self):
        return asdict(self)


def get_employee_roster_day(employee, work_date):
    return EmployeeRosterDay.objects.filter(employee=employee, date=work_date).select_related("shift").first()


def roster_completeness(employee, start_date, end_date):
    rows = EmployeeRosterDay.objects.filter(employee=employee, date__range=(start_date, end_date)).only("date", "status")
    configured = {row.date for row in rows}
    expected_dates = []
    current = start_date
    while current <= end_date:
        expected_dates.append(current)
        current += timedelta(days=1)
    missing_dates = [value for value in expected_dates if value not in configured]
    return {
        "complete": not missing_dates,
        "expected_days": sum(1 for row in rows if row.status == RosterDayStatus.WORK),
        "missing_dates": missing_dates,
    }


def expected_attendance_days(employee, start_date, end_date):
    result = roster_completeness(employee, start_date, end_date)
    if not result["complete"]:
        raise IncompleteRosterError(result["missing_dates"])
    return result["expected_days"]


def generate_roster(employee, start_date, end_date, shift, work_days, rest_days, *, actor=None, notes="", working_weekdays=None):
    if start_date > end_date:
        raise ValueError("The roster end date cannot be before its start date.")
    if work_days < 1 or rest_days < 1:
        raise ValueError("Work and rest pattern lengths must both be at least one day.")
    summary = RosterGenerationSummary()
    pattern_length = work_days + rest_days
    selected_weekdays = set(working_weekdays) if working_weekdays is not None else None

    with transaction.atomic():
        existing = {row.date: row for row in EmployeeRosterDay.objects.select_for_update().filter(employee=employee, date__range=(start_date, end_date))}
        current = start_date
        index = 0
        while current <= end_date:
            status = RosterDayStatus.WORK if (
                current.weekday() in selected_weekdays
                if selected_weekdays is not None
                else index % pattern_length < work_days
            ) else RosterDayStatus.REST
            desired_shift = shift if status == RosterDayStatus.WORK else None
            row = existing.get(current)
            if row is None:
                EmployeeRosterDay.objects.create(employee=employee, date=current, status=status, shift=desired_shift, source=RosterDaySource.GENERATED, notes=notes, created_by=actor, updated_by=actor)
                summary.created += 1
            elif row.source in {RosterDaySource.MANUAL, RosterDaySource.OVERRIDE}:
                summary.skipped += 1
                summary.conflicts.append(current.isoformat())
            elif row.status != status or row.shift_id != (desired_shift.id if desired_shift else None) or row.notes != notes:
                row.status = status
                row.shift = desired_shift
                row.notes = notes
                row.updated_by = actor
                row.save()
                summary.updated += 1
            else:
                summary.skipped += 1
            current += timedelta(days=1)
            index += 1
        if summary.created or summary.updated:
            AuditService.log(event_type="attendance.roster_generated", module="attendance", employee=employee, actor=actor, object=employee, severity=AuditSeverity.SUCCESS, title="Employee roster generated", description=f"Generated roster dates for {employee.full_name}.", metadata={"start_date": start_date.isoformat(), "end_date": end_date.isoformat(), "work_days": work_days, "rest_days": rest_days, **summary.as_dict()})
    return summary


def generate_rotation_roster(employee, start_date, end_date, starting_shift, day_shift, night_shift, *, actor=None, notes=""):
    """Generate a Monday-based Day/Night rotation as authoritative roster dates."""
    if start_date > end_date:
        raise ValueError("The roster end date cannot be before its start date.")
    if start_date.weekday() != 0:
        raise ValueError("A weekly rotation must start on a Monday.")

    other_shift = night_shift if starting_shift == day_shift else day_shift
    desired = {}
    current = start_date
    while current <= end_date:
        week_shift = starting_shift if ((current - start_date).days // 7) % 2 == 0 else other_shift
        if current.weekday() < 6:
            desired[current] = (RosterDayStatus.WORK, week_shift)
        elif week_shift == day_shift:
            desired[current] = (RosterDayStatus.WORK, night_shift)
        else:
            desired[current] = (RosterDayStatus.REST, None)
        current += timedelta(days=1)

    with transaction.atomic():
        existing = {
            row.date: row
            for row in EmployeeRosterDay.objects.select_for_update().filter(
                employee=employee, date__range=(start_date, end_date)
            )
        }
        conflicts = []
        for roster_date, (status, desired_shift) in desired.items():
            row = existing.get(roster_date)
            if row is None:
                continue
            desired_shift_id = desired_shift.id if desired_shift else None
            if row.source != RosterDaySource.GENERATED or row.status != status or row.shift_id != desired_shift_id:
                conflicts.append(roster_date.isoformat())
        if conflicts:
            raise RosterGenerationConflict(conflicts)

        summary = RosterGenerationSummary()
        for roster_date, (status, desired_shift) in desired.items():
            row = existing.get(roster_date)
            if row is None:
                EmployeeRosterDay.objects.create(
                    employee=employee,
                    date=roster_date,
                    status=status,
                    shift=desired_shift,
                    source=RosterDaySource.GENERATED,
                    notes=notes,
                    created_by=actor,
                    updated_by=actor,
                )
                summary.created += 1
            elif row.notes != notes:
                row.notes = notes
                row.updated_by = actor
                row.save(update_fields=["notes", "updated_by", "updated_at"])
                summary.updated += 1
            else:
                summary.skipped += 1
        if summary.created or summary.updated:
            AuditService.log(
                event_type="attendance.roster_rotation_generated",
                module="attendance",
                employee=employee,
                actor=actor,
                object=employee,
                severity=AuditSeverity.SUCCESS,
                title="Employee roster rotation generated",
                description=f"Generated Day/Night rotation dates for {employee.full_name}.",
                metadata={
                    "start_date": start_date.isoformat(),
                    "end_date": end_date.isoformat(),
                    "starting_shift": starting_shift.name,
                    **summary.as_dict(),
                },
            )
    return summary

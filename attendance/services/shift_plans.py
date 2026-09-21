"""Keep every employee's work roster in step with their shift plan, automatically.

The roster (EmployeeRosterDay) stays the single source of truth for the rest of the system (attendance, meals,
payroll). A plan only decides what the generated days are; manual changes and overrides are never overwritten.
"""

from dataclasses import dataclass
from datetime import timedelta

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from attendance.models import EmployeeRosterDay, RosterDaySource, RosterDayStatus, ShiftPlan, ShiftPlanAssignment

HORIZON_DAYS = 120  # about 17 weeks ahead
PROTECTED_SOURCES = {RosterDaySource.MANUAL, RosterDaySource.OVERRIDE}


def monday_of(day):
    return day - timedelta(days=day.weekday())


def day_group_for_week(plan, monday):
    """Which group ("A" or "B") is on the Day shift in the week that starts on this Monday."""
    weeks = (monday - plan.anchor_monday).days // 7
    return "A" if weeks % 2 == 0 else "B"


def planned_day(plan, group, day):
    """What a plan says about one date: (status, shift)."""
    if plan.kind == "fixed":
        return (RosterDayStatus.WORK, plan.shift) if day.weekday() in (plan.working_weekdays or []) else (RosterDayStatus.REST, None)
    on_day_this_week = day_group_for_week(plan, monday_of(day)) == group
    if day.weekday() < 6:  # Monday to Saturday: the week's shift
        return RosterDayStatus.WORK, (plan.day_shift if on_day_this_week else plan.night_shift)
    # Sunday: only the night shift works. The people finishing a Day week start their Night week at 19:00;
    # the people finishing a Night week are resting until Monday morning.
    return (RosterDayStatus.WORK, plan.night_shift) if on_day_this_week else (RosterDayStatus.REST, None)


@dataclass
class SyncSummary:
    created: int = 0
    updated: int = 0
    kept: int = 0  # already right, or a manual/override day that was left alone

    def add(self, other):
        self.created += other.created
        self.updated += other.updated
        self.kept += other.kept

    def as_dict(self):
        return {"created": self.created, "updated": self.updated, "kept": self.kept}


def sync_rosters(assignments, start, end):
    """Write the planned days for these assignments between start and end (inclusive)."""
    summary = SyncSummary()
    assignments = list(assignments)
    if not assignments or start > end:
        return summary
    employee_ids = {a.employee_id for a in assignments}
    existing = {(r.employee_id, r.date): r for r in EmployeeRosterDay.objects.filter(employee_id__in=employee_ids, date__range=(start, end))}
    to_create, to_update = [], []
    for assignment in assignments:
        first = max(start, assignment.start_date)
        last = min(end, assignment.end_date) if assignment.end_date else end
        day = first
        while day <= last:
            status, shift = planned_day(assignment.plan, assignment.group, day)
            row = existing.get((assignment.employee_id, day))
            note = f"Plan: {assignment.plan.name}"
            if row is None:
                to_create.append(EmployeeRosterDay(employee_id=assignment.employee_id, date=day, status=status, shift=shift, source=RosterDaySource.GENERATED, notes=note))
            elif row.source in PROTECTED_SOURCES:
                summary.kept += 1
            elif row.status != status or row.shift_id != (shift.pk if shift else None) or row.notes != note:
                row.status, row.shift, row.notes = status, shift, note
                to_update.append(row)
            else:
                summary.kept += 1
            day += timedelta(days=1)
    with transaction.atomic():
        EmployeeRosterDay.objects.bulk_create(to_create, batch_size=2000)
        EmployeeRosterDay.objects.bulk_update(to_update, ["status", "shift", "notes", "updated_at"], batch_size=2000)
    summary.created, summary.updated = len(to_create), len(to_update)
    return summary


def assign_plan(employees, plan, *, group="", start_date=None, actor=""):
    """Put these people on a plan from start_date. A person's earlier assignment is closed the day before, and the
    generated roster from start_date on is rewritten to follow the new plan (manual days are kept)."""
    if plan.kind == "rotation" and group not in ("A", "B"):
        raise ValueError("Choose Group A or Group B for a rotation plan.")
    if plan.kind == "rotation" and not plan.anchor_monday:
        raise ValueError("This rotation has no start week set.")
    if plan.kind == "fixed" and (plan.shift is None or not plan.working_weekdays):
        raise ValueError("This plan has no shift or working days set.")
    start_date = start_date or timezone.localdate()
    created = []
    with transaction.atomic():
        for employee in employees:
            for old in ShiftPlanAssignment.objects.filter(employee=employee).filter(Q(end_date__isnull=True) | Q(end_date__gte=start_date)):
                if old.start_date >= start_date:
                    old.delete()
                else:
                    old.end_date = start_date - timedelta(days=1)
                    old.save(update_fields=["end_date"])
            created.append(ShiftPlanAssignment.objects.create(employee=employee, plan=plan, group=group if plan.kind == "rotation" else "", start_date=start_date, assigned_by=str(actor)))
        horizon_end = timezone.localdate() + timedelta(days=HORIZON_DAYS)
        # Days planned earlier beyond the horizon (from an old plan or an older generation) would be stale.
        EmployeeRosterDay.objects.filter(employee__in=[a.employee_id for a in created], date__gt=horizon_end, source=RosterDaySource.GENERATED).delete()
        summary = sync_rosters(created, start_date, horizon_end)
    return created, summary


def extend_rosters(today=None, horizon_days=HORIZON_DAYS):
    """The daily job: make sure everyone on a plan has their roster written for the next ~17 weeks, so the weekly
    Day/Night swap needs nobody's attention. Safe to run as often as you like."""
    today = today or timezone.localdate()
    end = today + timedelta(days=horizon_days)
    assignments = (
        ShiftPlanAssignment.objects.select_related("plan", "plan__shift", "plan__day_shift", "plan__night_shift")
        .filter(employee__status="active", plan__active=True, start_date__lte=end)
        .filter(Q(end_date__isnull=True) | Q(end_date__gte=today))
    )
    return sync_rosters(assignments, today, end)


def flip_rotation_week(plan):
    """Swap which group is on Day: moves the reference week on by one. For when the plan started the wrong way round."""
    if plan.kind != "rotation":
        raise ValueError("Only a rotation plan has groups to swap.")
    plan.anchor_monday = plan.anchor_monday + timedelta(days=7)
    plan.save(update_fields=["anchor_monday"])
    return extend_rosters()

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


def label_week_monday(on_date):
    """The Monday of the week a date counts as for naming groups. A Sunday belongs to the week about to start: that
    is when the night team's week begins and the morning team rests before its first Monday."""
    return monday_of(on_date + timedelta(days=1)) if on_date.weekday() == 6 else monday_of(on_date)


def group_label(plan, stored_group, on_date=None):
    """The group letter people see: A is the morning (first-shift) team THIS week, B the evening (second-shift) team.
    A person's letter changes by itself every week as their shift swaps. The stored group is only which of the two
    teams they are in; this turns it into what the letter means on that date. "" when the plan has no groups."""
    if plan.kind not in ("rotation", "alternating") or not stored_group or not plan.anchor_monday:
        return ""
    on_date = on_date or timezone.localdate()
    return "A" if day_group_for_week(plan, label_week_monday(on_date)) == stored_group else "B"


def stored_group_for_label(plan, label, on_date):
    """The reverse of group_label: which stored team is the morning team (label A) or evening team (label B) in the
    week `on_date` falls in - so that "A from Monday" starts someone on mornings that week."""
    morning_team = day_group_for_week(plan, label_week_monday(on_date))
    evening_team = "B" if morning_team == "A" else "A"
    return morning_team if label == "A" else evening_team


def planned_day(plan, group, day, day_off=None):
    """What a plan says about one date: (status, shift). `day_off` is the person's own weekly day off (0 = Monday),
    which is a rest day whatever the plan says."""
    if day_off is not None and day.weekday() == day_off:
        return RosterDayStatus.REST, None
    if plan.kind == "fixed":
        if day.weekday() not in (plan.working_weekdays or []):
            return RosterDayStatus.REST, None
        if day.weekday() == 5 and plan.saturday_shift_id:
            return RosterDayStatus.WORK, plan.saturday_shift
        return RosterDayStatus.WORK, plan.shift
    if plan.kind == "alternating":
        if day.weekday() not in (plan.working_weekdays or []):
            return RosterDayStatus.REST, None
        first_this_week = day_group_for_week(plan, monday_of(day)) == group
        return RosterDayStatus.WORK, (plan.day_shift if first_this_week else plan.night_shift)
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
    to_create, replaced = [], []  # replaced: (id of the row to rewrite, its new version)
    for assignment in assignments:
        first = max(start, assignment.start_date)
        last = min(end, assignment.end_date) if assignment.end_date else end
        day = first
        while day <= last:
            status, shift = planned_day(assignment.plan, assignment.group, day, assignment.day_off)
            row = existing.get((assignment.employee_id, day))
            note = f"Plan: {assignment.plan.name}"
            if row is None:
                to_create.append(EmployeeRosterDay(employee_id=assignment.employee_id, date=day, status=status, shift=shift, source=RosterDaySource.GENERATED, notes=note))
            elif row.source in PROTECTED_SOURCES:
                summary.kept += 1
            elif row.status != status or row.shift_id != (shift.pk if shift else None) or row.notes != note:
                replaced.append((row.pk, EmployeeRosterDay(employee_id=assignment.employee_id, date=day, status=status, shift=shift, source=RosterDaySource.GENERATED, notes=note)))
            else:
                summary.kept += 1
            day += timedelta(days=1)
    # Rewriting a row is a delete and insert of the same day, done in bulk: nothing refers to a roster row, and
    # bulk_update on tens of thousands of rows (a rotation swap rewrites most of them) takes far too long here.
    with transaction.atomic():
        ids = [row_id for row_id, _ in replaced]
        for chunk_start in range(0, len(ids), 1000):
            EmployeeRosterDay.objects.filter(pk__in=ids[chunk_start:chunk_start + 1000]).delete()
        EmployeeRosterDay.objects.bulk_create(to_create + [fresh for _, fresh in replaced], batch_size=2000)
    summary.created, summary.updated = len(to_create), len(replaced)
    return summary


def split_groups(people):
    """Divide people between Group A and Group B, evenly within each department (by staff number), so every
    department has both a day team and a night team. Odd leftovers alternate between the groups."""
    a, b = [], []
    shift = 0
    departments = {}
    for person in sorted(people, key=lambda e: e.employee_id):
        departments.setdefault(person.department_id, []).append(person)
    for members in departments.values():
        for index, person in enumerate(members):
            (a if (index + shift) % 2 == 0 else b).append(person)
        shift += len(members) % 2
    return a, b


KEEP = object()  # assign_plan: carry the person's existing day off over to the new plan


def assign_plan(employees, plan, *, group="", start_date=None, actor="", day_off=KEEP, by_label=False):
    """Put these people on a plan from start_date. A person's earlier assignment is closed the day before, and the
    generated roster from start_date on is rewritten to follow the new plan (manual days are kept).
    `group` is the stored team, unless by_label=True: then "A" means the morning team and "B" the evening team in the
    week start_date falls in (this is how people choose it on the screens)."""
    if plan.kind in ("rotation", "alternating") and group not in ("A", "B"):
        raise ValueError("Choose Group A or Group B for a rotation plan.")
    if plan.kind in ("rotation", "alternating") and not plan.anchor_monday:
        raise ValueError("This rotation has no start week set.")
    if plan.kind == "alternating" and (plan.day_shift is None or plan.night_shift is None or not plan.working_weekdays):
        raise ValueError("This plan needs its two shifts and its working days set.")
    if plan.kind == "fixed" and (plan.shift is None or not plan.working_weekdays):
        raise ValueError("This plan has no shift or working days set.")
    if day_off is not KEEP and day_off is not None and not (isinstance(day_off, int) and 0 <= day_off <= 6):
        raise ValueError("The day off must be a weekday from Monday (0) to Sunday (6).")
    start_date = start_date or timezone.localdate()
    if by_label and plan.kind in ("rotation", "alternating"):
        group = stored_group_for_label(plan, group, start_date)
    created = []
    with transaction.atomic():
        for employee in employees:
            own_day_off = None if day_off is KEEP else day_off
            for old in ShiftPlanAssignment.objects.filter(employee=employee).filter(Q(end_date__isnull=True) | Q(end_date__gte=start_date)).order_by("start_date"):
                if day_off is KEEP and old.day_off is not None:
                    own_day_off = old.day_off  # the latest earlier assignment's day off wins
                if old.start_date >= start_date:
                    old.delete()
                else:
                    old.end_date = start_date - timedelta(days=1)
                    old.save(update_fields=["end_date"])
            created.append(ShiftPlanAssignment.objects.create(employee=employee, plan=plan, group=group if plan.kind in ("rotation", "alternating") else "", day_off=own_day_off, start_date=start_date, assigned_by=str(actor)))
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
        ShiftPlanAssignment.objects.select_related("plan", "plan__shift", "plan__saturday_shift", "plan__day_shift", "plan__night_shift")
        .filter(employee__status="active", plan__active=True, start_date__lte=end)
        .filter(Q(end_date__isnull=True) | Q(end_date__gte=today))
    )
    return sync_rosters(assignments, today, end)


def next_shift_week_start(today=None):
    """The date a new shift week begins: the Sunday (the night shift that starts a night week begins on Sunday)
    that has not started yet. Swapping groups from here lets this week finish as it was worked, so nobody is moved
    from a night shift straight onto a day shift."""
    today = today or timezone.localdate()
    sunday = monday_of(today) + timedelta(days=6)
    return sunday if sunday >= today else sunday + timedelta(days=7)


def flip_rotation_week(plan, from_date=None):
    """Swap which group is on Day: moves the reference week on by one, so from `from_date` Group A does what Group B
    was going to do and the other way round. Default: the start of the next shift week. Days before it are left as
    they were worked. The weekly alternation then carries on by itself. For when the plan started the wrong way round."""
    if plan.kind not in ("rotation", "alternating"):
        raise ValueError("Only a rotation plan has groups to swap.")
    start = from_date or next_shift_week_start()
    plan.anchor_monday = plan.anchor_monday + timedelta(days=7)
    plan.save(update_fields=["anchor_monday"])
    return extend_rosters(today=start)


def clear_false_absences_on_rest_days(employees, start, end, *, actor=""):
    """A day that has become a rest day cannot be an absence. The attendance job skips rest days without touching
    what is already there, so an absence written (with its pending exception) before the day off was set would stay
    and could be deducted. Remove only absences the system made - nobody clocked in - and write each to the audit
    trail. Anyone who did come in keeps their attendance."""
    from audit.models import AuditSeverity
    from audit.services import AuditService
    from attendance.models import DailyAttendance

    removed = []
    rest_days = EmployeeRosterDay.objects.filter(employee__in=employees, date__range=(start, end), status=RosterDayStatus.REST)
    for row in rest_days.select_related("employee"):
        attendance = DailyAttendance.objects.filter(employee=row.employee, date=row.date, status="absent", actual_clock_in__isnull=True).first()
        if attendance is None:
            continue
        AuditService.log(
            event_type="attendance.absence_cleared_by_day_off",
            module="attendance",
            employee=row.employee,
            actor=None,
            severity=AuditSeverity.WARNING,
            title="Absence removed: it was the person's day off",
            description=f"{row.employee.full_name} was recorded absent on {row.date}, which is their weekly day off. The absence (and its exception) was removed.",
            metadata={"date": row.date.isoformat(), "set_by": str(actor)},
        )
        attendance.delete()
        removed.append((row.employee.employee_id, row.date))
    return removed


def set_day_off(employees, day_off, effective_from, *, actor=""):
    """Give these people a weekly day off (or take it away with day_off=None) from a date, keeping their plan.

    Their earlier history is left as it was: an assignment that started before the date is closed the day before and
    carried on from the date with the new day off. The roster is rewritten from the date on (manual changes are kept)
    and any false absence the change leaves behind is cleared."""
    if day_off is not None and not (isinstance(day_off, int) and 0 <= day_off <= 6):
        raise ValueError("The day off must be a weekday from Monday (0) to Sunday (6).")
    people = list(employees)
    horizon_end = timezone.localdate() + timedelta(days=HORIZON_DAYS)
    summary = {"people": 0, "no_plan": [], "roster": SyncSummary(), "absences_cleared": []}
    with transaction.atomic():
        touched = []
        for employee in people:
            overlapping = list(ShiftPlanAssignment.objects.filter(employee=employee).filter(Q(end_date__isnull=True) | Q(end_date__gte=effective_from)).order_by("start_date"))
            if not overlapping:
                summary["no_plan"].append(employee.employee_id)
                continue
            for assignment in overlapping:
                if assignment.start_date >= effective_from:
                    assignment.day_off = day_off
                    assignment.save(update_fields=["day_off"])
                    touched.append(assignment)
                else:
                    carried = ShiftPlanAssignment.objects.create(
                        employee=employee, plan=assignment.plan, group=assignment.group, day_off=day_off,
                        start_date=effective_from, end_date=assignment.end_date, assigned_by=str(actor),
                    )
                    assignment.end_date = effective_from - timedelta(days=1)
                    assignment.save(update_fields=["end_date"])
                    touched.append(carried)
            summary["people"] += 1
        summary["roster"] = sync_rosters(touched, effective_from, horizon_end)
        summary["absences_cleared"] = clear_false_absences_on_rest_days(people, effective_from, min(timezone.localdate(), horizon_end), actor=actor)
    return summary

"""Correct a rotation that ran the wrong way round, and redo the attendance it distorted.

The roster of the rotation plan is swapped from a date (Group A does what Group B was going to do, and the other way
round) and rewritten from there. Everyone's attendance from that date is then recalculated from the punches against
the corrected shifts - a punch the old roster read as a late arrival may really have been someone clocking out.

Decisions that were taken on the old, wrong picture are removed so the corrected cases come up fresh: only those
whose reviewer name starts with --discard-decisions-by (default: the September test review) - never anyone else's.
Preview with --dry-run (it reports before and after, then saves nothing). Undo by running it again with the same date."""

from datetime import date, timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.utils import timezone

from attendance.models import AttendanceException, DailyAttendance, ShiftPlan, ShiftPlanAssignment
from attendance.services.processing import process_employee_attendance
from attendance.services.shift_plans import HORIZON_DAYS, clear_false_absences_on_rest_days, sync_rosters


def snapshot(employee_ids, start, end):
    rows = DailyAttendance.objects.filter(employee_id__in=employee_ids, date__range=(start, end))
    exceptions = AttendanceException.objects.filter(attendance__employee_id__in=employee_ids, attendance__date__range=(start, end))
    return {
        "attendance": dict(rows.values_list("status").annotate(n=Count("id"))),
        "late minutes": rows.aggregate(total=Sum("late_minutes"))["total"] or 0,
        "exceptions": dict(exceptions.values_list("exception_type").annotate(n=Count("id"))),
        "exceptions pending": exceptions.filter(status="pending").count(),
    }


class Command(BaseCommand):
    help = "Swap a rotation plan's groups from a date and recalculate attendance from the punches."

    def add_arguments(self, parser):
        parser.add_argument("--plan", help="Plan name. Default: the only rotation plan.")
        parser.add_argument("--from", dest="start", required=True, help="First day the swap applies (YYYY-MM-DD), e.g. the Sunday a night week began.")
        parser.add_argument("--discard-decisions-by", default="September test review", help="Remove decisions whose reviewer starts with this, on the recalculated days.")
        parser.add_argument("--dry-run", action="store_true", help="Report before and after, save nothing.")

    @transaction.atomic
    def handle(self, *args, **options):
        plans = ShiftPlan.objects.filter(kind="rotation", active=True)
        if options["plan"]:
            plans = plans.filter(name=options["plan"])
        if plans.count() != 1:
            raise CommandError("Name the plan with --plan (there must be exactly one match).")
        plan = plans.get()
        start, today = date.fromisoformat(options["start"]), timezone.localdate()
        if start > today:
            raise CommandError("The date is in the future - use swap_rotation_groups for a swap that has not started yet.")

        def current_assignments():
            # loaded fresh each time: the plan objects inside carry the reference week the roster is built from
            return list(
                ShiftPlanAssignment.objects.filter(plan=plan, employee__status="active").filter(Q(end_date__isnull=True) | Q(end_date__gte=start)).select_related("plan", "plan__day_shift", "plan__night_shift", "employee")
            )

        employees = {assignment.employee_id: assignment.employee for assignment in current_assignments()}
        ids = list(employees)
        self.stdout.write(f"{plan.name}: {len(ids)} people. Swapping from {start}; recalculating {start} to {today}.")
        before = snapshot(ids, start, today)
        self.stdout.write(f"BEFORE: {before}")

        plan.anchor_monday = plan.anchor_monday + timedelta(days=7)
        plan.save(update_fields=["anchor_monday"])
        roster = sync_rosters(current_assignments(), start, today + timedelta(days=HORIZON_DAYS))
        self.stdout.write(f"Roster rewritten: {roster.as_dict()}")

        prefix = options["discard_decisions_by"]
        discarded = AttendanceException.objects.filter(
            attendance__employee_id__in=ids, attendance__date__range=(start, today), reviewed_by__startswith=prefix
        ).exclude(status="pending")
        discarded_count = discarded.count()
        discarded.delete()
        kept_others = AttendanceException.objects.filter(attendance__employee_id__in=ids, attendance__date__range=(start, today)).exclude(status="pending").count()
        self.stdout.write(f"Decisions taken on the old roster removed: {discarded_count}. Other people's decisions left alone: {kept_others}.")

        cleared = clear_false_absences_on_rest_days(list(employees.values()), start, today, actor="realign_rotation")
        day = start
        processed = 0
        while day <= today:
            for employee in employees.values():
                process_employee_attendance(employee, day)
                processed += 1
            day += timedelta(days=1)
        self.stdout.write(f"Attendance recalculated for {processed} person-days; false absences on new rest days removed: {len(cleared)}.")
        after = snapshot(ids, start, today)
        self.stdout.write(f"AFTER:  {after}")
        if options["dry_run"]:
            transaction.set_rollback(True)
            self.stdout.write(self.style.WARNING("DRY RUN - nothing was saved."))

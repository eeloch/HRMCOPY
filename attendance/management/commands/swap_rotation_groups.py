"""Swap which group is on Day in a rotation plan (Group A does what Group B was going to do, and the other way round).

From the start of the next shift week by default (the coming Sunday night), so the week in progress finishes as it
was worked. The weekly alternation then carries on by itself, and the daily job keeps the roster written ahead.
Preview with --dry-run; the swap can be undone by running it again."""

from datetime import date, timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Count
from django.utils import timezone

from attendance.models import EmployeeRosterDay, ShiftPlan, ShiftPlanAssignment
from attendance.services.shift_plans import day_group_for_week, flip_rotation_week, monday_of, next_shift_week_start


class Command(BaseCommand):
    help = "Swap the Day/Night groups of a rotation plan from the next shift week (or --from DATE)."

    def add_arguments(self, parser):
        parser.add_argument("--plan", help="Plan name. Default: the only rotation plan.")
        parser.add_argument("--from", dest="start", help="First day the swap applies (YYYY-MM-DD). Default: the coming Sunday.")
        parser.add_argument("--dry-run", action="store_true", help="Show what would change without saving.")

    @transaction.atomic
    def handle(self, *args, **options):
        plans = ShiftPlan.objects.filter(kind="rotation", active=True)
        if options["plan"]:
            plans = plans.filter(name=options["plan"])
        if plans.count() != 1:
            raise CommandError("Name the plan with --plan (there must be exactly one match).")
        plan = plans.get()
        start = date.fromisoformat(options["start"]) if options["start"] else next_shift_week_start()
        today = timezone.localdate()
        members = ShiftPlanAssignment.objects.filter(plan=plan, end_date__isnull=True, employee__status="active")
        counts = {group: members.filter(group=group).count() for group in ("A", "B")}
        week = monday_of(today)
        self.stdout.write(f"{plan.name}: Group A {counts['A']} people, Group B {counts['B']}. Reference Monday {plan.anchor_monday}.")
        self.stdout.write(f"This week (from {week}) Group {day_group_for_week(plan, week)} is on Day. Swapping from {start}.")
        summary = flip_rotation_week(plan, from_date=start)
        for offset in (0, 7):
            monday = week + timedelta(days=offset)
            self.stdout.write(f"  after the swap, week of {monday}: Group {day_group_for_week(plan, monday)} on Day")
        on_day = {}
        for day_value in (start, start + timedelta(days=1)):
            rows = EmployeeRosterDay.objects.filter(date=day_value, employee__shift_plan_assignments__plan=plan, employee__shift_plan_assignments__end_date__isnull=True)
            on_day[day_value] = dict(rows.values_list("shift__name").annotate(n=Count("employee", distinct=True)))
        for day_value, shifts in on_day.items():
            self.stdout.write(f"  roster on {day_value:%a %d %b}: {shifts}")
        self.stdout.write(f"Roster days rewritten: {summary.updated} changed, {summary.created} added.")
        if options["dry_run"]:
            transaction.set_rollback(True)
            self.stdout.write(self.style.WARNING("DRY RUN - nothing was saved."))

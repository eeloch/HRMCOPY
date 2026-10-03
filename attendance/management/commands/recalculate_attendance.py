"""Recalculate attendance from the punches for a group of people over a past date range - after their roster was
corrected for days that had already been worked (a roster upload with a past start date rewrites the roster only).

Who: people whose shift plan assignment was created since --assigned-since (the people a roster upload changed), or
--employees. What: false absences on days that are now rest days are removed, decisions the named test reviewer took
on those days (made on the old, wrong roster) are discarded, and every person-day is reprocessed from the punches.
Nobody else's decisions are touched. --dry-run reports before and after and saves nothing."""

from datetime import date, datetime, timedelta, timezone as dt_timezone

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from attendance.management.commands.realign_rotation import snapshot
from attendance.models import AttendanceException, ShiftPlanAssignment
from attendance.services.processing import process_employee_attendance
from attendance.services.shift_plans import clear_false_absences_on_rest_days
from employees.models import Employee


class Command(BaseCommand):
    help = "Reprocess attendance from the punches for people whose roster was corrected for past days."

    def add_arguments(self, parser):
        parser.add_argument("--from", dest="start", required=True, help="First day to recalculate (YYYY-MM-DD).")
        parser.add_argument("--assigned-since", help="UTC time (YYYY-MM-DDTHH:MM): everyone given a new plan assignment since then.")
        parser.add_argument("--employees", help="Comma-separated staff numbers instead of --assigned-since.")
        parser.add_argument("--discard-decisions-by", default="September test review", help="Discard decisions whose reviewer starts with this, on the recalculated days.")
        parser.add_argument("--dry-run", action="store_true", help="Report before and after, save nothing.")

    @transaction.atomic
    def handle(self, *args, **options):
        start, today = date.fromisoformat(options["start"]), timezone.localdate()
        if start > today:
            raise CommandError("The start date is in the future.")
        if options["employees"]:
            employees = list(Employee.objects.filter(employee_id__in=[n.strip() for n in options["employees"].split(",") if n.strip()], status="active"))
        elif options["assigned_since"]:
            since = datetime.fromisoformat(options["assigned_since"]).replace(tzinfo=dt_timezone.utc)
            ids = ShiftPlanAssignment.objects.filter(created_at__gte=since).values_list("employee_id", flat=True).distinct()
            employees = list(Employee.objects.filter(pk__in=list(ids), status="active"))
        else:
            raise CommandError("Say who: --assigned-since or --employees.")
        ids = [employee.pk for employee in employees]
        self.stdout.write(f"{len(employees)} people; recalculating {start} to {today}.")
        before = snapshot(ids, start, today)
        self.stdout.write(f"BEFORE: {before}")

        cleared = clear_false_absences_on_rest_days(employees, start, today, actor="recalculate_attendance")
        discarded = AttendanceException.objects.filter(
            attendance__employee_id__in=ids, attendance__date__range=(start, today), reviewed_by__startswith=options["discard_decisions_by"]
        ).exclude(status="pending")
        discarded_count = discarded.count()
        discarded.delete()
        others = AttendanceException.objects.filter(attendance__employee_id__in=ids, attendance__date__range=(start, today)).exclude(status="pending").count()
        self.stdout.write(f"False absences on rest days removed: {len(cleared)}. Test decisions on the old roster discarded: {discarded_count}. Other people's decisions left alone: {others}.")

        processed, day = 0, start
        while day <= today:
            for employee in employees:
                process_employee_attendance(employee, day)
                processed += 1
            day += timedelta(days=1)
        self.stdout.write(f"Reprocessed {processed} person-days.")
        self.stdout.write(f"AFTER:  {snapshot(ids, start, today)}")
        if options["dry_run"]:
            transaction.set_rollback(True)
            self.stdout.write(self.style.WARNING("DRY RUN - nothing was saved."))

"""Give the staff with a fixed weekly day off (list of 2026-10-02) that day off, from a start date.

Each person keeps their shift plan; only their own day off is added on top. Their roster is rewritten from the start
date on (manual changes are kept), and an absence recorded on what is now their day off is cleared. Safe to run again.
Run with --dry-run first to see who is affected."""

from datetime import date

from django.core.management.base import BaseCommand
from django.db import transaction

from attendance.models import DailyAttendance
from attendance.services.shift_plans import set_day_off
from employees.models import Employee

DAYS = {"Monday": 0, "Tuesday": 1, "Wednesday": 2, "Thursday": 3, "Friday": 4, "Saturday": 5, "Sunday": 6}
# staff number -> day off
OFF_DAYS = {
    "000986": "Thursday", "000753": "Tuesday", "000982": "Friday", "000817": "Thursday", "001142": "Wednesday", "000892": "Thursday",
    "000684": "Thursday", "000116": "Wednesday", "001211": "Tuesday", "000925": "Thursday", "000602": "Saturday",
}


class Command(BaseCommand):
    help = "Apply the weekly days off from a start date (default 2026-10-01)."

    def add_arguments(self, parser):
        parser.add_argument("--from", dest="start", default="2026-10-01", help="First day the day off applies (YYYY-MM-DD).")
        parser.add_argument("--dry-run", action="store_true", help="Show what would change without saving.")

    @transaction.atomic
    def handle(self, *args, **options):
        start = date.fromisoformat(options["start"])
        for staff_number, day_name in OFF_DAYS.items():
            employee = Employee.objects.filter(employee_id=staff_number).first()
            if employee is None:
                self.stdout.write(self.style.WARNING(f"{staff_number}: no such employee - skipped."))
                continue
            result = set_day_off([employee], DAYS[day_name], start, actor="apply_weekly_days_off")
            if result["no_plan"]:
                self.stdout.write(self.style.WARNING(f"{staff_number} {employee.full_name}: not on a shift plan - give them a plan first. Skipped."))
                continue
            cleared = ", ".join(f"{day:%a %d %b}" for _, day in result["absences_cleared"])
            came_in = [
                row.date for row in DailyAttendance.objects.filter(employee=employee, date__gte=start, actual_clock_in__isnull=False)
                if employee.roster_days.filter(date=row.date, status="rest").exists()
            ]
            self.stdout.write(
                f"{staff_number} {employee.full_name}: {day_name} off from {start} - roster {result['roster'].as_dict()}"
                + (f"; absence cleared: {cleared}" if cleared else "")
                + (f"; CAME IN on their day off: {', '.join(f'{day:%a %d %b}' for day in came_in)} (attendance kept)" if came_in else "")
            )
        if options["dry_run"]:
            transaction.set_rollback(True)
            self.stdout.write(self.style.WARNING("DRY RUN - nothing was saved."))

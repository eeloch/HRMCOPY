from datetime import date

from django.core.management.base import BaseCommand, CommandError

from attendance.services.processing import process_attendance_for_date
from employees.models import Employee


class Command(BaseCommand):
    help = "Process attendance events for one work date."

    def add_arguments(self, parser):
        parser.add_argument("--date", required=True, help="Work date in YYYY-MM-DD format.")
        parser.add_argument("--employee", type=int, help="Optional employee primary key.")

    def handle(self, *args, **options):
        try:
            work_date = date.fromisoformat(options["date"])
        except ValueError as error:
            raise CommandError("--date must use YYYY-MM-DD format.") from error

        employee = None
        if options["employee"]:
            try:
                employee = Employee.objects.get(pk=options["employee"])
            except Employee.DoesNotExist as error:
                raise CommandError("Employee not found.") from error

        records = process_attendance_for_date(work_date, employee=employee)
        self.stdout.write(self.style.SUCCESS(f"Processed {len(records)} attendance record(s)."))

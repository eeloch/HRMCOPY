"""The "No Attendance Required" shift and its two plans, for people who need not come in (management) or who work out
and about (drivers): nobody has to punch, every working day is recorded as present automatically, so lateness, missing
punches and absences never arise, and their meal tickets are unaffected - unless someone specifically marks a day as
not worked. Nobody is assigned here. Safe to run again."""

from django.core.management.base import BaseCommand
from django.db import transaction

from attendance.models import Shift, ShiftPlan

PLANS = (
    ("No Attendance Required (Mon-Fri)", [0, 1, 2, 3, 4], "Nobody has to punch; present every Monday to Friday unless someone marks a day as not worked."),
    ("No Attendance Required (Mon-Sat)", [0, 1, 2, 3, 4, 5], "Nobody has to punch; present every Monday to Saturday unless someone marks a day as not worked."),
)


class Command(BaseCommand):
    help = "Create the No Attendance Required shift and plans (idempotent)."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Show what would happen without saving.")

    @transaction.atomic
    def handle(self, *args, **options):
        shift, made = Shift.objects.get_or_create(name="No Attendance Required", defaults={"start_time": "08:00", "end_time": "17:00", "is_overnight": False, "attendance_not_required": True})
        if not shift.attendance_not_required:
            shift.attendance_not_required = True
            shift.save(update_fields=["attendance_not_required"])
        self.stdout.write(f"{'Created' if made else 'Already there'}: shift {shift.name}")
        for name, weekdays, description in PLANS:
            plan, made = ShiftPlan.objects.get_or_create(name=name, defaults={"kind": "fixed", "shift": shift, "working_weekdays": weekdays, "description": description})
            self.stdout.write(f"{'Created' if made else 'Already there'}: plan {plan.name}")
        if options["dry_run"]:
            transaction.set_rollback(True)
            self.stdout.write(self.style.WARNING("DRY RUN - nothing was saved."))

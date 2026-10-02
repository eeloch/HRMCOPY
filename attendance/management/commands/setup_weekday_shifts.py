"""The Monday-Friday 7AM-7PM shifts from the shift roster (2026-10-02).

Shift 1: the existing plan "Admin Shift (Mon-Friday)" is amended to 7:00 AM - 7:00 PM, Monday to Friday.
Shift 2: a new plan, Monday to Friday 7:00 AM - 7:00 PM plus a half Saturday, 7:00 AM - 3:00 PM.
Nobody is assigned here. Safe to run again."""

from django.core.management.base import BaseCommand
from django.db import transaction

from attendance.models import Shift, ShiftPlan, ShiftPlanAssignment

ADMIN_PLAN = "Admin Shift (Mon-Friday)"
STANDARD = ("Standard Day Shift (7AM-7PM)", "07:00", "19:00")
HALF_SATURDAY = ("Half Saturday Shift (7AM-3PM)", "07:00", "15:00")
NEW_PLAN = "Mon-Fri 7AM-7PM + Half Saturday (7AM-3PM)"


class Command(BaseCommand):
    help = "Amend the Admin Shift (Mon-Friday) plan to 7AM-7PM and create the Mon-Fri + half Saturday plan (idempotent)."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Show what would happen without saving.")

    @transaction.atomic
    def handle(self, *args, **options):
        plan = ShiftPlan.objects.filter(name=ADMIN_PLAN).select_related("shift").first()
        if plan is None or plan.shift is None:
            self.stdout.write(self.style.WARNING(f'No plan named "{ADMIN_PLAN}" with a shift - nothing amended.'))
        else:
            shift = plan.shift
            if (shift.start_time.strftime("%H:%M"), shift.end_time.strftime("%H:%M")) == ("07:00", "19:00"):
                self.stdout.write(f"Already 7AM-7PM: {shift.name}")
            else:
                before = f"{shift.start_time:%H:%M}-{shift.end_time:%H:%M}"
                shift.start_time, shift.end_time = "07:00", "19:00"
                shift.save(update_fields=["start_time", "end_time"])
                people = list(ShiftPlanAssignment.objects.filter(plan=plan, end_date__isnull=True).values_list("employee__employee_id", "employee__first_name", "employee__last_name"))
                self.stdout.write(f"Amended {shift.name}: {before} -> 07:00-19:00 (Monday to Friday). Affects {len(people)} people now on it: " + ", ".join(f"{number} {first} {last}" for number, first, last in people))
            if "7AM-7PM" not in plan.description:
                plan.description = "Administrative staff, 7AM-7PM, Monday to Friday."
                plan.save(update_fields=["description"])

        shifts = {}
        for name, begins, ends in (STANDARD, HALF_SATURDAY):
            shifts[name], made = Shift.objects.get_or_create(name=name, defaults={"start_time": begins, "end_time": ends, "is_overnight": False})
            self.stdout.write(f"{'Created' if made else 'Already there'}: shift {name}")
        new_plan, made = ShiftPlan.objects.get_or_create(
            name=NEW_PLAN,
            defaults=dict(kind="fixed", shift=shifts[STANDARD[0]], saturday_shift=shifts[HALF_SATURDAY[0]], working_weekdays=[0, 1, 2, 3, 4, 5],
                          description="Monday to Friday 7AM-7PM; Saturday a half day, 7AM-3PM."),
        )
        self.stdout.write(f"{'Created' if made else 'Already there'}: plan {new_plan.name}")
        if options["dry_run"]:
            transaction.set_rollback(True)
            self.stdout.write(self.style.WARNING("DRY RUN - nothing was saved."))

"""The two security plans (2026-10-12), both working all seven days:

1. "Security Shift (Mon-Sun 7AM-7PM) - Rotation": Day 7AM-7PM and Night 7PM-7AM, swapping every week, Monday to Sunday.
   Group A is on the first (Day) shift in a week when Group B is on the second (Night), and they swap the week after.
2. "Permanent Security Night Shift (Mon-Sun 7PM-7AM)": the Night shift every night of the week.
Nobody is assigned here. Safe to run again."""

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from attendance.models import Shift, ShiftPlan

SEVEN_DAYS = [0, 1, 2, 3, 4, 5, 6]
ROTATING = "Rotating Day / Night (weekly)"
ROTATION_PLAN = "Security Shift (Mon-Sun 7AM-7PM) - Rotation"
ROTATION_DESCRIPTION = "Weeks run Sunday to Saturday. If A is on Day (7am-7pm), the Day shift starts Sunday 7am and the Night shift (B) starts Sunday 7pm to 7am. The groups swap every week."
NIGHT_PLAN = "Permanent Security Night Shift (Mon-Sun 7PM-7AM)"


class Command(BaseCommand):
    help = "Create the rotating Security shift plan and the permanent Security night shift plan (idempotent)."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Show what would happen without saving.")

    @transaction.atomic
    def handle(self, *args, **options):
        rotation = ShiftPlan.objects.filter(name=ROTATING).first()
        if rotation is None or not rotation.anchor_monday:
            raise CommandError(f'Run seed_shift_plans first: there is no plan "{ROTATING}" with a start week.')
        day, made = Shift.objects.get_or_create(name="Day Shift", defaults={"start_time": "07:00", "end_time": "19:00", "is_overnight": False})
        night, _ = Shift.objects.get_or_create(name="Night Shift", defaults={"start_time": "19:00", "end_time": "07:00", "is_overnight": True})
        for shift, begins, ends in ((day, "07:00", "19:00"), (night, "19:00", "07:00")):
            if (shift.start_time.strftime("%H:%M"), shift.end_time.strftime("%H:%M")) != (begins, ends):
                raise CommandError(f"{shift.name} is not {begins}-{ends}; the security plans need 7AM-7PM and 7PM-7AM.")
        plans = [
            dict(name=ROTATION_PLAN, kind="alternating", day_shift=day, night_shift=night, working_weekdays=SEVEN_DAYS, anchor_monday=rotation.anchor_monday,
                 week_starts_sunday=True, description=ROTATION_DESCRIPTION),
            dict(name=NIGHT_PLAN, kind="fixed", shift=night, working_weekdays=SEVEN_DAYS, description="Security: Night shift 7PM-7AM, every night, Monday to Sunday."),
        ]
        for spec in plans:
            name = spec.pop("name")
            plan, created = ShiftPlan.objects.get_or_create(name=name, defaults=spec)
            self.stdout.write(f"{'Created' if created else 'Already there'}: plan {plan.name}")
            if plan.name == ROTATION_PLAN and (not plan.week_starts_sunday or plan.description != ROTATION_DESCRIPTION):
                plan.week_starts_sunday, plan.description = True, ROTATION_DESCRIPTION
                plan.save(update_fields=["week_starts_sunday", "description"])
                self.stdout.write("  now runs Sunday to Saturday, with the new description")
        if options["dry_run"]:
            transaction.set_rollback(True)
            self.stdout.write(self.style.WARNING("DRY RUN - nothing was saved."))

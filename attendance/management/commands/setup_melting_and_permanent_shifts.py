"""The four plans from the shift roster (2026-10-03): Melting, Flexible, and two permanent shifts.

1. Melting Shift Plan - Monday to Saturday, Day and Night 7AM-7PM, rotating weekly. No Sunday work: the last shift of
   the week is Saturday night and they resume on Monday morning.
2. Flexible Shift - no resumption or closing time (some security heads); present if they punch that day.
3. Permanent Shift 8AM-7PM and 4. Permanent Shift 7AM-6PM - the same shift every working day.
Nobody is assigned here. Safe to run again."""

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from attendance.models import Shift, ShiftPlan

SIX_DAYS = [0, 1, 2, 3, 4, 5]
ROTATING = "Rotating Day / Night (weekly)"


class Command(BaseCommand):
    help = "Create the Melting, Flexible and two Permanent shift plans (idempotent)."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Show what would happen without saving.")

    def shift(self, name, begins, ends, **extra):
        shift, made = Shift.objects.get_or_create(name=name, defaults={"start_time": begins, "end_time": ends, **extra})
        self.stdout.write(f"{'Created' if made else 'Already there'}: shift {name}")
        return shift

    @transaction.atomic
    def handle(self, *args, **options):
        rotation = ShiftPlan.objects.filter(name=ROTATING).first()
        if rotation is None or not rotation.anchor_monday:
            raise CommandError(f'Run seed_shift_plans first: there is no plan "{ROTATING}" with a start week.')
        day = self.shift("Day Shift", "07:00", "19:00", is_overnight=False)
        night = self.shift("Night Shift", "19:00", "07:00", is_overnight=True)
        for shift, begins, ends in ((day, "07:00", "19:00"), (night, "19:00", "07:00")):
            if (shift.start_time.strftime("%H:%M"), shift.end_time.strftime("%H:%M")) != (begins, ends):
                raise CommandError(f"{shift.name} is not {begins}-{ends}; the Melting plan needs 7AM-7PM and 7PM-7AM.")
        flexible = self.shift("Flexible (no fixed hours)", "00:00", "23:59:59", is_overnight=False, is_flexible=True)
        if not flexible.is_flexible:
            flexible.is_flexible = True
            flexible.save(update_fields=["is_flexible"])
        eight_to_seven = self.shift("Permanent Shift (8AM-7PM)", "08:00", "19:00", is_overnight=False)
        seven_to_six = self.shift("Permanent Shift (7AM-6PM)", "07:00", "18:00", is_overnight=False)

        plans = [
            dict(name="Melting Shift Plan (Mon-Sat Day/Night)", kind="alternating", day_shift=day, night_shift=night, working_weekdays=SIX_DAYS,
                 anchor_monday=rotation.anchor_monday,
                 description="Melting: Monday to Saturday, Day and Night 7AM-7PM, swapping weekly. No Sunday; the last shift is Saturday night, back Monday morning."),
            dict(name="Flexible Shift (no fixed hours)", kind="fixed", shift=flexible, working_weekdays=SIX_DAYS,
                 description="No resumption or closing time; present on any day they punch. Monday to Saturday."),
            dict(name="Permanent Shift (8AM-7PM)", kind="fixed", shift=eight_to_seven, working_weekdays=SIX_DAYS, description="8AM-7PM, Monday to Saturday."),
            dict(name="Permanent Shift (7AM-6PM)", kind="fixed", shift=seven_to_six, working_weekdays=SIX_DAYS, description="7AM-6PM, Monday to Saturday."),
        ]
        for spec in plans:
            name = spec.pop("name")
            plan, made = ShiftPlan.objects.get_or_create(name=name, defaults=spec)
            self.stdout.write(f"{'Created' if made else 'Already there'}: plan {plan.name}")
        if options["dry_run"]:
            transaction.set_rollback(True)
            self.stdout.write(self.style.WARNING("DRY RUN - nothing was saved."))

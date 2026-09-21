from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from attendance.models import Shift, ShiftPlan
from attendance.services.shift_plans import monday_of


class Command(BaseCommand):
    help = "Create the Rotic shifts and shift plans from the shift system document (safe to run again)."

    def add_arguments(self, parser):
        parser.add_argument("--anchor", help="A Monday (YYYY-MM-DD) in a week when Group A is on the Day shift. Default: this week's Monday.")

    def handle(self, *args, **options):
        from datetime import date

        anchor = date.fromisoformat(options["anchor"]) if options.get("anchor") else monday_of(timezone.localdate())
        if anchor.weekday() != 0:
            anchor -= timedelta(days=anchor.weekday())
        day, _ = Shift.objects.get_or_create(name="Day Shift", defaults={"start_time": "07:00", "end_time": "19:00", "is_overnight": False})
        night, _ = Shift.objects.get_or_create(name="Night Shift", defaults={"start_time": "19:00", "end_time": "07:00", "is_overnight": True})
        admin, made = Shift.objects.get_or_create(name="Admin Shift (8AM-6PM)", defaults={"start_time": "08:00", "end_time": "18:00", "is_overnight": False})
        if made:
            self.stdout.write("Created the Admin Shift (8AM-6PM).")
        six_days = [0, 1, 2, 3, 4, 5]
        plans = [
            dict(name="Rotating Day / Night (weekly)", kind="rotation", day_shift=day, night_shift=night, anchor_monday=anchor,
                 description="Day week Monday to Saturday 7AM-7PM, then Night from Sunday 7PM; the other group does the opposite."),
            dict(name="Permanent Day (Mon-Sat)", kind="fixed", shift=day, working_weekdays=six_days, description="Day Shift 7AM-7PM, Monday to Saturday."),
            dict(name="Permanent Night (Mon-Sat)", kind="fixed", shift=night, working_weekdays=six_days, description="Night Shift 7PM-7AM, starting Monday to Saturday."),
            dict(name="Admin (8AM-6PM, Mon-Sat)", kind="fixed", shift=admin, working_weekdays=six_days, description="Administrative staff, 8AM-6PM, Monday to Saturday."),
        ]
        for spec in plans:
            name = spec.pop("name")
            plan, created = ShiftPlan.objects.get_or_create(name=name, defaults=spec)
            self.stdout.write(f"{'Created' if created else 'Already there'}: {plan.name}")

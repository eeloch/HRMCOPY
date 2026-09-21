from django.core.management.base import BaseCommand

from meals.gating import gated_employees, reconcile


class Command(BaseCommand):
    help = "Switch people covered by MEAL_GATING_EMPLOYEE_IDS on or off at the meal terminal to match their tickets today. --release switches everyone back on."

    def add_arguments(self, parser):
        parser.add_argument("--release", action="store_true", help="Switch everyone this has switched off back on, whatever the setting says.")

    def handle(self, *args, **options):
        if not options["release"]:
            self.stdout.write(f"Managed: {', '.join(e.employee_id for e in gated_employees()) or 'nobody'}")
        queued = reconcile(release=options["release"])
        self.stdout.write(f"Queued {queued} terminal switch(es). The gateway sends them within a few seconds.")

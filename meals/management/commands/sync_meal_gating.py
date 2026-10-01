from django.core.management.base import BaseCommand

from meals.gating import gated_employees, reconcile


class Command(BaseCommand):
    help = "Switch people covered by MEAL_GATING_EMPLOYEE_IDS on or off at the meal terminal to match their tickets today. --release switches everyone back on. --exited switches off every exited employee still enrolled on a meal terminal."

    def add_arguments(self, parser):
        parser.add_argument("--release", action="store_true", help="Switch everyone this has switched off back on, whatever the setting says.")
        parser.add_argument("--exited", action="store_true", help="Switch off every non-active (exited) employee still enrolled on a meal terminal - closes the gap left when someone drops out of gating scope on exit.")

    def handle(self, *args, **options):
        if options["exited"]:
            from meals.gating import disable_everywhere
            from employees.models import Employee

            queued = 0
            people = 0
            for employee in Employee.objects.exclude(status="active").filter(biometric_identities__isnull=False).distinct():
                sent = disable_everywhere(employee)
                if sent:
                    people += 1
                    queued += sent
            self.stdout.write(f"Switched off {queued} terminal identity(ies) across {people} exited employee(s).")
            return

        if not options["release"]:
            self.stdout.write(f"Managed: {', '.join(e.employee_id for e in gated_employees()) or 'nobody'}")
        queued = reconcile(release=options["release"])
        self.stdout.write(f"Queued {queued} terminal switch(es). The gateway sends them within a few seconds.")

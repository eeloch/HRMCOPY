"""Send the legacy `enableuser` OFF (or ON) for everyone a meal terminal is recorded as switched off, once.

Needed after 2026-09-24: AI Meal Ticket 2 acked the profile switch but still let switched-off people through by
face, so everyone already OFF there needs the legacy command as well. Dry run unless --apply."""

from django.core.management.base import BaseCommand

from attendance.models import BiometricDevice, DeviceCommand
from employees.models import BiometricIdentity
from attendance.integrations.aiface_protocol import IDENTITY_SYSTEM
from meals.models import MealTerminalUserState


class Command(BaseCommand):
    help = "Queue the legacy enableuser switch for people recorded as switched off at a meal terminal."

    def add_arguments(self, parser):
        parser.add_argument("--device", required=True, help="Terminal name, e.g. 'AI Meal Ticket 2'")
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        device = BiometricDevice.objects.get(name=options["device"], purpose="meal_ticket")
        queued = skipped = 0
        for state in MealTerminalUserState.objects.filter(device_serial=device.serial_number, enabled=False).select_related("employee"):
            identities = BiometricIdentity.objects.filter(employee=state.employee, system=IDENTITY_SYSTEM, source_identifier=device.serial_number, is_active=True)
            for identity in identities:
                if not identity.external_user_id.isdigit():
                    continue
                enrollid = int(identity.external_user_id)
                waiting = DeviceCommand.objects.filter(device=device, command_type="set_user_enabled", status__in=("pending", "sent"), payload__enrollid=enrollid, payload__enabled=False, payload__has_key="legacy").exists()
                if waiting:
                    skipped += 1
                    continue
                queued += 1
                if options["apply"]:
                    DeviceCommand.objects.create(device=device, command_type="set_user_enabled", payload={"enrollid": enrollid, "enabled": False, "employee_id": state.employee_id, "legacy": True})
        self.stdout.write(("QUEUED " if options["apply"] else "DRY RUN ") + f"{queued} legacy switch-off(s) for {device.name}; {skipped} already waiting")

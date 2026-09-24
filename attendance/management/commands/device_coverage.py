"""What is really enrolled on each terminal, slot by slot, and where the terminals disagree.

`queue` asks every reachable terminal for its full user list (read-only). `report` compares the latest
answers: for each kind of credential (face, fingerprint, card, password) how many people have it on one
terminal but not on another - the reason one terminal recognises someone and its neighbour does not.
"""

from collections import Counter, defaultdict

from django.core.management.base import BaseCommand

from attendance.integrations.aiface_protocol import IDENTITY_SYSTEM
from attendance.models import BiometricDevice, DeviceCommand
from employees.models import BiometricIdentity

KINDS = {"face": lambda n: n == 50 or 20 <= n <= 27, "fingerprint": lambda n: 0 <= n <= 9, "card": lambda n: n == 11, "password": lambda n: n == 10}


def kinds_of(backupnums):
    return {kind for kind, test in KINDS.items() if any(test(int(n)) for n in backupnums if n is not None)}


class Command(BaseCommand):
    help = "queue: ask each terminal for its enrolled slots. report: compare the latest answers."

    def add_arguments(self, parser):
        parser.add_argument("action", choices=["queue", "report", "sync"])
        parser.add_argument("--all-devices", action="store_true", help="Include meal terminals in the report.")

    def handle(self, *args, **options):
        if options["action"] == "queue":
            return self.queue()
        if options["action"] == "sync":
            return self.sync()
        return self.report(include_meal=options["all_devices"])

    def sync(self):
        """One pass of what the gateway does every 15 minutes: link ids, then queue the missing credentials."""
        from attendance.services.device_sync import link_ids_by_staff_number, plan_slot_clones

        devices = list(BiometricDevice.objects.all())
        linked = link_ids_by_staff_number(devices)
        planned = plan_slot_clones(devices)
        self.stdout.write(f"ids linked: {linked} | people with credentials queued for copying: {len(planned)}")

    def queue(self):
        queued = 0
        for device in BiometricDevice.objects.all():
            if DeviceCommand.objects.filter(device=device, command_type="list_user_slots", status__in=("pending", "sent")).exists():
                continue
            DeviceCommand.objects.create(device=device, command_type="list_user_slots", payload={})
            queued += 1
            self.stdout.write(f"queued for {device.name}")
        self.stdout.write(f"{queued} request(s) queued. Each terminal answers when it next connects and is idle.")

    def report(self, include_meal):
        devices = [d for d in BiometricDevice.objects.order_by("name") if include_meal or d.purpose == "attendance"]
        slots_by_device, when = {}, {}
        for device in devices:
            command = DeviceCommand.objects.filter(device=device, command_type="list_user_slots", status="acked").order_by("-id").first()
            if command is None:
                self.stdout.write(f"{device.name}: no slot list yet")
                continue
            per_id = defaultdict(set)
            for enrollid, backupnum in command.result.get("slots", []):
                per_id[str(enrollid)].add(backupnum)
            slots_by_device[device.serial_number] = per_id
            when[device.serial_number] = command.completed_at
        if len(slots_by_device) < 1:
            return

        identities = defaultdict(dict)
        for identity in BiometricIdentity.objects.filter(system=IDENTITY_SYSTEM, is_active=True, employee__status="active", source_identifier__in=list(slots_by_device)).select_related("employee"):
            identities[identity.employee_id][identity.source_identifier] = identity.external_user_id

        self.stdout.write("\nPer terminal (from the terminal itself):")
        for device in devices:
            per_id = slots_by_device.get(device.serial_number)
            if per_id is None:
                continue
            counts = Counter(kind for backupnums in per_id.values() for kind in kinds_of(backupnums))
            no_credential = sum(1 for backupnums in per_id.values() if not kinds_of(backupnums))
            self.stdout.write(f"  {device.name} ({device.serial_number}) at {when[device.serial_number]:%d %b %H:%M}: {len(per_id)} ids | face {counts['face']} | fingerprint {counts['fingerprint']} | card {counts['card']} | password {counts['password']} | ids with no credential {no_credential}")

        serials = list(slots_by_device)
        self.stdout.write("\nPeople who have a credential on some terminals but not others (active staff we can match by id):")
        missing_by_kind = Counter()
        per_device_missing = defaultdict(Counter)
        examples = defaultdict(list)
        for employee_id, by_serial in identities.items():
            have = {}
            for serial in serials:
                enrollid = by_serial.get(serial)
                have[serial] = kinds_of(slots_by_device[serial].get(enrollid, set())) if enrollid is not None else set()
            for kind in KINDS:
                holders = [s for s in serials if kind in have[s]]
                if not holders:
                    continue
                for serial in serials:
                    if kind not in have[serial]:
                        missing_by_kind[kind] += 1
                        per_device_missing[serial][kind] += 1
                        if len(examples[(serial, kind)]) < 3:
                            examples[(serial, kind)].append(employee_id)
        names = {d.serial_number: d.name for d in devices}
        for serial in serials:
            row = per_device_missing[serial]
            self.stdout.write(f"  {names[serial]}: missing face {row['face']} | fingerprint {row['fingerprint']} | card {row['card']} | password {row['password']}")
        self.stdout.write(f"  total credential gaps: {dict(missing_by_kind)}")

        unlinked = {serial: sum(1 for enrollid in per_id if not any(by.get(serial) == enrollid for by in identities.values())) for serial, per_id in slots_by_device.items()}
        self.stdout.write(f"\nIds on a terminal that we cannot match to an active employee: { {names[s]: n for s, n in unlinked.items()} }")

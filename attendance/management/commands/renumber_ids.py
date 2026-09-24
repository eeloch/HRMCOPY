"""Move people who sit on a terminal under a made-up id to their staff-number id (dry run unless --apply).

Each move is a background job run by the gateway; see run_aiface_gateway._run_renumber. Needs a fresh slot listing
per terminal (`device_coverage queue`)."""

from django.core.management.base import BaseCommand

from attendance.models import BiometricDevice
from attendance.services.device_sync import latest_slots, plan_renumbers, RENUMBER_LISTING_FRESH_FOR


class Command(BaseCommand):
    help = "Plan (and with --apply queue) same-terminal moves from stray ids to staff-number ids."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--limit", type=int, default=None, help="Queue at most this many moves.")
        parser.add_argument("--device", default=None, help="Only this terminal (name).")

    def handle(self, *args, **options):
        devices = [d for d in BiometricDevice.objects.order_by("name") if options["device"] in (None, d.name)]
        for device in devices:
            if latest_slots(device, max_age=RENUMBER_LISTING_FRESH_FOR) is None:
                self.stdout.write(f"{device.name}: no listing from the last hour - skipped")
        planned = plan_renumbers(devices, limit=options["limit"], dry_run=not options["apply"])
        per_device = {}
        for move in planned:
            per_device.setdefault(move["device"].name, []).append(move)
        for name, moves in per_device.items():
            with_faces = sum(1 for m in moves if any(n == 50 or 20 <= n <= 27 for n in m["slots"]))
            self.stdout.write(f"{name}: {len(moves)} move(s), {with_faces} with a face, {sum(1 for m in moves if not m['slots'])} only need the old entry removed")
            for move in moves[:3]:
                self.stdout.write(f"    {move['employee'].employee_id} {move['employee'].full_name}: {move['from_id']} -> {move['to_id']} slots {move['slots']}")
        self.stdout.write(("QUEUED " if options["apply"] else "DRY RUN ") + f"{len(planned)} move(s)")

"""Leave every terminal holding the same people: the active staff, nobody else.

  mirror_plan --probe    ask the terminals for the names behind leftover entries (read-only)
  mirror_plan            show what would be done, per terminal (nothing is changed)
  mirror_plan --apply    queue it: leavers' entries deleted, leftover copies moved onto the staff number

Needs a fresh slot listing per terminal (`device_coverage queue`). Entries whose owner is not certain are listed as
`review` and never touched."""

from django.core.management.base import BaseCommand

from attendance.models import BiometricDevice
from attendance.services.device_sync import plan_mirror, queue_name_probes


class Command(BaseCommand):
    help = "Plan (and with --apply queue) the removal of leftover terminal entries."

    def add_arguments(self, parser):
        parser.add_argument("--probe", action="store_true")
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--limit", type=int, default=None)

    def handle(self, *args, **options):
        devices = list(BiometricDevice.objects.order_by("name"))
        if options["probe"]:
            self.stdout.write(f"queued {queue_name_probes(devices)} name probe(s)")
            return
        report = plan_mirror(devices, apply=options["apply"], limit=options["limit"])
        totals = {"leaver": 0, "move": 0, "extra_copy": 0, "review": 0}
        for device, plan in report.items():
            self.stdout.write(f"{device.name}: delete {len(plan['leaver'])} leaver entries, move {len(plan['move'])} copies onto the staff number ({sum(1 for m in plan['move'] if any(n == 50 or 20 <= n <= 27 for n in m['slots']))} with a face), delete {len(plan['extra_copy'])} extra copies, review {len(plan['review'])}")
            reasons = {}
            for item in plan["review"]:
                reasons[item["reason"]] = reasons.get(item["reason"], 0) + 1
            if reasons:
                self.stdout.write(f"    review reasons: {reasons}")
            for key in totals:
                totals[key] += len(plan[key])
        self.stdout.write(("QUEUED " if options["apply"] else "DRY RUN ") + str(totals))

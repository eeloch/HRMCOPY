"""Undo the duplicates the old sync fallback made: point each person's identity back at their own staff-number id
on every terminal that already holds them under it, re-send their meal switch to that id, and (only with
--delete-copies) queue the removal of the stray copy from the terminal.

Dry run by default. Needs a fresh slot listing per terminal (`device_coverage queue`)."""

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import transaction

from attendance.models import BiometricDevice, DeviceCommand
from attendance.services.device_sync import find_duplicate_ids, latest_slots


class Command(BaseCommand):
    help = "Re-point duplicated identities at the person's own terminal id and optionally remove the stray copies."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Re-point identities (database only).")
        parser.add_argument("--delete-copies", action="store_true", help="With --apply: also queue deletion of safe-to-delete copies.")
        parser.add_argument("--max-age-hours", type=float, default=3)

    def handle(self, *args, **options):
        from meals.gating import _queue, gated_employees, tickets_left_today

        apply, delete = options["apply"], options["delete_copies"]
        max_age = timedelta(hours=options["max_age_hours"])
        totals = {"repointed": 0, "deletable": 0, "kept_for_review": 0, "purges_queued": 0, "switches_queued": 0}
        for device in BiometricDevice.objects.order_by("name"):
            if latest_slots(device, max_age=max_age) is None:
                self.stdout.write(f"{device.name}: no fresh listing - skipped")
                continue
            rows = find_duplicate_ids(device)
            deletable = [r for r in rows if r["deletable"]]
            review = [r for r in rows if not r["deletable"] and r["copy_present"]]
            self.stdout.write(f"{device.name}: {len(rows)} to re-point, {len(deletable)} copies safe to delete, {len(review)} copies kept for review")
            for row in review[:5]:
                self.stdout.write(f"    review: {row['employee'].employee_id} {row['employee'].full_name} own {row['own_id']} {row['own_kinds']} | copy {row['copy_id']} {row['copy_kinds']}")
            totals["repointed"] += len(rows); totals["deletable"] += len(deletable); totals["kept_for_review"] += len(review)
            if not apply:
                continue
            gated = {e.pk for e in gated_employees()} if device.purpose == "meal_ticket" else set()
            with transaction.atomic():
                for row in rows:
                    identity = row["identity"]
                    identity.external_user_id = str(row["own_id"])
                    identity.save(update_fields=["external_user_id"])
                    if row["employee"].pk in gated:
                        totals["switches_queued"] += int(_queue(device, row["employee"], row["own_id"], tickets_left_today(row["employee"]) > 0))
            if delete:
                for row in deletable:
                    already = DeviceCommand.objects.filter(device=device, command_type="purge_user", status__in=("pending", "sent"), payload__enrollid=row["copy_id"]).exists()
                    if not already:
                        DeviceCommand.objects.create(device=device, command_type="purge_user", payload={"enrollid": row["copy_id"], "reason": "duplicate copy left by the old sync fallback"})
                        totals["purges_queued"] += 1
        self.stdout.write(("APPLIED " if apply else "DRY RUN ") + str(totals))

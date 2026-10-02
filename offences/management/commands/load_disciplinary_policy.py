"""Load the company Disciplinary Action Policy (offences with 1st/2nd/3rd penalties, and rewards) into the app.

Safe to run again: lines are matched by category + name, so re-running updates the wording and amounts and adds
anything new, and never duplicates or deletes. Whether a line is active is left as the administrator set it."""

from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction

from offences.models import OffenceType, RewardType
from offences.policy_data import OFFENCES, REWARDS


def money(value):
    return None if value is None else Decimal(str(value))


class Command(BaseCommand):
    help = "Load the Disciplinary Action Policy offences and rewards (idempotent)."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Report what would change without saving.")

    @transaction.atomic
    def handle(self, *args, **options):
        created = updated = adopted = 0
        for item in OFFENCES:
            values = {
                "sort_order": item["sort_order"],
                "penalty_first": item["penalty_first"], "penalty_second": item["penalty_second"], "penalty_third": item["penalty_third"],
                "amount_first": money(item["amount_first"]), "amount_second": money(item["amount_second"]), "amount_third": money(item["amount_third"]),
                "default_amount": money(item["amount_first"]) or Decimal("0.00"),
            }
            obj = OffenceType.objects.filter(category=item["category"], name=item["name"]).first()
            if obj is None:
                # A type set up by hand before the policy was loaded, never used and with no category, is adopted
                # rather than left beside a duplicate.
                obj = OffenceType.objects.filter(category="", name__iexact=item["name"], offences__isnull=True).first()
                if obj is not None:
                    obj.category, obj.name = item["category"], item["name"]
                    adopted += 1
            if obj is None:
                OffenceType.objects.create(category=item["category"], name=item["name"], **values)
                created += 1
            else:
                for field, value in values.items():
                    setattr(obj, field, value)
                obj.save()
                updated += 1
        rewards_created = rewards_updated = 0
        for item in REWARDS:
            values = {
                "sort_order": item["sort_order"], "reward_first": item["reward_first"], "reward_second": item["reward_second"],
                "amount_first": money(item["amount_first"]), "amount_second": money(item["amount_second"]), "auto_rule": item["auto_rule"],
            }
            _, was_created = RewardType.objects.update_or_create(category=item["category"], name=item["name"], defaults=values)
            rewards_created += was_created
            rewards_updated += not was_created
        summary = (f"Offences: {created} added, {updated} updated ({adopted} existing hand-made type(s) adopted). "
                   f"Rewards: {rewards_created} added, {rewards_updated} updated.")
        if options["dry_run"]:
            transaction.set_rollback(True)
            summary = "DRY RUN (nothing saved). " + summary
        self.stdout.write(summary)

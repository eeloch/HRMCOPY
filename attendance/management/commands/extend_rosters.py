from django.core.management.base import BaseCommand

from attendance.services.shift_plans import HORIZON_DAYS, extend_rosters


class Command(BaseCommand):
    help = "Write everyone's planned roster days for the coming weeks (the gateway does this daily by itself)."

    def add_arguments(self, parser):
        parser.add_argument("--horizon", type=int, default=HORIZON_DAYS, help="How many days ahead to cover.")

    def handle(self, *args, **options):
        summary = extend_rosters(horizon_days=options["horizon"])
        self.stdout.write(f"Roster days created {summary.created}, changed {summary.updated}, already right or protected {summary.kept}.")

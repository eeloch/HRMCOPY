"""Propose the policy's automatic "No absence" / "No lateness" rewards for a finished month.

Run daily from the attendance job (deploy/scripts/process_attendance.sh). Without --month it looks at last month,
and only during the first few days of a month - after that a month's attendance is settled and nothing new is
proposed unless someone asks for it with --month. Safe to run repeatedly: a reward is proposed once per person
per month."""

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from bonuses.services import AttendanceRewardService

FIRST_DAYS_OF_MONTH = 5


class Command(BaseCommand):
    help = "Propose the automatic attendance & punctuality rewards for last month (or --month YYYY-MM)."

    def add_arguments(self, parser):
        parser.add_argument("--month", help="YYYY-MM. Default: last month, during the first days of a month.")
        parser.add_argument("--dry-run", action="store_true", help="Show what would be proposed without saving.")

    def handle(self, *args, **options):
        today = timezone.localdate()
        if options["month"]:
            try:
                year, month = (int(part) for part in options["month"].split("-"))
                if not 1 <= month <= 12:
                    raise ValueError
            except ValueError:
                raise CommandError("--month must look like 2026-09.")
        elif today.day <= FIRST_DAYS_OF_MONTH:
            year, month = AttendanceRewardService.previous_month(today)
        else:
            self.stdout.write("Not the start of a month - nothing to propose (use --month YYYY-MM to run for a specific month).")
            return
        try:
            summary = AttendanceRewardService.propose_for_month(year, month, dry_run=options["dry_run"])
        except ValueError as error:
            self.stdout.write(f"Skipped: {error}")
            return
        prefix = "DRY RUN - " if options["dry_run"] else ""
        proposed = ", ".join(f"{name}: {count}" for name, count in summary["proposed"].items()) or "none"
        self.stdout.write(f"{prefix}{summary['month']}: proposed {proposed}; {summary['already_proposed']} already proposed earlier.")
        if summary["missing_reward"]:
            self.stdout.write(f"No active reward is set up for: {', '.join(summary['missing_reward'])} (run load_disciplinary_policy).")

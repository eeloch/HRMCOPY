"""Apply the ready-made attendance exception rules (the same ones as the "Quick rules" on the Attendance Exceptions
page) to everything still waiting. Preview with --dry-run. Each decision is recorded exactly as a reviewer's would be."""

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from attendance.models import AttendanceException
from attendance.views.exception_review import decide, filtered_queryset, rule_definitions


class Command(BaseCommand):
    help = "Apply the quick rules to pending attendance exceptions (all rules, or --rule KEY)."

    def add_arguments(self, parser):
        parser.add_argument("--rule", action="append", help="Rule key to apply (repeatable). Default: all rules, in order.")
        parser.add_argument("--by", default="Rule run (command line)", help="Name recorded as the reviewer.")
        parser.add_argument("--dry-run", action="store_true", help="Show how many cases each rule would decide without saving.")

    def handle(self, *args, **options):
        rules = rule_definitions()
        keys = {rule["key"] for rule in rules}
        wanted = options["rule"] or [rule["key"] for rule in rules]
        unknown = [key for key in wanted if key not in keys]
        if unknown:
            raise CommandError(f"Unknown rule(s): {', '.join(unknown)}. Choose from: {', '.join(sorted(keys))}.")
        total = 0
        for rule in rules:
            if rule["key"] not in wanted:
                continue
            ids = list(filtered_queryset(rule["filters"]).filter(status="pending").values_list("id", flat=True))
            decided = 0
            if not options["dry_run"]:
                for start in range(0, len(ids), 200):
                    with transaction.atomic():
                        for exception in AttendanceException.objects.select_for_update(of=("self",)).select_related("attendance", "attendance__employee").filter(pk__in=ids[start:start + 200], status="pending"):
                            decide(exception, rule["decision"], rule["comment"], reviewer_name=options["by"])
                            decided += 1
            else:
                decided = len(ids)
            total += decided
            self.stdout.write(f"{rule['label']}: {decided} case(s) {'would be ' if options['dry_run'] else ''}{rule['decision']}")
        self.stdout.write(f"{'DRY RUN - ' if options['dry_run'] else ''}{total} case(s) in total.")

"""Set up the Sales & Planning team's working pattern (their work schedule, 2026-10-02) and put them on it.

Franklyn works mornings and Peter afternoons every working day; Isaac and Faith swap between the two every week.
Everyone has their own day off, so each pattern is its own plan. Safe to run again: shifts and plans that exist are
left alone, and anyone already on their plan is not touched."""

from datetime import date, timedelta

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from attendance.models import Shift, ShiftPlan, ShiftPlanAssignment
from attendance.services.shift_plans import assign_plan, monday_of
from employees.models import Employee

MORNING = ("Sales & Planning Morning (7AM-6PM)", "07:00", "18:00")
AFTERNOON = ("Sales & Planning Afternoon (11AM-10PM)", "11:00", "22:00")
WEEKDAYS = {"Monday": 0, "Tuesday": 1, "Wednesday": 2, "Thursday": 3, "Friday": 4, "Saturday": 5}


def working_days(day_off):
    """Monday to Saturday, except the person's day off (Sunday is a rest day for everyone, as on the other plans)."""
    return [number for name, number in WEEKDAYS.items() if name != day_off]


class Command(BaseCommand):
    help = "Create the Sales & Planning shifts and plans and assign the team (idempotent)."

    def add_arguments(self, parser):
        parser.add_argument("--start", help="First day on the new plan (YYYY-MM-DD). Default: today.")
        parser.add_argument("--anchor", help="A Monday of the week Isaac works Morning and Faith Afternoon. Default: the Monday of the start week.")
        parser.add_argument("--dry-run", action="store_true", help="Show what would happen without saving.")

    @transaction.atomic
    def handle(self, *args, **options):
        start = date.fromisoformat(options["start"]) if options.get("start") else timezone.localdate()
        anchor = date.fromisoformat(options["anchor"]) if options.get("anchor") else monday_of(start)
        if anchor.weekday() != 0:
            anchor -= timedelta(days=anchor.weekday())

        shifts = {}
        for name, begins, ends in (MORNING, AFTERNOON):
            shift, made = Shift.objects.get_or_create(name=name, defaults={"start_time": begins, "end_time": ends, "is_overnight": False})
            shifts[name] = shift
            self.stdout.write(f"{'Created' if made else 'Already there'}: shift {name}")
        morning, afternoon = shifts[MORNING[0]], shifts[AFTERNOON[0]]

        specs = {
            "morning_wed": dict(name="Sales & Planning - Morning (off Wednesday)", kind="fixed", shift=morning, working_weekdays=working_days("Wednesday"),
                                description="Morning shift 7AM-6PM, Monday to Saturday except Wednesday."),
            "afternoon_thu": dict(name="Sales & Planning - Afternoon (off Thursday)", kind="fixed", shift=afternoon, working_weekdays=working_days("Thursday"),
                                  description="Afternoon shift 11AM-10PM, Monday to Saturday except Thursday."),
            "alternating_tue": dict(name="Sales & Planning - Alternating Morning/Afternoon (off Tuesday)", kind="alternating", day_shift=morning, night_shift=afternoon,
                                    working_weekdays=working_days("Tuesday"), anchor_monday=anchor,
                                    description="Swaps weekly between Morning 7AM-6PM and Afternoon 11AM-10PM. Group A is on Morning in the start week, Group B on Afternoon."),
            "alternating_fri": dict(name="Sales & Planning - Alternating Morning/Afternoon (off Friday)", kind="alternating", day_shift=morning, night_shift=afternoon,
                                    working_weekdays=working_days("Friday"), anchor_monday=anchor,
                                    description="Swaps weekly between Morning 7AM-6PM and Afternoon 11AM-10PM. Group A is on Morning in the start week, Group B on Afternoon."),
        }
        plans = {}
        for key, spec in specs.items():
            name = spec.pop("name")
            plans[key], made = ShiftPlan.objects.get_or_create(name=name, defaults=spec)
            self.stdout.write(f"{'Created' if made else 'Already there'}: plan {name}")

        # staff number, plan, group ("" for a fixed plan). Isaac is on Morning this week, Faith on Afternoon.
        team = [("001142", "morning_wed", ""), ("000817", "afternoon_thu", ""), ("000753", "alternating_tue", "A"), ("000982", "alternating_fri", "B")]
        for staff_number, key, group in team:
            employee = Employee.objects.filter(employee_id=staff_number, status="active").first()
            if employee is None:
                self.stdout.write(self.style.WARNING(f"Skipped {staff_number}: no active employee with this staff number."))
                continue
            today = timezone.localdate()
            current = ShiftPlanAssignment.objects.filter(employee=employee, start_date__lte=today).filter(end_date__isnull=True).first()
            if current is not None and current.plan_id == plans[key].pk and current.group == group:
                self.stdout.write(f"Already on their plan: {employee.full_name} ({staff_number})")
                continue
            _, summary = assign_plan([employee], plans[key], group=group, start_date=start, actor="setup_sales_planning_shifts")
            self.stdout.write(f"Assigned {employee.full_name} ({staff_number}) to {plans[key].name}{f' (Group {group})' if group else ''} from {start}: {summary.as_dict()}")

        if options["dry_run"]:
            transaction.set_rollback(True)
            self.stdout.write(self.style.WARNING("DRY RUN - nothing was saved."))

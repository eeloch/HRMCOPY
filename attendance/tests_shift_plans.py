from datetime import date, time, timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from rest_framework.test import APIClient

from employees.models import Department, Employee

from .models import EmployeeRosterDay, RosterDaySource, Shift, ShiftPlan, ShiftPlanAssignment
from .services.shift_plans import assign_plan, day_group_for_week, extend_rosters, flip_rotation_week, planned_day

MONDAY = date(2026, 9, 21)  # a Monday: Group A starts on Day this week


class PlanTestCase(TestCase):
    def setUp(self):
        self.day = Shift.objects.create(name="Day", start_time=time(7), end_time=time(19))
        self.night = Shift.objects.create(name="Night", start_time=time(19), end_time=time(7), is_overnight=True)
        self.admin_shift = Shift.objects.create(name="Admin", start_time=time(8), end_time=time(18))
        self.rotation = ShiftPlan.objects.create(name="Rotation", kind="rotation", day_shift=self.day, night_shift=self.night, anchor_monday=MONDAY)
        self.perm_day = ShiftPlan.objects.create(name="Perm day", kind="fixed", shift=self.day, working_weekdays=[0, 1, 2, 3, 4, 5])
        self.perm_night = ShiftPlan.objects.create(name="Perm night", kind="fixed", shift=self.night, working_weekdays=[0, 1, 2, 3, 4, 5])
        self.admin = ShiftPlan.objects.create(name="Admin", kind="fixed", shift=self.admin_shift, working_weekdays=[0, 1, 2, 3, 4, 5])

    def week(self, plan, group, monday):
        return [(planned_day(plan, group, monday + timedelta(days=i))) for i in range(7)]

    def names(self, week):
        return [(status[0].upper() + (":" + shift.name if shift else "")) for status, shift in week]


class ShiftSystemDocumentTests(PlanTestCase):
    """The rules from 'The shift system at Rotic': Day Mon-Sat, Night from Sunday 7pm, Sunday is Night only."""

    def test_the_day_week_is_monday_to_saturday_days_then_sunday_night(self):
        self.assertEqual(self.names(self.week(self.rotation, "A", MONDAY)), ["W:Day"] * 6 + ["W:Night"])  # Sunday 7pm starts the Night week

    def test_the_night_week_is_monday_to_saturday_nights_then_sunday_rest(self):
        self.assertEqual(self.names(self.week(self.rotation, "B", MONDAY)), ["W:Night"] * 6 + ["R"])  # rests Sunday 7am to Monday 7am

    def test_the_groups_swap_every_week_forever_in_both_directions(self):
        for weeks in range(-6, 9):
            monday = MONDAY + timedelta(weeks=weeks)
            self.assertEqual(day_group_for_week(self.rotation, monday), "A" if weeks % 2 == 0 else "B")

    def test_on_a_sunday_only_the_night_shift_works(self):
        for weeks in range(4):
            sunday = MONDAY + timedelta(weeks=weeks, days=6)
            shifts = {planned_day(self.rotation, group, sunday)[1] for group in ("A", "B")} - {None}
            self.assertEqual(shifts, {self.night})

    def test_someone_who_finishes_the_day_week_works_seven_nights_then_rests_before_days_again(self):
        # Group A, over two weeks: day week (6 days), then the Night week: Sunday night + Mon-Sat nights, Sunday rest
        two_weeks = self.names(self.week(self.rotation, "A", MONDAY) + self.week(self.rotation, "A", MONDAY + timedelta(weeks=1)))
        self.assertEqual(two_weeks, ["W:Day"] * 6 + ["W:Night"] + ["W:Night"] * 6 + ["R"])
        third = self.names(self.week(self.rotation, "A", MONDAY + timedelta(weeks=2)))
        self.assertEqual(third, ["W:Day"] * 6 + ["W:Night"])  # back on Days the Monday after the rest day

    def test_permanent_day_night_and_admin_shifts(self):
        self.assertEqual(self.names(self.week(self.perm_day, "", MONDAY)), ["W:Day"] * 6 + ["R"])
        self.assertEqual(self.names(self.week(self.perm_night, "", MONDAY)), ["W:Night"] * 6 + ["R"])
        self.assertEqual(self.names(self.week(self.admin, "", MONDAY)), ["W:Admin"] * 6 + ["R"])


class AutomaticRosterTests(PlanTestCase):
    def setUp(self):
        super().setUp()
        self.people = [Employee.objects.create(employee_id=f"E{i:03d}", first_name="P", last_name=str(i)) for i in range(6)]

    def test_assigning_writes_the_roster_and_the_daily_job_keeps_extending_it(self):
        assign_plan(self.people[:1], self.rotation, group="A", start_date=MONDAY)
        first = EmployeeRosterDay.objects.filter(employee=self.people[0])
        self.assertTrue(first.filter(date=MONDAY, shift=self.day).exists())
        last_before = first.order_by("-date").first().date
        extend_rosters(today=MONDAY + timedelta(days=60))  # weeks later: the job reaches further ahead
        last_after = EmployeeRosterDay.objects.filter(employee=self.people[0]).order_by("-date").first().date
        self.assertGreater(last_after, last_before)
        self.assertEqual(EmployeeRosterDay.objects.filter(employee=self.people[0], date=MONDAY + timedelta(weeks=8, days=6)).first().shift, self.night)  # week 8 Sunday, Day week: starts nights

    def test_manual_days_are_never_overwritten_and_running_again_changes_nothing(self):
        assign_plan(self.people[:1], self.rotation, group="A", start_date=MONDAY)
        EmployeeRosterDay.objects.filter(employee=self.people[0], date=MONDAY + timedelta(days=2)).update(status="rest", shift=None, source=RosterDaySource.OVERRIDE)
        summary = extend_rosters(today=MONDAY)
        self.assertEqual((summary.created, summary.updated), (0, 0))
        self.assertEqual(EmployeeRosterDay.objects.get(employee=self.people[0], date=MONDAY + timedelta(days=2)).status, "rest")

    def test_changing_plan_closes_the_old_assignment_and_rewrites_the_future(self):
        assign_plan(self.people[:1], self.perm_day, start_date=MONDAY)
        assign_plan(self.people[:1], self.perm_night, start_date=MONDAY + timedelta(days=7))
        old, new = ShiftPlanAssignment.objects.filter(employee=self.people[0]).order_by("start_date")
        self.assertEqual((old.plan, old.end_date, new.plan), (self.perm_day, MONDAY + timedelta(days=6), self.perm_night))
        self.assertEqual(EmployeeRosterDay.objects.get(employee=self.people[0], date=MONDAY).shift, self.day)
        self.assertEqual(EmployeeRosterDay.objects.get(employee=self.people[0], date=MONDAY + timedelta(days=7)).shift, self.night)

    def test_a_rotation_needs_a_group(self):
        with self.assertRaises(ValueError):
            assign_plan(self.people[:1], self.rotation, group="", start_date=MONDAY)

    def test_flipping_the_week_swaps_who_is_on_days(self):
        from django.utils import timezone

        next_monday = timezone.localdate() - timedelta(days=timezone.localdate().weekday()) + timedelta(days=7)
        self.rotation.anchor_monday = next_monday
        self.rotation.save()
        assign_plan(self.people[:1], self.rotation, group="A", start_date=next_monday)
        self.assertEqual(EmployeeRosterDay.objects.get(employee=self.people[0], date=next_monday).shift, self.day)  # A is on Days that week
        flip_rotation_week(self.rotation)
        self.assertEqual(day_group_for_week(self.rotation, next_monday), "B")
        self.assertEqual(EmployeeRosterDay.objects.get(employee=self.people[0], date=next_monday).shift, self.night)  # so A is now on Nights

class ShiftPlanApiTests(PlanTestCase):
    def setUp(self):
        super().setUp()
        self.dept = Department.objects.create(name="Melting")
        self.people = [Employee.objects.create(employee_id=f"M{i:03d}", first_name="M", last_name=str(i), department=self.dept) for i in range(5)]
        user = get_user_model().objects.create_user("shift-mgr", password="pw")
        user.user_permissions.add(Permission.objects.get(codename="manage_shifts"))
        self.client = APIClient()
        self.client.force_authenticate(user)

    def test_a_whole_department_is_split_evenly_into_the_two_groups_in_one_go(self):
        preview = self.client.post("/api/attendance/shift-plans/assign/", {"plan": self.rotation.pk, "group": "split", "department_ids": [self.dept.pk], "dry_run": True}, format="json").json()
        self.assertEqual((preview["people"], preview["split"]), (5, {"A": 3, "B": 2}))
        self.assertFalse(ShiftPlanAssignment.objects.exists())
        done = self.client.post("/api/attendance/shift-plans/assign/", {"plan": self.rotation.pk, "group": "split", "department_ids": [self.dept.pk], "start_date": MONDAY.isoformat()}, format="json")
        self.assertEqual(done.status_code, 200)
        groups = list(ShiftPlanAssignment.objects.order_by("employee__employee_id").values_list("group", flat=True))
        self.assertEqual(groups, ["A", "B", "A", "B", "A"])
        # the two groups are on opposite shifts on the same Monday
        a, b = self.people[0], self.people[1]
        self.assertEqual(EmployeeRosterDay.objects.get(employee=a, date=MONDAY).shift, self.day)
        self.assertEqual(EmployeeRosterDay.objects.get(employee=b, date=MONDAY).shift, self.night)

    def test_the_split_is_balanced_inside_every_department(self):
        from .services.shift_plans import split_groups

        other = Department.objects.create(name="Scrap")
        more = [Employee.objects.create(employee_id=f"S{i:03d}", first_name="S", last_name=str(i), department=other) for i in range(4)]
        group_a, group_b = split_groups(self.people + more)
        for dept in (self.dept, other):
            in_a = sum(1 for e in group_a if e.department_id == dept.pk)
            in_b = sum(1 for e in group_b if e.department_id == dept.pk)
            self.assertLessEqual(abs(in_a - in_b), 1)
        self.assertEqual(len(group_a) + len(group_b), 9)
        self.assertLessEqual(abs(len(group_a) - len(group_b)), 1)  # the odd leftovers alternate, so the whole is balanced too

    def test_the_list_shows_who_is_on_days_this_week_and_the_counts(self):
        self.client.post("/api/attendance/shift-plans/assign/", {"plan": self.rotation.pk, "group": "A", "department_ids": [self.dept.pk]}, format="json")
        data = self.client.get("/api/attendance/shift-plans/").json()
        rotation = next(p for p in data["results"] if p["kind"] == "rotation")
        self.assertEqual((rotation["members"], rotation["group_a"], rotation["group_b"]), (5, 5, 0))
        self.assertIn(rotation["this_week"]["day_group"], ("A", "B"))
        self.assertEqual((data["on_a_plan"], data["active_employees"]), (5, 5))

    def test_permissions_and_bad_requests(self):
        self.assertEqual(self.client.post("/api/attendance/shift-plans/assign/", {"plan": self.rotation.pk, "group": "A"}, format="json").status_code, 400)  # nobody chosen
        self.assertEqual(self.client.post("/api/attendance/shift-plans/assign/", {"plan": self.rotation.pk, "department_ids": [self.dept.pk]}, format="json").status_code, 400)  # rotation without a group
        outsider = APIClient()
        outsider.force_authenticate(get_user_model().objects.create_user("nobody", password="pw"))
        self.assertEqual(outsider.post("/api/attendance/shift-plans/assign/", {"plan": self.rotation.pk, "group": "A", "everyone": True}, format="json").status_code, 403)

    def test_employee_shift_plan_reflects_the_real_current_plan_and_todays_actual_shift(self):
        """The per-employee endpoint must show the true, live plan/group - not a static shift that never
        updates when a rotation flips Day/Night week to week."""
        person = self.people[0]
        # No plan yet.
        empty = self.client.get(f"/api/attendance/shift-plans/for-employee/{person.pk}/").json()
        self.assertIsNone(empty["plan"])

        self.client.post("/api/attendance/shift-plans/assign/", {"plan": self.rotation.pk, "group": "A", "department_ids": [self.dept.pk], "start_date": MONDAY.isoformat()}, format="json")
        data = self.client.get(f"/api/attendance/shift-plans/for-employee/{person.pk}/").json()
        self.assertEqual(data["plan"]["name"], self.rotation.name)
        self.assertEqual(data["plan"]["group"], "A")
        # today's actual roster shift is included too, not just the plan
        self.assertIn(data["today"]["status"], ("work", "rest"))


class LiveDashboardTests(TestCase):
    """Punches show on the Workforce Operations dashboard the same day, and nobody is 'absent' while their shift is still running."""

    def setUp(self):
        from django.utils import timezone as tz

        from .models import AttendanceEvent, BiometricDevice
        from .services.processing import process_attendance_for_date

        self.tz, self.AttendanceEvent, self.process = tz, AttendanceEvent, process_attendance_for_date
        today = tz.localdate()
        self.always = Shift.objects.create(name="All Day", start_time=time(0, 0), end_time=time(23, 59))  # covers "right now" whenever this runs
        self.device = BiometricDevice.objects.create(name="Gate", serial_number="LIVE1", purpose="attendance", is_online=True)
        self.people = [Employee.objects.create(employee_id=f"L{i}", first_name="L", last_name=str(i)) for i in range(3)]
        for person in self.people:
            EmployeeRosterDay.objects.create(employee=person, date=today, status="work", shift=self.always)
        self.today = today

    def test_a_punch_shows_as_present_and_the_others_as_not_in_yet_not_absent(self):
        from .services.dashboard import DashboardService

        self.AttendanceEvent.objects.create(employee=self.people[0], device=self.device, timestamp=self.tz.now())
        self.process(self.today)
        data = DashboardService.get_dashboard()
        self.assertEqual(data["morning"]["present"] + data["morning"]["late"], 1)
        self.assertEqual(data["morning"]["absent"], 0)  # the shift has not ended
        self.assertEqual((data["morning"]["expected"], data["morning"]["not_yet_in"]), (3, 2))
        self.assertEqual(data["recent_events"][0]["employee_number"], "L0")
        self.assertEqual(data["recent_events"][0]["device"], "Gate")
        self.assertEqual(data["device_status"][0]["name"], "Gate")


class SalesAndPlanningScheduleTests(TestCase):
    """The Sales & Planning team's work schedule (2026-10-02): own day off each, two of them swap weekly."""

    FRIDAY = date(2026, 10, 2)

    def setUp(self):
        from django.core.management import call_command

        self.department = Department.objects.create(name="Planning")
        self.people = {
            number: Employee.objects.create(employee_id=number, first_name=name, last_name="Test", department=self.department)
            for number, name in (("001142", "Franklyn"), ("000817", "Peter"), ("000753", "Isaac"), ("000982", "Faith"))
        }
        self.old_day, _ = Shift.objects.get_or_create(name="Day Shift", defaults={"start_time": time(7), "end_time": time(19)})
        self.old_plan, _ = ShiftPlan.objects.get_or_create(name="Permanent Day (Mon-Sat)", defaults={"kind": "fixed", "shift": self.old_day, "working_weekdays": [0, 1, 2, 3, 4, 5]})
        assign_plan(list(self.people.values()), self.old_plan, start_date=date(2026, 9, 28))
        call_command("setup_sales_planning_shifts", "--start", "2026-10-02")

    def day(self, number, when):
        row = EmployeeRosterDay.objects.get(employee=self.people[number], date=when)
        return (row.status, row.shift.name if row.shift else None)

    def week(self, number, monday):
        return [self.day(number, monday + timedelta(days=offset)) for offset in range(7)]

    def test_the_shifts_have_the_hours_on_the_schedule(self):
        morning = Shift.objects.get(name="Sales & Planning Morning (7AM-6PM)")
        afternoon = Shift.objects.get(name="Sales & Planning Afternoon (11AM-10PM)")
        self.assertEqual((morning.start_time, morning.end_time, morning.is_overnight), (time(7), time(18), False))
        self.assertEqual((afternoon.start_time, afternoon.end_time, afternoon.is_overnight), (time(11), time(22), False))

    def test_franklyn_works_mornings_and_is_off_on_wednesday(self):
        week = self.week("001142", date(2026, 10, 5))
        self.assertEqual(week[2], ("rest", None))  # Wednesday
        self.assertEqual({shift for status, shift in week if status == "work"}, {"Sales & Planning Morning (7AM-6PM)"})
        self.assertEqual(sum(1 for status, _ in week if status == "work"), 5)

    def test_peter_works_afternoons_and_is_off_on_thursday(self):
        week = self.week("000817", date(2026, 10, 5))
        self.assertEqual(week[3], ("rest", None))  # Thursday
        self.assertEqual({shift for status, shift in week if status == "work"}, {"Sales & Planning Afternoon (11AM-10PM)"})

    def test_isaac_is_on_morning_this_week_with_tuesday_off_then_swaps_to_afternoon(self):
        this_week, next_week = self.week("000753", date(2026, 9, 28)), self.week("000753", date(2026, 10, 5))
        self.assertEqual(self.day("000753", self.FRIDAY), ("work", "Sales & Planning Morning (7AM-6PM)"))
        self.assertEqual(next_week[1], ("rest", None))  # Tuesday off
        self.assertEqual({shift for status, shift in next_week if status == "work"}, {"Sales & Planning Afternoon (11AM-10PM)"})
        self.assertEqual(self.day("000753", date(2026, 10, 12)), ("work", "Sales & Planning Morning (7AM-6PM)"))  # and back again
        self.assertEqual(this_week[4], ("work", "Sales & Planning Morning (7AM-6PM)"))

    def test_faith_is_on_afternoon_this_week_with_friday_off_then_swaps_to_morning(self):
        self.assertEqual(self.day("000982", self.FRIDAY), ("rest", None))  # today is her Friday off
        self.assertEqual(self.day("000982", date(2026, 10, 3)), ("work", "Sales & Planning Afternoon (11AM-10PM)"))  # Saturday, still this week
        next_week = self.week("000982", date(2026, 10, 5))
        self.assertEqual(next_week[4], ("rest", None))
        self.assertEqual({shift for status, shift in next_week if status == "work"}, {"Sales & Planning Morning (7AM-6PM)"})

    def test_isaac_and_faith_are_always_on_opposite_shifts(self):
        for offset in range(0, 56):
            when = date(2026, 10, 5) + timedelta(days=offset)
            isaac, faith = self.day("000753", when), self.day("000982", when)
            if isaac[0] == "work" and faith[0] == "work":
                self.assertNotEqual(isaac[1], faith[1], when)

    def test_nobody_is_rostered_on_a_sunday(self):
        for number in self.people:
            self.assertEqual(self.day(number, date(2026, 10, 4)), ("rest", None))

    def test_running_the_setup_again_changes_nothing(self):
        from django.core.management import call_command

        before = ShiftPlanAssignment.objects.count()
        call_command("setup_sales_planning_shifts", "--start", "2026-10-02")
        self.assertEqual(ShiftPlanAssignment.objects.count(), before)
        self.assertEqual(ShiftPlan.objects.filter(name__startswith="Sales & Planning").count(), 4)

    def test_history_before_the_start_date_is_not_rewritten(self):
        self.assertEqual(self.day("000817", date(2026, 9, 30)), ("work", "Day Shift"))

    def test_an_alternating_plan_needs_a_group(self):
        plan = ShiftPlan.objects.get(name__contains="off Tuesday")
        with self.assertRaisesMessage(ValueError, "Group"):
            assign_plan([self.people["000753"]], plan, group="", start_date=date(2026, 10, 5))

    def test_the_groups_can_be_swapped_for_an_alternating_plan(self):
        plan = ShiftPlan.objects.get(name__contains="off Tuesday")
        flip_rotation_week(plan)
        self.assertEqual(day_group_for_week(plan, date(2026, 9, 28)), "B")

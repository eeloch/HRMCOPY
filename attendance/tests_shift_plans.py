from datetime import date, time, timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from rest_framework.test import APIClient

from employees.models import Department, Employee

from .models import EmployeeRosterDay, RosterDaySource, Shift, ShiftPlan, ShiftPlanAssignment
from .services.shift_plans import assign_plan, day_group_for_week, extend_rosters, flip_rotation_week, planned_day, set_day_off

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


class WeekdayShiftsTests(TestCase):
    """The shift roster of 2026-10-02: Admin plan moves to 7AM-7PM; a new Monday-Friday plan with a half Saturday."""

    MONDAY_NEXT = date(2026, 10, 5)

    def setUp(self):
        from django.core.management import call_command

        self.department = Department.objects.create(name="Admin")
        self.person = Employee.objects.create(employee_id="WK-001", first_name="Ada", last_name="Test", department=self.department)
        self.old_admin, _ = Shift.objects.get_or_create(name="Admin Shift (MON-Friday)", defaults={"start_time": time(8), "end_time": time(18)})
        Shift.objects.filter(pk=self.old_admin.pk).update(start_time=time(8), end_time=time(18))
        self.admin_plan, _ = ShiftPlan.objects.get_or_create(name="Admin Shift (Mon-Friday)", defaults={"kind": "fixed", "shift": self.old_admin, "working_weekdays": [0, 1, 2, 3, 4]})
        ShiftPlan.objects.filter(pk=self.admin_plan.pk).update(shift=self.old_admin, description="")
        assign_plan([self.person], self.admin_plan, start_date=self.MONDAY_NEXT)
        call_command("setup_weekday_shifts")

    def hours(self, employee, when):
        row = EmployeeRosterDay.objects.get(employee=employee, date=when)
        return (row.status, f"{row.shift.start_time:%H:%M}-{row.shift.end_time:%H:%M}") if row.shift else (row.status, None)

    def test_the_admin_plan_is_now_7am_to_7pm_monday_to_friday(self):
        self.old_admin.refresh_from_db()
        self.assertEqual((self.old_admin.start_time, self.old_admin.end_time), (time(7), time(19)))
        # people already on it follow automatically: their roster days point at this shift
        self.assertEqual(self.hours(self.person, self.MONDAY_NEXT), ("work", "07:00-19:00"))
        self.assertEqual(self.hours(self.person, date(2026, 10, 10)), ("rest", None))  # Saturday still off on this plan

    def test_the_new_plan_has_a_half_saturday(self):
        plan = ShiftPlan.objects.get(name="Mon-Fri 7AM-7PM + Half Saturday (7AM-3PM)")
        other = Employee.objects.create(employee_id="WK-002", first_name="Bola", last_name="Test", department=self.department)
        assign_plan([other], plan, start_date=self.MONDAY_NEXT)
        week = [self.hours(other, self.MONDAY_NEXT + timedelta(days=offset)) for offset in range(7)]
        self.assertEqual(week[:5], [("work", "07:00-19:00")] * 5)
        self.assertEqual(week[5], ("work", "07:00-15:00"))  # Saturday
        self.assertEqual(week[6], ("rest", None))  # Sunday

    def test_a_plan_without_a_saturday_shift_is_unchanged(self):
        plan = ShiftPlan.objects.create(name="Plain six days", kind="fixed", shift=self.old_admin, working_weekdays=[0, 1, 2, 3, 4, 5])
        status, shift = planned_day(plan, "", date(2026, 10, 10))
        self.assertEqual((status, shift), ("work", self.old_admin))

    def test_nobody_is_assigned_to_the_new_plan_and_running_again_changes_nothing(self):
        from django.core.management import call_command

        plan = ShiftPlan.objects.get(name="Mon-Fri 7AM-7PM + Half Saturday (7AM-3PM)")
        self.assertEqual(plan.assignments.count(), 0)
        call_command("setup_weekday_shifts")
        self.assertEqual(ShiftPlan.objects.filter(name=plan.name).count(), 1)
        self.assertEqual(Shift.objects.filter(name__in=["Standard Day Shift (7AM-7PM)", "Half Saturday Shift (7AM-3PM)"]).count(), 2)


class PersonalDayOffTests(PlanTestCase):
    """A personal weekly day off sits on top of whatever plan the person is on (2026-10-02 request)."""

    WEDNESDAY = 2
    START = date(2026, 10, 1)

    def person(self, number="OFF-001"):
        return Employee.objects.create(employee_id=number, first_name="Off", last_name="Day")

    def status(self, employee, when):
        row = EmployeeRosterDay.objects.get(employee=employee, date=when)
        return row.status, row.shift.name if row.shift else None

    def test_the_day_off_is_a_rest_day_on_a_fixed_plan_and_the_other_days_are_unchanged(self):
        employee = self.person()
        assign_plan([employee], self.perm_day, start_date=date(2026, 9, 28))
        set_day_off([employee], self.WEDNESDAY, self.START)
        self.assertEqual(self.status(employee, date(2026, 10, 7)), ("rest", None))
        self.assertEqual(self.status(employee, date(2026, 10, 8)), ("work", "Day"))
        self.assertEqual(self.status(employee, date(2026, 10, 14)), ("rest", None))  # every week

    def test_it_works_on_the_day_night_rotation_too(self):
        employee = self.person("OFF-002")
        assign_plan([employee], self.rotation, group="A", start_date=date(2026, 9, 28))
        set_day_off([employee], 3, self.START)  # Thursday
        self.assertEqual(self.status(employee, date(2026, 10, 8))[0], "rest")
        self.assertEqual(self.status(employee, date(2026, 10, 9))[0], "work")

    def test_history_before_the_start_date_is_kept_and_the_old_assignment_is_closed(self):
        employee = self.person("OFF-003")
        assign_plan([employee], self.perm_day, start_date=date(2026, 9, 21))
        set_day_off([employee], self.WEDNESDAY, self.START)
        self.assertEqual(self.status(employee, date(2026, 9, 23)), ("work", "Day"))  # a Wednesday before the start
        first, second = ShiftPlanAssignment.objects.filter(employee=employee).order_by("start_date")
        self.assertEqual((first.end_date, first.day_off), (date(2026, 9, 30), None))
        self.assertEqual((second.start_date, second.day_off, second.plan_id), (self.START, self.WEDNESDAY, self.perm_day.pk))

    def test_a_false_absence_on_the_new_day_off_is_cleared_but_real_attendance_is_kept(self):
        from django.utils import timezone

        from .models import AttendanceException, DailyAttendance

        absent, worked = self.person("OFF-004"), self.person("OFF-005")
        for employee in (absent, worked):
            assign_plan([employee], self.perm_day, start_date=date(2026, 9, 28))
        thursday = date(2026, 10, 1)
        missed = DailyAttendance.objects.create(employee=absent, date=thursday, status="absent", shift=self.day)
        AttendanceException.objects.create(attendance=missed, exception_type="absence", status="pending")
        came = DailyAttendance.objects.create(employee=worked, date=thursday, status="late", shift=self.day, late_minutes=16, actual_clock_in=timezone.make_aware(__import__("datetime").datetime(2026, 10, 1, 7, 16)))

        result = set_day_off([absent, worked], 3, self.START)  # Thursday off for both

        self.assertEqual(result["absences_cleared"], [("OFF-004", thursday)])
        self.assertFalse(DailyAttendance.objects.filter(pk=missed.pk).exists())
        self.assertFalse(AttendanceException.objects.filter(attendance_id=missed.pk).exists())
        self.assertTrue(DailyAttendance.objects.filter(pk=came.pk).exists())

    def test_the_day_off_follows_the_person_to_a_new_plan_unless_changed(self):
        employee = self.person("OFF-006")
        assign_plan([employee], self.perm_day, start_date=date(2026, 9, 28))
        set_day_off([employee], self.WEDNESDAY, self.START)
        assign_plan([employee], self.admin, start_date=date(2026, 10, 12))
        self.assertEqual(self.status(employee, date(2026, 10, 14)), ("rest", None))
        self.assertEqual(ShiftPlanAssignment.objects.get(employee=employee, start_date=date(2026, 10, 12)).day_off, self.WEDNESDAY)
        assign_plan([employee], self.admin, start_date=date(2026, 10, 19), day_off=None)
        self.assertEqual(self.status(employee, date(2026, 10, 21))[0], "work")

    def test_the_day_off_can_be_removed_and_a_bad_value_is_refused(self):
        employee = self.person("OFF-007")
        assign_plan([employee], self.perm_day, start_date=date(2026, 9, 28))
        set_day_off([employee], self.WEDNESDAY, self.START)
        set_day_off([employee], None, date(2026, 10, 12))
        self.assertEqual(self.status(employee, date(2026, 10, 7))[0], "rest")   # before the removal date
        self.assertEqual(self.status(employee, date(2026, 10, 14))[0], "work")  # after it
        with self.assertRaises(ValueError):
            set_day_off([employee], 9, self.START)

    def test_someone_with_no_plan_is_reported_not_guessed(self):
        employee = self.person("OFF-008")
        self.assertEqual(set_day_off([employee], 2, self.START)["no_plan"], ["OFF-008"])

    def test_a_meal_on_a_day_off_is_not_an_entitled_meal(self):
        from datetime import datetime

        from django.utils import timezone

        from meals.models import EmployeeMealEntitlement, MealCollectionStatus, MealDevice, MealExcessException, MealTicketRate
        from meals.services import MealService
        from employees.models import BiometricIdentity

        employee = self.person("OFF-009")
        assign_plan([employee], self.perm_day, start_date=date(2026, 9, 28))
        set_day_off([employee], self.WEDNESDAY, self.START)
        wednesday = date(2026, 10, 7)
        EmployeeMealEntitlement.objects.create(employee=employee, tickets_per_work_day=2, effective_from=date(2026, 9, 1), reason="test")
        MealTicketRate.objects.create(amount=700, effective_from=date(2026, 9, 1))
        MealDevice.objects.create(name="Off day device", serial_number="OFFDEV1", active=True)
        BiometricIdentity.objects.create(employee=employee, system="device", source_identifier="OFFDEV1", external_user_id="9001", is_active=True)
        collection, _ = MealService.ingest(system="device", source_identifier="OFFDEV1", device_serial_number="OFFDEV1", external_user_id="9001", external_event_id="E-OFF-1", timestamp=timezone.make_aware(datetime(2026, 10, 7, 13, 0)))
        self.assertEqual((collection.entitlement_snapshot, collection.status), (0, MealCollectionStatus.REST_DAY))
        self.assertTrue(MealExcessException.objects.filter(employee=employee, work_date=wednesday).exists())  # flagged for review


class DayOffApiTests(PlanTestCase):
    def setUp(self):
        super().setUp()
        self.user = get_user_model().objects.create_user(username="dayoff-manager", password="pw")
        self.user.user_permissions.add(Permission.objects.get(codename="manage_shifts"))
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.department = Department.objects.create(name="Off Dept")
        self.employee = Employee.objects.create(employee_id="API-OFF-1", first_name="Api", last_name="Off", department=self.department)

    def test_assigning_a_plan_with_a_day_off(self):
        response = self.client.post("/api/attendance/shift-plans/assign/", {"plan": self.perm_day.pk, "employee_ids": [self.employee.pk], "start_date": "2026-10-05", "day_off": 3}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(ShiftPlanAssignment.objects.get(employee=self.employee).day_off, 3)
        self.assertEqual(EmployeeRosterDay.objects.get(employee=self.employee, date=date(2026, 10, 8)).status, "rest")

    def test_a_bad_day_off_is_refused(self):
        response = self.client.post("/api/attendance/shift-plans/assign/", {"plan": self.perm_day.pk, "employee_ids": [self.employee.pk], "day_off": 9}, format="json")
        self.assertEqual(response.status_code, 400)

    def test_setting_a_day_off_for_people_already_on_a_plan(self):
        assign_plan([self.employee], self.perm_day, start_date=date(2026, 10, 1))
        response = self.client.post("/api/attendance/shift-plans/day-off/", {"employee_ids": [self.employee.pk], "day_off": 2, "start_date": "2026-10-05"}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(EmployeeRosterDay.objects.get(employee=self.employee, date=date(2026, 10, 7)).status, "rest")
        removed = self.client.post("/api/attendance/shift-plans/day-off/", {"employee_ids": [self.employee.pk], "day_off": "none", "start_date": "2026-10-12"}, format="json")
        self.assertEqual(removed.status_code, 200)
        self.assertEqual(EmployeeRosterDay.objects.get(employee=self.employee, date=date(2026, 10, 14)).status, "work")

    def test_someone_without_a_plan_gets_a_clear_message(self):
        response = self.client.post("/api/attendance/shift-plans/day-off/", {"employee_ids": [self.employee.pk], "day_off": 2}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("not on a shift plan", response.json()["detail"])

    def test_it_needs_the_shift_management_permission(self):
        plain = get_user_model().objects.create_user(username="dayoff-plain", password="pw")
        client = APIClient()
        client.force_authenticate(plain)
        self.assertEqual(client.post("/api/attendance/shift-plans/day-off/", {"employee_ids": [self.employee.pk], "day_off": 2}, format="json").status_code, 403)

    def test_the_employee_shift_plan_shows_the_day_off(self):
        assign_plan([self.employee], self.perm_day, start_date=date(2026, 10, 1), day_off=1)
        data = self.client.get(f"/api/attendance/shift-plans/for-employee/{self.employee.pk}/").json()
        self.assertEqual(data["plan"]["day_off"], 1)


class WeekShiftsLineTests(PlanTestCase):
    """The shift panel says which shift a rotating person works this week and next."""

    def setUp(self):
        super().setUp()
        from django.utils import timezone

        from .services.shift_plans import monday_of

        self.user = get_user_model().objects.create_user(username="weeks-viewer", password="pw")
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.department = Department.objects.create(name="Weeks Dept")
        self.monday = monday_of(timezone.localdate())
        # a rotation whose reference week is this week: Group A is on Day now and on Night next week
        ShiftPlan.objects.filter(pk=self.rotation.pk).update(anchor_monday=self.monday)
        self.rotation.refresh_from_db()

    def person(self, number):
        return Employee.objects.create(employee_id=number, first_name="Wk", last_name=number, department=self.department)

    def weeks(self, employee):
        return self.client.get(f"/api/attendance/shift-plans/for-employee/{employee.pk}/").json()["weeks"]

    def test_a_rotating_person_sees_this_week_and_next_week(self):
        group_a, group_b = self.person("WK-A"), self.person("WK-B")
        assign_plan([group_a], self.rotation, group="A", start_date=self.monday)
        assign_plan([group_b], self.rotation, group="B", start_date=self.monday)
        a, b = self.weeks(group_a), self.weeks(group_b)
        self.assertEqual((a["this_week"]["shift"], a["next_week"]["shift"]), ("Day", "Night"))
        self.assertEqual((b["this_week"]["shift"], b["next_week"]["shift"]), ("Night", "Day"))
        self.assertEqual(a["next_week"]["monday"], (self.monday + timedelta(days=7)).isoformat())

    def test_a_person_on_a_fixed_plan_has_no_weekly_line(self):
        fixed = self.person("WK-F")
        assign_plan([fixed], self.perm_day, start_date=self.monday)
        self.assertIsNone(self.weeks(fixed))

    def test_a_rotating_person_with_the_first_day_off_still_gets_the_weeks_shift(self):
        off = self.person("WK-O")
        assign_plan([off], self.rotation, group="A", start_date=self.monday, day_off=0)  # Monday off
        self.assertEqual(self.weeks(off)["this_week"]["shift"], "Day")


class SwapRotationGroupsTests(PlanTestCase):
    """Swapping the groups starts at the next shift week, so nobody goes from a night shift straight onto a day shift."""

    def setUp(self):
        super().setUp()
        self.dept = Department.objects.create(name="Swap Dept")
        self.a = Employee.objects.create(employee_id="SW-A", first_name="A", last_name="Group", department=self.dept)
        self.b = Employee.objects.create(employee_id="SW-B", first_name="B", last_name="Group", department=self.dept)
        # a Saturday inside a week where Group A is on Night (the week after the anchor week)
        self.saturday = MONDAY + timedelta(days=7 + 5)
        ShiftPlan.objects.filter(pk=self.rotation.pk).update(anchor_monday=MONDAY)
        assign_plan([self.a], self.rotation, group="A", start_date=MONDAY)
        assign_plan([self.b], self.rotation, group="B", start_date=MONDAY)

    def shift(self, employee, when):
        row = EmployeeRosterDay.objects.get(employee=employee, date=when)
        return (row.status, row.shift.name if row.shift else None)

    def test_the_next_shift_week_starts_on_the_coming_sunday(self):
        from .services.shift_plans import next_shift_week_start

        self.assertEqual(next_shift_week_start(date(2026, 10, 3)), date(2026, 10, 4))   # Saturday -> tomorrow
        self.assertEqual(next_shift_week_start(date(2026, 10, 5)), date(2026, 10, 11))  # Monday -> the Sunday after the week
        self.assertEqual(next_shift_week_start(date(2026, 10, 4)), date(2026, 10, 4))   # a Sunday -> that night

    def test_the_swap_leaves_this_week_alone_and_flips_the_next(self):
        from .services.shift_plans import monday_of

        this_monday = monday_of(date(2026, 10, 1))  # anchor week 2: week 2 after MONDAY=Sep 21 is Oct 5? use explicit dates below
        saturday, sunday, monday = date(2026, 10, 3), date(2026, 10, 4), date(2026, 10, 5)
        before = {name: (self.shift(emp, saturday), self.shift(emp, sunday), self.shift(emp, monday)) for name, emp in (("a", self.a), ("b", self.b))}
        flip_rotation_week(self.rotation, from_date=sunday)
        after = {name: (self.shift(emp, saturday), self.shift(emp, sunday), self.shift(emp, monday)) for name, emp in (("a", self.a), ("b", self.b))}
        self.assertEqual(before["a"][0], after["a"][0])  # Saturday untouched
        self.assertEqual(before["b"][0], after["b"][0])
        self.assertEqual(after["a"][2], before["b"][2])  # from Monday each group works what the other was going to
        self.assertEqual(after["b"][2], before["a"][2])
        self.assertEqual(after["a"][1], before["b"][1])  # and Sunday night starts the new week
        self.assertIsNotNone(this_monday)

    def test_swapping_twice_puts_everything_back(self):
        snapshot = lambda: [(row.date, row.shift_id, row.status) for row in EmployeeRosterDay.objects.filter(employee=self.a, date__gte=date(2026, 10, 4), date__lt=date(2027, 1, 1)).order_by("date")]  # noqa: E731
        before = snapshot()
        flip_rotation_week(self.rotation, from_date=date(2026, 10, 4))
        self.assertNotEqual(snapshot(), before)
        flip_rotation_week(self.rotation, from_date=date(2026, 10, 4))
        self.assertEqual(snapshot(), before)

    def test_the_api_swaps_from_a_chosen_date_and_refuses_a_bad_one(self):
        user = get_user_model().objects.create_user(username="swap-manager", password="pw")
        user.user_permissions.add(Permission.objects.get(codename="manage_shifts"))
        client = APIClient()
        client.force_authenticate(user)
        bad = client.post(f"/api/attendance/shift-plans/{self.rotation.pk}/flip-week/", {"start_date": "not-a-date"}, format="json")
        self.assertEqual(bad.status_code, 400)
        ok = client.post(f"/api/attendance/shift-plans/{self.rotation.pk}/flip-week/", {"start_date": "2026-10-04"}, format="json")
        self.assertEqual(ok.status_code, 200)
        self.rotation.refresh_from_db()
        self.assertEqual(self.rotation.anchor_monday, MONDAY + timedelta(days=7))

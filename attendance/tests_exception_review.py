from datetime import date

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from audit.models import AuditEvent
from employees.models import Department, Employee

from .models import AttendanceException, DailyAttendance, Shift


@override_settings(ATTENDANCE_GO_LIVE_DATE="2026-09-23", ATTENDANCE_LATE_ALLOWANCE_MINUTES=15, ATTENDANCE_LONG_LATE_MINUTES=60)
class ExceptionReviewTests(TestCase):
    """The review queue: server-side filters, select-everything-that-matches, bulk decisions and ready-made rules."""

    def setUp(self):
        self.reviewer = get_user_model().objects.create_user(username="exc-reviewer", password="pw")
        self.reviewer.user_permissions.add(Permission.objects.get(codename="review_attendanceexception"))
        self.viewer = get_user_model().objects.create_user(username="exc-viewer", password="pw")
        self.client = APIClient()
        self.client.force_authenticate(self.reviewer)
        self.packaging = Department.objects.create(name="Packaging")
        self.scrap = Department.objects.create(name="Scrap")
        self.shift = Shift.objects.create(name="Exc Day", start_time="07:00", end_time="19:00")
        self.people = {
            "ada": Employee.objects.create(employee_id="EX-1", first_name="Ada", last_name="Obi", department=self.packaging),
            "bayo": Employee.objects.create(employee_id="EX-2", first_name="Bayo", last_name="Eze", department=self.scrap),
        }

    def case(self, person, day, kind, minutes=0, amount="0"):
        attendance, _ = DailyAttendance.objects.get_or_create(employee=self.people[person], date=day, defaults={"shift": self.shift})
        return AttendanceException.objects.create(attendance=attendance, exception_type=kind, minutes_affected=minutes, proposed_deduction=amount)

    def queue(self, **params):
        return self.client.get("/api/attendance/exceptions/queue/", params).json()

    def test_the_queue_filters_on_the_server_and_pages(self):
        for day in range(23, 31):
            self.case("ada", date(2026, 9, day), "late", 10 + day)
        self.case("bayo", date(2026, 9, 25), "absence", 720)
        everything = self.queue(page_size=5)
        self.assertEqual((everything["count"], len(everything["results"]), everything["page"]), (9, 5, 1))
        self.assertNotEqual(self.queue(page=2, page_size=5)["results"][0]["id"], everything["results"][0]["id"])
        self.assertEqual(len(self.queue(page=2, page_size=5)["results"]), 4)
        self.assertEqual(self.queue(type="absence")["count"], 1)
        self.assertEqual(self.queue(search="bayo")["count"], 1)
        self.assertEqual(self.queue(department=self.packaging.pk)["count"], 8)
        self.assertEqual(self.queue(date_from="2026-09-28", date_to="2026-09-30")["count"], 3)
        self.assertEqual(self.queue(min_minutes=37, type="late")["count"], 4)
        self.assertEqual(everything["by_type"], {"late": 8, "absence": 1})

    def test_the_total_proposed_deduction_follows_the_filter(self):
        self.case("ada", date(2026, 9, 24), "late", 20, "300.00")
        self.case("ada", date(2026, 9, 25), "absence", 720, "2000.00")
        self.assertEqual(self.queue(type="late")["total_proposed_deduction"], "300.00")
        self.assertEqual(self.queue()["total_proposed_deduction"], "2300.00")

    def test_select_all_matching_returns_every_matching_pending_id(self):
        wanted = [self.case("ada", date(2026, 9, day), "late", 5).pk for day in range(23, 28)]
        self.case("ada", date(2026, 9, 28), "absence", 720)
        done = self.case("bayo", date(2026, 9, 24), "late", 5)
        done.status = "waived"
        done.save()
        ids = self.client.get("/api/attendance/exceptions/queue/ids/", {"type": "late"}).json()["ids"]
        self.assertEqual(sorted(ids), sorted(wanted))

    def test_bulk_decision_decides_each_case_records_the_reviewer_and_audits_it(self):
        first, second = self.case("ada", date(2026, 9, 24), "late", 5), self.case("bayo", date(2026, 9, 24), "late", 8)
        response = self.client.post("/api/attendance/exceptions/bulk-decision/", {"ids": [first.pk, second.pk], "decision": "waived", "comment": "Within allowance"}, format="json")
        self.assertEqual(response.json(), {"decided": 2, "skipped": 0})
        first.refresh_from_db()
        self.assertEqual((first.status, first.admin_comment, first.reviewed_by), ("waived", "Within allowance", "exc-reviewer"))
        self.assertEqual(AuditEvent.objects.filter(event_type="attendance.exception_waived").count(), 2)

    def test_a_case_already_decided_is_skipped_not_overwritten(self):
        case = self.case("ada", date(2026, 9, 24), "late", 5)
        self.client.post("/api/attendance/exceptions/bulk-decision/", {"ids": [case.pk], "decision": "approved"}, format="json")
        again = self.client.post("/api/attendance/exceptions/bulk-decision/", {"ids": [case.pk, 999999], "decision": "waived", "comment": "Changed my mind"}, format="json")
        self.assertEqual(again.json(), {"decided": 0, "skipped": 2})
        case.refresh_from_db()
        self.assertEqual(case.status, "approved")

    def test_waiving_or_holding_needs_a_reason_and_bad_requests_are_refused(self):
        case = self.case("ada", date(2026, 9, 24), "late", 5)
        post = lambda body: self.client.post("/api/attendance/exceptions/bulk-decision/", body, format="json").status_code  # noqa: E731
        self.assertEqual(post({"ids": [case.pk], "decision": "waived"}), 400)
        self.assertEqual(post({"ids": [case.pk], "decision": "held", "comment": " "}), 400)
        self.assertEqual(post({"ids": [], "decision": "approved"}), 400)
        self.assertEqual(post({"ids": [case.pk], "decision": "maybe"}), 400)
        self.assertEqual(post({"ids": list(range(1, 400)), "decision": "approved"}), 400)

    def test_only_reviewers_can_decide_or_select_everything(self):
        case = self.case("ada", date(2026, 9, 24), "late", 5)
        client = APIClient()
        client.force_authenticate(self.viewer)
        self.assertEqual(client.get("/api/attendance/exceptions/queue/").status_code, 200)  # looking is open, as before
        self.assertEqual(client.post("/api/attendance/exceptions/bulk-decision/", {"ids": [case.pk], "decision": "approved"}, format="json").status_code, 403)
        self.assertEqual(client.get("/api/attendance/exceptions/queue/ids/").status_code, 403)
        self.assertEqual(client.get("/api/attendance/exceptions/rules/").status_code, 403)

    def test_an_approved_absence_still_updates_the_meal_penalties(self):
        from unittest import mock

        case = self.case("ada", date(2026, 9, 24), "absence", 720)
        with mock.patch("attendance.views.exception_review.MealService.sync_absence_penalties") as sync:
            self.client.post("/api/attendance/exceptions/bulk-decision/", {"ids": [case.pk], "decision": "approved"}, format="json")
        sync.assert_called_once()

    def rules(self):
        return {rule["key"]: rule for rule in self.client.get("/api/attendance/exceptions/rules/").json()["results"]}

    def test_each_ready_made_rule_matches_exactly_the_cases_it_describes(self):
        self.case("ada", date(2026, 9, 21), "absence", 720)             # before go-live
        self.case("bayo", date(2026, 9, 22), "late", 90)                # before go-live
        self.case("ada", date(2026, 9, 24), "late", 10)                 # within the allowance
        self.case("ada", date(2026, 9, 25), "late", 15)                 # exactly the allowance
        self.case("ada", date(2026, 9, 26), "late", 16)                 # in between: no rule
        self.case("bayo", date(2026, 9, 27), "late", 61)                # long
        self.case("bayo", date(2026, 9, 28), "late", 60)                # exactly 60: not long
        self.case("ada", date(2026, 9, 29), "missing_clock_in")
        self.case("bayo", date(2026, 9, 29), "missing_clock_out")
        self.case("ada", date(2026, 9, 30), "absence", 720)
        rules = self.rules()
        self.assertEqual({key: rule["matching"] for key, rule in rules.items()}, {"before_go_live": 2, "short_lateness": 2, "long_lateness": 1, "missing_punches": 2, "absences_hold": 1})
        self.assertEqual({key: rule["decision"] for key, rule in rules.items()}, {"before_go_live": "waived", "short_lateness": "waived", "long_lateness": "approved", "missing_punches": "held", "absences_hold": "held"})

    def test_applying_a_rule_through_the_ids_and_bulk_endpoints_decides_exactly_its_cases(self):
        short = [self.case("ada", date(2026, 9, 24), "late", 5), self.case("bayo", date(2026, 9, 25), "late", 15)]
        other = self.case("ada", date(2026, 9, 26), "late", 30)
        rule = self.rules()["short_lateness"]
        ids = self.client.get("/api/attendance/exceptions/queue/ids/", rule["filters"]).json()["ids"]
        self.assertEqual(sorted(ids), sorted(case.pk for case in short))
        self.client.post("/api/attendance/exceptions/bulk-decision/", {"ids": ids, "decision": rule["decision"], "comment": rule["comment"]}, format="json")
        other.refresh_from_db()
        self.assertEqual(other.status, "pending")
        self.assertEqual({AttendanceException.objects.get(pk=case.pk).status for case in short}, {"waived"})

    def test_history_shows_decided_cases_newest_first(self):
        old, new = self.case("ada", date(2026, 9, 24), "late", 5), self.case("bayo", date(2026, 9, 25), "late", 5)
        self.client.post("/api/attendance/exceptions/bulk-decision/", {"ids": [old.pk], "decision": "approved"}, format="json")
        self.client.post("/api/attendance/exceptions/bulk-decision/", {"ids": [new.pk], "decision": "approved"}, format="json")
        results = self.queue(status="reviewed")["results"]
        self.assertEqual([item["id"] for item in results], [new.pk, old.pk])


@override_settings(ATTENDANCE_GO_LIVE_DATE="2026-09-23", ATTENDANCE_LATE_ALLOWANCE_MINUTES=15, ATTENDANCE_LONG_LATE_MINUTES=60)
class ApplyExceptionRulesCommandTests(TestCase):
    def setUp(self):
        department = Department.objects.create(name="Cmd Dept")
        self.person = Employee.objects.create(employee_id="CMD-1", first_name="Cee", last_name="Em", department=department)
        self.shift = Shift.objects.create(name="Cmd Day", start_time="07:00", end_time="19:00")

    def case(self, day, kind, minutes=0):
        attendance, _ = DailyAttendance.objects.get_or_create(employee=self.person, date=day, defaults={"shift": self.shift})
        return AttendanceException.objects.create(attendance=attendance, exception_type=kind, minutes_affected=minutes)

    def test_a_dry_run_changes_nothing_and_a_real_run_applies_every_rule(self):
        from io import StringIO

        from django.core.management import call_command

        early = self.case(date(2026, 9, 21), "absence", 720)
        small = self.case(date(2026, 9, 24), "late", 8)
        big = self.case(date(2026, 9, 25), "late", 90)
        punch = self.case(date(2026, 9, 26), "missing_clock_out")
        between = self.case(date(2026, 9, 27), "late", 30)
        out = StringIO()
        call_command("apply_exception_rules", "--dry-run", stdout=out)
        self.assertIn("DRY RUN - 4 case(s) in total.", out.getvalue())
        self.assertEqual(AttendanceException.objects.filter(status="pending").count(), 5)

        call_command("apply_exception_rules", "--by", "Test run", stdout=StringIO())
        statuses = {case.pk: AttendanceException.objects.get(pk=case.pk) for case in (early, small, big, punch, between)}
        self.assertEqual([statuses[early.pk].status, statuses[small.pk].status, statuses[big.pk].status, statuses[punch.pk].status, statuses[between.pk].status], ["waived", "waived", "approved", "held", "pending"])
        self.assertEqual(statuses[small.pk].reviewed_by, "Test run")
        self.assertTrue(AuditEvent.objects.filter(event_type="attendance.exception_waived").exists())

    def test_one_rule_can_be_chosen_and_a_wrong_name_is_refused(self):
        from django.core.management import CommandError, call_command

        small = self.case(date(2026, 9, 24), "late", 8)
        absent = self.case(date(2026, 9, 25), "absence", 720)
        call_command("apply_exception_rules", "--rule", "short_lateness", stdout=__import__("io").StringIO())
        self.assertEqual((AttendanceException.objects.get(pk=small.pk).status, AttendanceException.objects.get(pk=absent.pk).status), ("waived", "pending"))
        with self.assertRaises(CommandError):
            call_command("apply_exception_rules", "--rule", "nonsense")

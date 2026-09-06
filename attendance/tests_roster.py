from datetime import date

from django.contrib.auth.models import Permission, User
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from rest_framework.test import APIClient

from attendance.models import DailyAttendance, EmployeeRosterDay, Shift, ShiftAssignment
from attendance.services.processing import process_employee_attendance
from attendance.services.roster import IncompleteRosterError, RosterGenerationConflict, expected_attendance_days, generate_rotation_roster, generate_roster, roster_completeness
from audit.models import AuditEvent
from employees.models import Employee
from leave.models import LeaveRequest, LeaveStatus, LeaveType


class RosterServiceTests(TestCase):
    def setUp(self):
        self.employee = Employee.objects.create(employee_id="ROSTER-001", first_name="Roster", last_name="Employee")
        self.day = Shift.objects.create(name="Roster Day", start_time="07:00", end_time="19:00")
        self.night = Shift.objects.create(name="Roster Night", start_time="19:00", end_time="07:00", is_overnight=True)
        self.start = date(2031, 1, 1)

    def test_unique_and_valid_status_shift_combinations(self):
        EmployeeRosterDay.objects.create(employee=self.employee, date=self.start, status="work", shift=self.day)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                EmployeeRosterDay.objects.create(employee=self.employee, date=self.start, status="rest")
        invalid = EmployeeRosterDay(employee=self.employee, date=date(2031, 1, 2), status="work")
        with self.assertRaises(ValidationError):
            invalid.full_clean()
        invalid = EmployeeRosterDay(employee=self.employee, date=date(2031, 1, 3), status="rest", shift=self.day)
        with self.assertRaises(ValidationError):
            invalid.full_clean()

    def test_explicit_six_work_one_rest_generation_and_expected_days(self):
        summary = generate_roster(self.employee, self.start, date(2031, 1, 7), self.day, 6, 1)
        self.assertEqual(summary.created, 7)
        self.assertEqual(EmployeeRosterDay.objects.get(employee=self.employee, date=date(2031, 1, 7)).status, "rest")
        self.assertEqual(expected_attendance_days(self.employee, self.start, date(2031, 1, 7)), 6)
        self.assertTrue(AuditEvent.objects.filter(event_type="attendance.roster_generated").exists())

    def test_generation_preserves_override_and_incomplete_range_is_never_guessed(self):
        generate_roster(self.employee, self.start, date(2031, 1, 7), self.day, 6, 1)
        row = EmployeeRosterDay.objects.get(employee=self.employee, date=date(2031, 1, 2))
        row.status, row.shift, row.source, row.notes = "rest", None, "override", "Production shutdown"
        row.save()
        generate_roster(self.employee, self.start, date(2031, 1, 7), self.night, 5, 2)
        row.refresh_from_db()
        self.assertEqual((row.status, row.shift, row.source), ("rest", None, "override"))
        EmployeeRosterDay.objects.filter(employee=self.employee, date=date(2031, 1, 7)).delete()
        completeness = roster_completeness(self.employee, self.start, date(2031, 1, 7))
        self.assertFalse(completeness["complete"])
        with self.assertRaises(IncompleteRosterError):
            expected_attendance_days(self.employee, self.start, date(2031, 1, 7))

    def test_rest_roster_day_prevents_absence_and_leave_does_not_change_roster(self):
        ShiftAssignment.objects.create(employee=self.employee, shift=self.day, start_date=self.start)
        EmployeeRosterDay.objects.create(employee=self.employee, date=self.start, status="rest", source="manual")
        self.assertIsNone(process_employee_attendance(self.employee, self.start))
        self.assertFalse(DailyAttendance.objects.filter(employee=self.employee, date=self.start).exists())
        leave_type = LeaveType.objects.create(name="Roster Test Leave", code="ROSTER-LEAVE")
        LeaveRequest.objects.create(request_number="ROSTER-LEAVE-1", employee=self.employee, leave_type=leave_type, start_date=self.start, end_date=self.start, approved_start_date=self.start, approved_end_date=self.start, total_days=1, approved_days=1, reason="Leave", status=LeaveStatus.APPROVED)
        self.assertEqual(EmployeeRosterDay.objects.get(employee=self.employee, date=self.start).status, "rest")

    def test_standard_day_weekdays_create_monday_to_saturday_work_and_sunday_rest(self):
        start = date(2031, 1, 6)  # Monday
        end = date(2031, 1, 12)  # Sunday

        generate_roster(
            self.employee,
            start,
            end,
            self.day,
            6,
            1,
            working_weekdays=[0, 1, 2, 3, 4, 5],
        )

        rows = EmployeeRosterDay.objects.filter(
            employee=self.employee,
            date__range=(start, end),
        ).order_by("date")
        self.assertEqual(rows.count(), 7)
        self.assertEqual(
            [(row.date.weekday(), row.status, row.shift_id) for row in rows],
            [(weekday, "work", self.day.id) for weekday in range(6)]
            + [(6, "rest", None)],
        )

    def test_sunday_night_roster_is_a_work_day_with_night_shift(self):
        sunday = date(2031, 1, 5)

        generate_roster(
            self.employee,
            sunday,
            date(2031, 1, 11),
            self.night,
            1,
            6,
            working_weekdays=[6],
        )

        row = EmployeeRosterDay.objects.get(employee=self.employee, date=sunday)
        self.assertEqual((row.status, row.shift), ("work", self.night))
        self.assertFalse(
            EmployeeRosterDay.objects.filter(
                employee=self.employee,
                date=date(2031, 1, 6),
                status="work",
            ).exists()
        )

    def test_permanent_day_and_night_rosters_do_not_force_rotation(self):
        day_start = date(2031, 2, 3)  # Monday
        generate_roster(
            self.employee,
            day_start,
            date(2031, 2, 9),
            self.day,
            6,
            1,
            working_weekdays=[0, 1, 2, 3, 4, 5],
        )
        self.assertFalse(
            EmployeeRosterDay.objects.filter(
                employee=self.employee,
                shift=self.night,
            ).exists()
        )

        other = Employee.objects.create(
            employee_id="ROSTER-NIGHT-001",
            first_name="Permanent",
            last_name="Night",
        )
        generate_roster(
            other,
            day_start,
            date(2031, 2, 9),
            self.night,
            7,
            1,
            working_weekdays=[0, 1, 2, 3, 4, 5, 6],
        )
        self.assertEqual(
            set(
                EmployeeRosterDay.objects.filter(employee=other, status="work")
                .values_list("shift_id", flat=True)
            ),
            {self.night.id},
        )

    def test_custom_admin_shift_can_use_selected_working_weekdays(self):
        admin_shift = Shift.objects.create(
            name="Roster Admin Shift",
            start_time="08:00",
            end_time="18:00",
        )
        start = date(2031, 3, 3)  # Monday

        generate_roster(
            self.employee,
            start,
            date(2031, 3, 9),
            admin_shift,
            5,
            2,
            working_weekdays=[0, 1, 2, 3, 4],
        )

        work_rows = EmployeeRosterDay.objects.filter(
            employee=self.employee,
            status="work",
        )
        self.assertEqual(work_rows.count(), 5)
        self.assertEqual(
            set(work_rows.values_list("shift_id", flat=True)),
            {admin_shift.id},
        )

    def test_duplicate_generation_is_safe_and_reports_preserved_conflicts(self):
        start = date(2031, 4, 7)  # Monday
        end = date(2031, 4, 13)
        first = generate_roster(self.employee, start, end, self.day, 6, 1)
        second = generate_roster(self.employee, start, end, self.night, 5, 2)

        self.assertEqual(first.created, 7)
        self.assertEqual(second.created, 0)
        self.assertEqual(second.updated, 6)
        self.assertEqual(second.skipped, 1)
        self.assertEqual(second.conflicts, [])
        self.assertEqual(EmployeeRosterDay.objects.filter(employee=self.employee).count(), 7)

        row = EmployeeRosterDay.objects.get(employee=self.employee, date=start)
        row.source = "manual"
        row.shift = self.day
        row.save(update_fields=["source", "shift"])
        conflict = generate_roster(self.employee, start, start, self.night, 1, 1)
        row.refresh_from_db()
        self.assertEqual(conflict.conflicts, [start.isoformat()])
        self.assertEqual((row.source, row.shift), ("manual", self.day))

    def test_roster_generation_does_not_rewrite_historical_attendance(self):
        historical = DailyAttendance.objects.create(
            employee=self.employee,
            date=self.start,
            shift=self.day,
            status="present",
            worked_minutes=600,
        )

        generate_roster(self.employee, self.start, self.start, self.night, 1, 1)

        historical.refresh_from_db()
        self.assertEqual(
            (historical.status, historical.shift_id, historical.worked_minutes),
            ("present", self.day.id, 600),
        )

    def test_day_start_rotation_generates_day_then_night_changeover(self):
        start = date(2031, 5, 5)  # Monday
        end = date(2031, 5, 19)

        summary = generate_rotation_roster(self.employee, start, end, self.day, self.day, self.night)

        self.assertEqual(summary.created, 15)
        rows = EmployeeRosterDay.objects.filter(employee=self.employee, date__range=(start, end)).order_by("date")
        self.assertEqual(rows.count(), 15)
        self.assertEqual(EmployeeRosterDay.objects.get(employee=self.employee, date=date(2031, 5, 10)).shift, self.day)
        sunday_night = EmployeeRosterDay.objects.get(employee=self.employee, date=date(2031, 5, 11))
        self.assertEqual((sunday_night.status, sunday_night.shift), ("work", self.night))
        self.assertEqual(EmployeeRosterDay.objects.get(employee=self.employee, date=date(2031, 5, 12)).shift, self.night)
        sunday_recovery = EmployeeRosterDay.objects.get(employee=self.employee, date=date(2031, 5, 18))
        self.assertEqual((sunday_recovery.status, sunday_recovery.shift), ("rest", None))
        self.assertEqual(EmployeeRosterDay.objects.get(employee=self.employee, date=date(2031, 5, 19)).shift, self.day)

    def test_night_start_rotation_generates_overnight_nights_and_safe_return(self):
        start = date(2031, 6, 2)  # Monday
        generate_rotation_roster(self.employee, start, date(2031, 6, 16), self.night, self.day, self.night)

        self.assertEqual(EmployeeRosterDay.objects.filter(employee=self.employee, date__range=(start, date(2031, 6, 7)), shift=self.night).count(), 6)
        final_night = EmployeeRosterDay.objects.get(employee=self.employee, date=date(2031, 6, 7))
        self.assertEqual((final_night.status, final_night.shift), ("work", self.night))
        sunday_recovery = EmployeeRosterDay.objects.get(employee=self.employee, date=date(2031, 6, 8))
        self.assertEqual((sunday_recovery.status, sunday_recovery.shift), ("rest", None))
        self.assertNotEqual(sunday_recovery.shift, self.day)
        self.assertNotEqual(sunday_recovery.shift, self.night)
        self.assertEqual(EmployeeRosterDay.objects.get(employee=self.employee, date=date(2031, 6, 15)).shift, self.night)
        self.assertTrue(self.night.is_overnight)

    def test_rotation_is_idempotent_and_rejects_existing_conflicts_atomically(self):
        start = date(2031, 7, 7)  # Monday
        end = date(2031, 7, 20)
        first = generate_rotation_roster(self.employee, start, end, self.day, self.day, self.night)
        second = generate_rotation_roster(self.employee, start, end, self.day, self.day, self.night)
        self.assertEqual((first.created, second.created, second.skipped), (14, 0, 14))

        conflict_date = date(2031, 7, 13)
        row = EmployeeRosterDay.objects.get(employee=self.employee, date=conflict_date)
        row.source = "manual"
        row.shift = self.day
        row.save(update_fields=["source", "shift"])
        with self.assertRaises(RosterGenerationConflict) as error:
            generate_rotation_roster(self.employee, start, end, self.day, self.day, self.night)
        self.assertIn(conflict_date.isoformat(), error.exception.conflicts)
        self.assertEqual(EmployeeRosterDay.objects.filter(employee=self.employee, date__range=(start, end)).count(), 14)


class RosterAPITests(TestCase):
    def setUp(self):
        self.employee = Employee.objects.create(employee_id="ROSTER-API-001", first_name="Roster", last_name="Api")
        self.shift = Shift.objects.create(name="Roster Api Shift", start_time="07:00", end_time="19:00")
        self.admin = User.objects.create_superuser("roster-admin", "admin@example.com", "password")
        self.user = User.objects.create_user("roster-user", password="password")
        self.client = APIClient()

    def test_unauthorized_mutation_is_rejected_and_admin_override_is_audited(self):
        self.client.force_authenticate(self.user)
        response = self.client.post("/api/attendance/roster/override/", {"employee": self.employee.id, "date": "2031-02-01", "status": "work", "shift": self.shift.id}, format="json")
        self.assertEqual(response.status_code, 403)
        response = self.client.post(
            "/api/attendance/roster/generate/",
            {
                "employee": self.employee.id,
                "start_date": "2031-02-02",
                "end_date": "2031-02-08",
                "shift": self.shift.id,
                "work_days": 6,
                "rest_days": 1,
                "working_weekdays": [0, 1, 2, 3, 4, 5],
            },
            format="json",
        )
        self.assertEqual(response.status_code, 403)
        self.client.force_authenticate(self.admin)
        response = self.client.post(
            "/api/attendance/roster/generate/",
            {
                "employee": self.employee.id,
                "start_date": "2031-02-02",
                "end_date": "2031-02-08",
                "shift": self.shift.id,
                "work_days": 6,
                "rest_days": 1,
                "working_weekdays": [0, 1, 2, 3, 4, 5],
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        response = self.client.post("/api/attendance/roster/override/", {"employee": self.employee.id, "date": "2031-02-01", "status": "work", "shift": self.shift.id, "notes": "Cover shift"}, format="json")
        self.assertEqual(response.status_code, 201)
        row = EmployeeRosterDay.objects.get(employee=self.employee, date=date(2031, 2, 1))
        self.assertEqual(row.source, "manual")
        self.assertTrue(AuditEvent.objects.filter(event_type="attendance.roster_day_overridden", object_id=row.id).exists())

    def test_rotation_generation_requires_admin(self):
        night = Shift.objects.create(name="Roster API Night", start_time="19:00", end_time="07:00", is_overnight=True)
        payload = {
            "employee": self.employee.id,
            "start_date": "2031-08-04",
            "end_date": "2031-08-10",
            "starting_shift": "day",
            "day_shift": self.shift.id,
            "night_shift": night.id,
        }
        self.client.force_authenticate(self.user)
        self.assertEqual(self.client.post("/api/attendance/roster/rotation/generate/", payload, format="json").status_code, 403)
        self.client.force_authenticate(self.admin)
        response = self.client.post("/api/attendance/roster/rotation/generate/", payload, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(EmployeeRosterDay.objects.filter(employee=self.employee).count(), 7)

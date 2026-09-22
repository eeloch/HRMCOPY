from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from attendance.models import DailyAttendance, Shift
from employees.models import Department, Employee, EmploymentType
from leave.models import LeaveDuration, LeavePolicy, LeaveRequest, LeaveStatus, LeaveType
from meals.models import MealCollectionStatus, MealDevice, MealEvent
from meals.services import MealCollection

from .services import build_weekly_report, monday_of


class MondayOfTests(TestCase):
    def test_monday_of_a_wednesday_is_that_weeks_monday(self):
        self.assertEqual(monday_of(date(2026, 9, 23)), date(2026, 9, 21))

    def test_monday_of_a_monday_is_itself(self):
        self.assertEqual(monday_of(date(2026, 9, 21)), date(2026, 9, 21))


class WeeklyReportDataTests(TestCase):
    """The numbers behind the weekly report - each figure checked against data planted for one specific
    week, so a query that quietly drifted outside that week's boundary would fail here."""

    WEEK_START = date(2026, 9, 21)  # a Monday

    def setUp(self):
        self.department = Department.objects.create(name="Extrusion")
        self.day_shift = Shift.objects.create(name="Report Day", start_time=time(7), end_time=time(19))
        self.night_shift = Shift.objects.create(name="Report Night", start_time=time(19), end_time=time(7), is_overnight=True)

        self.employee = Employee.objects.create(
            employee_id="RPT-001", first_name="Report", last_name="Subject", department=self.department,
            status="active", employment_type=EmploymentType.PERMANENT,
        )
        Employee.objects.create(employee_id="RPT-002", first_name="Inactive", last_name="One", status="inactive")

    def test_workforce_counts(self):
        data = build_weekly_report(self.WEEK_START)
        self.assertEqual(data.total_employees, 2)
        self.assertEqual(data.active_employees, 1)
        self.assertEqual(data.inactive_employees, 1)
        self.assertEqual({row.name: row.count for row in data.by_department}, {"Extrusion": 1})

    def test_new_hires_and_exits_are_scoped_to_the_week(self):
        Employee.objects.create(employee_id="RPT-HIRE-IN", first_name="In", last_name="Week", status="active", employment_date=self.WEEK_START + timedelta(days=2))
        Employee.objects.create(employee_id="RPT-HIRE-OUT", first_name="Out", last_name="OfWeek", status="active", employment_date=self.WEEK_START - timedelta(days=10))
        Employee.objects.create(employee_id="RPT-EXIT-IN", first_name="Exit", last_name="ThisWeek", status="inactive", exit_date=self.WEEK_START + timedelta(days=3))

        data = build_weekly_report(self.WEEK_START)
        self.assertEqual([p.employee_id for p in data.new_hires], ["RPT-HIRE-IN"])
        self.assertEqual([p.employee_id for p in data.exits], ["RPT-EXIT-IN"])

    def test_attendance_is_scoped_to_monday_through_saturday(self):
        DailyAttendance.objects.create(employee=self.employee, date=self.WEEK_START, shift=self.day_shift, status="present")
        DailyAttendance.objects.create(employee=self.employee, date=self.WEEK_START + timedelta(days=2), shift=self.day_shift, status="late")
        DailyAttendance.objects.create(employee=self.employee, date=self.WEEK_START + timedelta(days=5), shift=self.night_shift, status="absent")
        # Sunday (day 6) is outside the reported working week and must not be counted.
        DailyAttendance.objects.create(employee=self.employee, date=self.WEEK_START + timedelta(days=6), shift=self.day_shift, status="present")

        data = build_weekly_report(self.WEEK_START)
        self.assertEqual(data.attendance_present, 1)
        self.assertEqual(data.attendance_late, 1)
        self.assertEqual(data.attendance_absent, 1)
        self.assertEqual(data.night_shift_records, 1)
        self.assertEqual(sum(row.present + row.late + row.absent for row in data.attendance_by_day), 3)
        self.assertEqual(len(data.attendance_by_day), 6)

    def test_leave_counts_by_submission_date_not_leave_date(self):
        leave_type = LeaveType.objects.create(name="Report Annual", code="RPT-ANNUAL")
        LeavePolicy.objects.create(leave_type=leave_type, employment_type=EmploymentType.PERMANENT, allocated_days=Decimal("15.00"))
        request = LeaveRequest.objects.create(
            request_number="LV-RPT-000001", employee=self.employee, leave_type=leave_type,
            start_date=date(2026, 11, 1), end_date=date(2026, 11, 2), total_days=Decimal("2.00"),
            reason="Testing", status=LeaveStatus.APPROVED, duration_type=LeaveDuration.FULL_DAY,
            approved_days=Decimal("2.00"), approved_start_date=date(2026, 11, 1), approved_end_date=date(2026, 11, 2),
        )
        # created_at is auto_now_add - move it into the reported week directly.
        LeaveRequest.objects.filter(pk=request.pk).update(
            created_at=timezone.make_aware(datetime.combine(self.WEEK_START + timedelta(days=1), time(9))),
            approved_at=timezone.make_aware(datetime.combine(self.WEEK_START + timedelta(days=3), time(9))),
        )

        data = build_weekly_report(self.WEEK_START)
        self.assertEqual(data.leave_submitted, 1)
        self.assertEqual(data.leave_approved, 1)
        self.assertEqual(data.leave_days_approved, Decimal("2.00"))
        self.assertEqual({row.name: row.count for row in data.leave_by_type}, {"Report Annual": 1})

    def test_meal_collections_exclude_voided_tickets(self):
        device = MealDevice.objects.create(name="Report Device", serial_number="RPT-DEVICE-1", active=True)
        good_event = MealEvent.objects.create(employee=self.employee, device=device, timestamp=timezone.make_aware(datetime.combine(self.WEEK_START, time(12))), external_event_id="RPT-E1", source_system="device")
        MealCollection.objects.create(event=good_event, employee=self.employee, work_date=self.WEEK_START, sequence_number=1, rate_snapshot=Decimal("700.00"), status=MealCollectionStatus.WITHIN)
        excess_event = MealEvent.objects.create(employee=self.employee, device=device, timestamp=timezone.make_aware(datetime.combine(self.WEEK_START, time(13))), external_event_id="RPT-E2", source_system="device")
        MealCollection.objects.create(event=excess_event, employee=self.employee, work_date=self.WEEK_START, sequence_number=2, rate_snapshot=Decimal("700.00"), status=MealCollectionStatus.EXCESS)
        voided_event = MealEvent.objects.create(employee=self.employee, device=device, timestamp=timezone.make_aware(datetime.combine(self.WEEK_START, time(14))), external_event_id="RPT-E3", source_system="device")
        MealCollection.objects.create(event=voided_event, employee=self.employee, work_date=self.WEEK_START, sequence_number=3, rate_snapshot=Decimal("700.00"), status=MealCollectionStatus.EXCESS, voided_at=timezone.now())

        data = build_weekly_report(self.WEEK_START)
        self.assertEqual(data.meal_collections, 2)  # the voided one is excluded
        self.assertEqual(data.meal_within_entitlement, 1)
        self.assertEqual(data.meal_excess, 1)


class WeeklyReportAPITests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="report-viewer", password="pw")
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_summary_defaults_to_the_current_week(self):
        response = self.client.get("/api/reports/weekly/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("workforce", response.data)
        self.assertIn("attendance", response.data)
        self.assertIn("leave", response.data)
        self.assertIn("meals", response.data)

    def test_summary_accepts_an_explicit_week_start(self):
        response = self.client.get("/api/reports/weekly/?week_start=2026-09-21")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["week_start"], date(2026, 9, 21))

    def test_summary_rejects_a_bad_date(self):
        response = self.client.get("/api/reports/weekly/?week_start=not-a-date")
        self.assertEqual(response.status_code, 400)

    def test_presentation_downloads_a_pptx_file(self):
        response = self.client.get("/api/reports/weekly/presentation/?week_start=2026-09-21")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/vnd.openxmlformats-officedocument.presentationml.presentation")
        self.assertIn("Week 39 HR Report.pptx", response["Content-Disposition"])
        self.assertGreater(len(response.content), 1000)

    def test_presentation_requires_authentication(self):
        response = APIClient().get("/api/reports/weekly/presentation/")
        self.assertEqual(response.status_code, 401)

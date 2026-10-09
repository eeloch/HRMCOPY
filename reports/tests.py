import io
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient
from pptx import Presentation

from accommodation.models import Building, BuildingKind, Room, RoomAssignment
from attendance.models import DailyAttendance, Shift
from employees.models import Department, Employee, EmploymentType
from leave.models import LeaveDuration, LeavePolicy, LeaveRequest, LeaveStatus, LeaveType
from meals.models import MealCollectionStatus, MealDevice, MealEvent
from meals.services import MealCollection
from offences.models import EmployeeOffence, EmployeeOffenceStatus, OffenceType

from .pptx_builder import build_weekly_report_pptx
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
        self.assertEqual(data.meal_total_cost, Decimal("1400.00"))  # the voided ticket's rate is excluded too
        self.assertEqual(data.meal_cost_per_ticket, Decimal("700.00"))

    def test_meal_cost_trend_covers_this_week_and_the_two_before_it(self):
        device = MealDevice.objects.create(name="Trend Device", serial_number="RPT-DEVICE-TREND", active=True)
        for weeks_back, amount in ((2, "100.00"), (1, "200.00"), (0, "300.00")):
            work_date = self.WEEK_START - timedelta(days=7 * weeks_back)
            event = MealEvent.objects.create(
                employee=self.employee, device=device,
                timestamp=timezone.make_aware(datetime.combine(work_date, time(12))),
                external_event_id=f"RPT-TREND-{weeks_back}", source_system="device",
            )
            MealCollection.objects.create(event=event, employee=self.employee, work_date=work_date, sequence_number=1, rate_snapshot=Decimal(amount), status=MealCollectionStatus.WITHIN)

        data = build_weekly_report(self.WEEK_START)
        self.assertEqual([cost for _, cost in data.meal_cost_trend], [Decimal("100.00"), Decimal("200.00"), Decimal("300.00")])
        self.assertEqual(data.previous_meal_total_cost, Decimal("200.00"))
        self.assertEqual(data.meal_total_cost, Decimal("300.00"))

    def test_retention_hire_and_attrition_rates(self):
        # Opening headcount is derived: active_employees - new_hires + exits.
        Employee.objects.create(employee_id="RPT-HIRE-A", first_name="A", last_name="Hire", status="active", employment_date=self.WEEK_START + timedelta(days=1))
        Employee.objects.create(employee_id="RPT-EXIT-A", first_name="A", last_name="Exit", status="inactive", exit_date=self.WEEK_START + timedelta(days=1))
        # active_employees = 2 (self.employee + the hire), opening = 2 - 1 + 1 = 2
        data = build_weekly_report(self.WEEK_START)
        self.assertEqual(data.active_employees, 2)
        self.assertEqual(data.previous_active_employees, 2)
        self.assertEqual(len(data.new_hires), 1)
        self.assertEqual(len(data.exits), 1)
        self.assertEqual(data.net_movement, 0)
        self.assertAlmostEqual(data.retention_rate, (2 - 1) / 2 * 100)
        self.assertAlmostEqual(data.hire_rate, 1 / 2 * 100)
        self.assertAlmostEqual(data.attrition_rate, 1 / 2 * 100)

    def test_department_approved_headcount_comparison(self):
        self.department.required_staff = 3
        self.department.save()
        over_target = Department.objects.create(name="Security", required_staff=1)
        Employee.objects.create(employee_id="RPT-SEC-1", first_name="Sec", last_name="One", status="active", department=over_target)
        Employee.objects.create(employee_id="RPT-SEC-2", first_name="Sec", last_name="Two", status="active", department=over_target)
        Department.objects.create(name="No Target Dept", required_staff=0)

        data = build_weekly_report(self.WEEK_START)
        by_name = {row.name: row for row in data.department_approved}
        self.assertEqual(by_name["Extrusion"].current, 1)
        self.assertEqual(by_name["Extrusion"].status, "Understaffed")
        self.assertEqual(by_name["Security"].diff, 1)
        self.assertEqual(by_name["Security"].status, "Surplus")
        self.assertNotIn("No Target Dept", by_name)  # required_staff=0 means no target set - excluded
        self.assertEqual(data.approved_headcount_total, 3 + 1)
        self.assertEqual(data.pending_hires_total, 2)  # Extrusion short by 2
        self.assertEqual(data.surplus_employees_total, 1)  # Security over by 1
        self.assertEqual([row.name for row in data.key_vacancy_departments], ["Extrusion"])

    def test_gender_counts_and_by_department(self):
        self.employee.gender = "male"
        self.employee.save()
        Employee.objects.create(employee_id="RPT-FEMALE", first_name="F", last_name="One", status="active", department=self.department, gender="female")
        Employee.objects.create(employee_id="RPT-NOGENDER", first_name="N", last_name="One", status="active", department=self.department)

        data = build_weekly_report(self.WEEK_START)
        self.assertEqual(data.gender_male, 1)
        self.assertEqual(data.gender_female, 1)
        self.assertEqual(data.gender_unspecified, 1)
        row = next(r for r in data.gender_by_department if r.name == "Extrusion")
        self.assertEqual((row.male, row.female), (1, 1))

    def test_accommodation_occupancy_by_kind(self):
        company_building = Building.objects.create(name="Main Hostel", kind=BuildingKind.COMPANY)
        external_building = Building.objects.create(name="Annex Lodge", kind=BuildingKind.EXTERNAL)
        company_room = Room.objects.create(building=company_building, name="Room 1", capacity=2)
        external_room = Room.objects.create(building=external_building, name="Room A", capacity=4)
        RoomAssignment.objects.create(employee=self.employee, room=company_room, bed_number=1)
        second_employee = Employee.objects.create(employee_id="RPT-ROOMMATE", first_name="Room", last_name="Mate", status="active", gender="female")
        RoomAssignment.objects.create(employee=second_employee, room=external_room, bed_number=1)

        data = build_weekly_report(self.WEEK_START)
        self.assertEqual((data.accommodation_company.capacity, data.accommodation_company.occupied), (2, 1))
        self.assertEqual(data.accommodation_company.vacant, 1)
        self.assertEqual((data.accommodation_external.capacity, data.accommodation_external.occupied), (4, 1))
        self.assertEqual(data.accommodation_company_male, 0)  # the male self.employee's gender wasn't set in this test

    def test_offences_are_scoped_to_the_week_and_grouped(self):
        offence_type = OffenceType.objects.create(name="Late Arrival", default_amount=Decimal("500.00"))
        in_week = EmployeeOffence.objects.create(
            employee=self.employee, offence_type=offence_type, amount=Decimal("500.00"),
            incident_date=self.WEEK_START, status=EmployeeOffenceStatus.PENDING,
        )
        EmployeeOffence.objects.filter(pk=in_week.pk).update(created_at=timezone.make_aware(datetime.combine(self.WEEK_START, time(9))))
        out_of_week = EmployeeOffence.objects.create(
            employee=self.employee, offence_type=offence_type, amount=Decimal("500.00"),
            incident_date=self.WEEK_START, status=EmployeeOffenceStatus.PENDING,
        )
        EmployeeOffence.objects.filter(pk=out_of_week.pk).update(created_at=timezone.make_aware(datetime.combine(self.WEEK_START - timedelta(days=10), time(9))))

        data = build_weekly_report(self.WEEK_START)
        self.assertEqual(data.offence_count, 1)
        self.assertEqual(data.offence_total_amount, Decimal("500.00"))
        self.assertEqual({row.name: row.count for row in data.offence_by_type}, {"Late Arrival": 1})
        self.assertEqual({row.name: row.count for row in data.offence_by_status}, {"Pending": 1})


class WeeklyReportPptxTests(TestCase):
    """The deck must build without error across the data shapes the service can hand it - fully populated,
    and every section empty (no gender, no accommodation, no offences, no department targets)."""

    def test_builds_with_populated_data(self):
        Department.objects.create(name="Rich Data Dept", required_staff=5)
        Employee.objects.create(employee_id="RPT-PPTX-1", first_name="Full", last_name="Data", status="active", gender="male")
        data = build_weekly_report(date(2026, 9, 21))
        content = build_weekly_report_pptx(data)
        prs = Presentation(io.BytesIO(content))
        self.assertEqual(len(prs.slides), 15)

    def test_builds_with_entirely_empty_data(self):
        """No employees, no departments, no accommodation, no offences at all."""
        data = build_weekly_report(date(2026, 9, 21))
        content = build_weekly_report_pptx(data)
        self.assertGreater(len(content), 1000)


class WeeklyReportAPITests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="report-viewer", password="pw")
        self.user.user_permissions.add(Permission.objects.get(codename="view_payroll"))  # reports are for people who manage the business
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

from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from employees.models import Employee, EmploymentType
from leave.models import LeaveDuration, LeavePolicy, LeaveType
from leave.services.request import LeaveRequestService


class LeaveRequestDurationTests(TestCase):
    def setUp(self):
        self.employee = Employee.objects.create(
            employee_id="LEAVE-DURATION-001",
            first_name="Leave",
            last_name="Duration",
            employment_type=EmploymentType.PERMANENT,
        )
        self.leave_type = LeaveType.objects.create(
            name="Annual Leave",
            code="DURATION-ANNUAL",
        )
        LeavePolicy.objects.create(
            leave_type=self.leave_type,
            employment_type=EmploymentType.PERMANENT,
            allocated_days=Decimal("30.00"),
        )

    def create_request(self, start_date, end_date, duration_type=LeaveDuration.FULL_DAY):
        return LeaveRequestService.create_request(
            employee=self.employee,
            leave_type=self.leave_type,
            start_date=start_date,
            end_date=end_date,
            duration_type=duration_type,
            reason="Planned leave.",
        )

    def test_full_day_range_is_stored_as_inclusive_calendar_days(self):
        result = self.create_request(date(2026, 9, 2), date(2026, 9, 3))

        self.assertTrue(result["success"])
        self.assertEqual(result["leave_request"].total_days, Decimal("2"))

    def test_single_full_day_is_stored_as_one_day(self):
        result = self.create_request(date(2026, 9, 2), date(2026, 9, 2))

        self.assertTrue(result["success"])
        self.assertEqual(result["leave_request"].total_days, Decimal("1"))

    def test_first_half_is_stored_as_half_a_day(self):
        result = self.create_request(
            date(2026, 9, 2),
            date(2026, 9, 2),
            LeaveDuration.FIRST_HALF,
        )

        self.assertTrue(result["success"])
        self.assertEqual(result["leave_request"].total_days, Decimal("0.5"))

    def test_second_half_is_stored_as_half_a_day(self):
        result = self.create_request(
            date(2026, 9, 2),
            date(2026, 9, 2),
            LeaveDuration.SECOND_HALF,
        )

        self.assertTrue(result["success"])
        self.assertEqual(result["leave_request"].total_days, Decimal("0.5"))

    def test_half_day_across_multiple_dates_is_rejected(self):
        result = self.create_request(
            date(2026, 9, 2),
            date(2026, 9, 3),
            LeaveDuration.FIRST_HALF,
        )

        self.assertFalse(result["success"])
        self.assertIn("duration_type", result["errors"])

    def test_end_date_before_start_date_is_rejected_without_an_exception(self):
        result = self.create_request(date(2026, 9, 3), date(2026, 9, 2))

        self.assertFalse(result["success"])
        self.assertIn("date_range", result["errors"])

    def test_api_ignores_a_client_supplied_total_days(self):
        user = get_user_model().objects.create_user("leave-requester", password="password")
        client = APIClient()
        client.force_authenticate(user)

        response = client.post(
            "/api/leave/request/",
            {
                "employee_id": self.employee.pk,
                "leave_type_id": self.leave_type.pk,
                "start_date": "2026-09-02",
                "end_date": "2026-09-03",
                "duration_type": LeaveDuration.FULL_DAY,
                "total_days": "99.00",
                "reason": "Planned leave.",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["result"]["total_days"], "2.00")

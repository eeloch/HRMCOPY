from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from employees.models import Employee, EmploymentType
from leave.models import LeaveDuration, LeavePolicy, LeaveStatus, LeaveType
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


class LeavePolicyMatrixTests(TestCase):
    """The screen that would have caught the "384 casual employees can't take any leave" gap: a full
    matrix of every leave type x employment type, and the only way to fill in a missing combination."""

    def setUp(self):
        self.leave_type = LeaveType.objects.create(name="Matrix Annual", code="MATRIX-ANNUAL", default_days=Decimal("15.00"))
        LeavePolicy.objects.create(leave_type=self.leave_type, employment_type=EmploymentType.PERMANENT, allocated_days=Decimal("15.00"))

        self.manager = get_user_model().objects.create_user(username="leave-policy-manager", password="pw")
        from django.contrib.auth.models import Permission
        self.manager.user_permissions.add(Permission.objects.get(codename="manage_leave_policy"))
        self.client = APIClient()
        self.client.force_authenticate(self.manager)

    def test_matrix_shows_the_gap(self):
        response = self.client.get("/api/leave/policies/")
        self.assertEqual(response.status_code, 200)
        covered = {p["employment_type"] for p in response.data["policies"] if p["leave_type"] == self.leave_type.pk}
        self.assertEqual(covered, {"permanent"})  # casual, contract, etc. have no row at all - the gap is visible

    def test_setting_a_missing_cell_makes_it_requestable(self):
        employee = Employee.objects.create(employee_id="POLICY-CASUAL", first_name="Cas", last_name="Ual", employment_type=EmploymentType.CASUAL)

        # Before: exactly the production bug - no policy matches, so no leave can be validated.
        before = LeaveRequestService.validate_request(employee=employee, leave_type=self.leave_type, start_date=date(2026, 10, 1), end_date=date(2026, 10, 2), reason="Rest")
        self.assertIn("leave_type", before["errors"])

        response = self.client.post("/api/leave/policies/set/", {"leave_type": self.leave_type.pk, "employment_type": "casual", "allocated_days": "10"}, format="json")
        self.assertEqual(response.status_code, 200)

        after = LeaveRequestService.validate_request(employee=employee, leave_type=self.leave_type, start_date=date(2026, 10, 1), end_date=date(2026, 10, 2), reason="Rest")
        self.assertTrue(after["is_valid"])
        self.assertEqual(after["balance"]["allocated_days"], Decimal("10.00"))

    def test_setting_an_existing_cell_updates_it_in_place_not_duplicates(self):
        self.client.post("/api/leave/policies/set/", {"leave_type": self.leave_type.pk, "employment_type": "permanent", "allocated_days": "20"}, format="json")
        self.assertEqual(LeavePolicy.objects.filter(leave_type=self.leave_type, employment_type="permanent").count(), 1)
        self.assertEqual(LeavePolicy.objects.get(leave_type=self.leave_type, employment_type="permanent").allocated_days, Decimal("20.00"))

    def test_requires_permission_to_set(self):
        outsider = APIClient()
        outsider.force_authenticate(get_user_model().objects.create_user(username="no-perm", password="pw"))
        response = outsider.post("/api/leave/policies/set/", {"leave_type": self.leave_type.pk, "employment_type": "casual", "allocated_days": "10"}, format="json")
        self.assertEqual(response.status_code, 403)

    def test_negative_days_rejected(self):
        response = self.client.post("/api/leave/policies/set/", {"leave_type": self.leave_type.pk, "employment_type": "casual", "allocated_days": "-5"}, format="json")
        self.assertEqual(response.status_code, 400)

    def test_invalid_employment_type_rejected(self):
        response = self.client.post("/api/leave/policies/set/", {"leave_type": self.leave_type.pk, "employment_type": "made-up", "allocated_days": "5"}, format="json")
        self.assertEqual(response.status_code, 400)


class LeaveCancelTests(TestCase):
    """Withdrawing a pending request - by its requester, or by anyone who can approve leave."""

    def setUp(self):
        from django.contrib.auth.models import Permission

        self.employee = Employee.objects.create(employee_id="CANCEL-001", first_name="Can", last_name="Cel", employment_type=EmploymentType.PERMANENT)
        self.leave_type = LeaveType.objects.create(name="Cancel Annual", code="CANCEL-ANNUAL")
        LeavePolicy.objects.create(leave_type=self.leave_type, employment_type=EmploymentType.PERMANENT, allocated_days=Decimal("15.00"))

        self.requester = get_user_model().objects.create_user(username="cancel-requester", password="pw")
        self.manager = get_user_model().objects.create_user(username="cancel-manager", password="pw")
        self.manager.user_permissions.add(Permission.objects.get(codename="approve_leave"))
        self.bystander = get_user_model().objects.create_user(username="cancel-bystander", password="pw")

        self.request = LeaveRequestService.create_request(
            employee=self.employee, leave_type=self.leave_type, start_date=date(2026, 11, 2), end_date=date(2026, 11, 3),
            reason="Testing cancellation.", requested_by=self.requester,
        )["leave_request"]

    def test_requester_can_cancel_their_own_request(self):
        client = APIClient()
        client.force_authenticate(self.requester)
        response = client.post(f"/api/leave/requests/{self.request.pk}/cancel/", {}, format="json")
        self.assertEqual(response.status_code, 200)
        self.request.refresh_from_db()
        self.assertEqual(self.request.status, LeaveStatus.CANCELLED)
        self.assertEqual(self.request.approved_by, self.requester)

    def test_a_leave_manager_can_cancel_anyones_request(self):
        client = APIClient()
        client.force_authenticate(self.manager)
        response = client.post(f"/api/leave/requests/{self.request.pk}/cancel/", {}, format="json")
        self.assertEqual(response.status_code, 200)
        self.request.refresh_from_db()
        self.assertEqual(self.request.status, LeaveStatus.CANCELLED)

    def test_an_unrelated_user_cannot_cancel_it(self):
        client = APIClient()
        client.force_authenticate(self.bystander)
        response = client.post(f"/api/leave/requests/{self.request.pk}/cancel/", {}, format="json")
        self.assertEqual(response.status_code, 400)
        self.request.refresh_from_db()
        self.assertEqual(self.request.status, LeaveStatus.PENDING)

    def test_an_already_decided_request_cannot_be_cancelled(self):
        from leave.services.approval import LeaveApprovalService

        LeaveApprovalService.approve(self.request.pk, self.manager)
        client = APIClient()
        client.force_authenticate(self.requester)
        response = client.post(f"/api/leave/requests/{self.request.pk}/cancel/", {}, format="json")
        self.assertEqual(response.status_code, 400)

    def test_cancelling_does_not_notify_the_person_who_cancelled_it(self):
        from notifications.models import Notification

        client = APIClient()
        client.force_authenticate(self.requester)
        client.post(f"/api/leave/requests/{self.request.pk}/cancel/", {}, format="json")
        self.assertFalse(Notification.objects.filter(recipient=self.requester, event_type="leave.cancelled").exists())

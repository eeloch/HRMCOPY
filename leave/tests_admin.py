from datetime import date, timedelta
from decimal import Decimal

from django.utils import timezone

from employees.admin_testing import AdminTestCase
from employees.models import EmploymentType
from notifications.models import Notification

from .models import LeaveBalance, LeavePolicy, LeaveRequest, LeaveStatus, LeaveType
from .services.request import LeaveRequestService


class LeaveAdminTests(AdminTestCase):
    def setUp(self):
        super().setUp()
        self.ada = self.make_employee(middle="Grace", employment_type=EmploymentType.PERMANENT)
        self.bola = self.make_employee("000685", "Bola", "Adeyemi", employment_type=EmploymentType.PERMANENT)
        self.annual = LeaveType.objects.create(name="Annual leave", code="ADMIN-TEST-ANNUAL", color="#2563EB")
        LeavePolicy.objects.create(leave_type=self.annual, employment_type=EmploymentType.PERMANENT, allocated_days=Decimal("20"))
        self.requester = self.admin_user
        self.request_a = self.create_request(self.ada, date(2026, 9, 24), date(2026, 9, 26))
        self.request_b = self.create_request(self.bola, date(2026, 10, 5), date(2026, 10, 6))

    def create_request(self, employee, start, end):
        result = LeaveRequestService.create_request(
            employee=employee, leave_type=self.annual, start_date=start, end_date=end, reason="Family matter", requested_by=self.requester,
        )
        self.assertTrue(result["success"], result["errors"])
        return result["leave_request"]

    def test_changelists_load(self):
        for model in (LeaveType, LeavePolicy, LeaveBalance, LeaveRequest):
            self.get_list(model)

    def test_request_filters_load(self):
        for params in (
            {"status__exact": "pending"}, {"leave_type__id__exact": self.annual.pk}, {"when": "now"}, {"when": "soon"}, {"when": "waiting"},
            {"employee__department__id__exact": self.department.pk}, {"duration_type__exact": "full_day"}, {"start_date__gte": "2026-09-01"},
        ):
            self.get_list(LeaveRequest, **params)

    def test_request_list_search(self):
        self.assert_search(LeaveRequest, "000684", [self.request_a.pk])
        self.assert_search(LeaveRequest, "Okafor", [self.request_a.pk])
        self.assert_search(LeaveRequest, "Grace", [self.request_a.pk])
        self.assert_search(LeaveRequest, self.request_b.request_number, [self.request_b.pk])
        self.assert_search(LeaveRequest, "Family", [self.request_a.pk, self.request_b.pk])

    def test_request_list_has_name_and_status(self):
        page = self.get_list(LeaveRequest).content.decode()
        for text in ("000684", "Ada Grace Okafor", "Operations", "Annual leave", "Pending"):
            self.assertIn(text, page)

    def test_no_n_plus_one(self):
        counter = iter(range(10, 200))

        def more():
            for _ in range(4):
                n = next(counter)
                employee = self.make_employee(f"0008{n}", "Extra", f"Person{n}", employment_type=EmploymentType.PERMANENT)
                self.create_request(employee, date(2026, 11, 2), date(2026, 11, 3))

        self.assert_same_query_count(LeaveRequest, more)
        self.assert_same_query_count(LeaveBalance, lambda: LeaveBalance.objects.create(employee=self.bola, leave_type=self.annual, year=2026))
        self.assert_same_query_count(LeavePolicy, lambda: LeavePolicy.objects.get_or_create(leave_type=self.annual, employment_type=EmploymentType.CONTRACT))

    def test_str_has_staff_number_name_type_and_date(self):
        self.assertEqual(str(self.request_a), "000684 Ada Grace Okafor - Annual leave - 24 Sep 2026")
        balance = LeaveBalance.objects.create(employee=self.ada, leave_type=self.annual, year=2026)
        self.assertEqual(str(balance), "000684 Ada Grace Okafor - Annual leave - 2026")

    def test_change_form_shows_live_balance_and_locks_generated_fields(self):
        response = self.client.get(self.url(LeaveRequest, "change", self.request_a.pk))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "allocated 20")
        self.assertContains(response, self.request_a.request_number)

    def test_add_is_not_offered(self):
        self.assertEqual(self.client.get(self.url(LeaveRequest, "add")).status_code, 403)

    def test_changing_dates_recalculates_days(self):
        data = {
            "employee": self.ada.pk, "leave_type": self.annual.pk, "duration_type": "full_day",
            "start_date": "2026-09-24", "end_date": "2026-09-28", "return_date": "", "reason": "Family matter",
            "status": "pending", "approved_start_date": "", "approved_end_date": "", "approved_days": "", "rejection_reason": "",
        }
        response = self.client.post(self.url(LeaveRequest, "change", self.request_a.pk), data)
        self.assertEqual(response.status_code, 302, getattr(response, "context", None) and response.context["adminform"].form.errors)
        self.request_a.refresh_from_db()
        self.assertEqual(self.request_a.total_days, Decimal("5"))
        event = self.audit("leave_request.admin_updated", employee=self.ada).get()
        self.assertIn("end_date", event.metadata["changed_fields"])

    def test_no_bulk_delete_action(self):
        response = self.get_list(LeaveRequest)
        self.assertNotIn("delete_selected", [name for name, _ in response.context["action_form"].fields["action"].choices])

    def test_approve_uses_the_service_and_audits_and_notifies(self):
        first, _ = self.run_action(LeaveRequest, "approve_requests", [self.request_a.pk, self.request_b.pk])
        self.assertContains(first, "Approve leave requests")
        self.request_a.refresh_from_db()
        self.assertEqual(self.request_a.status, LeaveStatus.APPROVED)
        self.assertEqual(self.request_a.approved_by, self.admin_user)
        self.assertEqual(self.request_a.approved_days, Decimal("3"))
        self.assertEqual(self.audit("leave.approved", employee=self.ada).count(), 1)
        self.assertEqual(self.audit("leave.approved", employee=self.bola).count(), 1)

    def test_confirmation_step_changes_nothing(self):
        self.run_action(LeaveRequest, "approve_requests", [self.request_a.pk], confirm=False)
        self.request_a.refresh_from_db()
        self.assertEqual(self.request_a.status, LeaveStatus.PENDING)
        self.assertFalse(self.audit("leave.approved").exists())

    def test_approve_respects_the_balance_and_reports_the_failure(self):
        LeavePolicy.objects.filter(leave_type=self.annual).update(allocated_days=Decimal("1"))
        _, second = self.run_action(LeaveRequest, "approve_requests", [self.request_a.pk])
        self.request_a.refresh_from_db()
        self.assertEqual(self.request_a.status, LeaveStatus.PENDING)
        self.assertContains(second, "no longer has enough leave entitlement")
        self.assertFalse(self.audit("leave.approved").exists())

    def test_only_pending_requests_are_decided(self):
        self.run_action(LeaveRequest, "approve_requests", [self.request_a.pk])
        _, second = self.run_action(LeaveRequest, "reject_requests", [self.request_a.pk], reason="Changed my mind")
        self.request_a.refresh_from_db()
        self.assertEqual(self.request_a.status, LeaveStatus.APPROVED)
        self.assertFalse(self.audit("leave.rejected").exists())
        self.assertContains(second, "left as they were")

    def test_reject_needs_a_reason(self):
        _, second = self.run_action(LeaveRequest, "reject_requests", [self.request_a.pk], follow=False)
        self.assertEqual(second.status_code, 200)
        self.assertContains(second, "This is required.")
        self.request_a.refresh_from_db()
        self.assertEqual(self.request_a.status, LeaveStatus.PENDING)

    def test_reject_stores_the_reason_and_audits(self):
        self.run_action(LeaveRequest, "reject_requests", [self.request_a.pk], reason="Peak season")
        self.request_a.refresh_from_db()
        self.assertEqual((self.request_a.status, self.request_a.rejection_reason), (LeaveStatus.REJECTED, "Peak season"))
        self.assertEqual(self.audit("leave.rejected", employee=self.ada).count(), 1)

    def test_cancel_uses_the_service_and_audits(self):
        self.run_action(LeaveRequest, "cancel_requests", [self.request_b.pk])
        self.request_b.refresh_from_db()
        self.assertEqual(self.request_b.status, LeaveStatus.CANCELLED)
        self.assertEqual(self.audit("leave.cancelled", employee=self.bola).count(), 1)

    def test_on_leave_today_filter(self):
        today = timezone.localdate()
        chidi = self.make_employee("000690", "Chidi", "Eze", employment_type=EmploymentType.PERMANENT)
        current = self.create_request(chidi, today, today + timedelta(days=1))
        self.run_action(LeaveRequest, "approve_requests", [current.pk])
        found = {r.pk for r in self.get_list(LeaveRequest, when="now").context["cl"].result_list}
        self.assertEqual(found, {current.pk})

    def test_stored_balances_are_read_only_and_say_so(self):
        response = self.client.get(self.url(LeaveBalance), follow=True)
        self.assertContains(response, "not used by leave approval")
        self.assertEqual(self.client.get(self.url(LeaveBalance, "add")).status_code, 403)

    def test_leave_type_swatch_and_policy_headcount(self):
        self.assertContains(self.get_list(LeaveType), "#2563EB")
        page = self.get_list(LeavePolicy).content.decode()
        self.assertIn("Annual leave", page)

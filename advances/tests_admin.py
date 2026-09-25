from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase

from audit.admin_testing import admin_messages, run_admin_action, superuser
from audit.models import AuditEvent
from employees.models import Department, Employee

from .models import AdvanceStatus, SalaryAdvance
from .services import AdvanceService

URL = "/admin/advances/salaryadvance/"


class AdvanceAdminTests(TestCase):
    def setUp(self):
        self.root = superuser()
        self.client.force_login(self.root)
        self.hr = get_user_model().objects.create_user("hr", password="pw")
        dept = Department.objects.create(name="Melting")
        self.ada = Employee.objects.create(employee_id="000684", first_name="Ada", last_name="Okafor", department=dept, basic_salary=Decimal("100000.00"))
        self.bayo = Employee.objects.create(employee_id="000700", first_name="Bayo", last_name="Eze", department=dept, basic_salary=Decimal("100000.00"))
        self.advance = AdvanceService.record(employee=self.ada, amount=Decimal("30000"), repayment_months=3, deduct_from=(2026, 10), actor=self.hr)

    def test_lists_load_and_read_well(self):
        for url in (URL, "/admin/advances/advancerepayment/"):
            self.assertEqual(self.client.get(url).status_code, 200)
        response = self.client.get(URL)
        for text in ("000684", "Ada Okafor", "Melting", "N 30,000.00", "N 10,000.00", "Oct 2026", "Awaiting management approval"):
            self.assertContains(response, text)
        self.assertNotContains(response, "column-id")
        self.assertEqual(str(self.advance), "000684 Ada Okafor - advance N 30,000.00 (Awaiting management approval)")

    def test_search_by_number_and_name(self):
        AdvanceService.record(employee=self.bayo, amount=Decimal("5000"), actor=self.hr)
        for term, count in (("000684", 1), ("Okafor", 1), ("Ada Okafor", 1), ("Bayo", 1), ("Eze", 1)):
            self.assertEqual(len(self.client.get(URL, {"q": term}).context["cl"].result_list), count, term)

    def test_read_only_no_delete(self):
        detail = f"{URL}{self.advance.pk}/change/"
        self.assertEqual(self.client.get(detail).status_code, 200)
        self.assertEqual(self.client.post(detail, {"amount": "1.00", "_save": "Save"}).status_code, 403)
        self.assertEqual(self.client.get(URL + "add/").status_code, 403)
        self.assertEqual(self.client.get(f"{detail[:-7]}delete/").status_code, 403)
        self.assertNotContains(self.client.get(URL), "delete_selected")
        self.advance.refresh_from_db()
        self.assertEqual(self.advance.amount, Decimal("30000.00"))

    def test_approve_goes_through_the_service_and_is_audited(self):
        page, response = run_admin_action(self.client, URL, "approve_advances", [self.advance.pk], reason="ok by MD")
        self.assertContains(page, "000684 Ada Okafor - advance N 30,000.00")
        self.advance.refresh_from_db()
        self.assertEqual((self.advance.status, self.advance.decided_by, self.advance.decision_comment), (AdvanceStatus.APPROVED, self.root, "ok by MD"))
        self.assertTrue(AuditEvent.objects.filter(event_type="advance.approved", actor=self.root).exists())
        self.assertTrue(AuditEvent.objects.filter(event_type="admin.advances.approve_advances").exists())
        self.assertIn("Advances approved: 1 of 1 selected.", admin_messages(response))

    def test_decline_needs_a_reason(self):
        page, response = run_admin_action(self.client, URL, "decline_advances", [self.advance.pk], reason="  ")
        self.assertContains(response, "Reason for declining is required.")
        self.advance.refresh_from_db()
        self.assertEqual(self.advance.status, AdvanceStatus.REQUESTED)
        run_admin_action(self.client, URL, "decline_advances", [self.advance.pk], reason="Too much")
        self.advance.refresh_from_db()
        self.assertEqual(self.advance.status, AdvanceStatus.DECLINED)

    def test_cancel_works_before_payment_and_refuses_a_paid_advance(self):
        run_admin_action(self.client, URL, "cancel_advances", [self.advance.pk], reason="Recorded twice")
        self.advance.refresh_from_db()
        self.assertEqual(self.advance.status, AdvanceStatus.CANCELLED)
        self.assertTrue(AuditEvent.objects.filter(event_type="advance.cancelled").exists())

        paid = SalaryAdvance.objects.create(employee=self.bayo, amount=Decimal("9000"), deduct_from_year=2026, deduct_from_month=10, status=AdvanceStatus.PAID)
        _, response = run_admin_action(self.client, URL, "cancel_advances", [paid.pk])
        paid.refresh_from_db()
        self.assertEqual(paid.status, AdvanceStatus.PAID)
        self.assertTrue(any("Skipped" in text and "not been paid" in text for text in admin_messages(response)))

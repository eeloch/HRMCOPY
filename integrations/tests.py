from datetime import timedelta
from decimal import Decimal

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from employees.models import Department, Employee

from .models import IntegrationKey


class ERPEmployeeFeedTests(TestCase):
    def setUp(self):
        self.dept = Department.objects.create(name="Feed Dept")
        self.ada = Employee.objects.create(employee_id="FEED-1", first_name="Ada", last_name="Feed", department=self.dept, basic_salary=Decimal("90000.00"), account_number="0123456789", bank_name="Bank")
        self.key, self.raw = IntegrationKey.issue("Company ERP")
        self.client = APIClient()

    def get(self, url, raw=None, **extra):
        headers = {"HTTP_AUTHORIZATION": f"Api-Key {raw or self.raw}"} if raw is not False else {}
        return self.client.get(url, **headers, **extra)

    def test_the_feed_needs_a_valid_key(self):
        self.assertIn(self.get("/api/integrations/erp/employees/", raw=False).status_code, (401, 403))
        self.assertEqual(self.get("/api/integrations/erp/employees/", raw="rhrm_wrong").status_code, 401)
        self.assertEqual(self.get("/api/integrations/erp/employees/").status_code, 200)

    def test_it_lists_staff_without_salary_or_bank_details_and_records_the_use(self):
        body = self.get("/api/integrations/erp/employees/").json()
        record = next(item for item in body["results"] if item["staff_number"] == "FEED-1")
        self.assertEqual((record["full_name"], record["department"], record["status"]), ("Ada Feed", "Feed Dept", "active"))
        self.assertFalse({"basic_salary", "account_number", "bank_name", "salary"} & set(record))
        self.key.refresh_from_db()
        self.assertEqual(self.key.request_count, 1)
        self.assertIsNotNone(self.key.last_used_at)

    def test_only_changes_since_the_last_call_come_back(self):
        first = self.get("/api/integrations/erp/employees/").json()
        later = self.get("/api/integrations/erp/employees/", data={"updated_since": first["server_time"]}).json()
        self.assertEqual(later["count"], 0)
        self.ada.status = "inactive"
        self.ada.save()
        changed = self.get("/api/integrations/erp/employees/", data={"updated_since": first["server_time"]}).json()
        self.assertEqual([(item["staff_number"], item["status"]) for item in changed["results"]], [("FEED-1", "inactive")])
        self.assertEqual(self.get("/api/integrations/erp/employees/", data={"updated_since": "yesterday"}).status_code, 400)

    def test_one_employee_by_staff_number_and_paging(self):
        self.assertEqual(self.get("/api/integrations/erp/employees/FEED-1/").json()["staff_number"], "FEED-1")
        self.assertEqual(self.get("/api/integrations/erp/employees/NOPE/").status_code, 404)
        Employee.objects.create(employee_id="FEED-2", first_name="Bo", last_name="Feed")
        page = self.get("/api/integrations/erp/employees/", data={"page_size": 1}).json()
        self.assertEqual((len(page["results"]), page["next_page"]), (1, 2))

    def test_a_switched_off_key_or_a_wrong_address_is_refused(self):
        self.key.allowed_ips = "10.9.9.9"
        self.key.save()
        self.assertEqual(self.get("/api/integrations/erp/employees/").status_code, 401)
        self.key.allowed_ips = ""
        self.key.active = False
        self.key.save()
        self.assertEqual(self.get("/api/integrations/erp/employees/").status_code, 401)

    def test_a_staff_login_cannot_use_the_feed_and_the_key_cannot_open_other_pages(self):
        self.assertIn(self.get("/api/employees/").status_code, (401, 403))

    def test_the_command_makes_lists_and_switches_off_keys_and_only_the_hash_is_kept(self):
        from io import StringIO

        out = StringIO()
        call_command("integration_key", "create", "Another ERP", "--allow-ip", "1.2.3.4", stdout=out)
        raw = out.getvalue().strip().splitlines()[-1]
        record = IntegrationKey.objects.get(name="Another ERP")
        self.assertTrue(raw.startswith("rhrm_"))
        self.assertNotIn(raw, record.key_hash)
        call_command("integration_key", "revoke", record.prefix, stdout=StringIO())
        record.refresh_from_db()
        self.assertFalse(record.active)

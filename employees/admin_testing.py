"""Helpers for the admin tests of the employee-centred apps (used by employees/leave/offences/ppe/documents/
accommodation tests_admin.py)."""

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from audit.models import AuditEvent
from employees.models import Department, Employee, Position


class AdminTestCase(TestCase):
    """A superuser is logged in; helpers build the admin URLs, run a bulk action through both of its steps and
    count queries so a list that starts running one query per row fails the test."""

    def setUp(self):
        self.admin_user = get_user_model().objects.create_superuser(username="root-admin", password="test-password")
        self.client.force_login(self.admin_user)
        self.department = Department.objects.create(name="Operations", required_staff=3)
        self.position = Position.objects.create(name="Operator", department=self.department)

    def make_employee(self, employee_id="000684", first="Ada", last="Okafor", middle="", **extra):
        extra.setdefault("department", self.department)
        return Employee.objects.create(employee_id=employee_id, first_name=first, middle_name=middle, last_name=last, **extra)

    @staticmethod
    def url(model, view="changelist", *args):
        return reverse(f"admin:{model._meta.app_label}_{model._meta.model_name}_{view}", args=args)

    def get_list(self, model, **params):
        response = self.client.get(self.url(model), params)
        self.assertEqual(response.status_code, 200, f"{model.__name__} list {params} -> {response.status_code}")
        return response

    def assert_search(self, model, term, expected_pks):
        response = self.get_list(model, q=term)
        found = {obj.pk for obj in response.context["cl"].result_list}
        self.assertEqual(found, set(expected_pks), f"searching {term!r} in {model.__name__}")

    def assert_same_query_count(self, model, add_rows, **params):
        """The query count of the changelist must not grow with the number of rows."""
        self.get_list(model, **params)  # warm caches (content types, sessions)
        with CaptureQueriesContext(connection) as before:
            self.get_list(model, **params)
        add_rows()
        with CaptureQueriesContext(connection) as after:
            self.get_list(model, **params)
        self.assertEqual(len(before), len(after), f"{model.__name__} list query count grew with more rows")

    def run_action(self, model, action, pks, confirm=True, follow=True, **fields):
        """Post a changelist action. Returns (confirmation_response, final_response); the second is None when
        confirm is False. The confirmation page must not have changed anything."""
        url = self.url(model)
        data = {"action": action, "_selected_action": [str(pk) for pk in pks], "index": "0", "select_across": "0"}
        first = self.client.post(url, data)
        self.assertEqual(first.status_code, 200, "an action should first show its confirmation page")
        self.assertContains(first, 'name="confirm_step"')
        if not confirm:
            return first, None
        second = self.client.post(url, {**data, "confirm_step": "1", **{f"confirm_{k}": v for k, v in fields.items()}}, follow=follow)
        return first, second

    def audit(self, event_type, **filters):
        return AuditEvent.objects.filter(event_type=event_type, **filters)

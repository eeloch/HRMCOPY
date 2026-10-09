from datetime import date

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from rest_framework.test import APIClient

from audit.models import AuditEvent

from .models import Employee


class EmployeeChangesAreAuditedTests(TestCase):
    def setUp(self):
        self.person = Employee.objects.create(employee_id="000900", first_name="Aud", last_name="Ited", status="active", employment_type="permanent")

    def events(self):
        return AuditEvent.objects.filter(employee=self.person, event_type__in=["employees.status_changed", "employees.record_changed"])

    def test_a_new_employee_is_not_logged_as_a_change(self):
        self.assertFalse(self.events().exists())

    def test_a_status_change_is_logged_with_what_it_was_and_became(self):
        self.person.status = "inactive"
        self.person.save()
        event = self.events().get()
        self.assertEqual(event.event_type, "employees.status_changed")
        self.assertEqual(event.metadata["changes"]["status"], {"from": "active", "to": "inactive"})
        self.assertIn("status: active to inactive", event.description)
        self.assertIn("outside the app screens", event.description)  # no signed-in user: a script
        self.assertEqual(event.severity, "warning")

    def test_exit_date_and_type_changes_are_logged_too(self):
        self.person.exit_date = date(2026, 9, 18)
        self.person.employment_type = "contract"
        self.person.save()
        event = self.events().get()
        self.assertEqual(event.event_type, "employees.record_changed")
        self.assertEqual(set(event.metadata["changes"]), {"exit date", "employment type"})

    def test_saving_with_nothing_changed_logs_nothing(self):
        self.person.first_name = "Renamed"
        self.person.save()
        self.assertFalse(self.events().exists())

    def test_a_change_through_the_app_names_who_made_it(self):
        editor = get_user_model().objects.create_user(username="status-editor", password="pw")
        editor.user_permissions.add(Permission.objects.get(codename="change_employee"), Permission.objects.get(codename="view_employee"))
        client = APIClient()
        client.force_authenticate(editor)
        response = client.patch(f"/api/employees/{self.person.pk}/", {"status": "inactive"}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        event = self.events().get()
        self.assertEqual(event.actor, editor)
        self.assertNotIn("outside the app screens", event.description)

    def test_the_actor_does_not_leak_into_a_later_script_change(self):
        from core.actor import get_actor

        self.assertIsNone(get_actor())
        client = APIClient()
        client.force_authenticate(get_user_model().objects.create_user(username="somebody", password="pw"))
        client.get("/api/notifications/unread-count/")
        self.person.status = "suspended"
        self.person.save()
        self.assertIsNone(self.events().get().actor)

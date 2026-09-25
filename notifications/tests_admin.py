from django.contrib.auth import get_user_model
from django.test import TestCase

from audit.admin_testing import admin_messages, superuser
from audit.models import AuditEvent
from employees.models import Employee
from notifications.models import Notification
from notifications.services import NotificationService

URL = "/admin/notifications/notification/"


class NotificationAdminTests(TestCase):
    def setUp(self):
        self.client.force_login(superuser())
        self.other = get_user_model().objects.create_user("hr-officer", password="pw")
        self.ada = Employee.objects.create(employee_id="000684", first_name="Ada", last_name="Okafor")
        self.note = NotificationService.create(recipient=self.other, event_type="meals.x", title="Excess waived", message="Ada waived", employee=self.ada)

    def test_list_search_and_filters(self):
        response = self.client.get(URL)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "000684")
        self.assertContains(response, "Ada Okafor")
        for term in ("000684", "Okafor", "hr-officer", "waived"):
            self.assertEqual(len(self.client.get(URL, {"q": term}).context["cl"].result_list), 1, term)
        self.assertEqual(len(self.client.get(URL, {"is_read__exact": "1"}).context["cl"].result_list), 0)

    def test_mark_read_and_unread_work_on_anyones_notifications_and_are_audited(self):
        response = self.client.post(URL, {"action": "mark_read", "_selected_action": [self.note.pk], "index": 0}, follow=True)
        self.assertIn("Marked 1 notification(s) as read.", admin_messages(response))
        self.note.refresh_from_db()
        self.assertTrue(self.note.is_read)
        self.assertIsNotNone(self.note.read_at)
        self.assertTrue(AuditEvent.objects.filter(event_type="admin.notifications.mark_read", actor__username="root-admin").exists())

        self.client.post(URL, {"action": "mark_unread", "_selected_action": [self.note.pk], "index": 0}, follow=True)
        self.note.refresh_from_db()
        self.assertEqual((self.note.is_read, self.note.read_at), (False, None))
        self.assertTrue(AuditEvent.objects.filter(event_type="admin.notifications.mark_unread").exists())

    def test_a_notification_is_not_editable_by_form(self):
        detail = f"{URL}{self.note.pk}/change/"
        self.assertEqual(self.client.get(detail).status_code, 200)
        self.assertEqual(self.client.post(detail, {"message": "tampered", "_save": "Save"}).status_code, 403)
        self.assertEqual(self.client.get(URL + "add/").status_code, 403)
        self.assertEqual(Notification.objects.get().message, "Ada waived")

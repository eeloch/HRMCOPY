from django.contrib.auth import get_user_model
from django.test import TestCase

from audit.admin_testing import superuser
from audit.models import AuditEvent, AuditSeverity
from audit.services import AuditService
from employees.models import Department, Employee

URL = "/admin/audit/auditevent/"


class AuditAdminTests(TestCase):
    def setUp(self):
        self.client.force_login(superuser())
        self.actor = get_user_model().objects.create_user("hr-officer", first_name="Hilda", last_name="Roe", password="pw")
        dept = Department.objects.create(name="Melting")
        self.ada = Employee.objects.create(employee_id="000684", first_name="Ada", last_name="Okafor", department=dept)
        self.event = AuditService.log(
            event_type="payroll.generated", module="payroll", actor=self.actor, employee=self.ada, object=self.ada, severity=AuditSeverity.SUCCESS,
            title="Payroll records generated", description="Generated 3 record(s).", metadata={"period": "September 2026", "created": 3, "nested": {"a": 1}},
        )
        AuditService.log(event_type="meals.ticket_voided", module="meals", title="Meal ticket voided", severity=AuditSeverity.WARNING)

    def test_list_loads_and_shows_who_and_what(self):
        response = self.client.get(URL)
        self.assertEqual(response.status_code, 200)
        for text in ("000684", "Ada Okafor", "Hilda Roe", "Payroll records generated", "payroll.generated"):
            self.assertContains(response, text)

    def test_search_by_staff_number_name_actor_and_text(self):
        for term in ("000684", "Okafor", "Ada Okafor", "hr-officer", "Roe", "records generated", "payroll.generated"):
            rows = self.client.get(URL, {"q": term}).context["cl"].result_list
            self.assertEqual([e.pk for e in rows], [self.event.pk], term)

    def test_filters_by_module_event_type_severity_actor_and_date(self):
        for params in ({"module": "payroll"}, {"event_type": "payroll.generated"}, {"severity": "success"}, {"actor__id__exact": self.actor.pk}, {"employee__department__id__exact": self.ada.department_id}):
            rows = self.client.get(URL, params).context["cl"].result_list
            self.assertEqual([e.pk for e in rows], [self.event.pk], params)
        self.assertEqual(self.client.get(URL, {"created_at__gte": "2000-01-01"}).status_code, 200)
        self.assertEqual(self.client.get(URL).context["cl"].date_hierarchy, "created_at")

    def test_is_completely_read_only(self):
        self.assertEqual(self.client.get(URL + "add/").status_code, 403)
        detail = f"{URL}{self.event.pk}/change/"
        page = self.client.get(detail)
        self.assertEqual(page.status_code, 200)
        self.assertNotContains(page, 'name="_save"')
        self.assertEqual(self.client.post(detail, {"title": "tampered", "_save": "Save"}).status_code, 403)
        self.assertEqual(self.client.get(f"{URL}{self.event.pk}/delete/").status_code, 403)
        self.assertEqual(self.client.post(f"{URL}{self.event.pk}/delete/", {"post": "yes"}).status_code, 403)
        self.assertEqual(self.client.post(URL, {"action": "delete_selected", "_selected_action": [self.event.pk], "post": "yes"}).status_code, 200)
        self.event.refresh_from_db()
        self.assertEqual(self.event.title, "Payroll records generated")
        self.assertEqual(AuditEvent.objects.count(), 2)
        self.assertNotContains(self.client.get(URL), "delete_selected")

    def test_metadata_is_shown_as_a_readable_table(self):
        page = self.client.get(f"{URL}{self.event.pk}/change/")
        self.assertContains(page, "<th")
        self.assertContains(page, "September 2026")
        self.assertContains(page, "period")
        self.assertContains(page, "employees.employee #")

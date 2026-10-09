from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from rest_framework.test import APIClient

from employees.models import Department, Employee
from ppe.models import PPEType


def user_with(username, *codenames):
    user = get_user_model().objects.create_user(username=username, password="pw")
    for codename in codenames:
        user.user_permissions.add(Permission.objects.get(codename=codename))
    return user


def client_for(user):
    client = APIClient()
    client.force_authenticate(user)
    return client


class OnlyThePartsYouWorkInOpenTests(TestCase):
    """A store keeper who only issues PPE must see PPE - not the staff list, who is at work, accommodation, leave..."""

    def setUp(self):
        self.dept = Department.objects.create(name="Extrusion")
        self.person = Employee.objects.create(
            employee_id="000100", first_name="Ada", last_name="Okafor", department=self.dept, status="active", phone="0803 111 2222",
            biometric_user_id="100", basic_salary=Decimal("90000"),
        )
        PPEType.objects.create(name="Safety boots")
        self.store = client_for(user_with("store-keeper", "record_ppe_issue"))

    def test_the_ppe_pages_open(self):
        self.assertEqual(self.store.get("/api/ppe/types/").status_code, 200)
        self.assertEqual(self.store.get("/api/ppe/issues/").status_code, 200)

    def test_the_staff_list_is_a_name_and_number_lookup_only(self):
        response = self.store.get("/api/employees/")
        self.assertEqual(response.status_code, 200)
        row = response.json()["results"][0]
        self.assertEqual((row["employee_id"], row["full_name"], row["department_name"]), ("000100", "Ada Okafor", "Extrusion"))
        for leaked in ("phone", "biometric_user_id", "biometric_identities", "shift_plan", "current_shift", "basic_salary", "attention_reasons", "email", "date_of_birth"):
            self.assertNotIn(leaked, row)

    def test_the_lookup_only_lists_active_staff(self):
        Employee.objects.create(employee_id="000101", first_name="Gone", last_name="Away", status="inactive")
        self.assertEqual([r["employee_id"] for r in self.store.get("/api/employees/").json()["results"]], ["000100"])

    def test_everything_else_is_closed(self):
        closed = [
            f"/api/employees/{self.person.pk}/", f"/api/employees/{self.person.pk}/profile/", "/api/employees/summary/", "/api/employees/hires-exits/",
            "/api/employees/accommodation-report/", "/api/attendance/dashboard/", "/api/attendance/today/", "/api/attendance/records/?employee=1",
            "/api/attendance/roster/", "/api/attendance/shifts/", "/api/attendance/shift-plans/", "/api/attendance/overtime/", "/api/attendance/exceptions/",
            "/api/attendance/exceptions/queue/", "/api/attendance/biometric-events/", "/api/attendance/devices/", "/api/leave/requests/", "/api/leave/types/",
            "/api/leave/policies/", "/api/audit/activity/", "/api/reports/weekly/", "/api/payroll/periods/",
        ]
        for url in closed:
            self.assertEqual(self.store.get(url).status_code, 403, url)

    def test_they_can_still_use_their_own_account_pages(self):
        self.assertEqual(self.store.get("/api/auth/me/").status_code, 200)
        self.assertEqual(self.store.get("/api/notifications/unread-count/").status_code, 200)


class PeopleWhoNeedMoreStillGetIt(TestCase):
    def setUp(self):
        self.person = Employee.objects.create(employee_id="000200", first_name="Bo", last_name="Eze", status="active", phone="0803", biometric_user_id="200")

    def test_staff_with_the_directory_permission_see_the_whole_directory(self):
        row = client_for(user_with("hr-viewer", "view_employee")).get("/api/employees/").json()["results"][0]
        self.assertEqual(row["phone"], "0803")

    def test_someone_who_raises_leave_gets_the_lookup_and_the_leave_pages(self):
        client = client_for(user_with("leave-clerk", "raise_leave_request"))
        self.assertEqual(client.get("/api/leave/types/").status_code, 200)
        self.assertNotIn("phone", client.get("/api/employees/").json()["results"][0])
        self.assertEqual(client.get("/api/attendance/dashboard/").status_code, 403)

    def test_an_attendance_reviewer_sees_attendance_but_not_the_directory_details(self):
        client = client_for(user_with("attendance-reviewer", "review_attendanceexception"))
        self.assertEqual(client.get("/api/attendance/exceptions/queue/").status_code, 200)
        self.assertEqual(client.get(f"/api/employees/{self.person.pk}/").status_code, 403)

    def test_a_superuser_sees_everything(self):
        boss = get_user_model().objects.create_superuser(username="boss-all", password="pw", email="b@x.co")
        client = client_for(boss)
        for url in ("/api/employees/summary/", "/api/attendance/dashboard/", "/api/leave/requests/", "/api/audit/activity/", "/api/reports/weekly/"):
            self.assertEqual(client.get(url).status_code, 200, url)

    def test_a_person_with_no_permissions_at_all_sees_none_of_it(self):
        client = client_for(user_with("nothing"))
        for url in ("/api/employees/", "/api/ppe/types/", "/api/leave/types/", "/api/attendance/shifts/", "/api/audit/activity/"):
            self.assertEqual(client.get(url).status_code, 403, url)

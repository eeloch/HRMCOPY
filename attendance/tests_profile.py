from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from attendance.models import AttendanceException, DailyAttendance, Shift
from employees.models import Employee


class EmployeeAttendanceHistoryApiTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("profile-reader", password="password")
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.employee = Employee.objects.create(employee_id="PROFILE001", first_name="Profile", last_name="Employee")
        self.other_employee = Employee.objects.create(employee_id="PROFILE002", first_name="Other", last_name="Employee")
        self.shift = Shift.objects.create(name="Profile Day", start_time="07:00", end_time="19:00")

    def test_history_is_scoped_to_the_requested_employee_and_exposes_conflict_label(self):
        attendance = DailyAttendance.objects.create(employee=self.employee, date=date(2026, 9, 3), shift=self.shift, status="late")
        DailyAttendance.objects.create(employee=self.other_employee, date=date(2026, 9, 3), shift=self.shift, status="present")
        AttendanceException.objects.create(attendance=attendance, exception_type="leave_punch_conflict", status="pending")

        response = self.client.get(f"/api/attendance/records/?employee={self.employee.pk}")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["operational_status"], "leave_punch_conflict")

    def test_history_requires_employee_filter(self):
        response = self.client.get("/api/attendance/records/")
        self.assertEqual(response.status_code, 400)

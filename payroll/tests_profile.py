from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from rest_framework.test import APIClient

from employees.models import Employee
from payroll.models import EmployeePayroll, PayrollPeriod


class EmployeePayrollHistoryApiTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("payroll-reader", password="password")
        self.user.user_permissions.add(Permission.objects.get(codename="view_payroll"))
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.employee = Employee.objects.create(employee_id="PAY001", first_name="Payroll", last_name="Employee")
        self.other_employee = Employee.objects.create(employee_id="PAY002", first_name="Other", last_name="Employee")
        self.period = PayrollPeriod.objects.create(year=2026, month=9)

    def test_history_is_scoped_to_the_requested_employee(self):
        EmployeePayroll.objects.create(payroll_period=self.period, employee=self.employee, net_pay="200000.00")
        EmployeePayroll.objects.create(payroll_period=self.period, employee=self.other_employee, net_pay="100000.00")

        response = self.client.get(f"/api/payroll/employee-payrolls/?employee={self.employee.pk}")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["employee"], self.employee.pk)

    def test_history_requires_view_permission(self):
        unauthorized = get_user_model().objects.create_user("not-payroll", password="password")
        self.client.force_authenticate(unauthorized)

        response = self.client.get(f"/api/payroll/employee-payrolls/?employee={self.employee.pk}")
        self.assertEqual(response.status_code, 403)

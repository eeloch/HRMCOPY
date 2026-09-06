from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework import status
from rest_framework.test import APITestCase

from .models import BiometricIdentity, Department, Employee, Position


class EmployeeImportAPIViewTests(APITestCase):

    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="importer",
            password="test-password",
        )
        self.client.force_authenticate(
            user=self.user
        )

        self.department = Department.objects.create(
            name="Operations"
        )
        Position.objects.create(
            department=self.department,
            name="Operator",
        )

    def upload(self, content):
        return self.client.post(
            "/api/employees/import/",
            {
                "file": SimpleUploadedFile(
                    "employees.csv",
                    content.encode("utf-8"),
                    content_type="text/csv",
                )
            },
            format="multipart",
        )

    def test_imports_a_validated_spreadsheet(self):
        response = self.upload(
            "employee_id,first_name,last_name,department,position\n"
            "EMP-001,Ada,Okafor,Operations,Operator\n"
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_201_CREATED,
        )
        self.assertEqual(
            response.data["summary"],
            {
                "imported": 1,
                "skipped": 0,
                "failed": 0,
            },
        )
        self.assertTrue(
            Employee.objects.filter(
                employee_id="EMP-001"
            ).exists()
        )

    def test_rejects_invalid_spreadsheet_without_importing_rows(self):
        response = self.upload(
            "employee_id,first_name,last_name,department,position\n"
            "EMP-002,Ada,Okafor,Missing,Operator\n"
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_400_BAD_REQUEST,
        )
        self.assertEqual(
            response.data["summary"]["imported"],
            0,
        )
        self.assertEqual(
            Employee.objects.count(),
            0,
        )


class EmployeeProfileAPIViewTests(APITestCase):

    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="profile-viewer",
            password="test-password",
        )
        self.client.force_authenticate(user=self.user)
        self.employee = Employee.objects.create(
            employee_id="EMP-100",
            first_name="Chidi",
            last_name="Nwosu",
        )

    def test_profile_with_no_biometric_identity(self):
        response = self.client.get(f"/api/employees/{self.employee.pk}/profile/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsNone(response.data["biometric"])

    def test_profile_with_a_biometric_identity(self):
        BiometricIdentity.objects.create(
            employee=self.employee,
            system="vendor_flask_gateway",
            source_identifier="gateway",
            external_user_id="42",
        )

        response = self.client.get(f"/api/employees/{self.employee.pk}/profile/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            response.data["biometric"],
            {"user_id": "42", "system": "vendor_flask_gateway", "source": "gateway"},
        )

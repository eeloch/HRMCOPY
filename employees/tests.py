from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework import status
from rest_framework.test import APITestCase

from notifications.models import Notification
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
                "updated": 0,
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

    def test_rejects_existing_employee_id_by_default(self):
        Employee.objects.create(
            employee_id="EMP-003", first_name="Original", last_name="Name",
        )

        response = self.upload(
            "employee_id,first_name,last_name,department,position\n"
            "EMP-003,Ada,Okafor,Operations,Operator\n"
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(Employee.objects.get(employee_id="EMP-003").first_name, "Original")

    def test_update_existing_flag_updates_the_existing_employee_instead_of_rejecting(self):
        employee = Employee.objects.create(
            employee_id="EMP-004", first_name="Original", last_name="Name",
        )

        response = self.client.post(
            "/api/employees/import/",
            {
                "file": SimpleUploadedFile(
                    "employees.csv",
                    (
                        "employee_id,first_name,last_name,department,position\n"
                        "EMP-004,Updated,Person,Operations,Operator\n"
                    ).encode("utf-8"),
                    content_type="text/csv",
                ),
                "update_existing": "true",
            },
            format="multipart",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["summary"], {"imported": 0, "updated": 1, "skipped": 0, "failed": 0})
        employee.refresh_from_db()
        self.assertEqual(employee.first_name, "Updated")
        self.assertEqual(employee.last_name, "Person")
        self.assertEqual(Employee.objects.count(), 1)

    def test_update_existing_flag_still_creates_genuinely_new_employees(self):
        Employee.objects.create(employee_id="EMP-005", first_name="Existing", last_name="Person")

        response = self.client.post(
            "/api/employees/import/",
            {
                "file": SimpleUploadedFile(
                    "employees.csv",
                    (
                        "employee_id,first_name,last_name,department,position\n"
                        "EMP-005,Existing,Person,Operations,Operator\n"
                        "EMP-006,Brand,New,Operations,Operator\n"
                    ).encode("utf-8"),
                    content_type="text/csv",
                ),
                "update_existing": "true",
            },
            format="multipart",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["summary"], {"imported": 1, "updated": 1, "skipped": 0, "failed": 0})
        self.assertTrue(Employee.objects.filter(employee_id="EMP-006").exists())

    def test_duplicate_ids_within_the_same_file_still_fail_even_with_update_existing(self):
        response = self.client.post(
            "/api/employees/import/",
            {
                "file": SimpleUploadedFile(
                    "employees.csv",
                    (
                        "employee_id,first_name,last_name,department,position\n"
                        "EMP-007,First,Row,Operations,Operator\n"
                        "EMP-007,Second,Row,Operations,Operator\n"
                    ).encode("utf-8"),
                    content_type="text/csv",
                ),
                "update_existing": "true",
            },
            format="multipart",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(Employee.objects.count(), 0)

    def test_skip_invalid_flag_imports_the_good_rows_and_reports_the_rest(self):
        response = self.client.post(
            "/api/employees/import/",
            {
                "file": SimpleUploadedFile(
                    "employees.csv",
                    (
                        "employee_id,first_name,last_name,department,position\n"
                        "EMP-010,Good,Row,Operations,Operator\n"
                        "EMP-011,Bad,Row,NoSuchDepartment,Operator\n"
                    ).encode("utf-8"),
                    content_type="text/csv",
                ),
                "skip_invalid": "true",
            },
            format="multipart",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["summary"], {"imported": 1, "updated": 0, "skipped": 1, "failed": 0})
        self.assertTrue(Employee.objects.filter(employee_id="EMP-010").exists())
        self.assertFalse(Employee.objects.filter(employee_id="EMP-011").exists())
        self.assertEqual(response.data["errors"][0]["row"], 3)

    def test_skip_invalid_flag_still_fails_when_every_row_is_invalid(self):
        response = self.client.post(
            "/api/employees/import/",
            {
                "file": SimpleUploadedFile(
                    "employees.csv",
                    (
                        "employee_id,first_name,last_name,department,position\n"
                        "EMP-012,Bad,Row,NoSuchDepartment,Operator\n"
                    ).encode("utf-8"),
                    content_type="text/csv",
                ),
                "skip_invalid": "true",
            },
            format="multipart",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(Employee.objects.count(), 0)

    def test_skip_invalid_combines_with_update_existing(self):
        Employee.objects.create(employee_id="EMP-013", first_name="Original", last_name="Name")

        response = self.client.post(
            "/api/employees/import/",
            {
                "file": SimpleUploadedFile(
                    "employees.csv",
                    (
                        "employee_id,first_name,last_name,department,position\n"
                        "EMP-013,Updated,Name,Operations,Operator\n"
                        "EMP-014,Bad,Row,NoSuchDepartment,Operator\n"
                    ).encode("utf-8"),
                    content_type="text/csv",
                ),
                "update_existing": "true",
                "skip_invalid": "true",
            },
            format="multipart",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["summary"], {"imported": 0, "updated": 1, "skipped": 1, "failed": 0})
        self.assertEqual(Employee.objects.get(employee_id="EMP-013").first_name, "Updated")

    def test_missing_department_no_longer_blocks_import(self):
        response = self.upload(
            "employee_id,first_name,last_name,department,position\n"
            "EMP-015,No,Department,,\n"
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["summary"]["imported"], 1)
        employee = Employee.objects.get(employee_id="EMP-015")
        self.assertIsNone(employee.department_id)

    def test_missing_department_notifies_superusers_but_not_regular_users(self):
        admin = get_user_model().objects.create_superuser(username="hr-admin", password="test-password")
        get_user_model().objects.create_user(username="regular-user", password="test-password")

        response = self.upload(
            "employee_id,first_name,last_name,department,position\n"
            "EMP-016,No,Department,,\n"
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        notification = Notification.objects.get(recipient=admin)
        self.assertEqual(notification.event_type, "employees.import_incomplete_profile")
        self.assertIn("No Department", notification.message)
        self.assertFalse(Notification.objects.filter(recipient__username="regular-user").exists())

    def test_multiple_incomplete_employees_send_one_notification_per_admin(self):
        get_user_model().objects.create_superuser(username="hr-admin-2", password="test-password")

        response = self.upload(
            "employee_id,first_name,last_name,department,position\n"
            "EMP-017,First,Employee,,\n"
            "EMP-018,Second,Employee,,\n"
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Notification.objects.filter(event_type="employees.import_incomplete_profile").count(), 1)
        self.assertIn("2 employee(s)", Notification.objects.get(event_type="employees.import_incomplete_profile").message)

    def test_employees_with_a_department_do_not_trigger_a_notification(self):
        get_user_model().objects.create_superuser(username="hr-admin-3", password="test-password")

        response = self.upload(
            "employee_id,first_name,last_name,department,position\n"
            "EMP-019,Has,Department,Operations,Operator\n"
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertFalse(Notification.objects.filter(event_type="employees.import_incomplete_profile").exists())


class EmployeeImportPreviewAPIViewTests(APITestCase):

    def setUp(self):
        self.user = get_user_model().objects.create_user(username="previewer", password="test-password")
        self.client.force_authenticate(user=self.user)
        self.department = Department.objects.create(name="Operations")
        Position.objects.create(department=self.department, name="Operator")
        Employee.objects.create(employee_id="EMP-008", first_name="Original", last_name="Name")

    def preview(self, content, update_existing=None):
        payload = {
            "file": SimpleUploadedFile("employees.csv", content.encode("utf-8"), content_type="text/csv"),
        }
        if update_existing is not None:
            payload["update_existing"] = update_existing
        return self.client.post("/api/employees/import/preview/", payload, format="multipart")

    def test_existing_employee_id_blocks_import_by_default(self):
        response = self.preview(
            "employee_id,first_name,last_name,department,position\n"
            "EMP-008,Ada,Okafor,Operations,Operator\n"
        )

        self.assertFalse(response.data["can_import"])
        self.assertIn("Employee ID already exists in HRM.", response.data["results"][0]["errors"])

    def test_update_existing_flag_turns_the_duplicate_into_a_warning_not_an_error(self):
        response = self.preview(
            "employee_id,first_name,last_name,department,position\n"
            "EMP-008,Ada,Okafor,Operations,Operator\n",
            update_existing="true",
        )

        self.assertTrue(response.data["can_import"])
        row = response.data["results"][0]
        self.assertEqual(row["errors"], [])
        self.assertTrue(any("will UPDATE that record" in warning for warning in row["warnings"]))
        self.assertEqual(row["data"]["existing_employee_id"], Employee.objects.get(employee_id="EMP-008").id)

    def test_update_existing_flag_does_not_suppress_genuine_within_file_duplicates(self):
        response = self.preview(
            "employee_id,first_name,last_name,department,position\n"
            "EMP-009,First,Row,Operations,Operator\n"
            "EMP-009,Second,Row,Operations,Operator\n",
            update_existing="true",
        )

        self.assertFalse(response.data["can_import"])
        self.assertTrue(any("Duplicate Employee ID" in error for error in response.data["results"][0]["errors"]))

    def test_misspelled_department_gets_a_did_you_mean_suggestion(self):
        response = self.preview(
            "employee_id,first_name,last_name,department,position\n"
            "EMP-020,Ada,Okafor,Opertaions,Operator\n"
        )

        errors = response.data["results"][0]["errors"]
        self.assertTrue(any("Did you mean 'Operations'?" in error for error in errors))

    def test_misspelled_position_gets_a_did_you_mean_suggestion(self):
        response = self.preview(
            "employee_id,first_name,last_name,department,position\n"
            "EMP-021,Ada,Okafor,Operations,Opertaor\n"
        )

        errors = response.data["results"][0]["errors"]
        self.assertTrue(any("Did you mean 'Operator'?" in error for error in errors))

    def test_wildly_different_department_name_gets_no_suggestion(self):
        response = self.preview(
            "employee_id,first_name,last_name,department,position\n"
            "EMP-022,Ada,Okafor,Zzzzzzzzz,Operator\n"
        )

        errors = response.data["results"][0]["errors"]
        self.assertFalse(any("Did you mean" in error for error in errors))

    def test_missing_department_is_a_warning_not_an_error(self):
        response = self.preview(
            "employee_id,first_name,last_name,department,position\n"
            "EMP-023,Ada,Okafor,,\n"
        )

        row = response.data["results"][0]
        self.assertTrue(row["valid"])
        self.assertTrue(response.data["can_import"])
        self.assertTrue(any("without a department" in warning for warning in row["warnings"]))


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

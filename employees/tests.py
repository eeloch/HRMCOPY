from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
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


class EmployeeCurrentShiftTests(APITestCase):
    """current_shift must reflect today's live roster (including a rotation's weekly Day/Night swap),
    never the old static per-employee ShiftAssignment, which is not touched by the shift-plan system."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(username="shift-viewer", password="test-password")
        self.client.force_authenticate(user=self.user)
        self.employee = Employee.objects.create(employee_id="EMP-200", first_name="Ada", last_name="Obi")

    def test_no_roster_day_today_means_no_current_shift(self):
        response = self.client.get(f"/api/employees/{self.employee.pk}/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsNone(response.data["current_shift"])

    def test_a_rest_day_today_means_no_current_shift(self):
        from attendance.models import EmployeeRosterDay
        from django.utils import timezone

        EmployeeRosterDay.objects.create(employee=self.employee, date=timezone.localdate(), status="rest")
        response = self.client.get(f"/api/employees/{self.employee.pk}/")
        self.assertIsNone(response.data["current_shift"])

    def test_todays_roster_shift_is_returned(self):
        from attendance.models import EmployeeRosterDay, Shift
        from django.utils import timezone

        night = Shift.objects.get(name="Night Shift")
        EmployeeRosterDay.objects.create(employee=self.employee, date=timezone.localdate(), status="work", shift=night)
        response = self.client.get(f"/api/employees/{self.employee.pk}/")
        self.assertEqual(response.data["current_shift"]["name"], "Night Shift")

    def test_an_old_style_shift_assignment_alone_is_not_enough(self):
        """A leftover ShiftAssignment row with no matching roster day must not surface as the current shift."""
        from datetime import date

        from attendance.models import Shift, ShiftAssignment

        day = Shift.objects.get(name="Day Shift")
        ShiftAssignment.objects.create(employee=self.employee, shift=day, start_date=date(2020, 1, 1))
        response = self.client.get(f"/api/employees/{self.employee.pk}/")
        self.assertIsNone(response.data["current_shift"])


class EmployeeStatusRevokesBiometricAccessTests(TestCase):

    def setUp(self):
        self.employee = Employee.objects.create(employee_id="EMP-100", first_name="Ada", last_name="Okafor", status="active")
        self.identity = BiometricIdentity.objects.create(
            employee=self.employee, system="vendor_flask_gateway", source_identifier="DEVICE-1", external_user_id="100",
        )

    def test_leaving_active_status_revokes_all_biometric_identities(self):
        self.employee.status = "inactive"
        self.employee.save()

        self.identity.refresh_from_db()
        self.assertFalse(self.identity.is_active)

    def test_suspended_also_revokes_access(self):
        self.employee.status = "suspended"
        self.employee.save()

        self.identity.refresh_from_db()
        self.assertFalse(self.identity.is_active)

    def test_saving_without_a_status_change_does_not_touch_identities(self):
        self.employee.first_name = "Adaeze"
        self.employee.save()

        self.identity.refresh_from_db()
        self.assertTrue(self.identity.is_active)

    def test_returning_to_active_automatically_restores_access(self):
        self.employee.status = "inactive"
        self.employee.save()
        self.employee.status = "active"
        self.employee.save()

        self.identity.refresh_from_db()
        self.assertTrue(self.identity.is_active)

    def test_restore_covers_every_device_the_employee_was_revoked_on(self):
        BiometricIdentity.objects.create(
            employee=self.employee, system="vendor_flask_gateway", source_identifier="DEVICE-2", external_user_id="200",
        )
        self.employee.status = "terminated"
        self.employee.save()

        self.employee.status = "active"
        self.employee.save()

        self.assertEqual(
            BiometricIdentity.objects.filter(employee=self.employee, is_active=False).count(), 0,
        )

    def test_new_employee_created_directly_as_active_is_unaffected(self):
        # previous_status is None for a brand-new row - must not be treated as "returning to active".
        employee = Employee.objects.create(employee_id="EMP-102", first_name="Brand", last_name="New", status="active")
        self.assertEqual(employee.status, "active")

    def test_creating_an_already_inactive_employee_does_not_crash(self):
        employee = Employee.objects.create(employee_id="EMP-101", first_name="New", last_name="Hire", status="inactive")
        self.assertEqual(employee.status, "inactive")

    def test_multiple_devices_are_all_revoked_together(self):
        BiometricIdentity.objects.create(
            employee=self.employee, system="vendor_flask_gateway", source_identifier="DEVICE-2", external_user_id="200",
        )

        self.employee.status = "terminated"
        self.employee.save()

        self.assertEqual(
            BiometricIdentity.objects.filter(employee=self.employee, is_active=True).count(), 0,
        )


class SalaryVisibilityPermissionTests(APITestCase):

    def setUp(self):
        self.privileged = get_user_model().objects.create_user(username="pay-admin", password="test-password")
        self.privileged.user_permissions.add(Permission.objects.get(codename="view_salary", content_type__app_label="employees"))
        self.restricted = get_user_model().objects.create_user(username="regular-staff", password="test-password")
        self.department = Department.objects.create(name="Operations")
        Position.objects.create(department=self.department, name="Operator")
        self.employee = Employee.objects.create(
            employee_id="SAL-001", first_name="Ada", last_name="Okafor", basic_salary="150000.00",
        )

    def test_restricted_user_does_not_see_salary_in_list(self):
        self.client.force_authenticate(self.restricted)
        response = self.client.get("/api/employees/")

        row = next(item for item in response.data["results"] if item["employee_id"] == "SAL-001")
        self.assertNotIn("basic_salary", row)

    def test_privileged_user_sees_salary_in_list(self):
        self.client.force_authenticate(self.privileged)
        response = self.client.get("/api/employees/")

        row = next(item for item in response.data["results"] if item["employee_id"] == "SAL-001")
        self.assertEqual(row["basic_salary"], "150000.00")

    def test_restricted_user_does_not_see_salary_in_detail(self):
        self.client.force_authenticate(self.restricted)
        response = self.client.get(f"/api/employees/{self.employee.pk}/")

        self.assertNotIn("basic_salary", response.data)

    def test_restricted_user_does_not_see_salary_in_profile(self):
        self.client.force_authenticate(self.restricted)
        response = self.client.get(f"/api/employees/{self.employee.pk}/profile/")

        self.assertNotIn("basic_salary", response.data)

    def test_privileged_user_sees_salary_in_profile(self):
        self.client.force_authenticate(self.privileged)
        response = self.client.get(f"/api/employees/{self.employee.pk}/profile/")

        self.assertEqual(response.data["basic_salary"], "150000.00")

    def test_restricted_user_cannot_set_salary_when_creating_an_employee(self):
        self.client.force_authenticate(self.restricted)
        response = self.client.post("/api/employees/", {
            "employee_id": "SAL-002", "first_name": "New", "last_name": "Hire",
            "department": self.department.id, "basic_salary": "200000.00",
        }, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("basic_salary", response.data)

    def test_restricted_user_can_create_an_employee_without_touching_salary(self):
        self.client.force_authenticate(self.restricted)
        response = self.client.post("/api/employees/", {
            "employee_id": "SAL-003", "first_name": "New", "last_name": "Hire",
            "department": self.department.id,
        }, format="json")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Employee.objects.get(employee_id="SAL-003").basic_salary, 0)

    def test_privileged_user_can_set_salary_when_creating_an_employee(self):
        self.client.force_authenticate(self.privileged)
        response = self.client.post("/api/employees/", {
            "employee_id": "SAL-004", "first_name": "New", "last_name": "Hire",
            "department": self.department.id, "basic_salary": "200000.00",
        }, format="json")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(str(Employee.objects.get(employee_id="SAL-004").basic_salary), "200000.00")

    def test_restricted_user_cannot_change_salary_on_update(self):
        self.client.force_authenticate(self.restricted)
        response = self.client.patch(f"/api/employees/{self.employee.pk}/", {"basic_salary": "999999.00"}, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.employee.refresh_from_db()
        self.assertEqual(str(self.employee.basic_salary), "150000.00")

    def test_restricted_user_can_update_other_fields_without_touching_salary(self):
        self.client.force_authenticate(self.restricted)
        response = self.client.patch(f"/api/employees/{self.employee.pk}/", {"phone": "08012345678"}, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.employee.refresh_from_db()
        self.assertEqual(self.employee.phone, "08012345678")
        self.assertEqual(str(self.employee.basic_salary), "150000.00")

    def test_privileged_user_can_change_salary_on_update(self):
        self.client.force_authenticate(self.privileged)
        response = self.client.patch(f"/api/employees/{self.employee.pk}/", {"basic_salary": "175000.00"}, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.employee.refresh_from_db()
        self.assertEqual(str(self.employee.basic_salary), "175000.00")

    def test_superuser_sees_and_can_set_salary_without_explicit_grant(self):
        superuser = get_user_model().objects.create_superuser(username="root-admin", password="test-password")
        self.client.force_authenticate(superuser)

        response = self.client.get(f"/api/employees/{self.employee.pk}/")
        self.assertEqual(response.data["basic_salary"], "150000.00")

        response = self.client.patch(f"/api/employees/{self.employee.pk}/", {"basic_salary": "160000.00"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)


class SalaryVisibilityBulkImportTests(APITestCase):

    def setUp(self):
        self.privileged = get_user_model().objects.create_user(username="import-pay-admin", password="test-password")
        self.privileged.user_permissions.add(Permission.objects.get(codename="view_salary", content_type__app_label="employees"))
        self.restricted = get_user_model().objects.create_user(username="import-regular-staff", password="test-password")
        self.department = Department.objects.create(name="Operations")
        Position.objects.create(department=self.department, name="Operator")
        self.existing = Employee.objects.create(
            employee_id="BULK-SAL-001", first_name="Existing", last_name="Person", basic_salary="150000.00",
        )

    def upload(self, content):
        return SimpleUploadedFile("employees.csv", content.encode("utf-8"), content_type="text/csv")

    def test_restricted_importer_does_not_block_the_whole_row_on_salary(self):
        self.client.force_authenticate(self.restricted)
        response = self.client.post("/api/employees/import/", {
            "file": self.upload(
                "employee_id,first_name,last_name,department,position,basic_salary\n"
                "BULK-SAL-002,New,Hire,Operations,Operator,300000\n"
            ),
        }, format="multipart")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Employee.objects.get(employee_id="BULK-SAL-002").basic_salary, 0)

    def test_restricted_importer_does_not_overwrite_an_existing_employees_salary(self):
        self.client.force_authenticate(self.restricted)
        response = self.client.post("/api/employees/import/", {
            "file": self.upload(
                "employee_id,first_name,last_name,department,position,basic_salary\n"
                "BULK-SAL-001,Existing,Person,Operations,Operator,999999\n"
            ),
            "update_existing": "true",
        }, format="multipart")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.existing.refresh_from_db()
        self.assertEqual(str(self.existing.basic_salary), "150000.00")

    def test_privileged_importer_sets_salary_from_the_sheet(self):
        self.client.force_authenticate(self.privileged)
        response = self.client.post("/api/employees/import/", {
            "file": self.upload(
                "employee_id,first_name,last_name,department,position,basic_salary\n"
                "BULK-SAL-003,New,Hire,Operations,Operator,300000\n"
            ),
        }, format="multipart")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(str(Employee.objects.get(employee_id="BULK-SAL-003").basic_salary), "300000.00")


class BulkImportAccommodationTests(APITestCase):
    """The Accommodation and ROOM ALLOCATED columns place people through the normal Bulk Import.
    Rooms come from the accommodation tracker, so the import never creates one."""

    HEADER = "employee_id,first_name,last_name,status,accommodation,room allocated\n"

    def setUp(self):
        self.user = get_user_model().objects.create_user(username="acc-importer", password="test-password")
        self.client.force_authenticate(user=self.user)
        from accommodation.models import Building, Room
        hostel = Building.objects.create(name="Main Hostel")
        Room.objects.create(building=hostel, name="Room 301", capacity=4)

    def upload(self, rows, path="/api/employees/import/", **extra):
        payload = {"file": SimpleUploadedFile("staff.csv", (self.HEADER + rows).encode("utf-8"), content_type="text/csv"), **extra}
        return self.client.post(path, payload, format="multipart")

    def test_room_yes_and_no_become_inside_outside_and_none(self):
        response = self.upload(
            "000001,Ada,One,active,YES,Room 301 - Bed 1\n"
            "000002,Bayo,Two,active,YES,\n"
            "000003,Chi,Three,active,NO,\n"
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(response.data["accommodation"], {"inside": 1, "inside_no_bed": 0, "unknown_room": 0, "gender_mismatch": 0, "outside": 1, "none": 1, "vacated": 0})
        one, two, three = (Employee.objects.get(employee_id=n) for n in ("000001", "000002", "000003"))
        self.assertEqual((one.room_assignment.room.name, one.room_assignment.bed_number), ("Room 301", 1))
        self.assertTrue(one.lives_in_company_hostel and not one.lives_in_external_accommodation)
        self.assertTrue(two.lives_in_external_accommodation and not two.lives_in_company_hostel)
        self.assertFalse(three.lives_in_company_hostel or three.lives_in_external_accommodation)

    def test_a_room_that_does_not_exist_is_not_created(self):
        from accommodation.models import Room
        response = self.upload("000001,Ada,One,active,YES,Room 999 - Bed 1\n")
        self.assertEqual(response.data["accommodation"]["unknown_room"], 1)
        self.assertFalse(Room.objects.filter(name="Room 999").exists())
        self.assertTrue(Employee.objects.get(employee_id="000001").lives_in_company_hostel)

    def test_someone_who_has_left_frees_their_bed(self):
        from accommodation.models import RoomAssignment
        self.upload("000001,Ada,One,active,YES,Room 301 - Bed 1\n")
        self.upload("000001,Ada,One,inactive,YES,Room 301 - Bed 1\n", update_existing="true")
        self.assertFalse(RoomAssignment.objects.exists())

    def test_a_sheet_without_accommodation_columns_leaves_placement_alone(self):
        self.upload("000001,Ada,One,active,YES,Room 301 - Bed 1\n")
        response = self.client.post("/api/employees/import/", {"file": SimpleUploadedFile("s.csv", b"employee_id,first_name,last_name\n000001,Ada,Renamed\n", content_type="text/csv"), "update_existing": "true"}, format="multipart")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(response.data["accommodation"]["inside"], 0)
        self.assertTrue(Employee.objects.get(employee_id="000001").room_assignment)

    def test_two_people_on_the_same_bed_are_placed_the_second_without_a_bed_number(self):
        response = self.upload("000001,Ada,One,active,YES,Room 301 - Bed 1\n000002,Bayo,Two,active,YES,Room 301 - Bed 1\n")
        self.assertEqual(response.data["accommodation"]["inside"], 1)
        self.assertEqual(response.data["accommodation"]["inside_no_bed"], 1)

    def test_the_preview_says_what_will_happen_and_changes_nothing(self):
        response = self.upload("000001,Ada,One,active,YES,Room 301 - Bed 1\n000002,Bayo,Two,active,YES,\n", path="/api/employees/import/preview/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual([row["data"]["accommodation_placement"] for row in response.data["results"]], ["inside", "outside"])
        self.assertFalse(Employee.objects.exists())

    def test_gender_column_is_saved_on_the_employee(self):
        payload = {"file": SimpleUploadedFile("g.csv", b"employee_id,first_name,last_name,gender\n000001,Ada,One,Female\n000002,Bayo,Two,M\n", content_type="text/csv")}
        self.assertEqual(self.client.post("/api/employees/import/", payload, format="multipart").status_code, status.HTTP_201_CREATED)
        self.assertEqual(Employee.objects.get(employee_id="000001").gender, "female")
        self.assertEqual(Employee.objects.get(employee_id="000002").gender, "male")


class EmployeeDirectorySummaryAndFiltersTests(APITestCase):
    """The Employee Directory's summary strip and filter chips: counts and query-param filters."""

    def setUp(self):
        from datetime import date, time

        from attendance.models import Shift, ShiftPlan
        from attendance.services.shift_plans import assign_plan

        self.user = get_user_model().objects.create_user(username="directory-viewer", password="test-password")
        self.client.force_authenticate(self.user)
        self.department = Department.objects.create(name="Extrusion")

        self.day = Shift.objects.get_or_create(name="Dir Day", defaults={"start_time": time(7), "end_time": time(19)})[0]
        self.plan = ShiftPlan.objects.create(name="Dir Permanent Day", kind="fixed", shift=self.day, working_weekdays=[0, 1, 2, 3, 4, 5])

        self.on_plan = Employee.objects.create(employee_id="DIR-001", first_name="On", last_name="Plan", department=self.department, status="active", employment_type="permanent", gender="female", bank_name="GTB", account_number="0123456789", bank_code="058", biometric_user_id="B1")
        self.no_plan = Employee.objects.create(employee_id="DIR-002", first_name="No", last_name="Plan", department=self.department, status="active", employment_type="casual", gender="male")
        self.inactive = Employee.objects.create(employee_id="DIR-003", first_name="In", last_name="Active", department=self.department, status="inactive")
        assign_plan([self.on_plan], self.plan, start_date=date(2026, 1, 1))

    def test_summary_counts(self):
        data = self.client.get("/api/employees/summary/").json()
        self.assertEqual(data["total"], 3)
        self.assertEqual(data["by_status"]["active"], 2)
        self.assertEqual(data["by_status"]["inactive"], 1)
        self.assertEqual(data["not_assigned_shift_plan"], 1)  # no_plan, among active employees
        self.assertEqual(data["missing_bank_details"], 1)  # no_plan has no bank details
        self.assertEqual(data["missing_biometric"], 1)
        self.assertEqual(data["gender"], {"male": 1, "female": 1, "unspecified": 0})
        department_row = next(row for row in data["by_department"] if row["name"] == "Extrusion")
        self.assertEqual(department_row["count"], 2)  # active only, not the inactive one
        plan_row = next(row for row in data["by_shift_plan"] if row["id"] == self.plan.pk)
        self.assertEqual(plan_row["count"], 1)
        self.assertEqual(data["needs_attention"], 1)  # no_plan again: no plan AND no bank details AND no biometric

    def test_filter_by_shift_plan_id(self):
        response = self.client.get(f"/api/employees/?shift_plan={self.plan.pk}")
        self.assertEqual([row["employee_id"] for row in response.json()["results"]], ["DIR-001"])

    def test_filter_by_shift_plan_not_assigned(self):
        response = self.client.get("/api/employees/?shift_plan=not_assigned")
        ids = {row["employee_id"] for row in response.json()["results"]}
        self.assertEqual(ids, {"DIR-002", "DIR-003"})  # inactive employees have no plan either

    def test_filter_by_employment_type(self):
        response = self.client.get("/api/employees/?employment_type=casual")
        self.assertEqual([row["employee_id"] for row in response.json()["results"]], ["DIR-002"])

    def test_filter_by_gender(self):
        response = self.client.get("/api/employees/?gender=female")
        self.assertEqual([row["employee_id"] for row in response.json()["results"]], ["DIR-001"])

    def test_needs_attention_filter(self):
        response = self.client.get("/api/employees/?needs_attention=1&status=active")
        self.assertEqual([row["employee_id"] for row in response.json()["results"]], ["DIR-002"])

    def test_list_response_includes_shift_plan_and_current_shift(self):
        response = self.client.get("/api/employees/?status=active")
        row = next(item for item in response.data["results"] if item["employee_id"] == "DIR-001")
        self.assertEqual(row["shift_plan"]["name"], "Dir Permanent Day")

    def test_listing_many_employees_does_not_grow_query_count_per_row(self):
        """Regression guard: current_shift/shift_plan must be bulk-prefetched, not queried per employee."""
        from datetime import date, time

        from attendance.models import Shift, ShiftPlan
        from attendance.services.shift_plans import assign_plan

        night = Shift.objects.get_or_create(name="Dir Night", defaults={"start_time": time(19), "end_time": time(7), "is_overnight": True})[0]
        night_plan = ShiftPlan.objects.create(name="Dir Permanent Night", kind="fixed", shift=night, working_weekdays=[0, 1, 2, 3, 4, 5])
        more = [Employee.objects.create(employee_id=f"DIR-BULK-{i}", first_name="Bulk", last_name=str(i), status="active") for i in range(15)]
        assign_plan(more, night_plan, start_date=date(2026, 1, 1))

        # A handful of fixed, bulk queries (employee ids, the roster/plan/biometric prefetches, the list
        # itself, and Django's own one-time permission-check cache) - none of it grows with employee count.
        with self.assertNumQueries(8):
            response = self.client.get("/api/employees/?status=active")
        self.assertEqual(len(response.data["results"]), 17)  # on_plan, no_plan, and the 15 bulk employees

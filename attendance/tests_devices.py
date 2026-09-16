from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from rest_framework.test import APIClient

from attendance.management.commands.run_aiface_gateway import Command
from attendance.models import BiometricDevice, DeviceCommand
from employees.models import BiometricIdentity, Employee


class BiometricDeviceAPITests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.manager = get_user_model().objects.create_user(
            username="device-manager",
            password="test-password",
        )
        self.manager.user_permissions.add(
            Permission.objects.get(codename="manage_devices")
        )
        self.viewer = get_user_model().objects.create_user(
            username="device-viewer",
            password="test-password",
        )
        self.device = BiometricDevice.objects.create(
            name="Main Entrance",
            serial_number="AYTK14145399",
            location="Factory gate",
            device_type="factory",
            purpose="attendance",
        )

    def test_any_authenticated_user_can_list_devices(self):
        self.client.force_authenticate(self.viewer)
        response = self.client.get("/api/attendance/devices/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["serial_number"], "AYTK14145399")

    def test_list_can_filter_by_purpose(self):
        BiometricDevice.objects.create(
            name="Canteen", serial_number="MEAL0001", location="Canteen",
            device_type="factory", purpose="meal_ticket",
        )
        self.client.force_authenticate(self.viewer)
        response = self.client.get("/api/attendance/devices/?purpose=meal_ticket")
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["serial_number"], "MEAL0001")

    def test_unauthenticated_request_is_rejected(self):
        response = self.client.get("/api/attendance/devices/")
        self.assertEqual(response.status_code, 401)

    def test_viewer_without_permission_cannot_register_device(self):
        self.client.force_authenticate(self.viewer)
        response = self.client.post("/api/attendance/devices/", {
            "name": "New Terminal", "serial_number": "NEW001",
            "location": "Hostel gate", "device_type": "hostel",
        }, format="json")
        self.assertEqual(response.status_code, 403)

    def test_manager_can_register_device(self):
        self.client.force_authenticate(self.manager)
        response = self.client.post("/api/attendance/devices/", {
            "name": "New Terminal", "serial_number": "NEW001",
            "location": "Hostel gate", "device_type": "hostel", "purpose": "meal_ticket",
        }, format="json")
        self.assertEqual(response.status_code, 201)
        device = BiometricDevice.objects.get(serial_number="NEW001")
        self.assertEqual(device.purpose, "meal_ticket")

    def test_manager_can_rename_device(self):
        self.client.force_authenticate(self.manager)
        response = self.client.patch(f"/api/attendance/devices/{self.device.id}/", {
            "name": "Renamed Entrance",
        }, format="json")
        self.assertEqual(response.status_code, 200)
        self.device.refresh_from_db()
        self.assertEqual(self.device.name, "Renamed Entrance")

    def test_viewer_cannot_edit_device(self):
        self.client.force_authenticate(self.viewer)
        response = self.client.patch(f"/api/attendance/devices/{self.device.id}/", {
            "name": "Renamed Entrance",
        }, format="json")
        self.assertEqual(response.status_code, 403)

    def test_manager_can_delete_device(self):
        self.client.force_authenticate(self.manager)
        response = self.client.delete(f"/api/attendance/devices/{self.device.id}/")
        self.assertEqual(response.status_code, 204)
        self.assertFalse(BiometricDevice.objects.filter(pk=self.device.id).exists())

    def test_editing_unknown_device_returns_404(self):
        self.client.force_authenticate(self.manager)
        response = self.client.patch("/api/attendance/devices/999999/", {"name": "x"}, format="json")
        self.assertEqual(response.status_code, 404)

    def test_ip_address_and_online_status_are_not_client_writable(self):
        self.client.force_authenticate(self.manager)
        response = self.client.patch(f"/api/attendance/devices/{self.device.id}/", {
            "is_online": True, "ip_address": "10.0.0.5",
        }, format="json")
        self.assertEqual(response.status_code, 200)
        self.device.refresh_from_db()
        self.assertFalse(self.device.is_online)
        self.assertIsNone(self.device.ip_address)


class GatewayOnlineStatusTests(TestCase):
    def setUp(self):
        self.device = BiometricDevice.objects.create(
            name="Main Entrance",
            serial_number="AYTK14145399",
            location="Factory gate",
            device_type="factory",
        )

    def test_mark_device_online_records_ip_and_last_sync(self):
        Command._mark_device_online("AYTK14145399", "143.105.174.0")

        self.device.refresh_from_db()
        self.assertTrue(self.device.is_online)
        self.assertEqual(self.device.ip_address, "143.105.174.0")
        self.assertIsNotNone(self.device.last_sync_at)

    def test_mark_device_offline_clears_online_flag(self):
        Command._mark_device_online("AYTK14145399", "143.105.174.0")
        Command._mark_device_offline("AYTK14145399")

        self.device.refresh_from_db()
        self.assertFalse(self.device.is_online)

    def test_unregistered_serial_number_is_a_safe_no_op(self):
        Command._mark_device_online("UNKNOWN-SERIAL", "1.2.3.4")
        Command._mark_device_offline("UNKNOWN-SERIAL")


class GatewayCommandQueueTests(TestCase):
    def setUp(self):
        self.device = BiometricDevice.objects.create(
            name="Main Entrance",
            serial_number="AYTK14145399",
            location="Factory gate",
            device_type="factory",
        )

    def test_next_command_to_send_returns_oldest_pending_command(self):
        older = DeviceCommand.objects.create(device=self.device, command_type="refresh_enrolled_ids", payload={})
        DeviceCommand.objects.create(device=self.device, command_type="delete_user", payload={"enrollid": 5})

        result = Command._next_command_to_send("AYTK14145399")

        self.assertIsNotNone(result)
        command_id, wire_message = result
        self.assertEqual(command_id, older.id)
        self.assertEqual(wire_message["cmd"], "getuserids")

    def test_no_pending_command_returns_none(self):
        self.assertIsNone(Command._next_command_to_send("AYTK14145399"))

    def test_a_command_already_in_flight_blocks_the_next_one(self):
        DeviceCommand.objects.create(device=self.device, command_type="refresh_enrolled_ids", payload={}, status="sent")
        DeviceCommand.objects.create(device=self.device, command_type="delete_user", payload={"enrollid": 5})

        self.assertIsNone(Command._next_command_to_send("AYTK14145399"))

    def test_mark_command_sent_records_timestamp(self):
        command = DeviceCommand.objects.create(device=self.device, command_type="refresh_enrolled_ids", payload={})

        Command._mark_command_sent(command.id)

        command.refresh_from_db()
        self.assertEqual(command.status, "sent")
        self.assertIsNotNone(command.sent_at)

    def test_resolve_command_marks_successful_response_acked(self):
        command = DeviceCommand.objects.create(
            device=self.device, command_type="refresh_enrolled_ids", payload={}, status="sent",
        )
        response = {"ret": "getuserids", "sn": "AYTK14145399", "result": True, "count": 2, "record": ["3", "7"]}

        Command._resolve_command("AYTK14145399", response)

        command.refresh_from_db()
        self.assertEqual(command.status, "acked")
        self.assertEqual(command.result, response)
        self.assertIsNotNone(command.completed_at)

    def test_resolve_command_marks_failed_response(self):
        command = DeviceCommand.objects.create(
            device=self.device, command_type="delete_user", payload={"enrollid": 5}, status="sent",
        )
        response = {"ret": "deleteuser", "sn": "AYTK14145399", "result": False, "reason": 1}

        Command._resolve_command("AYTK14145399", response)

        command.refresh_from_db()
        self.assertEqual(command.status, "failed")

    def test_resolve_command_with_no_in_flight_command_is_a_safe_no_op(self):
        Command._resolve_command("AYTK14145399", {"ret": "getuserids", "result": True})


class GatewayIdentityLinkingTests(TestCase):
    def setUp(self):
        self.device = BiometricDevice.objects.create(
            name="Main Entrance",
            serial_number="AYTK14145399",
            location="Factory gate",
            device_type="factory",
        )
        self.employee = Employee.objects.create(employee_id="EMP-001", first_name="Ada", last_name="Okafor")

    def test_successful_enrollment_creates_biometric_identity(self):
        command = DeviceCommand.objects.create(
            device=self.device, command_type="enroll_user", status="sent",
            payload={"enrollid": 5, "employee_id": self.employee.id, "name": "Ada Okafor", "biometric_type": "face"},
        )

        Command._resolve_command("AYTK14145399", {"ret": "adduser", "result": True, "enrollid": 5})

        identity = BiometricIdentity.objects.get(employee=self.employee, system="vendor_flask_gateway", source_identifier="AYTK14145399")
        self.assertEqual(identity.external_user_id, "5")
        self.assertTrue(identity.is_active)

    def test_failed_enrollment_does_not_create_biometric_identity(self):
        DeviceCommand.objects.create(
            device=self.device, command_type="enroll_user", status="sent",
            payload={"enrollid": 5, "employee_id": self.employee.id, "name": "Ada Okafor", "biometric_type": "face"},
        )

        Command._resolve_command("AYTK14145399", {"ret": "adduser", "result": False, "reason": 1})

        self.assertFalse(BiometricIdentity.objects.filter(employee=self.employee).exists())

    def test_re_enrolling_updates_existing_identity_instead_of_duplicating(self):
        BiometricIdentity.objects.create(
            employee=self.employee, system="vendor_flask_gateway", source_identifier="AYTK14145399", external_user_id="5",
        )
        command = DeviceCommand.objects.create(
            device=self.device, command_type="enroll_user", status="sent",
            payload={"enrollid": 9, "employee_id": self.employee.id, "name": "Ada Okafor", "biometric_type": "fingerprint"},
        )

        Command._resolve_command("AYTK14145399", {"ret": "adduser", "result": True})

        self.assertEqual(BiometricIdentity.objects.filter(employee=self.employee).count(), 1)
        identity = BiometricIdentity.objects.get(employee=self.employee)
        self.assertEqual(identity.external_user_id, "9")

    def test_successful_deletion_removes_biometric_identity(self):
        BiometricIdentity.objects.create(
            employee=self.employee, system="vendor_flask_gateway", source_identifier="AYTK14145399", external_user_id="5",
        )
        DeviceCommand.objects.create(
            device=self.device, command_type="delete_user", status="sent",
            payload={"enrollid": 5, "employee_id": self.employee.id},
        )

        Command._resolve_command("AYTK14145399", {"ret": "deleteuser", "result": True})

        self.assertFalse(BiometricIdentity.objects.filter(employee=self.employee).exists())

    def test_deleted_enrollid_can_be_reassigned_to_a_new_employee(self):
        BiometricIdentity.objects.create(
            employee=self.employee, system="vendor_flask_gateway", source_identifier="AYTK14145399", external_user_id="5",
        )
        DeviceCommand.objects.create(
            device=self.device, command_type="delete_user", status="sent", payload={"enrollid": 5, "employee_id": self.employee.id},
        )
        Command._resolve_command("AYTK14145399", {"ret": "deleteuser", "result": True})

        new_employee = Employee.objects.create(employee_id="EMP-002", first_name="Bola", last_name="Ade")
        command = DeviceCommand.objects.create(
            device=self.device, command_type="enroll_user", status="sent",
            payload={"enrollid": 5, "employee_id": new_employee.id, "name": "Bola Ade", "biometric_type": "face"},
        )

        Command._resolve_command("AYTK14145399", {"ret": "adduser", "result": True})

        identity = BiometricIdentity.objects.get(system="vendor_flask_gateway", source_identifier="AYTK14145399", external_user_id="5")
        self.assertEqual(identity.employee, new_employee)


class DeviceCommandAPITests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.manager = get_user_model().objects.create_user(username="device-manager-2", password="test-password")
        self.manager.user_permissions.add(Permission.objects.get(codename="manage_devices"))
        self.viewer = get_user_model().objects.create_user(username="device-viewer-2", password="test-password")
        self.device = BiometricDevice.objects.create(
            name="Main Entrance", serial_number="AYTK14145399", location="Factory gate", device_type="factory",
        )
        self.employee = Employee.objects.create(employee_id="EMP-010", first_name="Chika", last_name="Nwosu")

    def url(self):
        return f"/api/attendance/devices/{self.device.id}/commands/"

    def test_viewer_without_permission_cannot_queue_a_command(self):
        self.client.force_authenticate(self.viewer)
        response = self.client.post(self.url(), {"command_type": "refresh_enrolled_ids"}, format="json")
        self.assertEqual(response.status_code, 403)

    def test_manager_can_queue_refresh_enrolled_ids(self):
        self.client.force_authenticate(self.manager)
        response = self.client.post(self.url(), {"command_type": "refresh_enrolled_ids"}, format="json")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["command_type"], "refresh_enrolled_ids")
        self.assertEqual(response.data["status"], "pending")

    def test_enroll_user_assigns_a_free_enrollid_starting_at_1(self):
        self.client.force_authenticate(self.manager)
        response = self.client.post(self.url(), {"command_type": "enroll_user", "employee": self.employee.id, "biometric_type": "face"}, format="json")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["payload"]["enrollid"], 1)
        self.assertEqual(response.data["payload"]["name"], "Chika Nwosu")

    def test_enroll_user_assigns_the_next_free_enrollid_after_existing_ones(self):
        BiometricIdentity.objects.create(employee=self.employee, system="vendor_flask_gateway", source_identifier="AYTK14145399", external_user_id="7")
        other_employee = Employee.objects.create(employee_id="EMP-011", first_name="Tobi", last_name="Lawal")
        self.client.force_authenticate(self.manager)
        response = self.client.post(self.url(), {"command_type": "enroll_user", "employee": other_employee.id, "biometric_type": "face"}, format="json")
        self.assertEqual(response.data["payload"]["enrollid"], 8)

    def test_re_enrolling_reuses_the_employees_existing_enrollid_on_this_device(self):
        BiometricIdentity.objects.create(employee=self.employee, system="vendor_flask_gateway", source_identifier="AYTK14145399", external_user_id="3")
        self.client.force_authenticate(self.manager)
        response = self.client.post(self.url(), {"command_type": "enroll_user", "employee": self.employee.id, "biometric_type": "fingerprint"}, format="json")
        self.assertEqual(response.data["payload"]["enrollid"], 3)

    def test_enroll_user_requires_a_valid_biometric_type(self):
        self.client.force_authenticate(self.manager)
        response = self.client.post(self.url(), {"command_type": "enroll_user", "employee": self.employee.id, "biometric_type": "palm"}, format="json")
        self.assertEqual(response.status_code, 400)

    def test_enroll_user_requires_an_existing_employee(self):
        self.client.force_authenticate(self.manager)
        response = self.client.post(self.url(), {"command_type": "enroll_user", "employee": 999999, "biometric_type": "face"}, format="json")
        self.assertEqual(response.status_code, 404)

    def test_delete_user_requires_employee_to_be_enrolled_on_this_device(self):
        self.client.force_authenticate(self.manager)
        response = self.client.post(self.url(), {"command_type": "delete_user", "employee": self.employee.id}, format="json")
        self.assertEqual(response.status_code, 400)

    def test_delete_user_uses_the_employees_enrollid_on_this_device(self):
        BiometricIdentity.objects.create(employee=self.employee, system="vendor_flask_gateway", source_identifier="AYTK14145399", external_user_id="4")
        self.client.force_authenticate(self.manager)
        response = self.client.post(self.url(), {"command_type": "delete_user", "employee": self.employee.id}, format="json")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["payload"]["enrollid"], 4)

    def test_unsupported_command_type_is_rejected(self):
        self.client.force_authenticate(self.manager)
        response = self.client.post(self.url(), {"command_type": "reboot_device"}, format="json")
        self.assertEqual(response.status_code, 400)

    def test_cannot_queue_a_second_command_while_one_is_in_progress(self):
        DeviceCommand.objects.create(device=self.device, command_type="refresh_enrolled_ids", payload={}, status="sent")
        self.client.force_authenticate(self.manager)
        response = self.client.post(self.url(), {"command_type": "refresh_enrolled_ids"}, format="json")
        self.assertEqual(response.status_code, 409)

    def test_any_authenticated_user_can_view_command_history(self):
        DeviceCommand.objects.create(device=self.device, command_type="refresh_enrolled_ids", payload={}, status="acked")
        self.client.force_authenticate(self.viewer)
        response = self.client.get(self.url())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["results"]), 1)


class DeviceReconcileEnrolledIdsAPITests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.manager = get_user_model().objects.create_user(username="reconcile-manager", password="test-password")
        self.manager.user_permissions.add(Permission.objects.get(codename="manage_devices"))
        self.viewer = get_user_model().objects.create_user(username="reconcile-viewer", password="test-password")
        self.device = BiometricDevice.objects.create(
            name="Main Entrance", serial_number="AYTK14145399", location="Factory gate", device_type="factory",
        )

    def url(self):
        return f"/api/attendance/devices/{self.device.id}/reconcile/"

    def refresh_result(self, record):
        return DeviceCommand.objects.create(
            device=self.device, command_type="refresh_enrolled_ids", status="acked",
            result={"record": record, "result": True},
        )

    def test_requires_manage_devices_permission(self):
        self.refresh_result(["1"])
        self.client.force_authenticate(self.viewer)
        response = self.client.post(self.url())
        self.assertEqual(response.status_code, 403)

    def test_requires_a_completed_refresh_first(self):
        self.client.force_authenticate(self.manager)
        response = self.client.post(self.url())
        self.assertEqual(response.status_code, 400)

    def test_links_employees_by_matching_numeric_id(self):
        Employee.objects.create(employee_id="000016", first_name="Sunday", last_name="Dare")
        self.refresh_result(["16"])

        self.client.force_authenticate(self.manager)
        response = self.client.post(self.url())

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["linked"], 1)
        identity = BiometricIdentity.objects.get(system="vendor_flask_gateway", source_identifier="AYTK14145399", external_user_id="16")
        self.assertEqual(identity.employee.employee_id, "000016")

    def test_device_id_with_no_matching_employee_is_reported_unmatched(self):
        self.refresh_result(["3"])

        self.client.force_authenticate(self.manager)
        response = self.client.post(self.url())

        self.assertEqual(response.data["linked"], 0)
        self.assertEqual(response.data["unmatched"], ["3"])

    def test_already_linked_ids_are_skipped_and_counted_separately(self):
        employee = Employee.objects.create(employee_id="000016", first_name="Sunday", last_name="Dare")
        BiometricIdentity.objects.create(employee=employee, system="vendor_flask_gateway", source_identifier="AYTK14145399", external_user_id="16")
        self.refresh_result(["16"])

        self.client.force_authenticate(self.manager)
        response = self.client.post(self.url())

        self.assertEqual(response.data["linked"], 0)
        self.assertEqual(response.data["already_linked"], 1)
        self.assertEqual(BiometricIdentity.objects.filter(employee=employee).count(), 1)

    def test_ambiguous_employee_id_collision_is_not_auto_linked(self):
        Employee.objects.create(employee_id="000016", first_name="First", last_name="Person")
        Employee.objects.create(employee_id="16", first_name="Second", last_name="Person")
        self.refresh_result(["16"])

        self.client.force_authenticate(self.manager)
        response = self.client.post(self.url())

        self.assertEqual(response.data["linked"], 0)
        self.assertEqual(response.data["unmatched"], ["16"])
        self.assertFalse(BiometricIdentity.objects.exists())

    def test_employee_already_linked_to_a_different_id_on_this_device_is_flagged_as_conflict(self):
        employee = Employee.objects.create(employee_id="000016", first_name="Sunday", last_name="Dare")
        BiometricIdentity.objects.create(employee=employee, system="vendor_flask_gateway", source_identifier="AYTK14145399", external_user_id="999")
        self.refresh_result(["16"])

        self.client.force_authenticate(self.manager)
        response = self.client.post(self.url())

        self.assertEqual(response.data["linked"], 0)
        self.assertEqual(len(response.data["conflicts"]), 1)
        self.assertEqual(response.data["conflicts"][0]["already_linked_to_device_id"], "999")

    def test_uses_the_most_recent_completed_refresh(self):
        Employee.objects.create(employee_id="000001", first_name="Old", last_name="List")
        Employee.objects.create(employee_id="000002", first_name="New", last_name="List")
        self.refresh_result(["1"])
        self.refresh_result(["2"])

        self.client.force_authenticate(self.manager)
        response = self.client.post(self.url())

        self.assertEqual(response.data["linked"], 1)
        self.assertTrue(BiometricIdentity.objects.filter(external_user_id="2").exists())
        self.assertFalse(BiometricIdentity.objects.filter(external_user_id="1").exists())

import asyncio
import io
import json
from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase, TransactionTestCase
from django.utils import timezone
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

    def test_a_stuck_sent_command_past_the_timeout_no_longer_blocks_the_next_one(self):
        stuck = DeviceCommand.objects.create(device=self.device, command_type="refresh_enrolled_ids", payload={}, status="sent")
        DeviceCommand.objects.filter(pk=stuck.pk).update(sent_at=timezone.now() - DeviceCommand.STALE_AFTER - timedelta(seconds=1))
        waiting = DeviceCommand.objects.create(device=self.device, command_type="delete_user", payload={"enrollid": 5})

        result = Command._next_command_to_send("AYTK14145399")

        self.assertIsNotNone(result)
        command_id, _wire_message = result
        self.assertEqual(command_id, waiting.id)
        stuck.refresh_from_db()
        self.assertEqual(stuck.status, "failed")

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

    def test_successful_enrollment_queues_a_clone_to_every_other_device(self):
        other_attendance = BiometricDevice.objects.create(
            name="Side Gate", serial_number="AYTK99999999", location="Side gate", device_type="factory", purpose="attendance",
        )
        meal_device = BiometricDevice.objects.create(
            name="Canteen", serial_number="MEAL00001", location="Canteen", device_type="factory", purpose="meal_ticket",
        )
        DeviceCommand.objects.create(
            device=self.device, command_type="enroll_user", status="sent",
            payload={"enrollid": 5, "employee_id": self.employee.id, "name": "Ada Okafor", "biometric_type": "face"},
        )

        Command._resolve_command("AYTK14145399", {"ret": "adduser", "result": True})

        clone = DeviceCommand.objects.get(device=self.device, command_type="clone_enrollment")
        self.assertEqual(clone.status, "pending")
        self.assertEqual(clone.payload["employee_id"], self.employee.id)
        self.assertEqual(clone.payload["enrollid"], 5)
        self.assertEqual(clone.payload["name"], "Ada Okafor")
        self.assertEqual(clone.payload["biometric_type"], "face")
        self.assertEqual(set(clone.payload["target_device_ids"]), {other_attendance.id, meal_device.id})

    def test_no_clone_queued_when_every_other_device_already_has_this_employee(self):
        other_device = BiometricDevice.objects.create(
            name="Side Gate", serial_number="AYTK99999999", location="Side gate", device_type="factory", purpose="attendance",
        )
        BiometricIdentity.objects.create(
            employee=self.employee, system="vendor_flask_gateway", source_identifier="AYTK99999999", external_user_id="3", is_active=True,
        )
        DeviceCommand.objects.create(
            device=self.device, command_type="enroll_user", status="sent",
            payload={"enrollid": 5, "employee_id": self.employee.id, "name": "Ada Okafor", "biometric_type": "face"},
        )

        Command._resolve_command("AYTK14145399", {"ret": "adduser", "result": True})

        self.assertFalse(DeviceCommand.objects.filter(device=self.device, command_type="clone_enrollment").exists())

    def test_no_clone_queued_when_there_are_no_other_devices(self):
        DeviceCommand.objects.create(
            device=self.device, command_type="enroll_user", status="sent",
            payload={"enrollid": 5, "employee_id": self.employee.id, "name": "Ada Okafor", "biometric_type": "face"},
        )

        Command._resolve_command("AYTK14145399", {"ret": "adduser", "result": True})

        self.assertFalse(DeviceCommand.objects.filter(command_type="clone_enrollment").exists())


class GatewayCloneEnrollmentHelperTests(TestCase):
    """Sync helpers _run_clone_enrollment relies on - the async relay itself
    needs a live websocket and is exercised manually against real hardware,
    same as _handle_sendlog/_poll_commands aren't unit tested directly either."""

    def setUp(self):
        self.source = BiometricDevice.objects.create(
            name="Main Entrance", serial_number="AYTK14145399", location="Factory gate", device_type="factory",
        )
        self.target = BiometricDevice.objects.create(
            name="Side Gate", serial_number="AYTK99999999", location="Side gate", device_type="factory",
        )
        self.employee = Employee.objects.create(employee_id="EMP-001", first_name="Ada", last_name="Okafor")

    def test_next_command_to_send_signals_clone_enrollment_needs_custom_handling(self):
        command = DeviceCommand.objects.create(
            device=self.source, command_type="clone_enrollment",
            payload={"employee_id": self.employee.id, "enrollid": 5, "name": "Ada Okafor", "biometric_type": "face", "target_device_ids": [self.target.id]},
        )

        result = Command._next_command_to_send("AYTK14145399")

        self.assertEqual(result, (command.id, None))

    def test_device_is_busy_true_when_a_command_is_in_flight(self):
        DeviceCommand.objects.create(device=self.target, command_type="refresh_enrolled_ids", payload={}, status="sent")
        self.assertTrue(Command._device_is_busy(self.target))

    def test_device_is_busy_false_when_idle(self):
        self.assertFalse(Command._device_is_busy(self.target))

    def test_next_free_enrollid_on_a_fresh_device_starts_at_1(self):
        self.assertEqual(Command._next_free_enrollid(self.target), 1)

    def test_next_free_enrollid_is_scoped_to_that_devices_own_identities(self):
        BiometricIdentity.objects.create(employee=self.employee, system="vendor_flask_gateway", source_identifier="AYTK14145399", external_user_id="7")
        self.assertEqual(Command._next_free_enrollid(self.target), 1)
        BiometricIdentity.objects.create(employee=self.employee, system="vendor_flask_gateway", source_identifier="AYTK99999999", external_user_id="3")
        self.assertEqual(Command._next_free_enrollid(self.target), 4)

    def test_link_cloned_identity_creates_the_identity_on_the_target_device(self):
        Command._link_cloned_identity(self.employee.id, self.target, 12)

        identity = BiometricIdentity.objects.get(employee=self.employee, system="vendor_flask_gateway", source_identifier="AYTK99999999")
        self.assertEqual(identity.external_user_id, "12")
        self.assertTrue(identity.is_active)

    def test_finish_clone_command_records_outcome_without_ever_storing_a_template(self):
        command = DeviceCommand.objects.create(
            device=self.source, command_type="clone_enrollment", status="sent",
            payload={"employee_id": self.employee.id, "enrollid": 5, "name": "Ada Okafor", "biometric_type": "face", "target_device_ids": [self.target.id]},
        )
        summary = {"cloned_to": [{"device_id": self.target.id, "device_name": self.target.name}], "skipped_offline": [], "skipped_busy": [], "failed": []}

        Command._finish_clone_command(command.id, "acked", summary)

        command.refresh_from_db()
        self.assertEqual(command.status, "acked")
        self.assertEqual(command.result, summary)
        self.assertNotIn("record", str(command.result))
        self.assertIsNotNone(command.completed_at)

    def test_expire_stale_leaves_a_long_running_clone_enrollment_alone(self):
        stuck = DeviceCommand.objects.create(
            device=self.source, command_type="clone_enrollment", status="sent",
            payload={"employee_id": self.employee.id, "enrollid": 5, "name": "Ada Okafor", "biometric_type": "face", "target_device_ids": [self.target.id]},
        )
        DeviceCommand.objects.filter(pk=stuck.pk).update(sent_at=timezone.now() - DeviceCommand.STALE_AFTER - timedelta(seconds=1))

        DeviceCommand.expire_stale()

        stuck.refresh_from_db()
        self.assertEqual(stuck.status, "sent")


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

    def test_enroll_uses_the_staff_number_as_the_user_id_when_it_is_free(self):
        """Terminals were enrolled with id == staff number and reconcile relies on
        it, so HRM-side enrollment must not hand out 'highest + 1' instead."""
        BiometricIdentity.objects.create(employee=self.employee, system="vendor_flask_gateway", source_identifier="OTHER-DEVICE", external_user_id="1330")
        staffer = Employee.objects.create(employee_id="001307", first_name="Umeadi", last_name="Emmanuella")
        BiometricIdentity.objects.create(employee=self.employee, system="vendor_flask_gateway", source_identifier="AYTK14145399", external_user_id="1330")
        self.client.force_authenticate(self.manager)

        response = self.client.post(self.url(), {"command_type": "enroll_user", "employee": staffer.id, "biometric_type": "face"}, format="json")

        self.assertEqual(response.data["payload"]["enrollid"], 1307)

    def test_enroll_falls_back_to_the_next_free_id_when_the_staff_number_is_taken_on_the_terminal(self):
        DeviceCommand.objects.create(
            device=self.device, command_type="refresh_enrolled_ids", status="acked", payload={}, result={"record": ["1307", "1330"]},
        )
        staffer = Employee.objects.create(employee_id="001307", first_name="Umeadi", last_name="Emmanuella")
        self.client.force_authenticate(self.manager)

        response = self.client.post(self.url(), {"command_type": "enroll_user", "employee": staffer.id, "biometric_type": "face"}, format="json")

        self.assertEqual(response.data["payload"]["enrollid"], 1331)

    def test_enroll_falls_back_to_the_next_free_id_when_the_staff_number_is_linked_to_someone_else(self):
        other = Employee.objects.create(employee_id="EMP-777", first_name="Bola", last_name="Ade")
        BiometricIdentity.objects.create(employee=other, system="vendor_flask_gateway", source_identifier="AYTK14145399", external_user_id="1307")
        staffer = Employee.objects.create(employee_id="001307", first_name="Umeadi", last_name="Emmanuella")
        self.client.force_authenticate(self.manager)

        response = self.client.post(self.url(), {"command_type": "enroll_user", "employee": staffer.id, "biometric_type": "face"}, format="json")

        self.assertEqual(response.data["payload"]["enrollid"], 1308)

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

    def test_a_sent_command_stuck_past_the_timeout_no_longer_blocks_the_queue(self):
        stuck = DeviceCommand.objects.create(device=self.device, command_type="refresh_enrolled_ids", payload={}, status="sent")
        DeviceCommand.objects.filter(pk=stuck.pk).update(sent_at=timezone.now() - DeviceCommand.STALE_AFTER - timedelta(seconds=1))
        self.client.force_authenticate(self.manager)
        response = self.client.post(self.url(), {"command_type": "refresh_enrolled_ids"}, format="json")
        self.assertEqual(response.status_code, 201)
        stuck.refresh_from_db()
        self.assertEqual(stuck.status, "failed")

    def test_any_authenticated_user_can_view_command_history(self):
        DeviceCommand.objects.create(device=self.device, command_type="refresh_enrolled_ids", payload={}, status="acked")
        self.client.force_authenticate(self.viewer)
        response = self.client.get(self.url())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["results"]), 1)

    def test_enroll_response_lists_devices_that_will_be_cloned_to_once_it_succeeds(self):
        """The clone itself only happens once the physical scan on this device
        actually succeeds (see GatewayIdentityLinkingTests) - this response is
        informational only, and creates no DeviceCommand rows for other devices."""
        other_attendance = BiometricDevice.objects.create(
            name="Side Gate", serial_number="AYTK99999999", location="Side gate", device_type="factory", purpose="attendance",
        )
        meal_device = BiometricDevice.objects.create(
            name="Canteen", serial_number="MEAL00001", location="Canteen", device_type="factory", purpose="meal_ticket",
        )
        self.client.force_authenticate(self.manager)
        response = self.client.post(self.url(), {"command_type": "enroll_user", "employee": self.employee.id, "biometric_type": "face"}, format="json")

        self.assertEqual(response.status_code, 201)
        will_clone_device_ids = {row["device_id"] for row in response.data["will_clone_to"]}
        self.assertEqual(will_clone_device_ids, {other_attendance.id, meal_device.id})
        self.assertFalse(DeviceCommand.objects.filter(device__in=[other_attendance, meal_device]).exists())

    def test_will_clone_to_excludes_a_device_the_employee_is_already_enrolled_on(self):
        already_enrolled_device = BiometricDevice.objects.create(
            name="Side Gate", serial_number="AYTK99999999", location="Side gate", device_type="factory", purpose="attendance",
        )
        BiometricIdentity.objects.create(
            employee=self.employee, system="vendor_flask_gateway", source_identifier="AYTK99999999", external_user_id="9", is_active=True,
        )
        self.client.force_authenticate(self.manager)
        response = self.client.post(self.url(), {"command_type": "enroll_user", "employee": self.employee.id, "biometric_type": "face"}, format="json")

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["will_clone_to"], [])

    def test_refresh_enrolled_ids_has_no_will_clone_to(self):
        BiometricDevice.objects.create(
            name="Side Gate", serial_number="AYTK99999999", location="Side gate", device_type="factory", purpose="attendance",
        )
        self.client.force_authenticate(self.manager)
        response = self.client.post(self.url(), {"command_type": "refresh_enrolled_ids"}, format="json")

        self.assertEqual(response.status_code, 201)
        self.assertNotIn("will_clone_to", response.data)


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


class DeviceSyncAllAPITests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.manager = get_user_model().objects.create_user(username="sync-manager", password="test-password")
        self.manager.user_permissions.add(Permission.objects.get(codename="manage_devices"))
        self.viewer = get_user_model().objects.create_user(username="sync-viewer", password="test-password")
        self.device_a = BiometricDevice.objects.create(
            name="Terminal A", serial_number="AYTK14145399", location="Main entrance", device_type="factory", purpose="attendance", is_online=True,
        )
        self.device_b = BiometricDevice.objects.create(
            name="Terminal B", serial_number="AYTK14145402", location="Side entrance", device_type="factory", purpose="attendance", is_online=True,
        )

    def url(self):
        return "/api/attendance/devices/sync-all/"

    def test_requires_manage_devices_permission(self):
        self.client.force_authenticate(self.viewer)
        response = self.client.post(self.url())
        self.assertEqual(response.status_code, 403)

    def test_requires_at_least_two_devices(self):
        self.device_b.delete()
        self.client.force_authenticate(self.manager)
        response = self.client.post(self.url())
        self.assertEqual(response.status_code, 400)

    def test_queues_a_clone_for_an_employee_missing_from_one_device(self):
        employee = Employee.objects.create(employee_id="EMP-001", first_name="Ada", last_name="Okafor")
        BiometricIdentity.objects.create(
            employee=employee, system="vendor_flask_gateway", source_identifier="AYTK14145399", external_user_id="5", is_active=True,
        )
        self.client.force_authenticate(self.manager)

        response = self.client.post(self.url())

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["queued"], 1)
        clone = DeviceCommand.objects.get(device=self.device_a, command_type="clone_enrollment")
        self.assertEqual(clone.status, "pending")
        self.assertEqual(clone.payload["employee_id"], employee.id)
        self.assertEqual(clone.payload["enrollid"], 5)
        self.assertEqual(clone.payload["target_device_ids"], [self.device_b.id])

    def test_no_clone_queued_for_an_employee_already_on_every_device(self):
        employee = Employee.objects.create(employee_id="EMP-001", first_name="Ada", last_name="Okafor")
        BiometricIdentity.objects.create(employee=employee, system="vendor_flask_gateway", source_identifier="AYTK14145399", external_user_id="5", is_active=True)
        BiometricIdentity.objects.create(employee=employee, system="vendor_flask_gateway", source_identifier="AYTK14145402", external_user_id="7", is_active=True)
        self.client.force_authenticate(self.manager)

        response = self.client.post(self.url())

        self.assertEqual(response.data["queued"], 0)
        self.assertFalse(DeviceCommand.objects.filter(command_type="clone_enrollment").exists())

    def test_an_inactive_identity_does_not_count_as_already_enrolled(self):
        employee = Employee.objects.create(employee_id="EMP-001", first_name="Ada", last_name="Okafor")
        BiometricIdentity.objects.create(employee=employee, system="vendor_flask_gateway", source_identifier="AYTK14145399", external_user_id="5", is_active=True)
        BiometricIdentity.objects.create(employee=employee, system="vendor_flask_gateway", source_identifier="AYTK14145402", external_user_id="7", is_active=False)
        self.client.force_authenticate(self.manager)

        response = self.client.post(self.url())

        self.assertEqual(response.data["queued"], 1)
        clone = DeviceCommand.objects.get(command_type="clone_enrollment")
        self.assertEqual(clone.payload["target_device_ids"], [self.device_b.id])

    def test_syncs_gaps_in_both_directions_at_once(self):
        only_on_a = Employee.objects.create(employee_id="EMP-001", first_name="Ada", last_name="Okafor")
        only_on_b = Employee.objects.create(employee_id="EMP-002", first_name="Bola", last_name="Ade")
        BiometricIdentity.objects.create(employee=only_on_a, system="vendor_flask_gateway", source_identifier="AYTK14145399", external_user_id="5", is_active=True)
        BiometricIdentity.objects.create(employee=only_on_b, system="vendor_flask_gateway", source_identifier="AYTK14145402", external_user_id="8", is_active=True)
        self.client.force_authenticate(self.manager)

        response = self.client.post(self.url())

        self.assertEqual(response.data["queued"], 2)
        self.assertEqual(DeviceCommand.objects.filter(device=self.device_a, command_type="clone_enrollment").count(), 1)
        self.assertEqual(DeviceCommand.objects.filter(device=self.device_b, command_type="clone_enrollment").count(), 1)

    def test_offline_devices_take_no_part_and_are_reported(self):
        offline = BiometricDevice.objects.create(
            name="Canteen", serial_number="MEAL00001", location="Canteen", device_type="factory", purpose="meal_ticket", is_online=False,
        )
        employee = Employee.objects.create(employee_id="EMP-001", first_name="Ada", last_name="Okafor")
        BiometricIdentity.objects.create(employee=employee, system="vendor_flask_gateway", source_identifier="AYTK14145399", external_user_id="5", is_active=True)
        BiometricIdentity.objects.create(employee=employee, system="vendor_flask_gateway", source_identifier="AYTK14145402", external_user_id="5", is_active=True)
        self.client.force_authenticate(self.manager)

        response = self.client.post(self.url())

        self.assertEqual(response.data["queued"], 0)
        self.assertIn("Canteen", response.data["detail"])
        self.assertFalse(DeviceCommand.objects.filter(command_type="clone_enrollment").exists())
        self.assertNotIn(offline.id, [t for c in DeviceCommand.objects.all() for t in c.payload.get("target_device_ids", [])])

    def test_needs_two_online_devices(self):
        BiometricDevice.objects.filter(pk=self.device_b.pk).update(is_online=False)
        self.client.force_authenticate(self.manager)
        response = self.client.post(self.url())
        self.assertEqual(response.status_code, 400)
        self.assertIn("Terminal B", response.data["detail"])

    def test_running_it_twice_does_not_queue_the_same_employee_twice(self):
        employee = Employee.objects.create(employee_id="EMP-001", first_name="Ada", last_name="Okafor")
        BiometricIdentity.objects.create(employee=employee, system="vendor_flask_gateway", source_identifier="AYTK14145399", external_user_id="5", is_active=True)
        self.client.force_authenticate(self.manager)

        first = self.client.post(self.url())
        second = self.client.post(self.url())

        self.assertEqual(first.data["queued"], 1)
        self.assertEqual(second.data["queued"], 0)
        self.assertEqual(DeviceCommand.objects.filter(command_type="clone_enrollment").count(), 1)


class CloneQueueRobustnessTests(TestCase):
    """The failure modes found on real hardware: terminals cycle their connection
    every ~20-30s, cutting relays off mid-flight, and a big Sync All backlog must
    neither lock the admin panel nor starve admin commands."""

    def setUp(self):
        self.client = APIClient()
        self.manager = get_user_model().objects.create_user(username="robust-manager", password="test-password")
        self.manager.user_permissions.add(Permission.objects.get(codename="manage_devices"))
        self.source = BiometricDevice.objects.create(name="A", serial_number="AYTK14145399", location="x", device_type="factory")
        self.target = BiometricDevice.objects.create(name="B", serial_number="AYTK14145402", location="x", device_type="factory")
        self.employee = Employee.objects.create(employee_id="EMP-001", first_name="Ada", last_name="Okafor")
        self.payload = {"employee_id": self.employee.id, "enrollid": 5, "name": "Ada Okafor", "biometric_type": "face", "target_device_ids": [self.target.id]}

    def clone(self, **kwargs):
        return DeviceCommand.objects.create(device=self.source, command_type="clone_enrollment", payload=dict(self.payload), **kwargs)

    def test_a_clone_relay_cut_off_by_a_dropped_connection_is_retried_when_the_device_reregisters(self):
        stuck = self.clone(status="sent")

        Command._recover_interrupted_clones("AYTK14145399")

        stuck.refresh_from_db()
        self.assertEqual(stuck.status, "pending")
        self.assertEqual(stuck.payload["attempts"], 1)
        self.assertIsNone(stuck.sent_at)

    def test_a_relay_that_keeps_getting_cut_off_eventually_gives_up(self):
        stuck = self.clone(status="sent")
        DeviceCommand.objects.filter(pk=stuck.pk).update(payload={**self.payload, "attempts": 2})

        Command._recover_interrupted_clones("AYTK14145399")

        stuck.refresh_from_db()
        self.assertEqual(stuck.status, "failed")

    def test_recovery_only_touches_clone_rows_in_flight_on_that_device(self):
        regular = DeviceCommand.objects.create(device=self.source, command_type="refresh_enrolled_ids", payload={}, status="sent")
        elsewhere = DeviceCommand.objects.create(device=self.target, command_type="clone_enrollment", payload=dict(self.payload), status="sent")

        Command._recover_interrupted_clones("AYTK14145399")

        regular.refresh_from_db()
        elsewhere.refresh_from_db()
        self.assertEqual(regular.status, "sent")
        self.assertEqual(elsewhere.status, "sent")

    def test_a_clone_stuck_past_the_clone_timeout_is_finally_failed(self):
        stuck = self.clone(status="sent")
        DeviceCommand.objects.filter(pk=stuck.pk).update(sent_at=timezone.now() - DeviceCommand.CLONE_STALE_AFTER - timedelta(seconds=1))

        DeviceCommand.expire_stale()

        stuck.refresh_from_db()
        self.assertEqual(stuck.status, "failed")

    def test_a_pending_clone_backlog_does_not_count_as_a_busy_target(self):
        DeviceCommand.objects.create(device=self.target, command_type="clone_enrollment", payload=dict(self.payload), status="pending")
        self.assertFalse(Command._device_is_busy(self.target))

    def test_a_clone_in_flight_does_not_count_as_a_busy_target(self):
        DeviceCommand.objects.create(device=self.target, command_type="clone_enrollment", payload=dict(self.payload), status="sent")
        self.assertFalse(Command._device_is_busy(self.target))

    def test_an_admin_command_awaiting_a_reply_still_counts_as_busy(self):
        DeviceCommand.objects.create(device=self.target, command_type="enroll_user", payload={"enrollid": 1}, status="sent")
        self.assertTrue(Command._device_is_busy(self.target))

    def test_a_clone_backlog_does_not_block_queuing_an_admin_command(self):
        for _ in range(3):
            self.clone(status="pending")
        self.client.force_authenticate(self.manager)

        response = self.client.post(f"/api/attendance/devices/{self.source.id}/commands/", {"command_type": "refresh_enrolled_ids"}, format="json")

        self.assertEqual(response.status_code, 201)

    def test_admin_commands_are_served_ahead_of_an_older_clone_backlog(self):
        self.clone(status="pending")
        admin = DeviceCommand.objects.create(device=self.source, command_type="refresh_enrolled_ids", payload={}, status="pending")

        command_id, wire_message = Command._next_command_to_send("AYTK14145399")

        self.assertEqual(command_id, admin.id)
        self.assertEqual(wire_message["cmd"], "getuserids")

    def test_target_enrollid_reuses_the_source_id_when_it_is_free(self):
        self.assertEqual(Command._enrollid_for_target(self.target, 1133), 1133)

    def test_target_enrollid_avoids_an_id_hrm_already_linked_there(self):
        other = Employee.objects.create(employee_id="EMP-002", first_name="Bola", last_name="Ade")
        BiometricIdentity.objects.create(employee=other, system="vendor_flask_gateway", source_identifier="AYTK14145402", external_user_id="1133")

        self.assertEqual(Command._enrollid_for_target(self.target, 1133), 1134)

    def test_target_enrollid_avoids_an_id_the_terminal_reported_but_hrm_never_linked(self):
        """Pushing onto an id someone else owns on the terminal would overwrite them."""
        DeviceCommand.objects.create(
            device=self.target, command_type="refresh_enrolled_ids", status="acked", payload={}, result={"record": ["1133", "1200"]},
        )

        self.assertEqual(Command._enrollid_for_target(self.target, 1133), 1201)


class FakeTerminal:
    """Stands in for a device's websocket: records what the gateway sends and
    answers the way the terminal would, through the same pending-reply slot the
    real receive loop resolves."""

    def __init__(self, gateway, serial, responder):
        self.gateway, self.serial, self.responder, self.sent = gateway, serial, responder, []

    async def send(self, raw):
        message = json.loads(raw)
        self.sent.append(message)
        reply = self.responder(message)
        if reply is not None:
            self.gateway._pending_replies[self.serial].set_result(reply)


TEMPLATE = "SECRET-BASE64-FACE-TEMPLATE"


class CloneEnrollmentRelayTests(TransactionTestCase):
    """Drives the real async relay against fake terminals. Uses
    TransactionTestCase because the relay talks to the DB from worker threads."""

    def setUp(self):
        self.source = BiometricDevice.objects.create(name="Terminal A", serial_number="AYTK14145399", location="x", device_type="factory", is_online=True)
        self.target = BiometricDevice.objects.create(name="Terminal B", serial_number="AYTK14145402", location="x", device_type="factory", is_online=True)
        self.employee = Employee.objects.create(employee_id="EMP-001", first_name="Ada", last_name="Okafor")
        BiometricIdentity.objects.create(employee=self.employee, system="vendor_flask_gateway", source_identifier="AYTK14145399", external_user_id="1133", is_active=True)
        self.log = io.StringIO()
        self.gateway = Command(stdout=self.log)
        self.gateway._connections, self.gateway._pending_replies, self.gateway._locks = {}, {}, {}
        self.job = DeviceCommand.objects.create(
            device=self.source, command_type="clone_enrollment", status="pending",
            payload={"employee_id": self.employee.id, "enrollid": 1133, "name": "Ada Okafor", "biometric_type": "face", "target_device_ids": [self.target.id]},
        )

    def run_relay(self, source_reply, target_reply, target_online=True):
        source_ws = FakeTerminal(self.gateway, "AYTK14145399", lambda m: source_reply if m["cmd"] == "getuserinfo" else None)
        target_ws = FakeTerminal(self.gateway, "AYTK14145402", lambda m: target_reply if m["cmd"] == "setuserinfo" else None)
        self.gateway._connections["AYTK14145399"] = source_ws
        if target_online:
            self.gateway._connections["AYTK14145402"] = target_ws
        with mock.patch("attendance.management.commands.run_aiface_gateway.CLONE_TARGET_WAIT_SECONDS", 0):
            asyncio.run(self.gateway._run_clone_enrollment(source_ws, "AYTK14145399", self.job.id))
        self.job.refresh_from_db()
        return source_ws, target_ws

    def test_pulls_the_template_from_the_source_and_pushes_it_to_the_target(self):
        source_ws, target_ws = self.run_relay(
            {"ret": "getuserinfo", "result": True, "record": TEMPLATE},
            {"ret": "setuserinfo", "result": True},
        )

        self.assertEqual([m["cmd"] for m in source_ws.sent], ["getuserinfo"])
        self.assertEqual(source_ws.sent[0]["enrollid"], 1133)
        self.assertEqual(len(target_ws.sent), 1)
        pushed = target_ws.sent[0]
        self.assertEqual((pushed["cmd"], pushed["record"], pushed["name"], pushed["enrollid"]), ("setuserinfo", TEMPLATE, "Ada Okafor", 1133))
        self.assertEqual(self.job.status, "acked")
        self.assertEqual(self.job.result["cloned_to"], [{"device_id": self.target.id, "device_name": "Terminal B"}])
        identity = BiometricIdentity.objects.get(employee=self.employee, source_identifier="AYTK14145402")
        self.assertEqual(identity.external_user_id, "1133")
        self.assertTrue(identity.is_active)

    def test_the_template_never_reaches_the_database_or_the_logs(self):
        self.run_relay({"ret": "getuserinfo", "result": True, "record": TEMPLATE}, {"ret": "setuserinfo", "result": True})

        self.assertNotIn(TEMPLATE, json.dumps(self.job.result))
        self.assertNotIn(TEMPLATE, json.dumps(self.job.payload))
        self.assertNotIn(TEMPLATE, self.log.getvalue())
        for command in DeviceCommand.objects.all():
            self.assertNotIn(TEMPLATE, json.dumps(command.result) + json.dumps(command.payload))

    def test_an_offline_target_means_the_source_is_never_asked_for_a_template(self):
        source_ws, _ = self.run_relay({"ret": "getuserinfo", "result": True, "record": TEMPLATE}, None, target_online=False)

        self.assertEqual(source_ws.sent, [])
        self.assertEqual(self.job.status, "failed")
        self.assertEqual(self.job.result["skipped_offline"], [{"device_id": self.target.id, "device_name": "Terminal B"}])
        self.assertFalse(BiometricIdentity.objects.filter(source_identifier="AYTK14145402").exists())

    def test_a_source_that_cannot_return_a_template_pushes_nothing(self):
        _, target_ws = self.run_relay({"ret": "getuserinfo", "result": False}, {"ret": "setuserinfo", "result": True})

        self.assertEqual(target_ws.sent, [])
        self.assertEqual(self.job.status, "failed")
        self.assertFalse(BiometricIdentity.objects.filter(source_identifier="AYTK14145402").exists())

    def test_a_target_that_rejects_the_template_is_reported_and_not_linked(self):
        self.run_relay({"ret": "getuserinfo", "result": True, "record": TEMPLATE}, {"ret": "setuserinfo", "result": False})

        self.assertEqual(self.job.status, "failed")
        self.assertEqual(self.job.result["failed"][0]["reason"], "device rejected the template")
        self.assertFalse(BiometricIdentity.objects.filter(source_identifier="AYTK14145402").exists())

    def test_does_not_overwrite_an_id_the_target_already_has_for_someone_else(self):
        other = Employee.objects.create(employee_id="EMP-002", first_name="Bola", last_name="Ade")
        BiometricIdentity.objects.create(employee=other, system="vendor_flask_gateway", source_identifier="AYTK14145402", external_user_id="1133", is_active=True)

        _, target_ws = self.run_relay({"ret": "getuserinfo", "result": True, "record": TEMPLATE}, {"ret": "setuserinfo", "result": True})

        self.assertEqual(target_ws.sent[0]["enrollid"], 1134)
        self.assertEqual(BiometricIdentity.objects.get(source_identifier="AYTK14145402", external_user_id="1133").employee, other)
        self.assertEqual(BiometricIdentity.objects.get(employee=self.employee, source_identifier="AYTK14145402").external_user_id, "1134")


class InactiveStaffTests(TestCase):
    """People who've left must never be synced between terminals or accepted on
    a scan, and can be removed from the terminals with a preview-then-confirm step."""

    def setUp(self):
        self.client = APIClient()
        self.manager = get_user_model().objects.create_user(username="inactive-manager", password="test-password")
        self.manager.user_permissions.add(Permission.objects.get(codename="manage_devices"))
        self.viewer = get_user_model().objects.create_user(username="inactive-viewer", password="test-password")
        self.a = BiometricDevice.objects.create(name="Terminal A", serial_number="AYTK14145399", location="x", device_type="factory", is_online=True)
        self.b = BiometricDevice.objects.create(name="Terminal B", serial_number="AYTK14145402", location="x", device_type="factory", is_online=True)
        self.active = Employee.objects.create(employee_id="000001", first_name="Ada", last_name="Okafor")
        self.left = Employee.objects.create(employee_id="000002", first_name="Bola", last_name="Ade", status="inactive")
        self.terminated = Employee.objects.create(employee_id="000003", first_name="Chi", last_name="Eze", status="terminated")
        self.suspended = Employee.objects.create(employee_id="000004", first_name="Dan", last_name="Obi", status="suspended")

    def link(self, employee, device, enrollid="1"):
        return BiometricIdentity.objects.create(employee=employee, system="vendor_flask_gateway", source_identifier=device.serial_number, external_user_id=enrollid)

    def test_an_identity_for_a_non_active_employee_is_created_on_hold(self):
        self.assertFalse(self.link(self.left, self.a, "2").is_active)
        self.assertFalse(self.link(self.suspended, self.a, "4").is_active)
        self.assertTrue(self.link(self.active, self.a, "1").is_active)

    def test_reactivating_the_employee_restores_their_held_identities(self):
        identity = self.link(self.left, self.a, "2")
        self.left.status = "active"
        self.left.save()
        identity.refresh_from_db()
        self.assertTrue(identity.is_active)

    def test_reconcile_links_a_departed_staff_member_but_on_hold(self):
        DeviceCommand.objects.create(device=self.a, command_type="refresh_enrolled_ids", status="acked", payload={}, result={"record": ["2"]})
        self.client.force_authenticate(self.manager)

        self.client.post(f"/api/attendance/devices/{self.a.id}/reconcile/")

        self.assertFalse(BiometricIdentity.objects.get(employee=self.left).is_active)

    def test_sync_all_never_spreads_a_departed_staff_member(self):
        self.link(self.active, self.a, "1")
        self.link(self.left, self.a, "2")
        BiometricIdentity.objects.filter(employee=self.left).update(is_active=True)  # as the old reconcile bug left them
        self.client.force_authenticate(self.manager)

        response = self.client.post("/api/attendance/devices/sync-all/")

        self.assertEqual(response.data["queued"], 1)
        self.assertEqual(DeviceCommand.objects.get(command_type="clone_enrollment").payload["employee_id"], self.active.id)

    def test_cannot_enroll_an_employee_who_is_not_active(self):
        self.client.force_authenticate(self.manager)
        response = self.client.post(f"/api/attendance/devices/{self.a.id}/commands/", {"command_type": "enroll_user", "employee": self.left.id, "biometric_type": "face"}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("not an active employee", response.data["detail"])

    def test_purge_preview_changes_nothing_and_counts_per_device(self):
        self.link(self.active, self.a, "1")
        self.link(self.left, self.a, "2")
        self.link(self.left, self.b, "2")
        self.link(self.terminated, self.a, "3")
        self.link(self.suspended, self.a, "4")
        self.client.force_authenticate(self.manager)

        response = self.client.post("/api/attendance/devices/purge-inactive/", {}, format="json")

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data["confirmed"])
        self.assertEqual(response.data["total"], 3)
        self.assertEqual(response.data["devices"], [{"device_name": "Terminal A", "count": 2}, {"device_name": "Terminal B", "count": 1}])
        self.assertFalse(DeviceCommand.objects.exists())

    def test_confirmed_purge_queues_a_removal_for_each_departed_person_but_not_active_or_suspended(self):
        self.link(self.active, self.a, "1")
        self.link(self.left, self.a, "2")
        self.link(self.suspended, self.a, "4")
        self.client.force_authenticate(self.manager)

        response = self.client.post("/api/attendance/devices/purge-inactive/", {"confirm": True}, format="json")

        self.assertTrue(response.data["confirmed"])
        removals = DeviceCommand.objects.filter(command_type="purge_user")
        self.assertEqual(removals.count(), 1)
        self.assertEqual((removals[0].device, removals[0].payload["enrollid"], removals[0].payload["employee_id"]), (self.a, 2, self.left.id))
        self.assertEqual(removals[0].status, "pending")

    def test_running_the_purge_twice_does_not_queue_the_same_removal_twice(self):
        self.link(self.left, self.a, "2")
        self.client.force_authenticate(self.manager)
        self.client.post("/api/attendance/devices/purge-inactive/", {"confirm": True}, format="json")
        second = self.client.post("/api/attendance/devices/purge-inactive/", {"confirm": True}, format="json")
        self.assertEqual(second.data["total"], 0)
        self.assertEqual(DeviceCommand.objects.filter(command_type="purge_user").count(), 1)

    def test_purge_requires_manage_devices_permission(self):
        self.client.force_authenticate(self.viewer)
        self.assertEqual(self.client.post("/api/attendance/devices/purge-inactive/", {"confirm": True}, format="json").status_code, 403)

    def test_a_successful_removal_sends_deleteuser_and_unlinks_the_identity(self):
        self.link(self.left, self.a, "2")
        DeviceCommand.objects.create(device=self.a, command_type="purge_user", payload={"enrollid": 2, "employee_id": self.left.id})

        command_id, wire = Command._next_command_to_send("AYTK14145399")
        self.assertEqual((wire["cmd"], wire["enrollid"], wire["backupnum"]), ("deleteuser", 2, 12))
        Command._mark_command_sent(command_id)
        Command._resolve_command("AYTK14145399", {"ret": "deleteuser", "result": True})

        self.assertFalse(BiometricIdentity.objects.filter(employee=self.left).exists())

    def test_a_removal_backlog_neither_blocks_nor_outranks_an_admin_command(self):
        for enrollid in (2, 3, 5):
            DeviceCommand.objects.create(device=self.a, command_type="purge_user", payload={"enrollid": enrollid, "employee_id": self.left.id})
        self.client.force_authenticate(self.manager)

        response = self.client.post(f"/api/attendance/devices/{self.a.id}/commands/", {"command_type": "refresh_enrolled_ids"}, format="json")
        self.assertEqual(response.status_code, 201)
        _, wire = Command._next_command_to_send("AYTK14145399")
        self.assertEqual(wire["cmd"], "getuserids")



class LostSwitchCommandTests(TestCase):
    """A meal-terminal switch that gets no answer is retried straight away, not left for the next 5-minute check."""

    def setUp(self):
        self.device = BiometricDevice.objects.create(name="Canteen", serial_number="SWITCH1", purpose="meal_ticket")

    def sent(self, attempts=0):
        from datetime import timedelta as delta

        from django.utils import timezone as tz

        return DeviceCommand.objects.create(device=self.device, command_type="set_user_enabled", payload={"enrollid": 5, "enabled": False, "employee_id": 1, "attempts": attempts}, status="sent", sent_at=tz.now() - delta(seconds=30))

    def test_an_unanswered_switch_goes_back_to_pending_with_a_retry_counted(self):
        command = self.sent()
        DeviceCommand.expire_stale()
        command.refresh_from_db()
        self.assertEqual((command.status, command.payload["attempts"], command.sent_at), ("pending", 1, None))

    def test_it_gives_up_after_six_tries(self):
        command = self.sent(attempts=5)
        DeviceCommand.expire_stale()
        command.refresh_from_db()
        self.assertEqual(command.status, "failed")

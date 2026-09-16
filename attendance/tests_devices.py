from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from rest_framework.test import APIClient

from attendance.management.commands.run_aiface_gateway import Command
from attendance.models import BiometricDevice


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

from django.test import TestCase
from django.utils import timezone

from attendance.integrations.aiface_protocol import IDENTITY_SYSTEM
from attendance.models import BiometricDevice, DeviceCommand
from attendance.services.device_sync import link_ids_by_staff_number, plan_slot_clones
from employees.models import BiometricIdentity, Employee


class SlotSyncTests(TestCase):
    def setUp(self):
        self.a = BiometricDevice.objects.create(name="A", serial_number="A1", location="x", device_type="factory", purpose="attendance")
        self.b = BiometricDevice.objects.create(name="B", serial_number="B1", location="x", device_type="factory", purpose="attendance")
        self.person = Employee.objects.create(employee_id="000007", first_name="Sync", last_name="Person", status="active")

    def listing(self, device, slots):
        DeviceCommand.objects.create(device=device, command_type="list_user_slots", status="acked", completed_at=timezone.now(), result={"slots": slots})

    def identity(self, device, enrollid, employee=None):
        return BiometricIdentity.objects.create(employee=employee or self.person, system=IDENTITY_SYSTEM, source_identifier=device.serial_number, external_user_id=str(enrollid))

    def test_an_id_matching_one_staff_number_is_linked(self):
        self.listing(self.a, [[7, 50]])
        self.assertEqual(link_ids_by_staff_number([self.a]), 1)
        self.assertTrue(BiometricIdentity.objects.filter(employee=self.person, source_identifier="A1", external_user_id="7").exists())
        self.assertEqual(link_ids_by_staff_number([self.a]), 0)  # idempotent

    def test_an_id_with_no_matching_employee_is_left_alone(self):
        self.listing(self.a, [[999, 50]])
        self.assertEqual(link_ids_by_staff_number([self.a]), 0)

    def test_an_ambiguous_staff_number_is_not_guessed(self):
        Employee.objects.create(employee_id="7", first_name="Other", last_name="Seven", status="active")
        self.listing(self.a, [[7, 50]])
        self.assertEqual(link_ids_by_staff_number([self.a]), 0)

    def test_a_fingerprint_and_face_only_on_one_terminal_are_relayed_to_the_other(self):
        self.identity(self.a, 7); self.identity(self.b, 7)
        self.listing(self.a, [[7, 50], [7, 0]])
        self.listing(self.b, [[7, 50]])  # has the face, lacks the fingerprint
        self.assertEqual(plan_slot_clones([self.a, self.b]), [self.person.pk])
        command = DeviceCommand.objects.get(command_type="clone_enrollment")
        self.assertEqual(command.device, self.a)
        self.assertEqual(command.payload["pushes"], [{"target_device_id": self.b.pk, "backupnums": [0]}])

    def test_nothing_is_queued_when_the_terminals_agree(self):
        self.identity(self.a, 7); self.identity(self.b, 7)
        self.listing(self.a, [[7, 50], [7, 0]]); self.listing(self.b, [[7, 50], [7, 0]])
        self.assertEqual(plan_slot_clones([self.a, self.b]), [])

    def test_a_person_missing_from_a_terminal_entirely_gets_every_credential(self):
        self.identity(self.a, 7)
        self.listing(self.a, [[7, 50], [7, 0], [7, 11]]); self.listing(self.b, [])
        plan_slot_clones([self.a, self.b])
        self.assertEqual(DeviceCommand.objects.get(command_type="clone_enrollment").payload["pushes"][0]["backupnums"], [0, 11, 50])

    def test_a_terminal_without_a_fresh_listing_is_not_planned_against(self):
        self.identity(self.a, 7)
        self.listing(self.a, [[7, 50]])
        self.assertEqual(plan_slot_clones([self.a, self.b]), [])  # b never answered: we don't guess what it holds

    def test_a_relay_already_waiting_is_not_queued_twice(self):
        self.identity(self.a, 7); self.identity(self.b, 7)
        self.listing(self.a, [[7, 50], [7, 0]]); self.listing(self.b, [[7, 50]])
        plan_slot_clones([self.a, self.b])
        self.assertEqual(plan_slot_clones([self.a, self.b]), [])
        self.assertEqual(DeviceCommand.objects.filter(command_type="clone_enrollment").count(), 1)

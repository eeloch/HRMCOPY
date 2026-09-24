from unittest import mock

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

    def refused_face(self, times, *, source=None, by="id"):
        for _ in range(times):
            failure = {"backupnum": 50, "reason": "rejected", "target": self.b.name}
            if by == "id":
                failure["target_device_id"] = self.b.pk
            DeviceCommand.objects.create(
                device=source or self.a, command_type="clone_enrollment", status="failed", completed_at=timezone.now(),
                payload={"employee_id": self.person.pk, "pushes": [{"target_device_id": self.b.pk, "backupnums": [50]}]},
                result={"failed": [failure]},
            )

    def face_only_on_a(self):
        self.identity(self.a, 7); self.identity(self.b, 7)
        self.listing(self.a, [[7, 50], [7, 0]]); self.listing(self.b, [[7, 0]])

    def test_a_face_the_target_keeps_refusing_is_left_alone_for_a_while(self):
        self.face_only_on_a(); self.refused_face(2)
        self.assertEqual(plan_slot_clones([self.a, self.b]), [])

    def test_one_refusal_is_retried(self):
        self.face_only_on_a(); self.refused_face(1)
        self.assertEqual(plan_slot_clones([self.a, self.b]), [self.person.pk])

    def test_refusals_recorded_only_by_terminal_name_still_count(self):
        self.face_only_on_a(); self.refused_face(2, by="name")
        self.assertEqual(plan_slot_clones([self.a, self.b]), [])

    def test_old_refusals_are_forgotten_after_the_cooldown(self):
        from datetime import timedelta
        self.face_only_on_a(); self.refused_face(3)
        DeviceCommand.objects.filter(command_type="clone_enrollment").update(completed_at=timezone.now() - timedelta(hours=7))
        self.assertEqual(plan_slot_clones([self.a, self.b]), [self.person.pk])

    def test_a_refused_face_does_not_stop_the_fingerprint_going_across(self):
        self.identity(self.a, 7); self.identity(self.b, 7)
        self.listing(self.a, [[7, 50], [7, 0]]); self.listing(self.b, [])
        self.refused_face(2)
        plan_slot_clones([self.a, self.b])
        self.assertEqual(DeviceCommand.objects.get(command_type="clone_enrollment", status="pending").payload["pushes"][0]["backupnums"], [0])

    def test_a_relay_already_waiting_is_not_queued_twice(self):
        self.identity(self.a, 7); self.identity(self.b, 7)
        self.listing(self.a, [[7, 50], [7, 0]]); self.listing(self.b, [[7, 50]])
        plan_slot_clones([self.a, self.b])
        self.assertEqual(plan_slot_clones([self.a, self.b]), [])
        self.assertEqual(DeviceCommand.objects.filter(command_type="clone_enrollment").count(), 1)

    def test_meal_terminals_take_part_and_an_attendance_terminal_is_the_preferred_source(self):
        meal = BiometricDevice.objects.create(name="M", serial_number="M1", location="x", device_type="factory", purpose="meal_ticket")
        self.identity(self.a, 7); self.identity(meal, 7)
        self.listing(self.a, [[7, 50], [7, 0]]); self.listing(meal, [[7, 50], [7, 0]]); self.listing(self.b, [])
        plan_slot_clones([self.a, self.b, meal])
        command = DeviceCommand.objects.get(command_type="clone_enrollment")
        self.assertEqual(command.device, self.a)  # not the meal terminal, though it holds the same credentials
        self.assertEqual(command.payload["pushes"], [{"target_device_id": self.b.pk, "backupnums": [0, 50]}])


class DuplicateRepairTests(TestCase):
    def setUp(self):
        from attendance.services.device_sync import find_duplicate_ids

        self.find = find_duplicate_ids
        self.dev = BiometricDevice.objects.create(name="D", serial_number="D1", location="x", device_type="factory", purpose="attendance")
        self.ada = Employee.objects.create(employee_id="000040", first_name="Ada", last_name="A", status="active")

    def listing(self, slots):
        DeviceCommand.objects.create(device=self.dev, command_type="list_user_slots", status="acked", completed_at=timezone.now(), result={"slots": slots})

    def identity(self, enrollid, employee=None):
        return BiometricIdentity.objects.create(employee=employee or self.ada, system=IDENTITY_SYSTEM, source_identifier="D1", external_user_id=str(enrollid))

    def test_a_copy_beside_the_persons_own_id_is_found_and_deletable(self):
        self.identity(1488); self.listing([[40, 50], [40, 0], [1488, 0]])
        (row,) = self.find(self.dev)
        self.assertEqual((row["own_id"], row["copy_id"], row["deletable"]), (40, 1488, True))

    def test_a_copy_holding_something_the_own_id_lacks_is_kept_for_review(self):
        self.identity(1488); self.listing([[40, 50], [1488, 0]])
        (row,) = self.find(self.dev)
        self.assertFalse(row["deletable"])

    def test_a_copy_that_is_another_persons_staff_number_is_never_deletable(self):
        Employee.objects.create(employee_id="001488", first_name="Bob", last_name="B", status="active")
        self.identity(1488); self.listing([[40, 50], [40, 0], [1488, 0]])
        (row,) = self.find(self.dev)
        self.assertFalse(row["deletable"])

    def test_someone_on_their_own_id_or_without_it_on_the_terminal_is_left_alone(self):
        self.identity(40); self.listing([[40, 50]])
        self.assertEqual(self.find(self.dev), [])
        BiometricIdentity.objects.all().delete()
        self.identity(1488); self.listing([[1488, 50]])  # own id not on the terminal: nothing to re-point to
        self.assertEqual(self.find(self.dev), [])

    def test_the_command_repoints_and_queues_the_deletion_only_when_asked(self):
        from io import StringIO

        from django.core.management import call_command

        self.identity(1488); self.listing([[40, 50], [40, 0], [1488, 0]])
        call_command("repair_duplicate_ids", stdout=StringIO())
        self.assertEqual(BiometricIdentity.objects.get().external_user_id, "1488")  # dry run changes nothing
        call_command("repair_duplicate_ids", "--apply", stdout=StringIO())
        self.assertEqual(BiometricIdentity.objects.get().external_user_id, "40")
        self.assertFalse(DeviceCommand.objects.filter(command_type="purge_user").exists())
        BiometricIdentity.objects.update(external_user_id="1488")
        call_command("repair_duplicate_ids", "--apply", "--delete-copies", stdout=StringIO())
        self.assertEqual(DeviceCommand.objects.get(command_type="purge_user").payload["enrollid"], 1488)


class BacklogAndPriorityTests(TestCase):
    def test_the_planner_stops_adding_relays_to_a_terminal_that_already_has_a_long_queue(self):
        from attendance.services import device_sync

        a = BiometricDevice.objects.create(name="A", serial_number="A1", location="x", device_type="factory", purpose="attendance")
        b = BiometricDevice.objects.create(name="B", serial_number="B1", location="x", device_type="factory", purpose="attendance")
        for number in range(1, 6):
            person = Employee.objects.create(employee_id=f"{number:06d}", first_name="P", last_name=str(number), status="active")
            for device in (a, b):
                BiometricIdentity.objects.create(employee=person, system=IDENTITY_SYSTEM, source_identifier=device.serial_number, external_user_id=str(number))
        DeviceCommand.objects.create(device=a, command_type="list_user_slots", status="acked", completed_at=timezone.now(), result={"slots": [[n, 0] for n in range(1, 6)]})
        DeviceCommand.objects.create(device=b, command_type="list_user_slots", status="acked", completed_at=timezone.now(), result={"slots": []})
        with mock.patch.object(device_sync, "MAX_WAITING_PER_SOURCE", 3):
            device_sync.plan_slot_clones([a, b])
        self.assertEqual(DeviceCommand.objects.filter(command_type="clone_enrollment", status="pending").count(), 3)

    def test_a_move_to_the_staff_number_goes_before_the_coverage_relays(self):
        from attendance.management.commands.run_aiface_gateway import Command

        device = BiometricDevice.objects.create(name="D", serial_number="D9", location="x", device_type="factory", purpose="attendance")
        relay = DeviceCommand.objects.create(device=device, command_type="clone_enrollment", payload={"pushes": [], "employee_id": 1})
        move = DeviceCommand.objects.create(device=device, command_type="clone_enrollment", payload={"renumber": True, "employee_id": 2})
        self.assertEqual(Command._next_command_to_send("D9")[0], move.pk)
        self.assertNotEqual(relay.pk, move.pk)


class MirrorPlanTests(TestCase):
    def setUp(self):
        from attendance.services import device_sync

        self.ds = device_sync
        self.dev = BiometricDevice.objects.create(name="D", serial_number="D1", location="x", device_type="factory", purpose="attendance")
        self.ada = Employee.objects.create(employee_id="000040", first_name="Ada", last_name="Okafor", status="active")

    def listing(self, slots):
        DeviceCommand.objects.create(device=self.dev, command_type="list_user_slots", status="acked", completed_at=timezone.now(), result={"slots": slots})

    def probe_result(self, names):
        DeviceCommand.objects.create(device=self.dev, command_type="clone_enrollment", status="acked", completed_at=timezone.now(), payload={"name_probe": True, "items": []}, result={"names": names})

    def own(self, enrollid=40):
        BiometricIdentity.objects.create(employee=self.ada, system=IDENTITY_SYSTEM, source_identifier="D1", external_user_id=str(enrollid))

    def test_names_compare_regardless_of_order_and_case(self):
        self.assertEqual(self.ds.name_key("OKAFOR  ada"), self.ds.name_key("Ada Okafor"))

    def test_the_smallest_credential_is_asked_for_and_a_face_only_when_nothing_else_exists(self):
        self.assertEqual(self.ds._probe_slot({50, 0, 11}), 11)
        self.assertEqual(self.ds._probe_slot({50, 3}), 3)
        self.assertEqual(self.ds._probe_slot({50}), 50)

    def test_only_unidentifiable_entries_are_probed_and_a_leavers_number_is_not(self):
        Employee.objects.create(employee_id="000099", first_name="Gone", last_name="Leaver", status="inactive")
        self.own(); self.listing([[40, 50], [1500, 0], [99, 0]])
        self.assertEqual(self.ds.queue_name_probes([self.dev]), 1)
        (job,) = DeviceCommand.objects.filter(payload__has_key="name_probe")
        self.assertEqual(job.payload["items"], [{"enrollid": 1500, "backupnum": 0}])

    def test_a_leftover_copy_of_an_active_person_is_moved_onto_their_staff_number(self):
        self.own(); self.listing([[40, 50], [1500, 0], [1500, 50]]); self.probe_result({"1500": "ada OKAFOR"})
        plan = self.ds.plan_mirror([self.dev])[self.dev]
        self.assertEqual([(m["id"], m["to"], m["slots"]) for m in plan["move"]], [(1500, 40, [0])])
        self.assertEqual(plan["leaver"] + plan["review"], [])

    def test_a_leavers_entry_and_a_useless_second_copy_are_deleted_and_an_unknown_name_is_left_for_review(self):
        Employee.objects.create(employee_id="000099", first_name="Gone", last_name="Leaver", status="inactive")
        self.own(); self.listing([[40, 50], [40, 0], [1500, 0], [1501, 0], [99, 0], [1600, 0]])
        self.probe_result({"1500": "Ada Okafor", "1501": "Ada Okafor", "1600": "Somebody Else"})
        plan = self.ds.plan_mirror([self.dev])[self.dev]
        self.assertEqual([i["id"] for i in plan["leaver"]], [99])
        self.assertEqual([i["id"] for i in plan["move"]], [1500])
        self.assertEqual([i["id"] for i in plan["extra_copy"]], [1501])
        self.assertEqual([(i["id"], i["reason"]) for i in plan["review"]], [(1600, "no employee with this name")])

    def test_apply_queues_the_deletions_and_moves_but_never_the_review_entries(self):
        Employee.objects.create(employee_id="000099", first_name="Gone", last_name="Leaver", status="inactive")
        self.own(); self.listing([[40, 50], [1500, 0], [99, 0], [1600, 0]]); self.probe_result({"1500": "Ada Okafor", "1600": "Nobody Known"})
        self.ds.plan_mirror([self.dev], apply=True)
        self.assertEqual(DeviceCommand.objects.get(command_type="purge_user").payload["enrollid"], 99)
        move = DeviceCommand.objects.get(payload__has_key="renumber")
        self.assertEqual((move.payload["from_id"], move.payload["to_id"]), (1500, 40))
        self.assertFalse(DeviceCommand.objects.filter(payload__enrollid=1600).exists())
        self.ds.plan_mirror([self.dev], apply=True)  # repeating queues nothing new
        self.assertEqual(DeviceCommand.objects.filter(command_type="purge_user").count(), 1)

    def test_an_active_persons_staff_number_held_by_someone_else_is_never_taken(self):
        bob = Employee.objects.create(employee_id="000041", first_name="Bob", last_name="B", status="active")
        BiometricIdentity.objects.create(employee=bob, system=IDENTITY_SYSTEM, source_identifier="D1", external_user_id="40")
        self.listing([[40, 0], [1500, 0]]); self.probe_result({"1500": "Ada Okafor"})
        plan = self.ds.plan_mirror([self.dev])[self.dev]
        self.assertEqual(plan["move"], [])
        self.assertEqual(plan["review"][0]["id"], 1500)

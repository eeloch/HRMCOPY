from datetime import datetime

from unittest import mock

from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from attendance.integrations.aiface_protocol import (
    build_adduser_command,
    build_deleteuser_command,
    build_device_command,
    build_enableuser_command,
    build_getuserids_command,
    build_getuserinfo_command,
    build_getuserlist_command,
    build_reg_ack,
    build_setuserinfo_command,
    build_sendlog_ack,
    build_senduser_ack,
    build_user_enabled_profile_command,
    stable_record_id,
    translate_sendlog_record,
)


class RegAckTests(SimpleTestCase):
    def test_asks_device_to_skip_user_sync_and_images(self):
        ack = build_reg_ack(datetime(2026, 1, 1, 8, 0, 0))

        self.assertEqual(ack["ret"], "reg")
        self.assertTrue(ack["result"])
        self.assertEqual(ack["cloudtime"], "2026-01-01 08:00:00")
        self.assertTrue(ack["nosenduser"])
        self.assertTrue(ack["nosendimage"])
        self.assertFalse(ack["nosendlog"])


class SendUserAckTests(SimpleTestCase):
    def test_always_acknowledges_success(self):
        ack = build_senduser_ack(datetime(2026, 1, 1, 8, 0, 0))

        self.assertEqual(ack, {"ret": "senduser", "result": True, "cloudtime": "2026-01-01 08:00:00"})


class SendLogAckTests(SimpleTestCase):
    def test_includes_count_and_logindex_when_device_expects_confirmation(self):
        ack = build_sendlog_ack(datetime(2026, 1, 1, 8, 0, 0), result=True, count=3, logindex=0)

        self.assertEqual(ack["count"], 3)
        self.assertEqual(ack["logindex"], 0)

    def test_omits_count_and_logindex_when_device_sent_none(self):
        ack = build_sendlog_ack(datetime(2026, 1, 1, 8, 0, 0), result=True, count=None, logindex=None)

        self.assertNotIn("count", ack)
        self.assertNotIn("logindex", ack)

    def test_can_report_failure_so_device_retries(self):
        ack = build_sendlog_ack(datetime(2026, 1, 1, 8, 0, 0), result=False, count=1, logindex=0)

        self.assertFalse(ack["result"])


class StableRecordIdTests(SimpleTestCase):
    def test_same_punch_always_hashes_to_the_same_id(self):
        record = {"enrollid": 123, "time": "2026-01-01 08:00:00", "mode": 1, "inout": 0, "event": 0}

        first = stable_record_id("LF00000001", record)
        second = stable_record_id("LF00000001", record)

        self.assertEqual(first, second)
        self.assertGreater(first, 0)

    def test_same_punch_on_different_devices_hashes_differently(self):
        record = {"enrollid": 123, "time": "2026-01-01 08:00:00", "mode": 1, "inout": 0, "event": 0}

        self.assertNotEqual(
            stable_record_id("LF00000001", record),
            stable_record_id("LF00000002", record),
        )

    def test_distinct_punches_hash_differently(self):
        base = {"enrollid": 123, "time": "2026-01-01 08:00:00", "mode": 1, "inout": 0, "event": 0}
        different_time = {**base, "time": "2026-01-01 08:00:01"}

        self.assertNotEqual(
            stable_record_id("LF00000001", base),
            stable_record_id("LF00000001", different_time),
        )


class TranslateSendlogRecordTests(SimpleTestCase):
    def test_maps_aiface_fields_to_bridge_shape(self):
        record = {
            "enrollid": 123,
            "time": "2026-01-01 08:00:00",
            "mode": 1,
            "inout": 0,
            "event": 0,
        }

        translated = translate_sendlog_record("LF00000001", record)

        self.assertEqual(translated["enroll_id"], 123)
        self.assertEqual(translated["device_serial_number"], "LF00000001")
        self.assertEqual(translated["timestamp"], "2026-01-01 08:00:00")
        self.assertEqual(translated["mode"], 1)
        self.assertEqual(translated["inout"], 0)
        self.assertEqual(translated["event"], 0)
        self.assertIsNone(translated["temperature"])
        self.assertIsInstance(translated["gateway_record_id"], int)
        self.assertGreater(translated["gateway_record_id"], 0)

    def test_converts_device_temperature_units_to_celsius(self):
        record = {"enrollid": 1, "time": "2026-01-01 08:00:00", "mode": 1, "inout": 0, "event": 0, "temp": 365}

        translated = translate_sendlog_record("LF00000001", record)

        self.assertEqual(translated["temperature"], 36.5)

    def test_never_forwards_raw_biometric_fields(self):
        record = {
            "enrollid": 1,
            "time": "2026-01-01 08:00:00",
            "mode": 1,
            "inout": 0,
            "event": 0,
            "image": "base64-face-photo-data",
        }

        translated = translate_sendlog_record("LF00000001", record)

        self.assertNotIn("image", translated)


class BuildAdduserCommandTests(SimpleTestCase):
    def test_face_enrollment_uses_backupnum_50(self):
        command = build_adduser_command("LF00000001", 42, "Test Employee", "face")

        self.assertEqual(command["cmd"], "adduser")
        self.assertEqual(command["enrollid"], 42)
        self.assertEqual(command["name"], "Test Employee")
        self.assertEqual(command["backupnum"], 50)

    def test_fingerprint_enrollment_uses_backupnum_0(self):
        command = build_adduser_command("LF00000001", 42, "Test Employee", "fingerprint")

        self.assertEqual(command["backupnum"], 0)

    def test_unknown_biometric_type_defaults_to_face(self):
        command = build_adduser_command("LF00000001", 42, "Test Employee", "palm")

        self.assertEqual(command["backupnum"], 50)


class BuildDeleteuserCommandTests(SimpleTestCase):
    def test_deletes_the_whole_user_not_one_slot(self):
        command = build_deleteuser_command("LF00000001", 42)

        self.assertEqual(command, {"cmd": "deleteuser", "sn": "LF00000001", "enrollid": 42, "aliasid": "42", "backupnum": 12})


class BuildGetuseridsCommandTests(SimpleTestCase):
    def test_requests_the_full_unpaginated_id_list(self):
        self.assertEqual(build_getuserids_command("LF00000001"), {"cmd": "getuserids", "sn": "LF00000001"})


class BuildGetuserinfoCommandTests(SimpleTestCase):
    def test_face_uses_backupnum_50(self):
        command = build_getuserinfo_command("LF00000001", 42, "face")
        self.assertEqual(command, {"cmd": "getuserinfo", "sn": "LF00000001", "enrollid": 42, "backupnum": 50})

    def test_fingerprint_uses_backupnum_0(self):
        command = build_getuserinfo_command("LF00000001", 42, "fingerprint")
        self.assertEqual(command["backupnum"], 0)


class BuildSetuserinfoCommandTests(SimpleTestCase):
    def test_carries_the_captured_record_through_unmodified(self):
        command = build_setuserinfo_command("LF00000002", 7, "Test Employee", "face", "base64-captured-template")

        self.assertEqual(command["cmd"], "setuserinfo")
        self.assertEqual(command["sn"], "LF00000002")
        self.assertEqual(command["enrollid"], 7)
        self.assertEqual(command["name"], "Test Employee")
        self.assertEqual(command["backupnum"], 50)
        self.assertEqual(command["admin"], 0)
        self.assertEqual(command["record"], "base64-captured-template")

    def test_fingerprint_uses_backupnum_0(self):
        command = build_setuserinfo_command("LF00000002", 7, "Test Employee", "fingerprint", "template-data")
        self.assertEqual(command["backupnum"], 0)


class BuildDeviceCommandTests(SimpleTestCase):
    def test_purge_user_is_a_plain_deleteuser_on_the_wire(self):
        self.assertEqual(build_device_command("LF1", "purge_user", {"enrollid": 9}), build_deleteuser_command("LF1", 9))

    def test_dispatches_enroll_user_to_adduser(self):
        command = build_device_command("LF00000001", "enroll_user", {"enrollid": 5, "name": "A", "biometric_type": "face"})

        self.assertEqual(command["cmd"], "adduser")

    def test_dispatches_delete_user_to_deleteuser(self):
        command = build_device_command("LF00000001", "delete_user", {"enrollid": 5})

        self.assertEqual(command["cmd"], "deleteuser")

    def test_dispatches_refresh_enrolled_ids_to_getuserids(self):
        command = build_device_command("LF00000001", "refresh_enrolled_ids", {})

        self.assertEqual(command["cmd"], "getuserids")

    def test_dispatches_set_user_enabled_to_the_profile_command_not_the_legacy_one(self):
        """Confirmed on production 2026-09-23: the legacy enableuser command is acked as successful but
        only actually blocks face verification, not card or fingerprint - see
        build_user_enabled_profile_command's docstring."""
        command = build_device_command("LF00000001", "set_user_enabled", {"enrollid": 5, "enabled": True})

        self.assertEqual(command, build_user_enabled_profile_command("LF00000001", 5, True))
        self.assertNotEqual(command, build_enableuser_command("LF00000001", 5, True))

    def test_unknown_command_type_raises(self):
        with self.assertRaises(ValueError):
            build_device_command("LF00000001", "reboot_device", {})


class BuildUserEnabledProfileCommandTests(SimpleTestCase):
    def test_enable(self):
        self.assertEqual(
            build_user_enabled_profile_command("LF00000001", 7, True),
            {"cmd": "setuserinfo", "sn": "LF00000001", "enrollid": 7, "enable": 1},
        )

    def test_disable(self):
        self.assertEqual(
            build_user_enabled_profile_command("LF00000001", 7, False),
            {"cmd": "setuserinfo", "sn": "LF00000001", "enrollid": 7, "enable": 0},
        )


class SendLogAckAccessTests(SimpleTestCase):
    def test_attendance_terminals_get_the_same_reply_as_before(self):
        ack = build_sendlog_ack(datetime(2026, 1, 1, 8, 0, 0), result=True, count=1, logindex=0)
        self.assertNotIn("access", ack)
        self.assertNotIn("message", ack)

    def test_meal_terminals_can_be_told_to_allow_and_what_to_show(self):
        ack = build_sendlog_ack(datetime(2026, 1, 1, 8, 0, 0), result=True, count=1, logindex=0, access=1, message="Ada: Ticket 1 of 1")
        self.assertEqual((ack["access"], ack["message"]), (1, "Ada: Ticket 1 of 1"))


class MinimalRegAckTests(SimpleTestCase):
    def test_a_meal_terminal_gets_only_the_documented_reply(self):
        from attendance.integrations.aiface_protocol import build_reg_ack
        ack = build_reg_ack(datetime(2026, 1, 1, 8, 0, 0), minimal=True)
        self.assertEqual(set(ack), {"ret", "result", "cloudtime"})

    def test_the_standard_reply_is_unchanged_for_attendance_terminals(self):
        from attendance.integrations.aiface_protocol import build_reg_ack
        ack = build_reg_ack(datetime(2026, 1, 1, 8, 0, 0))
        self.assertTrue(ack["nosenduser"] and ack["nosendimage"])


class ChooseTerminalReplyTests(SimpleTestCase):
    entitled = [{"access": 1, "entitled": True, "message": "Ticket 1 of 2 - Ada"}]
    extra = [{"access": 1, "entitled": False, "message": "Not entitled - Ada"}]
    unknown = [{"access": 0, "entitled": False, "message": "Not enrolled for meals"}]

    def test_minimal_adds_nothing(self):
        from attendance.integrations.aiface_protocol import choose_terminal_reply
        self.assertEqual(choose_terminal_reply("minimal", self.entitled), (None, None))
        self.assertEqual(choose_terminal_reply("gated", []), (None, None))

    def test_message_mode_allows_everyone_recognised_and_shows_the_line(self):
        from attendance.integrations.aiface_protocol import choose_terminal_reply
        self.assertEqual(choose_terminal_reply("message", self.extra), (1, "Not entitled - Ada"))
        self.assertEqual(choose_terminal_reply("message", self.unknown), (0, "Not enrolled for meals"))

    def test_gated_mode_only_allows_entitled_scans(self):
        from attendance.integrations.aiface_protocol import choose_terminal_reply
        self.assertEqual(choose_terminal_reply("gated", self.entitled), (1, "Ticket 1 of 2 - Ada"))
        self.assertEqual(choose_terminal_reply("gated", self.extra), (0, "Not entitled - Ada"))
        self.assertEqual(choose_terminal_reply("gated", self.unknown), (0, "Not enrolled for meals"))


class NextCommandPriorityTests(TestCase):
    """The meal terminal takes one command at a time, so what it is sent first decides how fast a person
    who has used their last ticket is switched off (2026-09-23: a third ticket got through behind a queue)."""

    def setUp(self):
        from attendance.management.commands.run_aiface_gateway import Command
        from attendance.models import BiometricDevice

        self.next_command = Command._next_command_to_send
        self.meal = BiometricDevice.objects.create(name="Meal", serial_number="MEAL1", location="x", device_type="factory", purpose="meal_ticket")
        self.attendance = BiometricDevice.objects.create(name="Att", serial_number="ATT1", location="x", device_type="factory", purpose="attendance")

    def queue(self, device, command_type, payload):
        from attendance.models import DeviceCommand

        return DeviceCommand.objects.create(device=device, command_type=command_type, payload=payload)

    def at_hour(self, hour):
        return mock.patch("attendance.management.commands.run_aiface_gateway.timezone.localtime", return_value=timezone.now().replace(hour=hour))

    def test_switching_a_person_off_goes_before_everything_else(self):
        self.queue(self.meal, "enroll_user", {"enrollid": 1})
        switch = self.queue(self.meal, "set_user_enabled", {"enrollid": 2, "enabled": False})
        self.assertEqual(self.next_command("MEAL1")[0], switch.pk)

    def scan_meal_minutes_ago(self, minutes):
        from datetime import timedelta

        from employees.models import Employee
        from meals.models import MealDevice, MealEvent

        employee = Employee.objects.create(employee_id="MP-1", first_name="M", last_name="P")
        device = MealDevice.objects.create(name="Meal", serial_number="MEAL1", active=True)
        MealEvent.objects.create(employee=employee, device=device, timestamp=timezone.now() - timedelta(minutes=minutes), external_event_id="e1", source_system="t")

    def test_a_clone_is_held_on_a_meal_terminal_while_meals_are_being_served(self):
        self.scan_meal_minutes_ago(2)
        self.queue(self.meal, "clone_enrollment", {"employee_id": 1, "enrollid": 1, "target_device_ids": [self.attendance.pk]})
        with self.at_hour(12):
            self.assertIsNone(self.next_command("MEAL1"))

    def test_a_clone_aimed_at_a_meal_terminal_is_held_while_meals_are_being_served(self):
        self.scan_meal_minutes_ago(2)
        self.queue(self.attendance, "clone_enrollment", {"employee_id": 1, "enrollid": 1, "target_device_ids": [self.meal.pk]})
        with self.at_hour(12):
            self.assertIsNone(self.next_command("ATT1"))

    def test_a_clone_runs_on_a_meal_terminal_once_it_has_been_idle(self):
        self.scan_meal_minutes_ago(30)
        clone = self.queue(self.meal, "clone_enrollment", {"employee_id": 1, "enrollid": 1, "target_device_ids": [self.attendance.pk]})
        with self.at_hour(12):
            self.assertEqual(self.next_command("MEAL1")[0], clone.pk)

    def test_held_background_jobs_do_not_block_a_switch_queued_behind_them(self):
        self.scan_meal_minutes_ago(2)
        self.queue(self.meal, "clone_enrollment", {"employee_id": 1, "enrollid": 1, "target_device_ids": []})
        switch = self.queue(self.meal, "set_user_enabled", {"enrollid": 2, "enabled": False})
        with self.at_hour(12):
            self.assertEqual(self.next_command("MEAL1")[0], switch.pk)

    def test_background_jobs_still_run_overnight(self):
        clone = self.queue(self.meal, "clone_enrollment", {"employee_id": 1, "enrollid": 1, "target_device_ids": []})
        with self.at_hour(2):
            self.assertEqual(self.next_command("MEAL1")[0], clone.pk)

    def test_attendance_terminals_are_unaffected(self):
        clone = self.queue(self.attendance, "clone_enrollment", {"employee_id": 1, "enrollid": 1, "target_device_ids": [self.attendance.pk]})
        with self.at_hour(12):
            self.assertEqual(self.next_command("ATT1")[0], clone.pk)


class ListUserSlotsTests(NextCommandPriorityTests):
    def test_the_request_pages_like_the_vendor_reference_server(self):
        self.assertEqual(build_getuserlist_command("S1", True), {"cmd": "getuserlist", "sn": "S1", "stn": True})
        self.assertEqual(build_getuserlist_command("S1", False)["stn"], False)

    def test_a_slot_listing_is_handed_to_the_multi_message_handler_not_sent_as_one_wire_message(self):
        command = self.queue(self.attendance, "list_user_slots", {})
        self.assertEqual(self.next_command("ATT1"), (command.pk, None))


class SlotListingCompletenessTests(SimpleTestCase):
    """A listing that stops early must never be stored as the terminal's whole list (2026-09-24: a terminal holding
    533 people was recorded as holding 48, another as holding 103, which planned hundreds of pointless relays)."""

    def run_listing(self, pages, previous=0):
        import asyncio

        from attendance.management.commands.run_aiface_gateway import Command

        command, finished = Command(), []
        replies = iter(pages)

        async def send_and_wait(ws, sn, message, timeout):
            reply = next(replies)
            if reply is None:
                raise asyncio.TimeoutError
            return reply

        async def run_db(func, *args):
            if func.__name__ == "_finish_clone_command":
                finished.append(args[1:])
            elif func.__name__ == "_previous_listing_size":
                return previous

        command._send_and_wait, command._run_db = send_and_wait, run_db
        asyncio.run(command._run_list_user_slots(None, "S1", 1))
        return finished

    @staticmethod
    def page(size):
        return {"result": True, "record": [{"enrollid": n, "backupnum": 50} for n in range(size)]}

    def test_an_empty_page_ends_the_list(self):
        finished = self.run_listing([self.page(100), self.page(60), {"result": True, "record": []}], previous=160)
        self.assertEqual(finished[0][0], "acked")
        self.assertEqual(len(finished[0][1]["slots"]), 160)

    def test_silence_after_the_last_page_is_accepted_when_the_size_matches_last_time(self):
        finished = self.run_listing([self.page(100), self.page(60), None], previous=165)
        self.assertEqual(finished[0][0], "acked")

    def test_silence_that_leaves_a_far_smaller_list_than_last_time_is_a_failure(self):
        finished = self.run_listing([self.page(100), self.page(100), None], previous=1000)
        self.assertEqual(finished[0][0], "failed")
        self.assertIn("incomplete", finished[0][1]["detail"])

    def test_the_first_ever_listing_is_accepted(self):
        finished = self.run_listing([self.page(100), None], previous=0)
        self.assertEqual(finished[0][0], "acked")


class SlotTargetIdTests(TestCase):
    """A relay must never invent a new terminal id for someone (2026-09-24: ~130 people duplicated on one terminal)."""

    def setUp(self):
        from attendance.integrations.aiface_protocol import IDENTITY_SYSTEM
        from attendance.management.commands.run_aiface_gateway import Command
        from attendance.models import BiometricDevice
        from employees.models import BiometricIdentity, Employee

        self.system, self.Identity, self.Command = IDENTITY_SYSTEM, BiometricIdentity, Command
        self.device = BiometricDevice.objects.create(name="T", serial_number="T1", location="x", device_type="factory", purpose="attendance")
        self.ada = Employee.objects.create(employee_id="000040", first_name="Ada", last_name="A", status="active")
        self.bob = Employee.objects.create(employee_id="000041", first_name="Bob", last_name="B", status="active")

    def link(self, employee, enrollid):
        return self.Identity.objects.create(employee=employee, system=self.system, source_identifier="T1", external_user_id=str(enrollid))

    def test_a_person_keeps_the_id_they_already_have_on_the_terminal(self):
        self.link(self.ada, 40)
        self.assertEqual(self.Command._slot_target_enrollid(self.ada.pk, self.device, 999), 40)

    def test_the_source_id_is_used_when_nobody_holds_it_there(self):
        self.assertEqual(self.Command._slot_target_enrollid(self.ada.pk, self.device, 40), 40)

    def test_an_id_held_by_someone_else_is_refused_not_replaced_by_a_new_number(self):
        self.link(self.bob, 40)
        self.assertIsNone(self.Command._slot_target_enrollid(self.ada.pk, self.device, 40))

    def test_linking_after_a_relay_never_overwrites_an_existing_identity(self):
        self.link(self.ada, 40)
        self.Command._link_identity_if_missing(self.ada.pk, self.device, 1500)
        self.assertEqual(self.Identity.objects.get(employee=self.ada, source_identifier="T1").external_user_id, "40")

    def test_an_id_on_the_terminal_that_is_the_persons_own_staff_number_is_theirs(self):
        from django.utils import timezone

        from attendance.models import DeviceCommand

        DeviceCommand.objects.create(device=self.device, command_type="list_user_slots", status="acked", completed_at=timezone.now(), result={"slots": [[40, 50]]})
        self.assertEqual(self.Command._slot_target_enrollid(self.ada.pk, self.device, 40), 40)

    def test_an_unlinked_id_on_the_terminal_that_is_not_the_persons_own_number_is_left_alone(self):
        from django.utils import timezone

        from attendance.models import DeviceCommand

        DeviceCommand.objects.create(device=self.device, command_type="list_user_slots", status="acked", completed_at=timezone.now(), result={"slots": [[77, 50]]})
        self.assertIsNone(self.Command._slot_target_enrollid(self.ada.pk, self.device, 77))


class PollerWatchdogTests(SimpleTestCase):
    """2026-09-24: a meal terminal's command poller sat idle ~40 min on a live connection, so switch-offs were not sent."""

    def run_watchdog(self, activity_offsets, task_done=False):
        import asyncio
        import io

        from attendance.management.commands.run_aiface_gateway import Command

        real_sleep = asyncio.sleep

        async def quick_sleep(_seconds):
            await real_sleep(0.001)

        class FakeWS:
            closed = False

            async def close(self):
                self.closed = True

        async def scenario():
            loop = asyncio.get_running_loop()
            now = loop.time()
            ws, log = FakeWS(), io.StringIO()
            command = Command(stdout=log)
            task = asyncio.create_task(real_sleep(0 if task_done else 3600))
            await real_sleep(0.01)
            activity = {"seen": now, "beat": now - activity_offsets["idle"], "busy_since": (now - activity_offsets["busy"]) if activity_offsets.get("busy") else None}
            with mock.patch("attendance.management.commands.run_aiface_gateway.asyncio.sleep", quick_sleep):
                watcher = asyncio.create_task(command._drop_if_silent(ws, activity, task, "S1"))
                await real_sleep(0.05)
                watcher.cancel()
            task.cancel()
            return ws.closed, log.getvalue()

        return asyncio.run(scenario())

    def test_an_idle_poller_that_has_stopped_looping_gets_the_connection_closed_and_the_stack_logged(self):
        closed, log = self.run_watchdog({"idle": 120})
        self.assertTrue(closed)
        self.assertIn("command poller stalled", log)

    def test_a_poller_that_keeps_looping_is_left_alone(self):
        closed, _ = self.run_watchdog({"idle": 1})
        self.assertFalse(closed)

    def test_a_long_job_is_allowed_its_time_but_not_forever(self):
        self.assertFalse(self.run_watchdog({"idle": 200, "busy": 200})[0])
        self.assertTrue(self.run_watchdog({"idle": 600, "busy": 600})[0])

    def test_a_poller_that_has_ended_closes_the_connection(self):
        self.assertTrue(self.run_watchdog({"idle": 1}, task_done=True)[0])


class PollerSurvivesErrorsTests(SimpleTestCase):
    """The poller task was found dead (`done=True`) on live connections after an exception inside a relay."""

    def run_poller(self, steps):
        import asyncio
        import io

        import websockets
        import websockets.exceptions

        from attendance.management.commands.run_aiface_gateway import Command

        calls = {"n": 0}
        command = Command(stdout=io.StringIO(), stderr=io.StringIO())

        async def fake_step(ws, sn, activity):
            index = calls["n"]
            calls["n"] += 1
            outcome = steps[index] if index < len(steps) else "stop"
            if outcome == "error":
                raise ValueError("boom")
            if outcome == "closed":
                raise websockets.exceptions.ConnectionClosedOK(None, None)
            if outcome == "stop":
                raise asyncio.CancelledError

        async def scenario():
            real_sleep = asyncio.sleep

            async def quick(_s):
                await real_sleep(0)

            with mock.patch.object(command, "_poll_step", fake_step), mock.patch("attendance.management.commands.run_aiface_gateway.asyncio.sleep", quick):
                await command._poll_commands(None, "S1", {})

        asyncio.run(scenario())
        return calls["n"], command.stderr.getvalue()

    def test_an_error_in_one_pass_is_logged_and_the_poller_carries_on(self):
        passes, errors = self.run_poller(["error", "ok", "ok"])
        self.assertGreaterEqual(passes, 3)
        self.assertIn("boom", errors)

    def test_a_closed_connection_ends_the_poller_quietly(self):
        passes, errors = self.run_poller(["ok", "closed"])
        self.assertEqual(passes, 2)
        self.assertEqual(errors, "")

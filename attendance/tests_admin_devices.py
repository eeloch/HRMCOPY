from datetime import date, time, timedelta

from django.contrib.admin.helpers import ACTION_CHECKBOX_NAME
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from attendance.admin_devices import command_summary, format_duration, result_summary, retry_blocked_reason, scrub, weekday_text
from attendance.models import BiometricDevice, DeviceCommand, Shift, ShiftAssignment, ShiftPlan, ShiftPlanAssignment
from employees.models import Employee

TEMPLATE_TEXT = "QUJDREVGR0hJSktMTU5PUFFSU1RVVldYWVo" * 30  # stands for a face template: must never reach a page


class AdminDevicesBase(TestCase):
    def setUp(self):
        self.root = get_user_model().objects.create_superuser("root", "root@example.com", "pw")
        self.client.force_login(self.root)
        self.dev2 = BiometricDevice.objects.create(
            name="Dev 2", serial_number="SN-2", location="Gate", device_type="factory", purpose="attendance",
            model="AiFace", ip_address="10.0.0.2", is_online=True, last_sync_at=timezone.now() - timedelta(seconds=20),
        )
        self.dev3 = BiometricDevice.objects.create(
            name="Dev 3", serial_number="SN-3", location="Canteen", device_type="factory", purpose="meal_ticket",
            ip_address="10.0.0.3", is_online=False, last_sync_at=timezone.now() - timedelta(hours=3),
        )
        self.never = BiometricDevice.objects.create(name="Spare", serial_number="SN-9", location="Store", device_type="office")
        self.ada = Employee.objects.create(employee_id="1604", first_name="Ada", last_name="Okafor")
        self.bola = Employee.objects.create(employee_id="62", first_name="Bola", last_name="Adeyemi")

    def command(self, device=None, command_type="list_user_slots", status="pending", payload=None, **extra):
        return DeviceCommand.objects.create(device=device or self.dev2, command_type=command_type, status=status, payload=payload or {}, **extra)

    def changelist(self, model, **params):
        return self.client.get(reverse(f"admin:attendance_{model}_changelist"), params)

    def run_action(self, model, action, objects):
        return self.client.post(
            reverse(f"admin:attendance_{model}_changelist"),
            {"action": action, ACTION_CHECKBOX_NAME: [obj.pk for obj in objects], "index": 0},
            follow=True,
        )

    def flashed(self, response):
        return " | ".join(str(message) for message in response.context["messages"])


class ChangelistsLoadTests(AdminDevicesBase):
    def setUp(self):
        super().setUp()
        self.day = Shift.objects.create(name="Day", start_time=time(7), end_time=time(19))
        self.night = Shift.objects.create(name="Night", start_time=time(19), end_time=time(7), is_overnight=True)
        self.fixed = ShiftPlan.objects.create(name="Permanent Day", kind="fixed", shift=self.day, working_weekdays=[0, 1, 2, 3, 4, 5], active=True)
        self.rotation = ShiftPlan.objects.create(name="Rotation", kind="rotation", day_shift=self.day, night_shift=self.night, anchor_monday=date(2026, 9, 21))
        ShiftAssignment.objects.create(employee=self.ada, shift=self.day, start_date=date(2026, 9, 1), assigned_by="hr")
        ShiftPlanAssignment.objects.create(employee=self.bola, plan=self.rotation, group="A", start_date=date(2026, 9, 21))
        self.command(payload={"enrollid": 1604, "employee_id": self.ada.pk}, command_type="purge_user")

    def test_every_changelist_and_change_page_loads_for_a_superuser(self):
        for model, obj in [
            ("biometricdevice", self.dev2), ("devicecommand", DeviceCommand.objects.first()), ("shift", self.day),
            ("shiftassignment", ShiftAssignment.objects.first()), ("shiftplan", self.rotation), ("shiftplanassignment", ShiftPlanAssignment.objects.first()),
        ]:
            with self.subTest(model=model):
                self.assertEqual(self.changelist(model).status_code, 200)
                self.assertEqual(self.client.get(reverse(f"admin:attendance_{model}_change", args=[obj.pk])).status_code, 200)

    def test_add_pages_load_except_for_commands_which_the_system_creates(self):
        for model in ("biometricdevice", "shift", "shiftassignment", "shiftplan", "shiftplanassignment"):
            with self.subTest(model=model):
                self.assertEqual(self.client.get(reverse(f"admin:attendance_{model}_add")).status_code, 200)
        self.assertEqual(self.client.get(reverse("admin:attendance_devicecommand_add")).status_code, 403)

    def test_filters_and_search_do_not_error(self):
        for model, params in [
            ("biometricdevice", {"purpose__exact": "meal_ticket"}), ("biometricdevice", {"device_type__exact": "factory"}),
            ("biometricdevice", {"is_online__exact": "1"}), ("biometricdevice", {"q": "10.0.0.3"}),
            ("devicecommand", {"status__exact": "pending"}), ("devicecommand", {"command_type__exact": "purge_user"}),
            ("devicecommand", {"created_at__gte": "2020-01-01"}), ("devicecommand", {"created_at__gte": "2020-01-01", "created_at__lt": "2100-01-01"}),
            ("shift", {"active__exact": "1"}), ("shift", {"q": "day"}),
            ("shiftassignment", {"state": "current"}), ("shiftassignment", {"state": "ended"}), ("shiftassignment", {"q": "1604"}),
            ("shiftplan", {"kind__exact": "rotation"}), ("shiftplanassignment", {"group__exact": "A"}), ("shiftplanassignment", {"state": "upcoming"}),
            ("shiftplanassignment", {"q": "Bola"}),
        ]:
            with self.subTest(model=model, params=params):
                self.assertEqual(self.changelist(model, **params).status_code, 200)

    def test_shift_list_flags_a_wrong_overnight_flag_and_counts_people(self):
        self.night.is_overnight = False
        self.night.save()
        page = self.changelist("shift").content.decode()
        self.assertIn("Crosses midnight, overnight is off", page)
        self.assertIn("07:00 - 19:00", page)
        self.assertIn("12 h", page)

    def test_shift_assignment_list_shows_staff_number_name_and_state(self):
        page = self.changelist("shiftassignment").content.decode()
        for text in ("1604", "Ada Okafor", "Day", "Current"):
            self.assertIn(text, page)
        ShiftAssignment.objects.create(employee=self.bola, shift=self.night, start_date=date(2020, 1, 1), end_date=date(2020, 2, 1))
        self.assertNotIn("Bola Adeyemi", self.changelist("shiftassignment", state="current").content.decode())
        self.assertIn("Bola Adeyemi", self.changelist("shiftassignment", state="ended").content.decode())

    def test_shift_plan_list_reads_as_words(self):
        page = self.changelist("shiftplan").content.decode()
        self.assertIn("Mon-Sat", page)
        self.assertIn("Day: Day / Night: Night", page)

    def test_a_plan_anchor_that_is_not_a_monday_is_flagged(self):
        self.rotation.anchor_monday = date(2026, 9, 22)
        self.rotation.save()
        self.assertIn("not a Monday", self.changelist("shiftplan").content.decode())

    def test_weekday_text(self):
        self.assertEqual(weekday_text([0, 1, 2, 3, 4, 5]), "Mon-Sat")
        self.assertEqual(weekday_text([0, 2, 6]), "Mon, Wed, Sun")
        self.assertEqual(weekday_text([]), "-")
        self.assertEqual(weekday_text(["x", 9, None]), "-")


class BiometricDeviceAdminTests(AdminDevicesBase):
    def test_list_reads_name_serial_purpose_last_seen_and_reachability(self):
        self.command(self.dev3, "clone_enrollment", payload={"renumber": True, "from_id": 1604, "to_id": 62, "slots": [0], "employee_id": self.ada.pk})
        self.command(self.dev3, "purge_user", payload={"enrollid": 5})
        page = self.changelist("biometricdevice").content.decode()
        for text in ("Dev 2", "SN-2", "Meal Ticket", "Canteen", "AiFace", "hours ago", "never", "Reachable", "Offline", "2 pending"):
            self.assertIn(text, page)

    def test_reachable_filter_uses_the_grace_period_and_counts_never_seen_terminals(self):
        yes = self.changelist("biometricdevice", reachable="yes").content.decode()
        no = self.changelist("biometricdevice", reachable="no").content.decode()
        self.assertIn("SN-2", yes)
        self.assertNotIn("SN-3", yes)
        self.assertNotIn("SN-2", no)
        self.assertIn("SN-3", no)
        self.assertIn("SN-9", no)
        self.dev3.last_sync_at = timezone.now() - timedelta(seconds=30)
        self.dev3.save()
        self.assertIn("SN-3", self.changelist("biometricdevice", reachable="yes").content.decode())

    def test_a_connected_flag_that_nothing_has_refreshed_for_a_long_time_is_shown_as_stale(self):
        self.dev2.last_sync_at = timezone.now() - timedelta(hours=2)
        self.dev2.save()
        self.assertIn("Flag stale", self.changelist("biometricdevice").content.decode())

    def test_search_by_name_serial_and_ip(self):
        for term, found, absent in [("Dev 3", "SN-3", "SN-2"), ("SN-2", "SN-2", "SN-3"), ("10.0.0.3", "SN-3", "SN-2")]:
            with self.subTest(term=term):
                page = self.changelist("biometricdevice", q=term).content.decode()
                self.assertIn(found, page)
                self.assertNotIn(absent, page)

    def test_filter_by_purpose(self):
        page = self.changelist("biometricdevice", purpose__exact="meal_ticket").content.decode()
        self.assertIn("SN-3", page)
        self.assertNotIn("SN-2", page)

    def test_the_status_fields_the_gateway_maintains_are_read_only(self):
        page = self.client.get(reverse("admin:attendance_biometricdevice_change", args=[self.dev2.pk])).content.decode()
        for field in ("is_online", "last_sync_at", "ip_address"):
            self.assertNotIn(f'name="{field}"', page)
        self.assertIn("10.0.0.2", page)

    def test_queue_a_fresh_slot_listing_creates_one_and_skips_a_terminal_that_already_waits(self):
        self.command(self.dev3, "list_user_slots", status="sent")
        response = self.run_action("biometricdevice", "queue_slot_listing", [self.dev2, self.dev3])
        created = DeviceCommand.objects.filter(command_type="list_user_slots", status="pending")
        self.assertEqual([c.device for c in created], [self.dev2])
        self.assertEqual(created[0].requested_by, self.root)
        self.assertEqual(created[0].payload, {})
        self.assertIn("Queued a slot listing for: Dev 2", self.flashed(response))
        self.assertIn("Dev 3", self.flashed(response))

    def test_cancel_pending_background_jobs_marks_them_failed_and_touches_nothing_else(self):
        relay = self.command(self.dev2, "clone_enrollment", payload={"employee_id": self.ada.pk, "pushes": []})
        purge = self.command(self.dev2, "purge_user", payload={"enrollid": 4})
        listing = self.command(self.dev2, "list_user_slots")
        enrol = self.command(self.dev2, "enroll_user", payload={"enrollid": 7})  # an admin's own command
        running = self.command(self.dev2, "clone_enrollment", status="sent")
        other_terminal = self.command(self.dev3, "purge_user", payload={"enrollid": 4})
        before = DeviceCommand.objects.count()

        response = self.run_action("biometricdevice", "cancel_background_jobs", [self.dev2])

        self.assertEqual(DeviceCommand.objects.count(), before)  # nothing deleted
        for cancelled in (relay, purge, listing):
            cancelled.refresh_from_db()
            self.assertEqual(cancelled.status, "failed")
            self.assertIn("Cancelled by root", cancelled.result["detail"])
            self.assertIsNotNone(cancelled.completed_at)
        for untouched, status in ((enrol, "pending"), (running, "sent"), (other_terminal, "pending")):
            untouched.refresh_from_db()
            self.assertEqual(untouched.status, status)
        self.assertIn("Cancelled 3 pending background job(s)", self.flashed(response))

    def test_actions_need_the_manage_devices_permission(self):
        viewer = get_user_model().objects.create_user("viewer", password="pw", is_staff=True)
        viewer.user_permissions.add(*Permission.objects.filter(codename__in=["view_biometricdevice", "view_devicecommand", "change_biometricdevice"]))
        self.client.force_login(viewer)
        page = self.changelist("biometricdevice").content.decode()
        self.assertNotIn("queue_slot_listing", page)
        self.assertNotIn("cancel_background_jobs", page)
        self.assertNotIn("retry_failed", self.changelist("devicecommand").content.decode())

    def test_str_is_readable(self):
        self.assertEqual(str(self.dev3), "Dev 3 (SN-3, Meal Ticket)")


class DeviceCommandAdminTests(AdminDevicesBase):
    def test_list_shows_terminal_type_status_person_and_a_short_summary(self):
        self.command(self.dev2, "clone_enrollment", status="acked", payload={"renumber": True, "employee_id": self.ada.pk, "name": "Ada Okafor", "from_id": 1604, "to_id": 62, "slots": [0]})
        self.command(self.dev3, "set_user_enabled", status="failed", payload={"enrollid": 40, "enabled": False, "legacy": True, "employee_id": self.bola.pk})
        page = self.changelist("devicecommand").content.decode()
        for text in ("Dev 2", "Dev 3", "Acknowledged", "Failed", "1604 - Ada Okafor", "62 - Bola Adeyemi", "renumber 1604 -&gt; 62 slots [0]", "switch OFF 40 (legacy)"):
            self.assertIn(text, page)
        self.assertNotIn("DeviceCommand object", page)

    def test_the_list_resolves_people_in_a_fixed_number_of_queries(self):
        for number in range(12):
            self.command(payload={"enrollid": number, "employee_id": self.ada.pk}, command_type="purge_user")
        with self.assertNumQueries(self.query_count_for(12)):
            self.changelist("devicecommand")

    def query_count_for(self, rows):
        # Measured once against an empty page, the count must not grow with the number of rows.
        DeviceCommand.objects.all().delete()
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        with CaptureQueriesContext(connection) as captured:
            self.changelist("devicecommand")
        for number in range(rows):
            self.command(payload={"enrollid": number, "employee_id": self.ada.pk}, command_type="purge_user")
        return len(captured) + 1  # the empty page has no employee lookup; the full one has exactly one

    def test_a_person_who_no_longer_exists_is_named_from_the_payload(self):
        self.command(command_type="clone_enrollment", payload={"renumber": True, "employee_id": 999999, "name": "Gone Person", "from_id": 1, "to_id": 2, "slots": []})
        page = self.changelist("devicecommand").content.decode()
        self.assertIn("Gone Person", page)
        self.assertIn("no longer in HRM", page)

    def test_a_junk_employee_id_does_not_break_the_page(self):
        self.command(command_type="purge_user", payload={"enrollid": 3, "employee_id": "not a number"})
        self.command(command_type="purge_user", payload=["unexpected", "list"])
        self.assertEqual(self.changelist("devicecommand").status_code, 200)

    def test_search_by_staff_number_name_enrollid_and_terminal(self):
        by_ada = self.command(command_type="purge_user", payload={"enrollid": 1500, "employee_id": self.ada.pk})
        by_bola = self.command(self.dev3, "purge_user", payload={"enrollid": 62, "employee_id": self.bola.pk})
        move = self.command(command_type="clone_enrollment", payload={"renumber": True, "employee_id": self.ada.pk, "from_id": 1777, "to_id": 1604, "slots": [0]})

        def found(term):
            page = self.changelist("devicecommand", q=term).content.decode()
            return {command.pk for command in (by_ada, by_bola, move) if f"#{command.pk}</a>" in page}

        self.assertEqual(found("1604"), {by_ada.pk, move.pk})  # staff number: everything queued for Ada
        self.assertEqual(found("Bola"), {by_bola.pk})  # name
        self.assertEqual(found("okafor"), {by_ada.pk, move.pk})
        self.assertEqual(found("62"), {by_bola.pk})  # terminal id / staff number
        self.assertEqual(found("1777"), {move.pk})  # the old end of a renumber
        self.assertEqual(found("SN-3"), {by_bola.pk})  # terminal serial
        self.assertEqual(found("Dev 2 Ada"), {by_ada.pk, move.pk})  # terms are ANDed
        self.assertEqual(found("nobody-called-this"), set())
        self.assertEqual(found("'; drop table --"), set())  # odd input is just a search

    def test_filters(self):
        pending = self.command(command_type="purge_user", payload={"enrollid": 1})
        acked = self.command(command_type="list_user_slots", status="acked", result={"slots": [[1, 0]], "pages": 1})
        off = self.command(self.dev3, "set_user_enabled", payload={"enrollid": 1, "enabled": False})
        on = self.command(self.dev3, "set_user_enabled", payload={"enrollid": 1, "enabled": True})
        renumber = self.command(command_type="clone_enrollment", payload={"renumber": True, "from_id": 1, "to_id": 2, "slots": []})

        def ids(**params):
            page = self.changelist("devicecommand", **params).content.decode()
            return {c.pk for c in (pending, acked, off, on, renumber) if f"#{c.pk}</a>" in page}

        self.assertEqual(ids(status__exact="pending"), {pending.pk, off.pk, on.pk, renumber.pk})
        self.assertEqual(ids(status__exact="acked"), {acked.pk})
        self.assertEqual(ids(command_type__exact="set_user_enabled"), {off.pk, on.pk})
        self.assertEqual(ids(device__id__exact=self.dev3.pk), {off.pk, on.pk})
        self.assertEqual(ids(kind="switch_off"), {off.pk})
        self.assertEqual(ids(kind="switch_on"), {on.pk})
        self.assertEqual(ids(kind="renumber"), {renumber.pk})
        self.assertEqual(ids(kind="background"), {pending.pk, acked.pk, renumber.pk})

    def test_commands_cannot_be_added_changed_or_deleted(self):
        command = self.command(status="failed")
        self.assertEqual(self.client.get(reverse("admin:attendance_devicecommand_add")).status_code, 403)
        change = reverse("admin:attendance_devicecommand_change", args=[command.pk])
        self.assertEqual(self.client.get(change).status_code, 200)  # viewing is fine
        self.assertNotIn('name="_save"', self.client.get(change).content.decode())
        self.assertEqual(self.client.post(change, {"status": "acked"}).status_code, 403)
        self.assertEqual(self.client.post(reverse("admin:attendance_devicecommand_delete", args=[command.pk]), {"post": "yes"}).status_code, 403)
        command.refresh_from_db()
        self.assertEqual(command.status, "failed")
        self.assertNotIn("delete_selected", self.changelist("devicecommand").content.decode())

    # -- actions --

    def test_cancel_selected_touches_only_pending_commands(self):
        pending = self.command(command_type="purge_user", payload={"enrollid": 1})
        sent = self.command(status="sent")
        acked = self.command(status="acked", result={"slots": []})
        failed = self.command(status="failed", result={"detail": "Timed out waiting for the device to respond."})
        before = DeviceCommand.objects.count()

        response = self.run_action("devicecommand", "cancel_pending", [pending, sent, acked, failed])

        self.assertEqual(DeviceCommand.objects.count(), before)
        pending.refresh_from_db()
        self.assertEqual((pending.status, pending.result), ("failed", {"detail": "Cancelled by root"}))
        self.assertIsNotNone(pending.completed_at)
        for command, status in ((sent, "sent"), (acked, "acked"), (failed, "failed")):
            command.refresh_from_db()
            self.assertEqual(command.status, status)
        failed.refresh_from_db()
        self.assertEqual(failed.result, {"detail": "Timed out waiting for the device to respond."})
        self.assertIn("Cancelled 1 pending", self.flashed(response))
        self.assertIn("3 selected command(s) were not pending", self.flashed(response))

    def test_retry_queues_a_fresh_copy_and_never_changes_the_original(self):
        original = self.command(
            self.dev3, "clone_enrollment", status="failed", payload={"employee_id": self.ada.pk, "pushes": [{"target_device_id": self.dev2.pk, "backupnums": [0, 50]}], "attempts": 3},
            result={"failed": [{"reason": "rejected"}]}, completed_at=timezone.now(), sent_at=timezone.now(),
        )
        response = self.run_action("devicecommand", "retry_failed", [original])

        original.refresh_from_db()
        self.assertEqual(original.status, "failed")
        self.assertEqual(original.payload["attempts"], 3)
        self.assertEqual(original.result, {"failed": [{"reason": "rejected"}]})
        copy = DeviceCommand.objects.exclude(pk=original.pk).get()
        self.assertEqual((copy.status, copy.device, copy.command_type, copy.result), ("pending", self.dev3, "clone_enrollment", {}))
        self.assertEqual(copy.payload, {"employee_id": self.ada.pk, "pushes": [{"target_device_id": self.dev2.pk, "backupnums": [0, 50]}]})  # the try counter starts again
        self.assertEqual(copy.requested_by, self.root)
        self.assertIsNone(copy.sent_at)
        self.assertIn("Queued 1 fresh copy", self.flashed(response))

    def test_retry_ignores_commands_that_are_not_failed_and_does_not_double_queue(self):
        pending = self.command(command_type="purge_user", payload={"enrollid": 1})
        acked = self.command(status="acked")
        failed_a = self.command(status="failed", payload={})
        failed_b = self.command(status="failed", payload={})  # the same job again
        response = self.run_action("devicecommand", "retry_failed", [pending, acked, failed_a, failed_b])
        self.assertEqual(DeviceCommand.objects.filter(status="pending").count(), 2)  # the old pending one and ONE new copy
        self.assertIn("Queued 1 fresh copy", self.flashed(response))
        self.assertIn("already waiting or running", self.flashed(response))
        self.assertIn("not failed", self.flashed(response))

    def test_retry_refuses_a_stale_switch_and_an_old_delete_but_allows_a_recent_one(self):
        switch = self.command(self.dev3, "set_user_enabled", status="failed", payload={"enrollid": 40, "enabled": False})
        old_purge = self.command(command_type="purge_user", status="failed", payload={"enrollid": 40}, completed_at=timezone.now() - timedelta(days=3))
        recent_purge = self.command(command_type="purge_user", status="failed", payload={"enrollid": 41}, completed_at=timezone.now() - timedelta(hours=1))
        self.run_action("devicecommand", "retry_failed", [switch, old_purge, recent_purge])
        fresh = DeviceCommand.objects.filter(status="pending")
        self.assertEqual([c.payload for c in fresh], [{"enrollid": 41}])
        self.assertIsNotNone(retry_blocked_reason(switch))
        self.assertIsNotNone(retry_blocked_reason(old_purge))
        self.assertIsNone(retry_blocked_reason(recent_purge))

    def test_a_cancelled_command_can_be_retried(self):
        job = self.command(command_type="purge_user", payload={"enrollid": 8})
        self.run_action("devicecommand", "cancel_pending", [job])
        self.run_action("devicecommand", "retry_failed", [job])
        self.assertEqual(DeviceCommand.objects.filter(status="pending", payload={"enrollid": 8}).count(), 1)

    def test_str_is_readable(self):
        command = self.command(self.dev3, "purge_user", status="failed")
        self.assertEqual(str(command), f"#{command.pk} Remove Inactive User From Device on Dev 3 (Failed)")
        self.assertEqual(str(DeviceCommand(device=self.dev3, command_type="list_user_slots")), "List Every Enrolled Slot On Terminal on Dev 3 (Pending)")

    # -- summaries: readable, short, and never the device's own data --

    def test_summaries_of_every_kind_of_job(self):
        names = {self.dev2.pk: "Dev 2", self.dev3.pk: "Dev 3"}

        def summary(command_type, payload):
            return command_summary(DeviceCommand(device=self.dev2, command_type=command_type, payload=payload), names)

        self.assertEqual(summary("clone_enrollment", {"renumber": True, "from_id": 1604, "to_id": 62, "slots": [0]}), "renumber 1604 -> 62 slots [0]")
        self.assertEqual(summary("clone_enrollment", {"pushes": [{"target_device_id": self.dev3.pk, "backupnums": [0, 50]}]}), "slot relay to Dev 3 [0,50]")
        self.assertEqual(summary("set_user_enabled", {"enrollid": 40, "enabled": False, "legacy": True}), "switch OFF 40 (legacy)")
        self.assertEqual(summary("set_user_enabled", {"enrollid": 40, "enabled": True}), "switch ON 40")
        self.assertEqual(summary("clone_enrollment", {"name_probe": True, "items": [{"enrollid": 1}, {"enrollid": 2}]}), "name probe of 2 ids")
        self.assertEqual(summary("clone_enrollment", {"target_device_ids": [self.dev3.pk], "enrollid": 5, "biometric_type": "face"}), "clone face of id 5 to Dev 3")
        self.assertEqual(summary("purge_user", {"enrollid": 9, "reason": "leftover entry"}), "purge id 9 - leftover entry")
        self.assertEqual(summary("list_user_slots", {}), "list every enrolled slot")
        self.assertEqual(summary("purge_user", {"enrollid": 9, "attempts": 2}), "purge id 9 (interrupted 2x)")
        self.assertEqual(summary("clone_enrollment", {"renumber": True, "from_id": 1, "to_id": 2, "slots": list(range(10))}), "renumber 1 -> 2 slots [0,1,2,3,4,5,+4]")

    def test_summaries_survive_junk_payloads(self):
        for payload in ({"pushes": "nonsense"}, {"renumber": True, "slots": "x"}, {"pushes": [1, None]}, ["a"], None):
            with self.subTest(payload=payload):
                self.assertIsInstance(command_summary(DeviceCommand(device=self.dev2, command_type="clone_enrollment", payload=payload)), str)

    def test_no_summary_or_page_ever_carries_a_record_or_a_template(self):
        listing = self.command(command_type="refresh_enrolled_ids", status="acked", result={"ret": "getuserid", "result": True, "record": [1, 2, 3], "sn": "SN-2"})
        one_shot = self.command(command_type="enroll_user", status="acked", payload={"enrollid": 7, "employee_id": self.ada.pk}, result={"result": True, "record": TEMPLATE_TEXT, "template": TEMPLATE_TEXT})
        relay = self.command(command_type="clone_enrollment", status="failed", payload={"employee_id": self.ada.pk, "pushes": []}, result={"failed": [{"reason": "rejected", "reply": {"msg": "face is double,id=1432", "record": TEMPLATE_TEXT}}], "record": TEMPLATE_TEXT})
        for command in (listing, one_shot, relay):
            self.assertNotIn("record", command_summary(command))
            self.assertNotIn("record", result_summary(command))
            self.assertNotIn(TEMPLATE_TEXT[:40], command_summary(command) + result_summary(command))
        self.assertEqual(result_summary(listing), "3 ids enrolled on the terminal")

        lists = self.changelist("devicecommand").content.decode()
        self.assertNotIn(TEMPLATE_TEXT[:40], lists)
        for command in (listing, one_shot, relay):
            detail = self.client.get(reverse("admin:attendance_devicecommand_change", args=[command.pk])).content.decode()
            self.assertNotIn(TEMPLATE_TEXT[:40], detail)
            self.assertNotIn("&quot;record&quot;", detail)
            self.assertNotIn('"record"', detail)

    def test_scrub_hides_biometric_keys_and_shortens_long_lists_and_texts(self):
        shown = scrub({"record": TEMPLATE_TEXT, "slots": [[n, 0] for n in range(100)], "note": "x" * 500, "ok": 1, "nested": {"template": "abc", "kept": 2}})
        self.assertNotIn("record", shown)
        self.assertNotIn("template", shown["nested"])
        self.assertEqual(shown["nested"]["kept"], 2)
        self.assertEqual(len(shown["slots"]), 26)
        self.assertIn("75 more", shown["slots"][-1])
        self.assertIn("500 characters", shown["note"])
        self.assertEqual(shown["ok"], 1)

    def test_result_summaries_read_as_sentences(self):
        def result(command_type, payload, outcome):
            return result_summary(DeviceCommand(device=self.dev2, command_type=command_type, payload=payload, result=outcome))

        self.assertEqual(result("list_user_slots", {}, {"slots": [[1, 0], [1, 50]], "pages": 1}), "2 slots on the terminal, read in 1 page(s)")
        self.assertEqual(result("clone_enrollment", {"renumber": True}, {"moved": [0], "old_entry_removed": True}), "moved [0]; old entry removed")
        self.assertEqual(result("clone_enrollment", {"pushes": [{}]}, {"pushed": 2, "failed": [{"reason": "rejected"}, {"reason": "rejected"}], "skipped_offline": ["Dev 3"]}), "pushed 2; failed rejected x2; 1 terminal(s) offline")
        self.assertEqual(result("purge_user", {}, {"detail": "Cancelled by root"}), "Cancelled by root")
        self.assertEqual(result("delete_user", {}, {"result": False, "reason": 3}), "terminal refused, reason 3")

    def test_format_duration(self):
        self.assertEqual(format_duration(timedelta(seconds=0.2)), "under 1 s")
        self.assertEqual(format_duration(timedelta(seconds=12)), "12 s")
        self.assertEqual(format_duration(timedelta(seconds=185)), "3 min 5 s")
        self.assertEqual(format_duration(timedelta(hours=2, minutes=5)), "2 h 5 min")
        self.assertEqual(format_duration(timedelta(days=3, hours=4)), "3 d 4 h")

    def test_duration_column(self):
        now = timezone.now()
        self.command(status="acked", sent_at=now - timedelta(seconds=30), completed_at=now - timedelta(seconds=20))
        self.command(status="pending")
        page = self.changelist("devicecommand").content.decode()
        self.assertIn("10 s", page)
        self.assertIn("waiting", page)


class ShiftStrTests(AdminDevicesBase):
    def test_str_of_the_shift_models_is_readable(self):
        day = Shift.objects.create(name="Day", start_time=time(7), end_time=time(19))
        night = Shift.objects.create(name="Night", start_time="19:00", end_time="07:00", is_overnight=True)  # raw strings, as tests elsewhere build them
        plan = ShiftPlan.objects.create(name="Rotation", kind="rotation", day_shift=day, night_shift=night)
        assignment = ShiftAssignment.objects.create(employee=self.ada, shift=day, start_date=date(2026, 9, 1))
        ended = ShiftAssignment.objects.create(employee=self.ada, shift=night, start_date=date(2026, 1, 1), end_date=date(2026, 2, 1))
        plan_assignment = ShiftPlanAssignment.objects.create(employee=self.bola, plan=plan, group="B", start_date=date(2026, 9, 21))
        self.assertEqual(str(day), "Day (07:00-19:00)")
        self.assertEqual(str(night), "Night (19:00-07:00, overnight)")
        self.assertEqual(str(plan), "Rotation (Weekly Day / Night rotation)")
        self.assertEqual(str(assignment), "1604 Ada Okafor: Day from 2026-09-01 onwards")
        self.assertEqual(str(ended), "1604 Ada Okafor: Night from 2026-01-01 to 2026-02-01")
        self.assertEqual(str(plan_assignment), "62 Bola Adeyemi: Rotation (group B) from 2026-09-21 onwards")

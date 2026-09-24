"""Run the AiFace BS WebSocket gateway that a Yunatt/TIMY terminal talks to directly.

The terminal is the WebSocket client: point its Comm/Server settings at this
process's host:port (default 7788, path /pub/chat) instead of Yunatt's cloud.
This command speaks that wire protocol and forwards translated punches to the
existing vendor-gateway HTTP bridge (`VendorGatewayPunchBridgeAPIView`), which
already does auth, validation, and idempotent ingestion — this command's only
job is protocol translation, deliberately kept outside Django's request/response
cycle since the device holds a long-lived push connection rather than making
discrete HTTP requests.

Requires the `websockets` package (see requirements.txt).
"""

import asyncio
import json
import urllib.error
import urllib.request
from datetime import datetime, timedelta

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import close_old_connections
from django.db.models import Case, When
from django.utils import timezone

from attendance.models import BiometricDevice, DeviceCommand
from attendance.integrations.aiface_protocol import (
    IDENTITY_SYSTEM,
    build_device_command,
    build_getuserinfo_command,
    build_getuserinfo_slot_command,
    build_getuserlist_command,
    build_reg_ack,
    build_sendlog_ack,
    choose_terminal_reply,
    build_senduser_ack,
    build_setuserinfo_command,
    build_setuserinfo_slot_command,
    translate_sendlog_record,
)

CLONE_REPLY_TIMEOUT_SECONDS = 20
CLONE_TARGET_WAIT_SECONDS = 6
CLONE_MAX_ATTEMPTS = 3
LIST_USER_SLOTS_MAX_PAGES = 600  # 40 records a page: room for 24,000 slots
LIST_USER_SLOTS_NEXT_PAGE_WAIT_SECONDS = 8

COMMAND_POLL_INTERVAL_SECONDS = 2
# The terminals were being cut off about every 40s: the websockets library pings each connection every 20s and
# hangs up when no pong comes back within 20s, and the terminals do not answer WebSocket pings. Server pings are
# off; instead a connection is dropped only when the terminal has sent nothing at all for DEVICE_SILENCE_LIMIT.
DEVICE_SILENCE_LIMIT_SECONDS = 180
LISTING_MIN_FRACTION_OF_PREVIOUS = 0.8
DEVICE_TOUCH_EVERY_SECONDS = 20
ATTENDANCE_REFRESH_SECONDS = 300  # turn punches into attendance records this often (today and yesterday)
ROSTER_EXTEND_CHECK_SECONDS = 3600  # once an hour we check whether today's roster extension has run
MEAL_IDLE_BEFORE_BACKGROUND = timedelta(minutes=10)  # no meal scan for this long = safe to run bulk jobs on a meal terminal
BACKGROUND_QUIET_HOURS_END = 5  # local hour before which bulk clone/purge jobs may use a meal terminal
AUTO_SYNC_SECONDS = 900  # every 30 min, queue clone relays for anyone still missing from a terminal
AUTO_SYNC_MAX_JOBS = 60  # per pass, so a big gap fills steadily instead of flooding the queues
GATING_FULL_CHECK_SECONDS = 300  # everyone is re-checked this often; a person's own scan re-checks them at once

try:
    import websockets
except ImportError:
    websockets = None


class Command(BaseCommand):
    help = "Run a WebSocket server speaking the AiFace BS protocol and relay punches into the vendor-gateway bridge."

    def add_arguments(self, parser):
        parser.add_argument("--host", default="0.0.0.0", help="Interface to bind (default: 0.0.0.0).")
        parser.add_argument("--port", type=int, default=7788, help="Port to bind (default: 7788, the AiFace default).")
        parser.add_argument(
            "--bridge-url",
            default="http://127.0.0.1:8000/api/attendance/integrations/vendor-gateway/punches/",
            help="URL of this Django app's attendance vendor-gateway punch bridge endpoint.",
        )
        parser.add_argument(
            "--meal-bridge-url",
            default="http://127.0.0.1:8000/api/meals/integrations/vendor-gateway/punches/",
            help="URL of this Django app's meal-ticket vendor-gateway punch bridge endpoint, "
            "used instead of --bridge-url for devices registered with purpose=meal_ticket.",
        )

    def handle(self, *args, **options):
        if websockets is None:
            raise CommandError("The 'websockets' package is required: pip install websockets")
        secret = settings.BIOMETRIC_BRIDGE_SECRET
        if not secret:
            raise CommandError("BIOMETRIC_BRIDGE_SECRET is not configured.")

        # Shared across every connection handler (all methods on this one Command
        # instance): _connections lets a clone_enrollment relay reach a *different*
        # device's live socket to push setuserinfo; _pending_replies lets
        # _send_and_wait correlate a specific getuserinfo/setuserinfo ret back to
        # the coroutine waiting on it, bypassing the generic per-DB-row resolution
        # in _resolve_command (which clone_enrollment deliberately doesn't use,
        # since the captured record must never be written to the database).
        self._connections = {}
        self._pending_replies = {}
        # One lock per serial so a device that is the *source* of its own relay
        # and simultaneously the *target* of another device's relay serializes
        # the two exchanges instead of overwriting each other's pending reply.
        self._locks = {}

        host, port, bridge_url, meal_bridge_url = options["host"], options["port"], options["bridge_url"], options["meal_bridge_url"]
        self.stdout.write(self.style.SUCCESS(
            f"AiFace gateway listening on ws://{host}:{port}/pub/chat, relaying attendance to {bridge_url} "
            f"and meal-ticket devices to {meal_bridge_url}"
        ))
        try:
            asyncio.run(self._serve(host, port, bridge_url, meal_bridge_url, secret))
        except KeyboardInterrupt:
            self.stdout.write("\nStopped.")

    _last_gating_check = float("-inf")
    _last_roster_check = float("-inf")
    _last_attendance_refresh = float("-inf")
    _last_auto_sync = float("-inf")
    _roster_extended_on = None

    async def _serve(self, host, port, bridge_url, meal_bridge_url, secret):
        async def handler(websocket):
            await self._handle_connection(websocket, bridge_url, meal_bridge_url, secret)

        async with websockets.serve(handler, host, port, ping_interval=None):
            await asyncio.Future()

    @staticmethod
    async def _run_db(fn, *args):
        """Every database-touching call in this file goes through here instead of a bare
        asyncio.to_thread. This process holds its connections open for hours or days outside
        Django's normal request/response cycle, which is the only place Django itself checks
        a connection's health - so once Postgres or the network drops a worker thread's
        connection (an idle timeout is enough), every future query on that thread fails with
        "connection already closed" forever, silently freezing that one device's status (and
        anything else that happens to land on the same thread) until the whole gateway is
        restarted. close_old_connections() is Django's own request_started/request_finished
        hook, invoked by hand since this process never fires those signals."""
        def call():
            close_old_connections()
            return fn(*args)
        return await asyncio.to_thread(call)

    async def _handle_connection(self, ws, bridge_url, meal_bridge_url, secret):
        sn = None
        peer = ws.remote_address
        poller_task = watchdog_task = None
        loop = asyncio.get_running_loop()
        activity = {"seen": loop.time(), "touched": loop.time()}
        try:
            async for raw_message in ws:
                activity["seen"] = loop.time()
                if sn and activity["seen"] - activity["touched"] >= DEVICE_TOUCH_EVERY_SECONDS:
                    # A connection that now lasts for hours has no fresh `reg` to show the terminal is alive.
                    activity["touched"] = activity["seen"]
                    await self._run_db(self._mark_device_online, sn, peer[0] if peer else None)
                try:
                    message = json.loads(raw_message)
                except (TypeError, ValueError):
                    self.stdout.write(f"[{peer}] ignoring non-JSON frame")
                    continue

                cmd = message.get("cmd")
                ret = message.get("ret")
                if cmd == "reg":
                    sn = message.get("sn")
                    self.stdout.write(f"[{peer}] reg from device sn={sn}")
                    self._connections[sn] = ws
                    await self._run_db(self._mark_device_online, sn, peer[0] if peer else None)
                    await self._run_db(self._recover_interrupted_clones, sn)
                    minimal = await self._run_db(self._minimal_reg_for_device, sn)
                    self.stdout.write(f"[{sn}] reg ack: {'minimal (vendor demo style)' if minimal else 'standard'}")
                    await ws.send(json.dumps(build_reg_ack(datetime.now(), minimal=minimal)))
                    activity["touched"] = activity["seen"]
                    if poller_task is None:
                        poller_task = asyncio.create_task(self._poll_commands(ws, sn))
                        watchdog_task = asyncio.create_task(self._drop_if_silent(ws, activity))
                elif cmd == "sendlog":
                    await self._handle_sendlog(ws, message, bridge_url, meal_bridge_url, secret)
                elif cmd == "senduser":
                    await ws.send(json.dumps(build_senduser_ack(datetime.now())))
                elif ret:
                    pending = self._pending_replies.get(sn)
                    if pending is not None and not pending.done():
                        # Claimed by an in-progress clone_enrollment relay (see
                        # _send_and_wait) - never logged, since a getuserinfo
                        # reply carries the raw biometric record.
                        pending.set_result(message)
                    else:
                        self.stdout.write(f"[{sn}] command response ret={ret}: {message}")
                        await self._run_db(self._resolve_command, sn, message)
                elif cmd:
                    self.stdout.write(f"[{peer}] unhandled cmd={cmd!r}")
        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            if poller_task is not None:
                poller_task.cancel()
            if watchdog_task is not None:
                watchdog_task.cancel()
            self.stdout.write(f"[{peer}] disconnected (sn={sn}) close_code={getattr(ws, 'close_code', None)} reason={getattr(ws, 'close_reason', None)!r}")
            if sn:
                # A switch on/off that was on the wire when the terminal dropped never got an answer: retry it
                # straight away on the next connection instead of waiting for it to time out.
                await self._run_db(self._requeue_lost_switches, sn)
            # A device that reconnected already has a newer socket registered under
            # this serial; only the current one may deregister/mark offline, or a
            # late-noticed dead connection would evict its replacement.
            if sn and self._connections.get(sn) is ws:
                self._connections.pop(sn, None)
                await self._run_db(self._mark_device_offline, sn)

    @staticmethod
    async def _drop_if_silent(ws, activity):
        """Hang up on a terminal that has sent nothing for DEVICE_SILENCE_LIMIT_SECONDS (a half-dead connection);
        it reconnects on its own. With server pings off this is the only dead-connection check."""
        loop = asyncio.get_running_loop()
        try:
            while True:
                await asyncio.sleep(15)
                if loop.time() - activity["seen"] > DEVICE_SILENCE_LIMIT_SECONDS:
                    await ws.close()
                    return
        except asyncio.CancelledError:
            pass

    async def _poll_commands(self, ws, sn):
        """Deliver queued admin commands (enroll/delete/refresh) one at a time.

        The AiFace protocol requires the server send a device's commands
        one-by-one, waiting for each response before the next (see the
        vendor's own Notes.txt) - enforced here by only picking a new
        "pending" command once no "sent" (in-flight) one remains for this
        device.
        """
        try:
            while True:
                await asyncio.sleep(COMMAND_POLL_INTERVAL_SECONDS)
                now = asyncio.get_running_loop().time()
                if now - self._last_gating_check >= GATING_FULL_CHECK_SECONDS:
                    # Safety net for everyone covered by MEAL_GATING_EMPLOYEE_IDS (day rollover, roster changes, a
                    # gateway that was down). Claim the slot first so the other terminals' loops skip it.
                    self._last_gating_check = now
                    await self._run_db(self._reconcile_meal_gating)
                if now - self._last_attendance_refresh >= ATTENDANCE_REFRESH_SECONDS:
                    self._last_attendance_refresh = now
                    await self._run_db(self._refresh_attendance)
                if now - self._last_roster_check >= ROSTER_EXTEND_CHECK_SECONDS:
                    self._last_roster_check = now
                    await self._run_db(self._extend_rosters_daily)
                if now - self._last_auto_sync >= AUTO_SYNC_SECONDS:
                    self._last_auto_sync = now
                    await self._run_db(self._auto_sync_enrollments)
                next_command = await self._run_db(self._next_command_to_send, sn)
                if next_command is None:
                    continue
                command_id, wire_message = next_command
                if wire_message is None:
                    kind = await self._run_db(self._command_kind, command_id)
                    if kind == "list_user_slots":
                        await self._run_list_user_slots(ws, sn, command_id)
                    elif kind == "slot_clone":
                        await self._run_slot_clone(ws, sn, command_id)
                    else:
                        await self._run_clone_enrollment(ws, sn, command_id)
                    continue
                self.stdout.write(f"[{sn}] sending queued command: {wire_message}")
                await ws.send(json.dumps(wire_message))
                await self._run_db(self._mark_command_sent, command_id)
        except asyncio.CancelledError:
            pass

    @staticmethod
    def _previous_listing_size(serial_number):
        last = DeviceCommand.objects.filter(device__serial_number=serial_number, command_type="list_user_slots", status="acked").order_by("-id").first()
        return len(last.result.get("slots", [])) if last else 0

    @staticmethod
    def _requeue_lost_switches(serial_number):
        DeviceCommand.objects.filter(device__serial_number=serial_number, command_type="set_user_enabled", status="sent").update(status="pending", sent_at=None)

    def _refresh_attendance(self):
        """Keep attendance records live: punches become records within minutes, not the next morning. While a shift
        is still running nobody is called absent; a finished day is judged in full (see process_employee_attendance)."""
        try:
            from datetime import timedelta

            from attendance.services.processing import process_attendance_for_date

            today = timezone.localdate()
            total = sum(len(process_attendance_for_date(day)) for day in (today - timedelta(days=1), today))
            self.stdout.write(f"attendance refresh: {total} record(s) up to date")
        except Exception as error:  # never let this take the gateway down
            self.stderr.write(f"attendance refresh failed: {error}")

    def _extend_rosters_daily(self):
        """Once a day, write everyone's planned roster for the coming weeks so the weekly Day/Night swap needs no one."""
        try:
            today = timezone.localdate()
            if self._roster_extended_on == today:
                return
            from attendance.services.shift_plans import extend_rosters

            summary = extend_rosters(today)
            self._roster_extended_on = today
            self.stdout.write(f"roster extension: created {summary.created}, changed {summary.updated}")
        except Exception as error:  # never let this take the gateway down
            self.stderr.write(f"roster extension failed: {error}")

    def _auto_sync_enrollments(self):
        """Keep every terminal - attendance and meal-ticket - identical without anyone pressing a button. Each pass: ask terminals
        for what they really hold (when the last answer is old), link ids that match a staff number (people
        enrolled at a terminal's own keypad are otherwise invisible - their scans are rejected as "unmapped"),
        then copy whatever credentials (face, fingerprint, card, password) any terminal lacks. Relays fail
        routinely (terminals drop their connection every ~30s), so the next pass simply retries what is left."""
        try:
            from attendance.services.device_sync import link_ids_by_staff_number, plan_slot_clones, queue_listings, reachable_devices

            devices = reachable_devices()
            listed = queue_listings(devices)
            linked = link_ids_by_staff_number(devices)
            planned = plan_slot_clones(devices, limit=AUTO_SYNC_MAX_JOBS)
            if listed or linked or planned:
                self.stdout.write(f"auto sync: listings requested {listed}, ids linked {linked}, slot relays queued {len(planned)}")
        except Exception as error:  # never let this take the gateway down
            self.stderr.write(f"auto sync failed: {error}")

    def _reconcile_meal_gating(self):
        try:
            from meals.gating import reconcile

            queued = reconcile()
            if queued:
                self.stdout.write(f"meal gating: queued {queued} terminal switch(es)")
        except Exception as error:  # never let this take the gateway down
            self.stderr.write(f"meal gating failed: {error}")

    def _lock_for(self, sn):
        return self._locks.setdefault(sn, asyncio.Lock())

    async def _send_and_wait(self, ws, sn, wire_message, timeout):
        """Send one wire message on this device's own connection and return its
        matching `ret` reply, bypassing the generic per-DB-row command resolution -
        see the `elif ret:` branch in _handle_connection. The per-serial lock keeps
        a device that is both the source of one relay and the target of another
        from having two replies raced through the single pending-reply slot."""
        async with self._lock_for(sn):
            future = asyncio.get_running_loop().create_future()
            self._pending_replies[sn] = future
            try:
                await ws.send(json.dumps(wire_message))
                return await asyncio.wait_for(future, timeout=timeout)
            finally:
                self._pending_replies.pop(sn, None)

    async def _wait_for_connection(self, serial_number, seconds):
        """Devices cycle their connection every ~20-30s, so a device that looks
        offline this instant is usually back within a second or two."""
        for _ in range(int(seconds * 2)):
            if serial_number in self._connections:
                return self._connections[serial_number]
            await asyncio.sleep(0.5)
        return self._connections.get(serial_number)

    @staticmethod
    def _command_kind(command_id):
        command = DeviceCommand.objects.filter(pk=command_id).only("command_type", "payload").first()
        if command is None:
            return None
        if command.command_type == "clone_enrollment" and command.payload.get("pushes"):
            return "slot_clone"
        return command.command_type

    async def _run_slot_clone(self, ws, sn, command_id):
        """Copy exactly the credentials (backupnums) a person has on this terminal and lacks on others: read each
        slot from this terminal once, push it to every terminal that needs it. Nothing read is stored or logged.
        The plan came from what the terminals really hold (see attendance/services/device_sync.plan_slot_clones),
        so it is safe to repeat - a relay cut off by a dropped connection just runs again and pushes what is
        still missing."""
        await self._run_db(self._mark_command_sent, command_id)
        command = await self._run_db(lambda: DeviceCommand.objects.get(pk=command_id))
        payload = command.payload
        enrollid, employee_id, name = payload["enrollid"], payload["employee_id"], payload.get("name", "")
        pushes = payload["pushes"]
        targets = {t.pk: t for t in await self._run_db(lambda: list(BiometricDevice.objects.filter(pk__in=[p["target_device_id"] for p in pushes])))}

        pushed, failed, skipped_offline, linked = 0, [], [], set()
        needs = {}
        for push in pushes:
            target = targets.get(push["target_device_id"])
            if target is None:
                continue
            if await self._wait_for_connection(target.serial_number, CLONE_TARGET_WAIT_SECONDS) is None:
                skipped_offline.append(target.name)
                continue
            for backupnum in push["backupnums"]:
                needs.setdefault(backupnum, []).append(target)

        for backupnum, wanting in needs.items():
            try:
                reply = await self._send_and_wait(ws, sn, build_getuserinfo_slot_command(sn, enrollid, backupnum), CLONE_REPLY_TIMEOUT_SECONDS)
            except asyncio.TimeoutError:
                failed.append({"backupnum": backupnum, "reason": "source did not answer"})
                continue
            if not reply.get("result"):
                failed.append({"backupnum": backupnum, "reason": "source could not return it"})
                continue
            record = reply.get("record")
            for target in wanting:
                target_ws = self._connections.get(target.serial_number)
                if target_ws is None:
                    skipped_offline.append(target.name)
                    continue
                target_enrollid = await self._run_db(self._enrollid_for_target, target, enrollid)
                try:
                    push_reply = await self._send_and_wait(target_ws, target.serial_number, build_setuserinfo_slot_command(target.serial_number, target_enrollid, name, backupnum, record), CLONE_REPLY_TIMEOUT_SECONDS)
                except asyncio.TimeoutError:
                    failed.append({"backupnum": backupnum, "target": target.name, "reason": "timed out"})
                    continue
                if not push_reply.get("result"):
                    failed.append({"backupnum": backupnum, "target": target.name, "reason": "rejected"})
                    continue
                pushed += 1
                if target.pk not in linked:
                    await self._run_db(self._link_cloned_identity, employee_id, target, target_enrollid)
                    linked.add(target.pk)
            record = None  # drop the only reference to the credential as soon as it has been relayed
        self.stdout.write(f"[{sn}] slot_clone: pushed={pushed} failed={len(failed)} offline={len(skipped_offline)}")
        await self._run_db(self._finish_clone_command, command_id, "acked" if pushed else "failed", {"pushed": pushed, "failed": failed, "skipped_offline": skipped_offline})

    async def _run_list_user_slots(self, ws, sn, command_id):
        """Read every enrolled (enrollid, backupnum) slot off one terminal, a page at a time, and store the list
        on the command. Read-only: it changes nothing on the terminal. The end of the list is a page with no
        records; going quiet is accepted as the end only if the list is not much smaller than the previous one."""
        await self._run_db(self._mark_command_sent, command_id)
        slots, pages, first, ended = [], 0, True, False
        try:
            while pages < LIST_USER_SLOTS_MAX_PAGES:
                timeout = CLONE_REPLY_TIMEOUT_SECONDS if first else LIST_USER_SLOTS_NEXT_PAGE_WAIT_SECONDS
                try:
                    reply = await self._send_and_wait(ws, sn, build_getuserlist_command(sn, first), timeout)
                except asyncio.TimeoutError:
                    if first:
                        raise
                    break
                first = False
                if not reply.get("result"):
                    ended = True
                    break
                records = reply.get("record") or []
                if not records:
                    ended = True
                    break
                slots.extend([record.get("enrollid"), record.get("backupnum")] for record in records)
                pages += 1
        except asyncio.TimeoutError:
            await self._run_db(self._finish_clone_command, command_id, "failed", {"detail": "The terminal did not answer the user-list request."})
            return
        if not ended and pages < LIST_USER_SLOTS_MAX_PAGES:
            # Silence after a page can be the end of the list (the vendor's own server relies on that) or a dropped
            # connection. Page sizes vary, so a short page proves nothing. Told apart by size: a list far smaller
            # than the last one we read is a cut-off list. Storing one made a terminal holding 533 people look like
            # it held 48 and another look like it held 103, which planned hundreds of pointless relays.
            previous = await self._run_db(self._previous_listing_size, sn)
            if previous and len(slots) < previous * LISTING_MIN_FRACTION_OF_PREVIOUS:
                await self._run_db(self._finish_clone_command, command_id, "failed", {"detail": f"The terminal stopped answering after {pages} page(s) with {len(slots)} slots, against {previous} last time; the list is incomplete and was discarded."})
                return
        self.stdout.write(f"[{sn}] list_user_slots: {len(slots)} slot(s) in {pages} page(s)")
        await self._run_db(self._finish_clone_command, command_id, "acked", {"slots": slots, "pages": pages})

    async def _run_clone_enrollment(self, ws, sn, command_id):
        """Relay an enrollment to every other device that doesn't have it yet,
        using getuserinfo (pull the captured template from the source) and
        setuserinfo (push it to a target) - so the person only has to physically
        scan once. The captured record is held only in a local variable for the
        life of this one relay and is never written to the database or logged;
        only device-level outcomes are.

        If the source's connection drops mid-relay this coroutine is cancelled and
        the row is left "sent"; _recover_interrupted_clones puts it back to
        "pending" when that device next registers.
        """
        await self._run_db(self._mark_command_sent, command_id)
        command = await self._run_db(lambda: DeviceCommand.objects.select_related("device").get(pk=command_id))
        payload = command.payload
        enrollid, employee_id, employee_name = payload["enrollid"], payload["employee_id"], payload.get("name", "")
        biometric_type = payload.get("biometric_type", "face")
        target_ids = payload.get("target_device_ids", [])
        targets = await self._run_db(lambda: list(BiometricDevice.objects.filter(pk__in=target_ids)))

        # Don't bother the source for a template nobody can receive right now.
        reachable, skipped_offline = [], []
        for target in targets:
            if await self._wait_for_connection(target.serial_number, CLONE_TARGET_WAIT_SECONDS) is None:
                skipped_offline.append({"device_id": target.id, "device_name": target.name})
            else:
                reachable.append(target)
        if not reachable:
            await self._run_db(self._finish_clone_command, command_id, "failed", {"detail": "None of the target devices were online.", "skipped_offline": skipped_offline})
            return

        try:
            reply = await self._send_and_wait(ws, sn, build_getuserinfo_command(sn, enrollid, biometric_type), CLONE_REPLY_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            self.stdout.write(f"[{sn}] clone_enrollment: timed out waiting for the source device to return the template")
            await self._run_db(self._finish_clone_command, command_id, "failed", {"detail": "Timed out waiting for the source device to return the enrolled template."})
            return
        if not reply.get("result"):
            await self._run_db(self._finish_clone_command, command_id, "failed", {"detail": "Source device could not return the enrolled template."})
            return
        record = reply.get("record")
        self.stdout.write(f"[{sn}] clone_enrollment: template captured for enrollid={enrollid}, relaying to {len(reachable)} device(s)")

        cloned_to, skipped_busy, failed = [], [], []
        for target in reachable:
            target_ws = self._connections.get(target.serial_number)
            if target_ws is None:
                skipped_offline.append({"device_id": target.id, "device_name": target.name})
                continue
            if await self._run_db(self._device_is_busy, target):
                skipped_busy.append({"device_id": target.id, "device_name": target.name})
                continue
            target_enrollid = await self._run_db(self._enrollid_for_target, target, enrollid)
            push_message = build_setuserinfo_command(target.serial_number, target_enrollid, employee_name, biometric_type, record)
            try:
                push_reply = await self._send_and_wait(target_ws, target.serial_number, push_message, CLONE_REPLY_TIMEOUT_SECONDS)
            except asyncio.TimeoutError:
                self.stdout.write(f"[{target.serial_number}] clone_enrollment: timed out waiting for setuserinfo ack")
                failed.append({"device_id": target.id, "device_name": target.name, "reason": "timed out"})
                continue
            if not push_reply.get("result"):
                failed.append({"device_id": target.id, "device_name": target.name, "reason": "device rejected the template"})
                continue
            await self._run_db(self._link_cloned_identity, employee_id, target, target_enrollid)
            cloned_to.append({"device_id": target.id, "device_name": target.name})

        record = None  # drop the only reference to the template as soon as we're done relaying it
        self.stdout.write(f"[{sn}] clone_enrollment: done - cloned={len(cloned_to)} offline={len(skipped_offline)} busy={len(skipped_busy)} failed={len(failed)}")
        await self._run_db(
            self._finish_clone_command, command_id, "acked" if cloned_to else "failed",
            {"cloned_to": cloned_to, "skipped_offline": skipped_offline, "skipped_busy": skipped_busy, "failed": failed},
        )

    async def _handle_sendlog(self, ws, message, bridge_url, meal_bridge_url, secret):
        sn = message.get("sn")
        records = message.get("record") or []
        logindex = message.get("logindex")
        count = message.get("count", len(records))

        target_url = await self._run_db(self._bridge_url_for_device, sn, bridge_url, meal_bridge_url)
        gateway_records = [translate_sendlog_record(sn, record) for record in records]
        result = True
        access, terminal_message = None, None
        if gateway_records:
            enroll_ids = [r["enroll_id"] for r in gateway_records]
            try:
                response = await asyncio.to_thread(self._post_to_bridge, target_url, secret, gateway_records)
                self.stdout.write(f"[{sn}] sendlog enroll_ids={enroll_ids} -> {target_url}: {response}")
                if target_url == meal_bridge_url:
                    access, terminal_message = choose_terminal_reply(getattr(settings, "MEAL_TERMINAL_REPLY_MODE", "minimal"), response.get("results", []))
            except (urllib.error.URLError, ValueError) as error:
                self.stderr.write(f"[{sn}] bridge post failed, asking device to retry: {error}")
                result = False

        await ws.send(json.dumps(build_sendlog_ack(datetime.now(), result=result, count=count, logindex=logindex, access=access, message=terminal_message)))

    @staticmethod
    def _minimal_reg_for_device(serial_number):
        """Meal-ticket terminals get the same bare handshake reply the vendor's own demo server sends."""
        if getattr(settings, "MEAL_TERMINAL_REPLY_MODE", "minimal") == "extended":
            return False
        return BiometricDevice.objects.filter(serial_number=serial_number, purpose="meal_ticket").exists()

    @staticmethod
    def _mark_device_online(serial_number, ip_address):
        """No-op for a serial number with no matching BiometricDevice row (unregistered devices are surfaced via the sendlog bridge response instead)."""
        BiometricDevice.objects.filter(serial_number=serial_number).update(
            is_online=True, last_sync_at=timezone.now(), ip_address=ip_address,
        )

    @staticmethod
    def _mark_device_offline(serial_number):
        BiometricDevice.objects.filter(serial_number=serial_number).update(is_online=False)

    @staticmethod
    def _bridge_url_for_device(serial_number, bridge_url, meal_bridge_url):
        """Route by the registered device's purpose. An unregistered serial number
        (never added on the Devices page) falls through to the attendance bridge,
        which already reports "unknown device" back through its own response -
        same behavior as before this device ever gets registered anywhere."""
        purpose = BiometricDevice.objects.filter(serial_number=serial_number).values_list("purpose", flat=True).first()
        return meal_bridge_url if purpose == "meal_ticket" else bridge_url

    @staticmethod
    def _background_job_held(command, device_purpose):
        """Bulk background jobs (clone/purge) do not run on, or aimed at, a meal-ticket terminal while meals
        are being served. The terminal takes one command at a time, so a relay in flight makes every
        "switch this person off" wait behind it - at the lunch rush that is how a third ticket got
        through (2026-09-23). They run when no meal has been scanned for MEAL_IDLE_BEFORE_BACKGROUND
        (or overnight), so the sync still happens, just between rushes."""
        if command.command_type not in DeviceCommand.BACKGROUND_TYPES:
            return False
        if timezone.localtime().hour < BACKGROUND_QUIET_HOURS_END:
            return False
        target_ids = command.payload.get("target_device_ids") or []
        involves_meal = device_purpose == "meal_ticket" or (
            bool(target_ids) and BiometricDevice.objects.filter(pk__in=target_ids, purpose="meal_ticket").exists()
        )
        if not involves_meal:
            return False
        from meals.models import MealEvent

        latest = MealEvent.objects.values_list("timestamp", flat=True).first()
        return latest is not None and timezone.now() - latest < MEAL_IDLE_BEFORE_BACKGROUND

    @staticmethod
    def _next_command_to_send(serial_number):
        """The next (id, wire message) to send for this device, or None if nothing's due.

        Returns None while a previously-sent command is still awaiting a
        response, so only one command is ever in flight per device. Switching
        someone on/off at a terminal goes first, ahead of enroll/delete and of
        bulk background jobs.
        """
        DeviceCommand.expire_stale()
        if DeviceCommand.objects.filter(device__serial_number=serial_number, status="sent").exists():
            return None
        purpose = BiometricDevice.objects.filter(serial_number=serial_number).values_list("purpose", flat=True).first()
        candidates = (
            DeviceCommand.objects.filter(device__serial_number=serial_number, status="pending")
            .order_by(
                Case(
                    When(command_type="set_user_enabled", then=0),
                    # A read-only listing is quick and must not wait behind a backlog of clone relays.
                    When(command_type="list_user_slots", then=1),
                    When(command_type__in=DeviceCommand.BACKGROUND_TYPES, then=2),
                    default=1,
                ),
                "created_at",
            )[:100]
        )
        command = next((c for c in candidates if not Command._background_job_held(c, purpose)), None)
        if command is None:
            return None
        if command.command_type in ("clone_enrollment", "list_user_slots"):
            # Needs custom multi-message async handling (_run_clone_enrollment, _run_list_user_slots),
            # not a single wire message - signal that to the caller with None.
            return command.id, None
        return command.id, build_device_command(serial_number, command.command_type, command.payload)

    @staticmethod
    def _device_is_busy(device):
        """True only while a regular one-shot command (enroll/delete/refresh) is
        awaiting its reply on this device. A merely "pending" backlog doesn't
        conflict with a push, and another clone_enrollment's exchange is already
        serialized per device by _send_and_wait's lock - counting either as busy
        made every device skip every other during a bulk sync."""
        DeviceCommand.expire_stale(device=device)
        return DeviceCommand.objects.filter(device=device, status="sent").exclude(command_type="clone_enrollment").exists()

    @staticmethod
    def _enrollid_for_target(device, preferred):
        """Same id as on the source when free on the target - see BiometricDevice.free_enrollid."""
        return device.free_enrollid(preferred=preferred)

    @staticmethod
    def _recover_interrupted_clones(serial_number):
        """A device that re-registers while one of its clone_enrollment rows is
        still "sent" lost its connection mid-relay (they cycle every ~20-30s).
        Put the row back to "pending" so it retries, up to CLONE_MAX_ATTEMPTS
        before giving up."""
        for command in DeviceCommand.objects.filter(device__serial_number=serial_number, command_type__in=("clone_enrollment", "list_user_slots"), status="sent"):
            attempts = command.payload.get("attempts", 0) + 1
            if attempts >= CLONE_MAX_ATTEMPTS:
                DeviceCommand.objects.filter(pk=command.pk).update(
                    status="failed", completed_at=timezone.now(),
                    result={"detail": f"Connection dropped mid-relay {attempts} times; gave up."},
                )
            else:
                DeviceCommand.objects.filter(pk=command.pk).update(status="pending", sent_at=None, payload={**command.payload, "attempts": attempts})

    @staticmethod
    def _next_free_enrollid(device):
        return device.free_enrollid()

    @staticmethod
    def _mark_command_sent(command_id):
        DeviceCommand.objects.filter(pk=command_id).update(status="sent", sent_at=timezone.now())

    @staticmethod
    def _finish_clone_command(command_id, status, result):
        DeviceCommand.objects.filter(pk=command_id).update(status=status, result=result, completed_at=timezone.now())

    @staticmethod
    def _link_cloned_identity(employee_id, device, enrollid):
        from employees.models import BiometricIdentity

        BiometricIdentity.objects.update_or_create(
            employee_id=employee_id, system=IDENTITY_SYSTEM, source_identifier=device.serial_number,
            defaults={"external_user_id": str(enrollid), "is_active": True},
        )

    @staticmethod
    def _resolve_command(serial_number, message):
        """Match an incoming `ret` response to this device's in-flight command and close it out.

        Only one command is ever in flight per device (see `_next_command_to_send`),
        so whichever "sent" row belongs to this device is the one this response is for.
        A `ret` with no in-flight command to match (e.g. a stale/duplicate reply) is
        logged by the caller and otherwise ignored here.
        """
        command = (
            DeviceCommand.objects.filter(device__serial_number=serial_number, status="sent")
            .order_by("sent_at")
            .first()
        )
        if command is None:
            return
        status = "acked" if message.get("result") else "failed"
        DeviceCommand.objects.filter(pk=command.id).update(status=status, result=message, completed_at=timezone.now())
        if status != "acked":
            return
        if command.command_type == "enroll_user":
            Command._link_biometric_identity(command)
        elif command.command_type in ("delete_user", "purge_user"):
            Command._unlink_biometric_identity(command)
        elif command.command_type == "set_user_enabled":
            from meals.gating import record_state

            record_state(command)

    @staticmethod
    def _link_biometric_identity(command):
        """On a successful on-device enrollment, map the employee to that enrollid so future punches resolve automatically."""
        from employees.models import BiometricIdentity

        employee_id = command.payload.get("employee_id")
        enrollid = command.payload.get("enrollid")
        if not employee_id or enrollid is None:
            return
        BiometricIdentity.objects.update_or_create(
            employee_id=employee_id,
            system=IDENTITY_SYSTEM,
            source_identifier=command.device.serial_number,
            defaults={"external_user_id": str(enrollid), "is_active": True},
        )
        Command._queue_clone_to_other_devices(command, employee_id)

    @staticmethod
    def _queue_clone_to_other_devices(command, employee_id):
        """Once a physical scan succeeds on one device, relay that enrollment
        (via getuserinfo/setuserinfo - see _run_clone_enrollment) to every other
        device that doesn't already have this employee, instead of requiring a
        separate physical scan at each one. A no-op if every other device
        already has them."""
        from employees.models import BiometricIdentity

        already_enrolled_serials = BiometricIdentity.objects.filter(
            employee_id=employee_id, system=IDENTITY_SYSTEM, is_active=True,
        ).values_list("source_identifier", flat=True)
        target_ids = list(
            BiometricDevice.objects.exclude(pk=command.device_id)
            .exclude(serial_number__in=already_enrolled_serials)
            .values_list("id", flat=True)
        )
        if not target_ids:
            return
        DeviceCommand.objects.create(
            device=command.device, command_type="clone_enrollment",
            payload={
                "employee_id": employee_id,
                "enrollid": command.payload.get("enrollid"),
                "name": command.payload.get("name", ""),
                "biometric_type": command.payload.get("biometric_type", "face"),
                "target_device_ids": target_ids,
            },
            requested_by=command.requested_by,
        )

    @staticmethod
    def _unlink_biometric_identity(command):
        """On a successful on-device deletion, remove the mapping so this enrollid is free to be reassigned on this device.

        Deletes rather than deactivates: (system, source_identifier,
        external_user_id) is unique, so a soft-deactivated row would block
        that same enrollid ever being linked to a new employee on this device.
        """
        from employees.models import BiometricIdentity

        enrollid = command.payload.get("enrollid")
        if enrollid is None:
            return
        BiometricIdentity.objects.filter(
            system=IDENTITY_SYSTEM,
            source_identifier=command.device.serial_number,
            external_user_id=str(enrollid),
        ).delete()

    @staticmethod
    def _post_to_bridge(bridge_url, secret, records):
        payload = json.dumps({"records": records}).encode()
        request = urllib.request.Request(
            bridge_url,
            data=payload,
            headers={"Content-Type": "application/json", "X-Biometric-Bridge-Key": secret},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.loads(response.read())

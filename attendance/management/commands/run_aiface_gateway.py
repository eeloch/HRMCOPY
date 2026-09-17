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
from datetime import datetime

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from attendance.models import BiometricDevice, DeviceCommand
from attendance.integrations.aiface_protocol import (
    IDENTITY_SYSTEM,
    build_device_command,
    build_reg_ack,
    build_sendlog_ack,
    build_senduser_ack,
    translate_sendlog_record,
)

COMMAND_POLL_INTERVAL_SECONDS = 2

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

        host, port, bridge_url, meal_bridge_url = options["host"], options["port"], options["bridge_url"], options["meal_bridge_url"]
        self.stdout.write(self.style.SUCCESS(
            f"AiFace gateway listening on ws://{host}:{port}/pub/chat, relaying attendance to {bridge_url} "
            f"and meal-ticket devices to {meal_bridge_url}"
        ))
        try:
            asyncio.run(self._serve(host, port, bridge_url, meal_bridge_url, secret))
        except KeyboardInterrupt:
            self.stdout.write("\nStopped.")

    async def _serve(self, host, port, bridge_url, meal_bridge_url, secret):
        async def handler(websocket):
            await self._handle_connection(websocket, bridge_url, meal_bridge_url, secret)

        async with websockets.serve(handler, host, port):
            await asyncio.Future()

    async def _handle_connection(self, ws, bridge_url, meal_bridge_url, secret):
        sn = None
        peer = ws.remote_address
        poller_task = None
        try:
            async for raw_message in ws:
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
                    await asyncio.to_thread(self._mark_device_online, sn, peer[0] if peer else None)
                    await ws.send(json.dumps(build_reg_ack(datetime.now())))
                    if poller_task is None:
                        poller_task = asyncio.create_task(self._poll_commands(ws, sn))
                elif cmd == "sendlog":
                    await self._handle_sendlog(ws, message, bridge_url, meal_bridge_url, secret)
                elif cmd == "senduser":
                    await ws.send(json.dumps(build_senduser_ack(datetime.now())))
                elif ret:
                    self.stdout.write(f"[{sn}] command response ret={ret}: {message}")
                    await asyncio.to_thread(self._resolve_command, sn, message)
                elif cmd:
                    self.stdout.write(f"[{peer}] unhandled cmd={cmd!r}")
        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            if poller_task is not None:
                poller_task.cancel()
            self.stdout.write(f"[{peer}] disconnected (sn={sn})")
            if sn:
                await asyncio.to_thread(self._mark_device_offline, sn)

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
                next_command = await asyncio.to_thread(self._next_command_to_send, sn)
                if next_command is None:
                    continue
                command_id, wire_message = next_command
                self.stdout.write(f"[{sn}] sending queued command: {wire_message}")
                await ws.send(json.dumps(wire_message))
                await asyncio.to_thread(self._mark_command_sent, command_id)
        except asyncio.CancelledError:
            pass

    async def _handle_sendlog(self, ws, message, bridge_url, meal_bridge_url, secret):
        sn = message.get("sn")
        records = message.get("record") or []
        logindex = message.get("logindex")
        count = message.get("count", len(records))

        target_url = await asyncio.to_thread(self._bridge_url_for_device, sn, bridge_url, meal_bridge_url)
        gateway_records = [translate_sendlog_record(sn, record) for record in records]
        result = True
        if gateway_records:
            enroll_ids = [r["enroll_id"] for r in gateway_records]
            try:
                response = await asyncio.to_thread(self._post_to_bridge, target_url, secret, gateway_records)
                self.stdout.write(f"[{sn}] sendlog enroll_ids={enroll_ids} -> {target_url}: {response}")
            except (urllib.error.URLError, ValueError) as error:
                self.stderr.write(f"[{sn}] bridge post failed, asking device to retry: {error}")
                result = False

        await ws.send(json.dumps(build_sendlog_ack(datetime.now(), result=result, count=count, logindex=logindex)))

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
    def _next_command_to_send(serial_number):
        """The next (id, wire message) to send for this device, or None if nothing's due.

        Returns None while a previously-sent command is still awaiting a
        response, so only one command is ever in flight per device.
        """
        DeviceCommand.expire_stale()
        if DeviceCommand.objects.filter(device__serial_number=serial_number, status="sent").exists():
            return None
        command = (
            DeviceCommand.objects.filter(device__serial_number=serial_number, status="pending")
            .order_by("created_at")
            .first()
        )
        if command is None:
            return None
        return command.id, build_device_command(serial_number, command.command_type, command.payload)

    @staticmethod
    def _mark_command_sent(command_id):
        DeviceCommand.objects.filter(pk=command_id).update(status="sent", sent_at=timezone.now())

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
        elif command.command_type == "delete_user":
            Command._unlink_biometric_identity(command)

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

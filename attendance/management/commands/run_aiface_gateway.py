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

from attendance.integrations.aiface_protocol import (
    build_reg_ack,
    build_sendlog_ack,
    build_senduser_ack,
    translate_sendlog_record,
)

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
            help="URL of this Django app's vendor-gateway punch bridge endpoint.",
        )

    def handle(self, *args, **options):
        if websockets is None:
            raise CommandError("The 'websockets' package is required: pip install websockets")
        secret = settings.BIOMETRIC_BRIDGE_SECRET
        if not secret:
            raise CommandError("BIOMETRIC_BRIDGE_SECRET is not configured.")

        host, port, bridge_url = options["host"], options["port"], options["bridge_url"]
        self.stdout.write(self.style.SUCCESS(
            f"AiFace gateway listening on ws://{host}:{port}/pub/chat, relaying to {bridge_url}"
        ))
        try:
            asyncio.run(self._serve(host, port, bridge_url, secret))
        except KeyboardInterrupt:
            self.stdout.write("\nStopped.")

    async def _serve(self, host, port, bridge_url, secret):
        async def handler(websocket):
            await self._handle_connection(websocket, bridge_url, secret)

        async with websockets.serve(handler, host, port):
            await asyncio.Future()

    async def _handle_connection(self, ws, bridge_url, secret):
        sn = None
        peer = ws.remote_address
        try:
            async for raw_message in ws:
                try:
                    message = json.loads(raw_message)
                except (TypeError, ValueError):
                    self.stdout.write(f"[{peer}] ignoring non-JSON frame")
                    continue

                cmd = message.get("cmd")
                if cmd == "reg":
                    sn = message.get("sn")
                    self.stdout.write(f"[{peer}] reg from device sn={sn}")
                    await ws.send(json.dumps(build_reg_ack(datetime.now())))
                elif cmd == "sendlog":
                    await self._handle_sendlog(ws, message, bridge_url, secret)
                elif cmd == "senduser":
                    await ws.send(json.dumps(build_senduser_ack(datetime.now())))
                elif cmd:
                    self.stdout.write(f"[{peer}] unhandled cmd={cmd!r}")
        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            self.stdout.write(f"[{peer}] disconnected (sn={sn})")

    async def _handle_sendlog(self, ws, message, bridge_url, secret):
        sn = message.get("sn")
        records = message.get("record") or []
        logindex = message.get("logindex")
        count = message.get("count", len(records))

        gateway_records = [translate_sendlog_record(sn, record) for record in records]
        result = True
        if gateway_records:
            try:
                response = await asyncio.to_thread(self._post_to_bridge, bridge_url, secret, gateway_records)
                self.stdout.write(f"[{sn}] sendlog: {response}")
            except (urllib.error.URLError, ValueError) as error:
                self.stderr.write(f"[{sn}] bridge post failed, asking device to retry: {error}")
                result = False

        await ws.send(json.dumps(build_sendlog_ack(datetime.now(), result=result, count=count, logindex=logindex)))

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

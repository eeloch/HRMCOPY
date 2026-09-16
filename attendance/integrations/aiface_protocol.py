"""Pure translation helpers for the AiFace BS WebSocket/JSON device protocol.

This is the wire format actually spoken by the Yunatt/TIMY terminal (confirmed
from the vendor's own Flask reference implementation and embedded protocol
docs): the device is the WebSocket client, connects to
``ws://<server>:7788/pub/chat``, and pushes ``reg``/``sendlog``/``senduser``
commands unprompted. It is not the ADMS-style HTTP push protocol used by some
other budget terminals.

Kept free of I/O and Django settings so the record mapping can be unit tested
without a live socket; ``run_aiface_gateway`` wires this into an actual
``websockets`` server and posts translated records to the existing
vendor-gateway HTTP bridge.
"""

import hashlib
from datetime import datetime
from typing import Any, Mapping


def build_reg_ack(now: datetime) -> dict:
    """Response to the device's `reg` handshake.

    ``nosenduser``/``nosendimage`` are requests (the device may ignore them)
    asking it to skip enrollment sync and photo attachments — the ingestion
    pipeline strips biometric payload fields anyway, so there is no reason to
    have the device spend bandwidth sending them.
    """
    return {
        "ret": "reg",
        "result": True,
        "cloudtime": now.strftime("%Y-%m-%d %H:%M:%S"),
        "tryseconds": 300,
        "nosenduser": True,
        "nosendimage": True,
        "nosendlog": False,
    }


def build_senduser_ack(now: datetime) -> dict:
    """We don't sync device-side enrollment changes back into HRM; ack so the device stops retrying."""
    return {"ret": "senduser", "result": True, "cloudtime": now.strftime("%Y-%m-%d %H:%M:%S")}


def build_sendlog_ack(now: datetime, *, result: bool, count: int | None, logindex: int | None) -> dict:
    ack: dict[str, Any] = {"ret": "sendlog", "result": result, "cloudtime": now.strftime("%Y-%m-%d %H:%M:%S")}
    if logindex is not None and logindex >= 0:
        ack["count"] = count
        ack["logindex"] = logindex
    return ack


def stable_record_id(device_serial_number: str, record: Mapping[str, Any]) -> int:
    """A deterministic positive id for one `sendlog` record, scoped to its device.

    The device doesn't hand us a stable per-record id (`logindex` covers a
    whole batch, not one punch), so we derive one from the fields that
    identify a unique punch. The same punch replayed after a dropped ack
    hashes to the same id, which is what makes the bridge's dedup-by-device
    idempotency guarantee actually hold.
    """
    basis = "|".join(
        str(record.get(field))
        for field in ("enrollid", "time", "mode", "inout", "event")
    )
    digest = hashlib.sha256(f"{device_serial_number}|{basis}".encode()).hexdigest()
    return int(digest[:12], 16) + 1


def translate_sendlog_record(device_serial_number: str, record: Mapping[str, Any]) -> dict:
    """Map one AiFace `sendlog` record into the vendor-gateway bridge's expected shape."""
    temp = record.get("temp")
    return {
        "gateway_record_id": stable_record_id(device_serial_number, record),
        "enroll_id": record.get("enrollid"),
        "device_serial_number": device_serial_number,
        "timestamp": record.get("time"),
        "mode": record.get("mode"),
        "inout": record.get("inout"),
        "event": record.get("event"),
        "temperature": round(temp / 10, 1) if temp is not None else None,
    }

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

# BiometricIdentity.system value for every identity resolved through this
# gateway. source_identifier is the specific device's serial number - see
# translate_sendlog_record and the enrollment views in attendance/views/devices.py.
IDENTITY_SYSTEM = "vendor_flask_gateway"


def build_reg_ack(now: datetime, *, minimal: bool = False) -> dict:
    """Response to the device's `reg` handshake. `minimal` is exactly what the vendor's demo server sends
    (nothing beyond the documented reply), used for meal terminals whose printer only works that way.

    ``nosenduser``/``nosendimage`` are requests (the device may ignore them)
    asking it to skip enrollment sync and photo attachments — the ingestion
    pipeline strips biometric payload fields anyway, so there is no reason to
    have the device spend bandwidth sending them.
    """
    if minimal:
        return {"ret": "reg", "result": True, "cloudtime": now.strftime("%Y-%m-%d %H:%M:%S")}
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


def build_sendlog_ack(now: datetime, *, result: bool, count: int | None, logindex: int | None, access: int | None = None, message: str | None = None) -> dict:
    """The reply to a `sendlog`. `access`/`message` are for terminals in Servermode:
    the terminal waits for `access` (1 = allow, 0 = deny) and shows `message`. A meal
    terminal's ticket printer follows that allow decision, so it is only added for
    meal terminals; attendance terminals get exactly the reply they always did."""
    ack: dict[str, Any] = {"ret": "sendlog", "result": result, "cloudtime": now.strftime("%Y-%m-%d %H:%M:%S")}
    if logindex is not None and logindex >= 0:
        ack["count"] = count
        ack["logindex"] = logindex
    if access is not None:
        ack["access"] = access
    if message:
        ack["message"] = message
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


BIOMETRIC_TYPE_BACKUPNUM = {"face": 50, "fingerprint": 0}


def build_adduser_command(sn: str, enrollid: int, name: str, biometric_type: str) -> dict:
    """Server -> device: start on-device enrollment.

    This only kicks off the prompt on the terminal's own screen - the device
    replies once the person has actually scanned there (or the attempt is
    cancelled/times out), not immediately. See protocol section 5.12 (`adduser`).
    """
    return {
        "cmd": "adduser",
        "sn": sn,
        "enrollid": enrollid,
        "aliasid": str(enrollid),
        "backupnum": BIOMETRIC_TYPE_BACKUPNUM.get(biometric_type, 50),
        "admin": 0,
        "flag": 0,
        "name": name,
    }


def build_deleteuser_command(sn: str, enrollid: int) -> dict:
    """backupnum 12 removes the whole user (every enrolled biometric slot), not just one."""
    return {"cmd": "deleteuser", "sn": sn, "enrollid": enrollid, "aliasid": str(enrollid), "backupnum": 12}


def build_getuserids_command(sn: str) -> dict:
    """Every currently-enrolled id in one response, unpaginated (protocol section 5.7)."""
    return {"cmd": "getuserids", "sn": sn}


def build_getuserlist_command(sn: str, first: bool) -> dict:
    """Server -> device: one page (up to 40 records) of every enrolled slot, as {enrollid, admin,
    backupnum}. `first` is true for the opening request and false to ask for the next page, exactly as
    the vendor's reference server does (Flask app.py get_user_list). Unlike getuserids this says which
    kind of thing is enrolled: backupnum 0-9 fingerprint, 10 password, 11 card, 20-27/50 face."""
    return {"cmd": "getuserlist", "sn": sn, "stn": bool(first)}


def build_getuserinfo_command(sn: str, enrollid: int, biometric_type: str) -> dict:
    """Server -> device: ask a device to send back one enrolled person's raw
    biometric data (a base64 photo for face, a fingerprint template for
    fingerprint) for the given slot - confirmed from the vendor's own Flask
    reference server (Services/PersonService.py, app.py get_user_info_websocket).
    Used only to relay an enrollment to another device (clone_enrollment);
    the response is never persisted, only forwarded to build_setuserinfo_command.
    """
    return {"cmd": "getuserinfo", "sn": sn, "enrollid": enrollid, "backupnum": BIOMETRIC_TYPE_BACKUPNUM.get(biometric_type, 50)}


def build_setuserinfo_command(sn: str, enrollid: int, name: str, biometric_type: str, record) -> dict:
    """Server -> device: push a previously-captured record (from
    build_getuserinfo_command's response) so a device enrolls this person
    without a live scan - same vendor reference source as getuserinfo above.
    """
    return {
        "cmd": "setuserinfo", "sn": sn, "enrollid": enrollid, "name": name,
        "backupnum": BIOMETRIC_TYPE_BACKUPNUM.get(biometric_type, 50), "admin": 0, "record": record,
    }


def build_getuserinfo_slot_command(sn: str, enrollid: int, backupnum: int) -> dict:
    """Ask a terminal for one exact slot of one person (0-9 fingerprint, 10 password, 11 card, 50 face photo)."""
    return {"cmd": "getuserinfo", "sn": sn, "enrollid": enrollid, "backupnum": int(backupnum)}


def build_setuserinfo_slot_command(sn: str, enrollid: int, name: str, backupnum: int, record) -> dict:
    """Push one exact slot (from build_getuserinfo_slot_command's reply) to a terminal without a live scan."""
    return {"cmd": "setuserinfo", "sn": sn, "enrollid": enrollid, "name": name, "backupnum": int(backupnum), "admin": 0, "record": record}


def build_enableuser_command(sn: str, enrollid: int, enabled: bool) -> dict:
    """Server -> device: switch one enrolled person on or off via the legacy `enableuser` command
    (protocol v1.7, 2017). The vendor doc's Disable example spells the key "enrolled"; both examples
    use the same `enableuser` command, and `enrollid` is the documented key for Enable.

    Confirmed on production 2026-09-23: this command is acked as successful but only actually blocks
    face verification - a card or fingerprint scan for the same enrollid still goes through afterward.
    Sent as a companion to build_user_enabled_profile_command on every switch (payload "legacy": true):
    2026-09-24, AI Meal Ticket 2 (AI24FB 486) kept letting switched-off people through by FACE (9 of 9 leaks were
    face scans) even though it acked the profile command, while AI Meal Ticket obeyed the profile command alone.
    Sending both covers every firmware.
    """
    return {"cmd": "enableuser", "sn": sn, "enrollid": enrollid, "enflag": 1 if enabled else 0}


def build_user_enabled_profile_command(sn: str, enrollid: int, enabled: bool) -> dict:
    """Server -> device: switch one enrolled person on or off via the newer `setuserinfo` profile
    command (protocol v2.9+, "AI face recognition device firmware version 5.09 or v2.09 or above" -
    section 37 of the vendor's websocket+json protocol doc). Its `enable` field is documented as
    governing the user's access as a whole ("0 User disabled, 1 User enable"), not tied to one
    verification method the way the legacy `enableuser` command apparently is in practice. Every other
    field in the documented payload (name, verifymode, department, card, pwd, shift/zone/group ids,
    access window, backupnum, record) is optional there, as it is throughout this protocol - sending
    only enrollid and enable leaves the rest of the person's profile untouched.
    """
    return {"cmd": "setuserinfo", "sn": sn, "enrollid": enrollid, "enable": 1 if enabled else 0}


def build_device_command(sn: str, command_type: str, payload: Mapping[str, Any]) -> dict:
    """Translate a DeviceCommand row's (command_type, payload) into the wire message to send."""
    if command_type == "enroll_user":
        return build_adduser_command(sn, payload["enrollid"], payload.get("name", ""), payload.get("biometric_type", "face"))
    if command_type in ("delete_user", "purge_user"):
        return build_deleteuser_command(sn, payload["enrollid"])
    if command_type == "refresh_enrolled_ids":
        return build_getuserids_command(sn)
    if command_type == "set_user_enabled":
        if payload.get("legacy"):
            return build_enableuser_command(sn, payload["enrollid"], bool(payload["enabled"]))
        return build_user_enabled_profile_command(sn, payload["enrollid"], bool(payload["enabled"]))
    raise ValueError(f"Unknown command_type: {command_type!r}")


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


def choose_terminal_reply(mode: str, results) -> tuple[int | None, str | None]:
    """What (access, message) to add to a meal terminal's `sendlog` reply.

    minimal  - nothing: the bare vendor-style reply (the terminal prints every verified scan itself).
    message  - allow everything and show a line on the screen.
    extended - same as message (older name).
    gated    - allow only entitled scans, deny the rest with a reason. For a terminal set to Server
               approval = Yes, denying is what stops the printer for someone who is not entitled.
    """
    replies = [item for item in results if "access" in item]
    if mode == "minimal" or not replies:
        return None, None
    message = replies[-1].get("message")
    if mode == "gated":
        allowed = all(item.get("access") == 1 and item.get("entitled") for item in replies)
        return (1 if allowed else 0), message
    return (1 if all(item.get("access") == 1 for item in replies) else 0), message

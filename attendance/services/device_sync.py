"""Fill enrollment gaps between terminals by queueing clone_enrollment relays.

Shared by the "Sync All Devices" button and the gateway's own periodic pass: a relay can fail for
ordinary reasons (the terminals drop their connection every ~30s, so a multi-step relay is often
cut off), and a failed relay used to stay failed until someone pressed the button again.
"""

from collections import defaultdict
from datetime import timedelta

from django.utils import timezone

from attendance.integrations.aiface_protocol import IDENTITY_SYSTEM
from attendance.models import BiometricDevice, DeviceCommand
from employees.models import BiometricIdentity, Employee


def queue_missing_clones(devices, *, requested_by=None, limit=None, target_purpose=None):
    """One clone job per employee missing from at least one of `devices`, sourced from a device that
    has them. Employees already waiting in the clone queue are skipped, so this is safe to repeat.
    `target_purpose` restricts which terminals count as missing something. Returns a list describing what was queued."""
    device_by_serial = {device.serial_number: device for device in devices}
    all_serials = set(device_by_serial)

    identities_by_employee = {}
    for identity in BiometricIdentity.objects.filter(
        system=IDENTITY_SYSTEM, source_identifier__in=all_serials, is_active=True, employee__status="active",
    ).select_related("employee"):
        identities_by_employee.setdefault(identity.employee_id, {})[identity.source_identifier] = identity

    already_waiting = {
        command.payload.get("employee_id")
        for command in DeviceCommand.objects.filter(command_type="clone_enrollment", status__in=("pending", "sent"))
    }

    queued = []
    for employee_id, identities_by_serial in identities_by_employee.items():
        if limit is not None and len(queued) >= limit:
            break
        missing_serials = all_serials - identities_by_serial.keys()
        if target_purpose:
            missing_serials = {serial for serial in missing_serials if device_by_serial[serial].purpose == target_purpose}
        if not missing_serials or employee_id in already_waiting:
            continue
        source_serial, source_identity = next(iter(identities_by_serial.items()))
        source_device = device_by_serial[source_serial]
        target_ids = [device_by_serial[serial].id for serial in missing_serials]

        DeviceCommand.objects.create(
            device=source_device, command_type="clone_enrollment",
            payload={
                "employee_id": employee_id,
                "enrollid": int(source_identity.external_user_id),
                "name": source_identity.employee.full_name,
                "biometric_type": "face",
                "target_device_ids": target_ids,
            },
            requested_by=requested_by,
        )
        queued.append({
            "employee_id": source_identity.employee.employee_id,
            "employee_name": source_identity.employee.full_name,
            "from_device": source_device.name,
            "to_devices": [device_by_serial[serial].name for serial in missing_serials],
        })
    return queued


def reachable_devices():
    return [device for device in BiometricDevice.objects.all() if device.is_reachable]


# ---- slot-level sync, driven by what each terminal really holds (list_user_slots) ----------------------------




LISTING_FRESH_FOR = timedelta(hours=3)
SYNC_SLOT_KINDS = (
    ("face", lambda n: n == 50 or 20 <= n <= 27),
    ("fingerprint", lambda n: 0 <= n <= 9),
    ("password", lambda n: n == 10),
    ("card", lambda n: n == 11),
)


def latest_slots(device, *, max_age=None):
    """{enrollid: {backupnum, ...}} from the terminal's most recent completed listing, or None when there is
    none (or it is older than max_age)."""
    command = DeviceCommand.objects.filter(device=device, command_type="list_user_slots", status="acked").order_by("-id").first()
    if command is None or (max_age is not None and command.completed_at < timezone.now() - max_age):
        return None
    per_id = defaultdict(set)
    for enrollid, backupnum in command.result.get("slots", []):
        if enrollid is not None and backupnum is not None:
            per_id[int(enrollid)].add(int(backupnum))
    return per_id


def queue_listings(devices, *, older_than=LISTING_FRESH_FOR):
    """Ask each terminal for its slot list unless a fresh answer or a request is already there."""
    queued = 0
    for device in devices:
        if DeviceCommand.objects.filter(device=device, command_type="list_user_slots", status__in=("pending", "sent")).exists():
            continue
        if latest_slots(device, max_age=older_than) is not None:
            continue
        DeviceCommand.objects.create(device=device, command_type="list_user_slots", payload={})
        queued += 1
    return queued


def link_ids_by_staff_number(devices):
    """A terminal id equal to exactly one active employee's staff number, with no identity yet on that terminal, is
    that employee (the convention every terminal here was enrolled by). Without the link a scan of that person is
    rejected as "unmapped employee". Returns how many were linked."""
    by_number = defaultdict(list)
    for employee in Employee.objects.filter(status="active").only("id", "employee_id"):
        if employee.employee_id.isdigit():
            by_number[int(employee.employee_id)].append(employee)
    linked = 0
    for device in devices:
        slots = latest_slots(device)
        if not slots:
            continue
        known_ids = set(BiometricIdentity.objects.filter(system=IDENTITY_SYSTEM, source_identifier=device.serial_number).values_list("external_user_id", flat=True))
        has_identity = set(BiometricIdentity.objects.filter(system=IDENTITY_SYSTEM, source_identifier=device.serial_number).values_list("employee_id", flat=True))
        for enrollid in slots:
            matches = by_number.get(enrollid, [])
            if len(matches) != 1 or str(enrollid) in known_ids or matches[0].pk in has_identity:
                continue
            BiometricIdentity.objects.create(employee=matches[0], system=IDENTITY_SYSTEM, source_identifier=device.serial_number, external_user_id=str(enrollid), is_active=True)
            linked += 1
    return linked


def _kind(backupnum):
    for name, test in SYNC_SLOT_KINDS:
        if test(backupnum):
            return name
    return None


def _held(backupnums):
    """{kind: {backupnum, ...}} for the slots one person holds on one terminal."""
    held = defaultdict(set)
    for backupnum in backupnums:
        kind = _kind(backupnum)
        if kind:
            held[kind].add(backupnum)
    return held


def plan_slot_clones(devices, *, limit=None):
    """Queue one relay per (person, source terminal) for every credential kind a person has on some terminal but
    not on another. Only terminals with a fresh listing take part, so the plan is built from what they hold now,
    not from what we once recorded. Safe to repeat: a person/source already waiting is skipped."""
    slots_by_serial, device_by_serial = {}, {}
    for device in devices:
        slots = latest_slots(device, max_age=LISTING_FRESH_FOR)
        if slots is not None:
            slots_by_serial[device.serial_number] = slots
            device_by_serial[device.serial_number] = device
    if len(slots_by_serial) < 2:
        return []

    enrollid_of = defaultdict(dict)  # employee id -> serial -> enrollid
    for identity in BiometricIdentity.objects.filter(system=IDENTITY_SYSTEM, is_active=True, employee__status="active", source_identifier__in=list(slots_by_serial)).select_related("employee"):
        if identity.external_user_id.isdigit():
            enrollid_of[identity.employee_id][identity.source_identifier] = int(identity.external_user_id)

    waiting = {
        (command.payload.get("employee_id"), command.device_id)
        for command in DeviceCommand.objects.filter(command_type="clone_enrollment", status__in=("pending", "sent"))
    }
    employees = {e.pk: e for e in Employee.objects.filter(pk__in=list(enrollid_of))}

    queued = []
    for employee_id, ids in enrollid_of.items():
        if limit is not None and len(queued) >= limit:
            break
        have = {serial: _held(slots_by_serial[serial].get(ids[serial], set()) if serial in ids else set()) for serial in slots_by_serial}
        plans = defaultdict(lambda: defaultdict(set))  # source serial -> target serial -> backupnums
        for kind, _test in SYNC_SLOT_KINDS:
            holders = [serial for serial in slots_by_serial if have[serial].get(kind)]
            if not holders:
                continue
            source = max(holders, key=lambda serial: sum(len(v) for v in have[serial].values()))
            for target in slots_by_serial:
                if target != source and not have[target].get(kind):
                    plans[source][target] |= have[source][kind]
        for source, targets in plans.items():
            source_device = device_by_serial[source]
            if (employee_id, source_device.pk) in waiting:
                continue
            DeviceCommand.objects.create(
                device=source_device, command_type="clone_enrollment",
                payload={
                    "employee_id": employee_id, "enrollid": ids[source], "name": employees[employee_id].full_name,
                    "target_device_ids": [device_by_serial[t].pk for t in targets],
                    "pushes": [{"target_device_id": device_by_serial[t].pk, "backupnums": sorted(bns)} for t, bns in targets.items()],
                },
            )
            queued.append(employee_id)
    return queued

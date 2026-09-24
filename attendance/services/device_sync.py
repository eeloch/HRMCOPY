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


# A terminal that has refused the same credential for the same person this many times lately is left alone for the
# cool-down: 2026-09-24, 79 refused pairs were retried three or more times each, keeping the queue busy with pushes
# that were never going to be accepted.
# Relays waiting on one source terminal before the planner stops adding more: the terminals work one job at a time
# over a connection that keeps dropping, and 2026-09-24 the queues reached 258 (Dev 3) while it was still planning.
MAX_WAITING_PER_SOURCE = 40
REJECTION_LIMIT = 2
REJECTION_COOLDOWN = timedelta(hours=6)


def recent_rejections(devices):
    """{(employee id, target serial, kind): times refused within the cool-down}, from finished relay jobs."""
    by_id = {device.pk: device for device in devices}
    by_name = {device.name: device for device in devices}
    counts = defaultdict(int)
    finished = DeviceCommand.objects.filter(
        command_type="clone_enrollment", payload__has_key="pushes", completed_at__gte=timezone.now() - REJECTION_COOLDOWN,
    )
    for command in finished:
        for failure in (command.result or {}).get("failed", []):
            if not isinstance(failure, dict) or failure.get("reason") != "rejected":
                continue
            target = by_id.get(failure.get("target_device_id")) or by_name.get(failure.get("target"))
            kind = _kind(failure["backupnum"]) if isinstance(failure.get("backupnum"), int) else None
            if target and kind:
                counts[(command.payload.get("employee_id"), target.serial_number, kind)] += 1
    return counts


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

    waiting, backlog = set(), defaultdict(int)
    for command in DeviceCommand.objects.filter(command_type="clone_enrollment", status__in=("pending", "sent")):
        waiting.add((command.payload.get("employee_id"), command.device_id))
        backlog[command.device_id] += 1
    employees = {e.pk: e for e in Employee.objects.filter(pk__in=list(enrollid_of))}
    refused = recent_rejections(list(device_by_serial.values()))

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
            # Prefer an attendance terminal as the source: reading from a meal terminal competes with its
            # switch-offs during service.
            source = max(holders, key=lambda serial: (device_by_serial[serial].purpose != "meal_ticket", sum(len(v) for v in have[serial].values())))
            for target in slots_by_serial:
                if target != source and not have[target].get(kind) and refused.get((employee_id, target, kind), 0) < REJECTION_LIMIT:
                    plans[source][target] |= have[source][kind]
        for source, targets in plans.items():
            source_device = device_by_serial[source]
            if (employee_id, source_device.pk) in waiting or backlog[source_device.pk] >= MAX_WAITING_PER_SOURCE:
                continue
            backlog[source_device.pk] += 1
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


# ---- repair: people the sync duplicated on a terminal under a brand-new id --------------------------------------


def find_duplicate_ids(device, slots=None):
    """People whose HRM identity on `device` points at an id other than their own staff number while the terminal also
    holds them under that staff number (terminals were enrolled by staff number). That is the footprint of the old
    "one past the highest id" fallback, which pushed a second copy and re-pointed the identity at it.

    One dict per person: the employee, `own_id` (staff number, present on the terminal), `copy_id` (what HRM tracks
    now) and whether the copy is safe to delete - it is only when nothing else claims it (no other identity, not
    another person's staff number) and every kind of credential it holds is also held under the person's own id."""
    slots = slots if slots is not None else latest_slots(device)
    if not slots:
        return []
    owners = defaultdict(list)
    for employee in Employee.objects.only("id", "employee_id", "status"):
        if employee.employee_id.isdigit():
            owners[int(employee.employee_id)].append(employee.pk)
    identities = list(BiometricIdentity.objects.filter(system=IDENTITY_SYSTEM, source_identifier=device.serial_number, is_active=True).select_related("employee"))
    claimed = defaultdict(set)
    for identity in identities:
        if identity.external_user_id.isdigit():
            claimed[int(identity.external_user_id)].add(identity.employee_id)

    found = []
    for identity in identities:
        employee = identity.employee
        if employee.status != "active" or not employee.employee_id.isdigit() or not identity.external_user_id.isdigit():
            continue
        own_id, copy_id = int(employee.employee_id), int(identity.external_user_id)
        if own_id == copy_id or owners[own_id] != [employee.pk] or own_id not in slots:
            continue
        if claimed[own_id] - {employee.pk}:
            continue  # another identity already claims the staff number
        own_kinds, copy_kinds = set(_held(slots[own_id])), set(_held(slots.get(copy_id, set())))
        someone_elses = bool(claimed[copy_id] - {employee.pk}) or any(pk != employee.pk for pk in owners.get(copy_id, []))
        found.append({
            "employee": employee, "identity": identity, "own_id": own_id, "copy_id": copy_id,
            "copy_present": copy_id in slots, "own_kinds": sorted(own_kinds), "copy_kinds": sorted(copy_kinds),
            "deletable": copy_id in slots and not someone_elses and copy_kinds <= own_kinds,
        })
    return found


# ---- move people from a stray terminal id to their staff-number id, on the same terminal ---------------------------

RENUMBER_LISTING_FRESH_FOR = timedelta(hours=1)


def plan_renumbers(devices, *, limit=None, dry_run=False):
    """One same-terminal move per person whose id on a terminal is not their staff number while their staff-number id
    is free there (nobody else's identity on it). The job (see run_aiface_gateway._run_renumber) carries only the
    slots the old id holds that the staff-number id does not already hold; with none left it just drops the old
    entry. Returns the list of planned moves; creates the jobs unless dry_run."""
    owners = defaultdict(list)
    for employee in Employee.objects.only("id", "employee_id"):
        if employee.employee_id.isdigit():
            owners[int(employee.employee_id)].append(employee.pk)
    waiting = {
        (command.device_id, command.payload.get("employee_id"))
        for command in DeviceCommand.objects.filter(command_type="clone_enrollment", status__in=("pending", "sent"), payload__has_key="renumber")
    }
    planned = []
    for device in devices:
        slots = latest_slots(device, max_age=RENUMBER_LISTING_FRESH_FOR)
        if not slots:
            continue
        identities = list(BiometricIdentity.objects.filter(system=IDENTITY_SYSTEM, source_identifier=device.serial_number, is_active=True, employee__status="active").select_related("employee"))
        claimed = defaultdict(set)
        for identity in identities:
            if identity.external_user_id.isdigit():
                claimed[int(identity.external_user_id)].add(identity.employee_id)
        for identity in identities:
            if limit is not None and len(planned) >= limit:
                return planned
            employee = identity.employee
            if not employee.employee_id.isdigit() or not identity.external_user_id.isdigit():
                continue
            own_id, old_id = int(employee.employee_id), int(identity.external_user_id)
            if own_id == old_id or owners[own_id] != [employee.pk] or claimed[own_id] - {employee.pk} or old_id not in slots:
                continue
            if (device.pk, employee.pk) in waiting:
                continue
            own_kinds = _held(slots.get(own_id, set()))
            to_move = sorted(number for kind, numbers in _held(slots[old_id]).items() if kind not in own_kinds for number in numbers)
            move = {"employee": employee, "device": device, "from_id": old_id, "to_id": own_id, "slots": to_move}
            planned.append(move)
            if not dry_run:
                DeviceCommand.objects.create(
                    device=device, command_type="clone_enrollment",
                    payload={"renumber": True, "employee_id": employee.pk, "name": employee.full_name, "from_id": old_id, "to_id": own_id, "slots": to_move},
                )
    return planned

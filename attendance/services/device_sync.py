"""Fill enrollment gaps between terminals by queueing clone_enrollment relays.

Shared by the "Sync All Devices" button and the gateway's own periodic pass: a relay can fail for
ordinary reasons (the terminals drop their connection every ~30s, so a multi-step relay is often
cut off), and a failed relay used to stay failed until someone pressed the button again.
"""

from attendance.integrations.aiface_protocol import IDENTITY_SYSTEM
from attendance.models import BiometricDevice, DeviceCommand
from employees.models import BiometricIdentity


def queue_missing_clones(devices, *, requested_by=None, limit=None):
    """One clone job per employee missing from at least one of `devices`, sourced from a device that
    has them. Employees already waiting in the clone queue are skipped, so this is safe to repeat.
    Returns a list describing what was queued."""
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

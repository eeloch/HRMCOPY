"""Keep chosen people switched on at the meal terminal only while they still have a ticket to collect today.

The terminal prints a receipt for every person it verifies and has no print command, so the only way to stop a
receipt for someone who is not entitled is for the terminal to refuse them. This switches people off (and on
again) with the protocol's `enableuser` command. It is limited to the staff numbers in
settings.MEAL_GATING_EMPLOYEE_IDS.
"""

from django.conf import settings
from django.utils import timezone

from attendance.integrations.aiface_protocol import IDENTITY_SYSTEM
from attendance.models import BiometricDevice, DeviceCommand, RosterDayStatus
from employees.models import BiometricIdentity, Employee

from .models import MealCollection, MealTerminalUserState
from .services import MealService

COMMAND_TYPE = "set_user_enabled"


def gated_employees():
    return Employee.objects.filter(employee_id__in=list(getattr(settings, "MEAL_GATING_EMPLOYEE_IDS", [])), status="active")


def tickets_left_today(employee, now=None):
    """Tickets this person may still collect today (0 on a rest day, with no allocation, or once used up)."""
    now = now or timezone.now()
    work_date, roster = MealService.resolve_work_day(employee, now)
    if not (roster and roster.status == RosterDayStatus.WORK):
        return 0
    entitlement = max(MealService.approved_entitlement(employee, work_date) - MealService.absence_penalty_reduction(employee, work_date), 0)
    used = MealCollection.objects.filter(employee=employee, work_date=work_date, voided_at__isnull=True).count()
    return max(entitlement - used, 0)


def _meal_identities(employee):
    serials = BiometricDevice.objects.filter(purpose="meal_ticket").values_list("serial_number", flat=True)
    return BiometricIdentity.objects.filter(employee=employee, system=IDENTITY_SYSTEM, is_active=True, source_identifier__in=list(serials))


def _queue(device, employee, enrollid, enabled):
    """One pending switch per person per direction is enough."""
    already = DeviceCommand.objects.filter(device=device, command_type=COMMAND_TYPE, status__in=["pending", "sent"], payload__enrollid=int(enrollid), payload__enabled=enabled).exists()
    if not already:
        DeviceCommand.objects.create(device=device, command_type=COMMAND_TYPE, payload={"enrollid": int(enrollid), "enabled": enabled, "employee_id": employee.pk})
        return True
    return False


def reconcile(*, release=False):
    """Queue the switches needed so each managed person's terminal state matches their tickets left today.
    `release` switches everyone this has ever switched off back on, whatever the setting says (a safety valve).
    Returns the number of commands queued."""
    queued = 0
    if release:
        for state in MealTerminalUserState.objects.filter(enabled=False).select_related("employee"):
            for identity in _meal_identities(state.employee).filter(source_identifier=state.device_serial):
                device = BiometricDevice.objects.get(serial_number=identity.source_identifier)
                queued += _queue(device, state.employee, identity.external_user_id, True)
        return queued
    for employee in gated_employees():
        wanted = tickets_left_today(employee) > 0
        for identity in _meal_identities(employee):
            state = MealTerminalUserState.objects.filter(employee=employee, device_serial=identity.source_identifier).first()
            if state is None or state.enabled != wanted:
                device = BiometricDevice.objects.get(serial_number=identity.source_identifier)
                queued += _queue(device, employee, identity.external_user_id, wanted)
    return queued


def record_state(command):
    """Called when the terminal confirms a switch: remember what it now is."""
    payload = command.payload
    MealTerminalUserState.objects.update_or_create(employee_id=payload["employee_id"], device_serial=command.device.serial_number, defaults={"enabled": bool(payload["enabled"])})

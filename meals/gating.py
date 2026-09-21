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


def _meal_serials():
    return list(BiometricDevice.objects.filter(purpose="meal_ticket").values_list("serial_number", flat=True))


def gated_employees(only=None):
    """The people whose terminal access follows their tickets: those named in MEAL_GATING_EMPLOYEE_IDS, or, when the
    list holds "*", every active employee who is enrolled on a meal terminal. `only` narrows it to some employee ids."""
    ids = list(getattr(settings, "MEAL_GATING_EMPLOYEE_IDS", []))
    employees = Employee.objects.filter(status="active")
    if "*" in ids:
        employees = employees.filter(biometric_identities__system=IDENTITY_SYSTEM, biometric_identities__is_active=True, biometric_identities__source_identifier__in=_meal_serials()).distinct()
    else:
        employees = employees.filter(employee_id__in=ids)
    if only is not None:
        employees = employees.filter(pk__in=list(only))
    return employees


def tickets_left_today(employee, now=None):
    """Tickets this person may still collect today (0 on a rest day, with no allocation, or once used up)."""
    now = now or timezone.now()
    from .authorizations import extra_unused

    work_date, roster = MealService.resolve_work_day(employee, now)
    entitlement = 0
    if roster and roster.status == RosterDayStatus.WORK:
        entitlement = max(MealService.approved_entitlement(employee, work_date) - MealService.absence_penalty_reduction(employee, work_date), 0)
    used = MealCollection.objects.filter(employee=employee, work_date=work_date, voided_at__isnull=True).count()
    # What is left of the entitlement, plus extras a supervisor authorised that nobody has collected yet
    # (that works on a rest day too). A used authorisation adds nothing more.
    return max(entitlement - used, 0) + extra_unused(employee, work_date)


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


def reconcile(*, release=False, employees=None):
    """Queue the switches needed so each managed person's terminal state matches their tickets left today.
    `employees` (ids) limits it to those people, which is what a scan does; without it everyone managed is checked.
    `release` switches everyone this has ever switched off back on, whatever the setting says (a safety valve).
    Returns the number of commands queued."""
    queued = 0
    if release:
        for state in MealTerminalUserState.objects.filter(enabled=False).select_related("employee"):
            for identity in _meal_identities(state.employee).filter(source_identifier=state.device_serial):
                device = BiometricDevice.objects.get(serial_number=identity.source_identifier)
                queued += _queue(device, state.employee, identity.external_user_id, True)
        return queued
    managed = list(gated_employees(only=employees))
    if not managed:
        return 0
    serials = _meal_serials()
    identities = {}
    for identity in BiometricIdentity.objects.filter(employee__in=managed, system=IDENTITY_SYSTEM, is_active=True, source_identifier__in=serials):
        identities.setdefault(identity.employee_id, []).append(identity)
    states = {(s.employee_id, s.device_serial): s for s in MealTerminalUserState.objects.filter(employee__in=managed)}
    devices = {d.serial_number: d for d in BiometricDevice.objects.filter(serial_number__in=serials)}
    for employee in managed:
        wanted = tickets_left_today(employee) > 0
        for identity in identities.get(employee.pk, []):
            state = states.get((employee.pk, identity.source_identifier))
            if state is None and wanted:
                # Nobody has ever been switched off here: the terminal starts everyone enabled, so just remember that.
                MealTerminalUserState.objects.update_or_create(employee=employee, device_serial=identity.source_identifier, defaults={"enabled": True})
            elif state is None or state.enabled != wanted:
                queued += _queue(devices[identity.source_identifier], employee, identity.external_user_id, wanted)
    return queued


def refresh(employee):
    """Re-check one person right now (after a scan, an authorisation, a voided ticket...). Never raises."""
    try:
        return reconcile(employees=[employee.pk if hasattr(employee, "pk") else employee])
    except Exception:
        return 0


def record_state(command):
    """Called when the terminal confirms a switch: remember what it now is."""
    payload = command.payload
    MealTerminalUserState.objects.update_or_create(employee_id=payload["employee_id"], device_serial=command.device.serial_number, defaults={"enabled": bool(payload["enabled"])})

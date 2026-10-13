"""Keep chosen people switched on at the meal terminal only while they still have a ticket to collect today.

The terminal prints a receipt for every person it verifies and has no print command, so the only way to stop a
receipt for someone who is not entitled is for the terminal to refuse them. This switches people off (and on
again) with the protocol's `setuserinfo enable` command. (A legacy `enableuser` twin was sent for a day on a wrong
diagnosis - see below - and removed: both terminals obey the profile command alone.) Limited to the staff numbers in
settings.MEAL_GATING_EMPLOYEE_IDS.

Meal Ticket 2 (2026-09-25): it obeys the switch-off, but logs the refused verification as a record with event 104 (its
"Access denied no LOG" setting is off). The bridge ignores non-zero events, so a refusal is never counted as a ticket.
"""

from datetime import datetime, timedelta

from django.conf import settings
from django.utils import timezone

from attendance.integrations.aiface_protocol import IDENTITY_SYSTEM
from attendance.models import AttendanceEvent, BiometricDevice, DeviceCommand, RosterDayStatus
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


CLOCK_IN_EARLIEST = timedelta(hours=3)  # a punch up to this long before the shift starts counts as arriving for it


def requires_clock_in(work_date, roster):
    """Unsupervised night meals: on the weekdays in MEAL_REQUIRE_CLOCK_IN_NIGHT_WEEKDAYS an overnight shift's meals open
    only after the person has clocked in, so nobody who is not at work can collect a ticket with no one watching."""
    weekdays = getattr(settings, "MEAL_REQUIRE_CLOCK_IN_NIGHT_WEEKDAYS", [])
    return bool(weekdays) and roster is not None and roster.shift_id is not None and roster.shift.is_overnight and work_date.weekday() in weekdays


def has_clocked_in(employee, work_date, shift):
    start = timezone.make_aware(datetime.combine(work_date, shift.start_time)) - CLOCK_IN_EARLIEST
    return AttendanceEvent.objects.filter(employee=employee, timestamp__gte=start).exists()


def refresh_for_clock_in(employee):
    """Called when someone's attendance punch is saved: if their meals wait for a clock-in, switch them on right away."""
    if not getattr(settings, "MEAL_REQUIRE_CLOCK_IN_NIGHT_WEEKDAYS", []):
        return
    work_date, roster = MealService.resolve_work_day(employee, timezone.now())
    if requires_clock_in(work_date, roster):
        refresh(employee.pk, trigger="clock-in punch saved")


def ticket_state(employee, now=None):
    """Everything that decides whether this person may collect a ticket right now, so a switch can say WHY it was made."""
    now = now or timezone.now()
    from .authorizations import extra_unused

    work_date, roster = MealService.resolve_work_day(employee, now)
    state = {
        "work_date": work_date.isoformat(), "roster": roster.status if roster else "none",
        "shift": roster.shift.name if roster and roster.shift_id else None,
        "entitlement_base": 0, "entitlement_today": 0, "absence_penalty": 0, "waiting_for_clock_in": False, "used": 0, "extra": 0, "left": 0,
    }
    entitlement = 0
    if roster and roster.status == RosterDayStatus.WORK:
        state["entitlement_base"] = MealService.approved_entitlement(employee, work_date)
        state["entitlement_today"] = MealService.entitlement_for_day(employee, work_date, roster)  # a Sunday night is one ticket
        state["absence_penalty"] = MealService.absence_penalty_reduction(employee, work_date)
        entitlement = max(state["entitlement_today"] - state["absence_penalty"], 0)
        if entitlement and requires_clock_in(work_date, roster) and not has_clocked_in(employee, work_date, roster.shift):
            state["waiting_for_clock_in"] = True
            entitlement = 0  # not at work yet: the terminal stays switched off for them until they clock in
    state["used"] = MealCollection.objects.filter(employee=employee, work_date=work_date, voided_at__isnull=True).count()
    state["extra"] = extra_unused(employee, work_date)
    # What is left of the entitlement, plus extras a supervisor authorised that nobody has collected yet
    # (that works on a rest day too). A used authorisation adds nothing more.
    state["left"] = max(entitlement - state["used"], 0) + state["extra"]
    return state


def tickets_left_today(employee, now=None):
    """Tickets this person may still collect today (0 on a rest day, with no allocation, or once used up)."""
    return ticket_state(employee, now)["left"]


def explain_switch(state, enabled):
    """A plain sentence for why the terminal was switched on or off for this person."""
    if enabled:
        parts = [f"{state['left']} ticket(s) left today ({state['used']} of {state['entitlement_today'] - state['absence_penalty']} used"]
        parts[0] += f", +{state['extra']} authorised extra" if state["extra"] else ""
        parts[0] += ")"
        return "Switched ON: " + parts[0]
    if state["roster"] != "work":
        return f"Switched OFF: not rostered to work today (roster: {state['roster']})"
    if state["waiting_for_clock_in"]:
        return "Switched OFF: waiting for their clock-in (this night's meals open after clock-in)"
    if state["entitlement_today"] == 0:
        return "Switched OFF: no meal entitlement for today"
    if state["entitlement_today"] - state["absence_penalty"] <= 0:
        return f"Switched OFF: a meal penalty from absence takes away their {state['entitlement_today']} ticket(s) today"
    cap = f", capped to {state['entitlement_today']} on this shift" if state["entitlement_today"] < state["entitlement_base"] else ""
    penalty = f", {state['absence_penalty']} taken off by an absence penalty" if state["absence_penalty"] else ""
    return f"Switched OFF: all {state['used']} ticket(s) used (entitlement {state['entitlement_base']}{cap}{penalty})"


def _meal_identities(employee):
    serials = BiometricDevice.objects.filter(purpose="meal_ticket").values_list("serial_number", flat=True)
    return BiometricIdentity.objects.filter(employee=employee, system=IDENTITY_SYSTEM, is_active=True, source_identifier__in=list(serials))


def disable_everywhere(employee):
    """Force every meal-terminal identity this person has switched off right now, regardless of gating scope
    or the identity's own active flag.

    gated_employees() only ever manages active employees, so the moment someone's status becomes anything else
    they simply drop out of every future reconcile() - nothing else ever tells the terminal to stop granting
    them a ticket. Call this the moment an employee exits. Identities already marked inactive are still included
    (their terminal-side enrolment can outlive our own revocation of them), so this also mops up anyone whose
    identity was revoked without the terminal ever being told."""
    serials = BiometricDevice.objects.filter(purpose="meal_ticket").values_list("serial_number", flat=True)
    identities = BiometricIdentity.objects.filter(employee=employee, system=IDENTITY_SYSTEM, source_identifier__in=list(serials))
    devices = {d.serial_number: d for d in BiometricDevice.objects.filter(serial_number__in=list(serials))}
    queued = 0
    for identity in identities:
        device = devices.get(identity.source_identifier)
        if device is not None:
            queued += _queue(device, employee, identity.external_user_id, False, trigger="employee exited - switched off everywhere")
    return queued


def _queue(device, employee, enrollid, enabled, trigger="terminal check"):
    """One pending switch per person per direction is enough. The command records WHY it was made: what triggered the
    check and the numbers it was decided on (the meal terminal access log)."""
    already = DeviceCommand.objects.filter(device=device, command_type=COMMAND_TYPE, status__in=["pending", "sent"], payload__enrollid=int(enrollid), payload__enabled=enabled).exists()
    if not already:
        try:
            state = ticket_state(employee)
            reason = {"trigger": trigger, "text": explain_switch(state, enabled), "state": state}
        except Exception:  # noqa: BLE001 - the log must never stop a switch
            reason = {"trigger": trigger, "text": "Switched " + ("ON" if enabled else "OFF"), "state": {}}
        DeviceCommand.objects.create(device=device, command_type=COMMAND_TYPE, payload={"enrollid": int(enrollid), "enabled": enabled, "employee_id": employee.pk, "reason": reason})
        return True
    return False


def reconcile(*, release=False, employees=None, trigger=None):
    """Queue the switches needed so each managed person's terminal state matches their tickets left today.
    `employees` (ids) limits it to those people, which is what a scan does; without it everyone managed is checked.
    `release` switches everyone this has ever switched off back on, whatever the setting says (a safety valve).
    Returns the number of commands queued."""
    queued = 0
    if release:
        for state in MealTerminalUserState.objects.filter(enabled=False).select_related("employee"):
            for identity in _meal_identities(state.employee).filter(source_identifier=state.device_serial):
                device = BiometricDevice.objects.get(serial_number=identity.source_identifier)
                queued += _queue(device, state.employee, identity.external_user_id, True, trigger="release - everyone switched back on")
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
                queued += _queue(devices[identity.source_identifier], employee, identity.external_user_id, wanted, trigger=trigger or ("periodic check" if employees is None else "re-check"))
    return queued


def refresh(employee, trigger="re-check"):
    """Re-check one person right now (after a scan, an authorisation, a voided ticket...). Never raises."""
    try:
        return reconcile(employees=[employee.pk if hasattr(employee, "pk") else employee], trigger=trigger)
    except Exception:
        return 0


def record_state(command):
    """Called when the terminal confirms a switch: remember what it now is."""
    payload = command.payload
    MealTerminalUserState.objects.update_or_create(employee_id=payload["employee_id"], device_serial=command.device.serial_number, defaults={"enabled": bool(payload["enabled"])})

"""What each day's attendance cases amounted to, for the attendance history: for every lateness, early departure, absence
and missing clock-in / clock-out on a day - what it is, where it stands (proposed, charged, waived, on hold) and, for
people who may see money, how much (the payroll deduction itself once it exists, otherwise the proposal)."""

from decimal import Decimal

KINDS = {
    "late": "Late", "early_departure": "Early departure", "absence": "Absent",
    "missing_clock_in": "No clock-in", "missing_clock_out": "No clock-out",
}
STATE = {"pending": "Proposed", "approved": "Charged", "waived": "Waived", "held": "On hold"}


def charges_by_attendance(records, *, show_amounts):
    from attendance.models import AttendanceException
    from payroll.models import PayrollLineItem

    ids = [record.pk for record in records]
    if not ids:
        return {}
    exceptions = list(AttendanceException.objects.filter(attendance_id__in=ids, exception_type__in=tuple(KINDS)).order_by("exception_type", "id"))
    deducted = {}
    if show_amounts and exceptions:
        employee_ids = {record.employee_id for record in records}
        for line in PayrollLineItem.objects.filter(payroll__employee_id__in=employee_ids, source_type="attendance_exception", source_reference__in=[str(x.pk) for x in exceptions]):
            deducted[line.source_reference] = line.amount
    result = {}
    for exception in exceptions:
        amount = None
        if show_amounts:
            if exception.status == "waived":
                amount = Decimal("0.00")
            else:
                amount = deducted.get(str(exception.pk), exception.proposed_deduction)
        result.setdefault(exception.attendance_id, []).append({
            "type": exception.exception_type, "label": KINDS[exception.exception_type], "minutes": exception.minutes_affected,
            "status": exception.status, "state": STATE.get(exception.status, exception.status),
            "amount": str(amount) if amount is not None else None,
        })
    return result

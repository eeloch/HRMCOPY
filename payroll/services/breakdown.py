"""Group payroll deduction lines into the few kinds a reader recognises (absence, lateness, meals, advances...), so a
deductions total can be explained at a glance - per employee and for a whole period."""

from collections import OrderedDict
from decimal import Decimal

# (key, label, line codes, source types) in the order they are shown
CATEGORIES = (
    ("absence", "Absence", {"ATTENDANCE_ABSENCE"}, set()),
    ("late", "Lateness", {"ATTENDANCE_LATE"}, set()),
    ("early_departure", "Early departure", {"ATTENDANCE_EARLY_DEPARTURE"}, set()),
    ("missing_punch", "No clock-in / clock-out", {"ATTENDANCE_MISSING_CLOCK_IN", "ATTENDANCE_MISSING_CLOCK_OUT"}, set()),
    ("unpaid_leave", "Unpaid leave", {"UNPAID_LEAVE"}, {"leave_request_day"}),
    ("meals", "Meals", {"MEAL_EXCESS", "MEAL_CHARGEBACK"}, {"meal_excess", "meal_chargeback"}),
    ("ppe", "PPE", set(), {"ppe_issue"}),
    ("salary_advance", "Salary advance", {"SALARY_ADVANCE"}, {"salary_advance"}),
    ("deferred_fund", "Deferred fund", {"DEFERRED_FUND"}, {"deferred_fund"}),
    ("offences", "Offences / fines", {"OFFENCE", "FINE"}, {"employee_offence"}),
)
OTHER = ("other", "Other / manual")


def category_of(code, source_type):
    for key, label, codes, sources in CATEGORIES:
        if code in codes or (source_type and source_type in sources):
            return key, label
    return OTHER


def breakdown(lines):
    """[{"key", "label", "amount", "count"}] for the deduction lines given, in display order, empty kinds left out."""
    totals = OrderedDict((key, [label, Decimal("0.00"), 0]) for key, label, _, _ in CATEGORIES)
    totals[OTHER[0]] = [OTHER[1], Decimal("0.00"), 0]
    for line in lines:
        if line.item_type != "deduction":
            continue
        key, _ = category_of(line.code, line.source_type)
        totals[key][1] += line.amount
        totals[key][2] += 1
    return [{"key": key, "label": label, "amount": str(amount), "count": count} for key, (label, amount, count) in totals.items() if count]

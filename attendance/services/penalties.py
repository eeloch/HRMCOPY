"""The attendance penalty bands - one place, used for the amount shown on an exception and for the payroll deduction.

Lateness (minutes after the shift starts):
    up to 5 min  waived              6-15 min   N300      16-30 min   N500
    31-59 min    N700                1 hr up to 4 hrs     N1,000
    4 hrs or more (11am on a 7am shift) - half a day's pay
Early departure (minutes before the shift ends):
    up to 5 min  waived              6-15 min   N500      16-30 min   N1,000
    31 min up to 7 hrs   N1,500
    7 hrs or more (noon on a 7pm close) - half a day's pay
The half-day points are measured from the shift, so they work for night shifts too."""

from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP

MONEY = Decimal("0.01")
DEFAULT_WORKING_DAYS = 26

LATE_WAIVED_UP_TO = 5
EARLY_WAIVED_UP_TO = 5
HALF_DAY_LATE_MINUTES = 4 * 60
HALF_DAY_EARLY_MINUTES = 7 * 60

LATE_BANDS = ((15, "6_15", Decimal("300")), (30, "16_30", Decimal("500")), (59, "31_59", Decimal("700")))
LATE_HOUR_FEE = Decimal("1000")
EARLY_BANDS = ((15, "6_15", Decimal("500")), (30, "16_30", Decimal("1000")))
EARLY_LONG_FEE = Decimal("1500")


def waived_up_to(exception_type):
    return LATE_WAIVED_UP_TO if exception_type == "late" else EARLY_WAIVED_UP_TO


def penalty_for(exception_type, minutes, daily_rate):
    """(amount, band, extra metadata) for a lateness or early departure, or None when there is nothing to charge
    (no minutes, or within the waived allowance). `daily_rate` prices the half-day bands."""
    if exception_type not in ("late", "early_departure") or minutes <= waived_up_to(exception_type):
        return None
    late = exception_type == "late"
    half_day_at = HALF_DAY_LATE_MINUTES if late else HALF_DAY_EARLY_MINUTES
    if minutes >= half_day_at:
        half_day = (daily_rate / Decimal("2")).quantize(MONEY, rounding=ROUND_HALF_UP)
        return half_day, "half_day", {"daily_rate": str(daily_rate), "half_day_rate": str(half_day)}
    for upper, band, amount in (LATE_BANDS if late else EARLY_BANDS):
        if minutes <= upper:
            return amount.quantize(MONEY), band, {}
    return (LATE_HOUR_FEE if late else EARLY_LONG_FEE).quantize(MONEY), "60_to_half_day" if late else "31_to_half_day", {}


def estimated_daily_rate(employee, work_date):
    """The day's pay, as payroll will work it out: salary over the days the roster expects that month (26 when the
    roster is empty). Only used to show the proposed amount on a half-day case before payroll exists."""
    from attendance.models import EmployeeRosterDay, RosterDayStatus

    first = work_date.replace(day=1)
    last = (first + timedelta(days=32)).replace(day=1) - timedelta(days=1)
    days = EmployeeRosterDay.objects.filter(employee=employee, date__range=(first, last), status=RosterDayStatus.WORK).count() or DEFAULT_WORKING_DAYS
    return (Decimal(employee.basic_salary or 0) / Decimal(days)).quantize(MONEY, rounding=ROUND_HALF_UP)

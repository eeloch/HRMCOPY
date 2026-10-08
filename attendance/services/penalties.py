"""The attendance penalty bands - one place, used for the amount shown on an exception and for the payroll deduction.

Lateness (minutes after the shift starts) and early departure (minutes before it ends) share one ladder:
    up to 5 min   waived             6-15 min   N300         16-30 min   N500        31-59 min   N700
    1 hr to 1 hr 59 min   N1,000
    from 2 hrs: N1,000 for every full hour, plus N500 for an extra half hour (2h N2,000, 2h30 N2,500, 3h N3,000 ...)
    Half-day pay only: arriving 12pm or later (5 hrs after a 7am start), or leaving at 12pm or earlier (7 hrs before a
    7pm close). The ladder never charges more than that half day.
    A proposed absence is raised for someone to confirm when it is worse still: arriving from 1pm (6 hrs late) or
    leaving before 12 noon (more than 7 hrs early).
The points are measured from the shift, so they work for night shifts too."""

from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP

MONEY = Decimal("0.01")
DEFAULT_WORKING_DAYS = 26

LATE_WAIVED_UP_TO = 5
EARLY_WAIVED_UP_TO = 5
HALF_DAY_LATE_MINUTES = 5 * 60      # 12pm on a 7am start
HALF_DAY_EARLY_MINUTES = 7 * 60     # 12 noon on a 7pm close
ABSENCE_ALERT_LATE_MINUTES = 6 * 60  # arriving from 1pm
ABSENCE_ALERT_EARLY_MINUTES = 7 * 60  # leaving before 12 noon (more than this)

SMALL_BANDS = ((15, "6_15", Decimal("300")), (30, "16_30", Decimal("500")), (59, "31_59", Decimal("700")))
HOUR = Decimal("1000")
HALF_HOUR = Decimal("500")


def waived_up_to(exception_type):
    return LATE_WAIVED_UP_TO if exception_type == "late" else EARLY_WAIVED_UP_TO


def ladder(minutes):
    """(amount, band) for a number of minutes beyond the waived allowance, before the half-day limit."""
    for upper, band, amount in SMALL_BANDS:
        if minutes <= upper:
            return amount, band
    hours, rest = divmod(minutes, 60)
    if hours == 1:
        return HOUR, "60_119"
    return HOUR * hours + (HALF_HOUR if rest >= 30 else Decimal("0")), "hourly"


def proposes_absence(exception_type, minutes):
    """True when it is bad enough that an absence should be proposed for someone to confirm."""
    if exception_type == "late":
        return minutes >= ABSENCE_ALERT_LATE_MINUTES
    if exception_type == "early_departure":
        return minutes > ABSENCE_ALERT_EARLY_MINUTES
    return False


def penalty_for(exception_type, minutes, daily_rate):
    """(amount, band, extra metadata) for a lateness or early departure, or None when there is nothing to charge
    (no minutes, or within the waived allowance). `daily_rate` prices the half-day limit."""
    if exception_type not in ("late", "early_departure") or minutes <= waived_up_to(exception_type):
        return None
    half_day_at = HALF_DAY_LATE_MINUTES if exception_type == "late" else HALF_DAY_EARLY_MINUTES
    half_day = (Decimal(daily_rate) / Decimal("2")).quantize(MONEY, rounding=ROUND_HALF_UP)
    extra = {"daily_rate": str(daily_rate), "half_day_rate": str(half_day)}
    if minutes >= half_day_at:
        return half_day, "half_day", extra
    amount, band = ladder(minutes)
    if half_day > 0 and amount > half_day:  # never more than the half day, however the ladder climbs
        return half_day, "half_day_limit", extra
    return amount.quantize(MONEY), band, {}


def estimated_daily_rate(employee, work_date):
    """The day's pay, as payroll will work it out: salary over the days the roster expects that month (26 when the
    roster is empty). Only used to show the proposed amount on a half-day case before payroll exists."""
    from attendance.models import EmployeeRosterDay, RosterDayStatus

    first = work_date.replace(day=1)
    last = (first + timedelta(days=32)).replace(day=1) - timedelta(days=1)
    days = EmployeeRosterDay.objects.filter(employee=employee, date__range=(first, last), status=RosterDayStatus.WORK).count() or DEFAULT_WORKING_DAYS
    return (Decimal(employee.basic_salary or 0) / Decimal(days)).quantize(MONEY, rounding=ROUND_HALF_UP)

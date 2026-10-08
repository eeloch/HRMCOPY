"""An employee's monthly statement: what they attended and what is being charged to them.

Built for HR to show or print when an employee asks "what days did I work, and what will come off my pay?".
It deliberately contains NO pay: no salary, gross, net or daily rate, and not the amount of the absence / lateness
deductions payroll works out (those are the daily rate times the days, so they would give the salary away). The
days and minutes are shown instead. Charges that are fixed sums (offences, extra meals, PPE, advance instalments)
and rewards are shown with their amounts."""

import calendar
from datetime import date
from decimal import Decimal

from django.db.models import Q
from django.utils import timezone

from advances.models import AdvanceRepayment, AdvanceStatus, SalaryAdvance
from attendance.models import AttendanceException, DailyAttendance, EmployeeRosterDay, RosterDayStatus
from bonuses.models import Bonus, BonusStatus
from meals.models import MealExcessException, MealExcessStatus
from offences.models import EmployeeOffence, EmployeeOffenceStatus
from ppe.models import EmployeePPEIssue, PPEDeductionStatus

MONTHS = ["", "January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]
STATUS_LABELS = {"present": "Present", "late": "Late", "absent": "Absent", "leave": "On leave", "incomplete": "Clocked in only", "rest": "Rest day", "none": "No record"}
CHARGE_STATUS_LABELS = {"pending": "Awaiting approval", "approved": "Approved - will be deducted", "deducted": "Deducted", "expected": "Expected from this month's pay"}
COUNTS_AS_CHARGED = {"approved", "deducted", "expected"}


def _money(value):
    return str(Decimal(value).quantize(Decimal("0.01")))


def _clock(value):
    return timezone.localtime(value).strftime("%H:%M") if value else None


CASE_LABELS = {"late": "Late arrival", "early_departure": "Early departure", "absence": "Absent"}
CASE_STATUS_LABELS = {"pending": "Awaiting a decision", "approved": "Charged", "waived": "Waived - no charge", "held": "On hold - being checked"}


def _attendance_cases(employee, first, last, can_change):
    """Every lateness / early departure / absence case of the month, with the punches it was judged on, so a charge the
    employee disputes can be checked against them. Minutes and decisions only - no amounts (see the module note)."""
    cases = []
    rows = AttendanceException.objects.filter(attendance__employee=employee, attendance__date__range=(first, last), exception_type__in=tuple(CASE_LABELS)).select_related("attendance", "attendance__shift").order_by("attendance__date", "exception_type")
    for case in rows:
        row = case.attendance
        cases.append({
            "id": case.pk, "date": row.date.isoformat(), "kind": CASE_LABELS[case.exception_type],
            "minutes": case.minutes_affected if case.exception_type != "absence" else None,
            "shift": row.shift.name if row.shift_id else None,
            "scheduled": f"{_clock(row.scheduled_start)} - {_clock(row.scheduled_end)}" if row.scheduled_start and row.scheduled_end else None,
            "clock_in": _clock(row.actual_clock_in), "clock_out": _clock(row.actual_clock_out),
            "status": case.status, "status_label": CASE_STATUS_LABELS.get(case.status, case.status),
            "decided_by": case.reviewed_by or None, "decided_on": timezone.localtime(case.reviewed_at).date().isoformat() if case.reviewed_at else None,
            "comment": case.admin_comment or None,
            "can_change": bool(can_change and case.status != "pending"),
        })
    return cases


def build_statement(employee, year, month, *, today=None, can_change=False):
    today = today or timezone.localdate()
    if not (2000 <= year <= 2100 and 1 <= month <= 12):
        raise ValueError("Choose a valid month.")
    if (year, month) > (today.year, today.month):
        raise ValueError("That month has not started yet.")
    first, last = date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])
    shown_until = min(last, today)

    attendance = {row.date: row for row in DailyAttendance.objects.filter(employee=employee, date__range=(first, last)).select_related("shift")}
    roster = {row.date: row for row in EmployeeRosterDay.objects.filter(employee=employee, date__range=(first, last))}

    days, counts = [], {key: 0 for key in ("present", "late", "absent", "leave", "incomplete", "rest")}
    worked_minutes = late_minutes = 0
    for day_number in range(1, shown_until.day + 1):
        current = date(year, month, day_number)
        row = attendance.get(current)
        if row is not None:
            status = row.status
        elif roster.get(current) is not None and roster[current].status == RosterDayStatus.REST:
            status = "rest"
        else:
            status = "none"
        if status in counts:
            counts[status] += 1
        if row is not None:
            worked_minutes += row.worked_minutes
            late_minutes += row.late_minutes
        days.append({
            "date": current.isoformat(),
            "weekday": current.strftime("%a"),
            "status": status,
            "status_label": STATUS_LABELS[status],
            "shift": row.shift.name if row is not None and row.shift_id else None,
            "clock_in": _clock(row.actual_clock_in) if row else None,
            "clock_out": _clock(row.actual_clock_out) if row else None,
            "late_minutes": row.late_minutes if row else 0,
            "hours_worked": round(row.worked_minutes / 60, 2) if row else 0,
        })

    charges = []
    for offence in EmployeeOffence.objects.filter(employee=employee, incident_date__range=(first, last)).exclude(status=EmployeeOffenceStatus.REJECTED).select_related("offence_type"):
        status = offence.status
        charges.append({
            "date": offence.incident_date.isoformat(), "kind": "Offence",
            "description": f"{offence.offence_type.name} ({offence.occurrence}{'st' if offence.occurrence == 1 else 'nd' if offence.occurrence == 2 else 'rd' if offence.occurrence == 3 else 'th'} time)" + (f" - {offence.penalty_text}" if offence.amount <= 0 and offence.penalty_text else ""),
            "amount": _money(offence.amount), "status": "deducted" if status == EmployeeOffenceStatus.DEDUCTED else status,
        })
    for exception in MealExcessException.objects.filter(employee=employee, work_date__range=(first, last)).exclude(status__in=[MealExcessStatus.DECLINED, MealExcessStatus.CANCELLED]):
        charges.append({
            "date": exception.work_date.isoformat(), "kind": "Extra meal",
            "description": f"Meal ticket beyond entitlement x{exception.excess_quantity}",
            "amount": _money(exception.proposed_deduction), "status": exception.status,
        })
    for issue in EmployeePPEIssue.objects.filter(employee=employee, employee_deductible=True, issue_date__range=(first, last)).exclude(deduction_status__in=[PPEDeductionStatus.NOT_DEDUCTIBLE]).select_related("ppe_type"):
        charges.append({
            "date": issue.issue_date.isoformat(), "kind": "PPE",
            "description": f"{issue.ppe_type.name} x{issue.quantity}",
            "amount": _money(issue.total_cost),
            "status": {"deducted": "deducted", "approved": "approved", "pending": "pending"}.get(issue.deduction_status, "pending"),
        })
    for advance in SalaryAdvance.objects.filter(employee=employee, status__in=[AdvanceStatus.PAID, AdvanceStatus.REPAID]):
        taken = AdvanceRepayment.objects.filter(advance=advance, payroll_period__year=year, payroll_period__month=month).first()
        if taken is not None:
            charges.append({"date": None, "kind": "Salary advance", "description": f"Repayment of the advance of {_money(advance.amount)}", "amount": _money(taken.amount), "status": "deducted"})
        elif advance.status == AdvanceStatus.PAID and (advance.deduct_from_year, advance.deduct_from_month) <= (year, month) and advance.balance > 0:
            charges.append({"date": None, "kind": "Salary advance", "description": f"Repayment of the advance of {_money(advance.amount)}", "amount": _money(min(advance.instalment, advance.balance)), "status": "expected"})
    charges.sort(key=lambda item: (item["date"] or "9999", item["kind"]))
    for item in charges:
        item["status_label"] = CHARGE_STATUS_LABELS.get(item["status"], item["status"])
    confirmed = sum((Decimal(item["amount"]) for item in charges if item["status"] in COUNTS_AS_CHARGED), Decimal("0"))
    pending = sum((Decimal(item["amount"]) for item in charges if item["status"] == "pending"), Decimal("0"))

    rewards = []
    for bonus in Bonus.objects.filter(employee=employee, performance_year=year, performance_month=month, status__in=[BonusStatus.PROPOSED, BonusStatus.APPROVED, BonusStatus.PAID]).select_related("reward_type"):
        rewards.append({
            "description": bonus.reward_type.name if bonus.reward_type_id else bonus.get_kind_display(),
            "amount": _money(bonus.amount),
            "status_label": {"proposed": "Awaiting approval", "approved": "Approved", "paid": "Added to pay"}[bonus.status],
        })

    return {
        "employee": {
            "id": employee.pk, "employee_id": employee.employee_id, "name": employee.full_name,
            "department": employee.department.name if employee.department_id else None,
            "position": employee.position.name if employee.position_id else None,
        },
        "period": {"year": year, "month": month, "label": f"{MONTHS[month]} {year}", "provisional": (year, month) == (today.year, today.month), "shown_until": shown_until.isoformat()},
        "attendance_summary": {
            "days_worked": counts["present"] + counts["late"] + counts["incomplete"],
            "present": counts["present"], "late": counts["late"], "absent": counts["absent"], "leave": counts["leave"],
            "incomplete": counts["incomplete"], "rest_days": counts["rest"],
            "scheduled_work_days": sum(1 for day, row in roster.items() if row.status == RosterDayStatus.WORK and day <= shown_until),
            "hours_worked": round(worked_minutes / 60, 2), "late_minutes": late_minutes,
        },
        "days": days,
        "attendance_cases": _attendance_cases(employee, first, shown_until, can_change),
        "can_change_cases": bool(can_change),
        "absent_dates": [day["date"] for day in days if day["status"] == "absent"],
        "attendance_deduction_note": (
            "Days absent and time late are deducted from pay by payroll at the standard rate. The amount is not shown on this statement."
            if counts["absent"] or late_minutes else None
        ),
        "charges": charges,
        "charges_total": _money(confirmed),
        "charges_pending_total": _money(pending),
        "rewards": rewards,
        "rewards_total": _money(sum((Decimal(item["amount"]) for item in rewards), Decimal("0"))),
    }

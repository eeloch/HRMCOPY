"""Roster-backed, reviewed attendance and leave payroll deductions."""

from dataclasses import asdict, dataclass, field
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP

from django.db import transaction

from attendance.models import AttendanceException, RosterDayStatus
from attendance.services.roster import IncompleteRosterError, expected_attendance_days
from audit.models import AuditSeverity
from audit.services import AuditService
from leave.models import LeaveDuration, LeaveRequest, LeaveStatus
from leave.services.balance import LeaveBalanceService
from payroll.models import EmployeePayroll, PayrollLineItem, PayrollLineItemType, PayrollPeriodStatus
from payroll.services.generation import recalculate_employee_payroll


LOCKED_PERIOD_STATUSES = {PayrollPeriodStatus.APPROVED, PayrollPeriodStatus.PAID, PayrollPeriodStatus.CLOSED}
MONEY = Decimal("0.01")
SYSTEM_SOURCE_TYPES = {"attendance_exception", "leave_request_day"}


@dataclass
class PayrollAttendanceSyncSummary:
    created: int = 0
    existing: int = 0
    updated: int = 0
    deleted: int = 0
    skipped: int = 0
    employees_processed: int = 0
    employees_skipped_incomplete_roster: int = 0
    expected_attendance_days: int = 0
    absence_deductions_created: int = 0
    absence_deductions_existing: int = 0
    lateness_deductions_created: int = 0
    lateness_deductions_existing: int = 0
    early_departure_deductions_created: int = 0
    early_departure_deductions_existing: int = 0
    unpaid_leave_deductions_created: int = 0
    unpaid_leave_deductions_existing: int = 0
    paid_leave_days_ignored: Decimal = Decimal("0.00")
    rest_days_ignored: int = 0
    pending: int = 0
    held: int = 0
    approved_absence: int = 0
    approved_lateness: int = 0
    approved_early_departure: int = 0
    approved_missing_punch: int = 0
    unpaid_leave: int = 0
    paid_leave: int = 0
    unsupported: int = 0
    roster_required: bool = False
    total_deduction_amount: Decimal = Decimal("0.00")
    incomplete_roster_employees: list = field(default_factory=list)
    errors: list = field(default_factory=list)

    def as_dict(self):
        return asdict(self)


def period_exceptions(period):
    return AttendanceException.objects.filter(attendance__date__range=(period.start_date, period.end_date)).select_related("attendance", "attendance__employee")


def pending_exception_count(period):
    return period_exceptions(period).filter(status="pending").count()


def _approved_leave_requests(period):
    return LeaveRequest.objects.filter(status__in=[LeaveStatus.APPROVED, LeaveStatus.PARTIALLY_APPROVED], start_date__lte=period.end_date, end_date__gte=period.start_date).select_related("employee", "leave_type")


def _leave_is_paid(leave_request):
    policy = LeaveBalanceService.resolve_policy(leave_request.employee, leave_request.leave_type, as_of=leave_request.approved_start_date or leave_request.start_date)
    return policy.is_paid if policy is not None else leave_request.leave_type.is_paid


def _approved_range(leave_request):
    return leave_request.approved_start_date or leave_request.start_date, leave_request.approved_end_date or leave_request.end_date


def _leave_day_factor(leave_request):
    return Decimal("0.50") if leave_request.duration_type in {LeaveDuration.FIRST_HALF, LeaveDuration.SECOND_HALF} else Decimal("1.00")


def daily_rate(basic_salary, expected_days):
    if expected_days <= 0:
        raise ValueError("Expected attendance must be greater than zero.")
    return (basic_salary / Decimal(expected_days)).quantize(MONEY, rounding=ROUND_HALF_UP)


def employee_expected_attendance(employee, period):
    try:
        return {"complete": True, "expected_days": expected_attendance_days(employee, period.start_date, period.end_date), "missing_dates": []}
    except IncompleteRosterError as error:
        return {"complete": False, "expected_days": None, "missing_dates": [value.isoformat() for value in error.missing_dates]}


def build_attendance_summary(period):
    summary = PayrollAttendanceSyncSummary()
    exceptions = period_exceptions(period)
    summary.pending = exceptions.filter(status="pending").count()
    summary.held = exceptions.filter(status="held").count()
    approved = exceptions.filter(status="approved")
    summary.approved_absence = approved.filter(exception_type="absence").count()
    summary.approved_lateness = approved.filter(exception_type="late").count()
    summary.approved_early_departure = approved.filter(exception_type="early_departure").count()
    summary.approved_missing_punch = approved.filter(exception_type__in=["missing_clock_in", "missing_clock_out"]).count()
    for leave_request in _approved_leave_requests(period):
        start, end = _approved_range(leave_request)
        if start <= period.end_date and end >= period.start_date:
            if _leave_is_paid(leave_request): summary.paid_leave += 1
            else: summary.unpaid_leave += 1
    summary.unsupported = summary.approved_missing_punch
    return summary


def _work_dates(employee, period):
    return {row.date for row in employee.roster_days.filter(date__range=(period.start_date, period.end_date), status=RosterDayStatus.WORK)}


def _leave_impacts(employee, period, work_dates, summary):
    covered_dates, unpaid_candidates = set(), []
    for leave_request in _approved_leave_requests(period).filter(employee=employee):
        start, end = _approved_range(leave_request)
        current, end = max(start, period.start_date), min(end, period.end_date)
        factor = _leave_day_factor(leave_request)
        while current <= end:
            if current not in work_dates:
                summary.rest_days_ignored += 1
            else:
                covered_dates.add(current)
                if _leave_is_paid(leave_request): summary.paid_leave_days_ignored += factor
                else: unpaid_candidates.append((leave_request, current, factor))
            current += timedelta(days=1)
    return covered_dates, unpaid_candidates


def _sync_system_line(payroll, *, source_type, source_reference, code, description, amount, metadata, summary, kind):
    line = PayrollLineItem.objects.filter(payroll=payroll, is_system_generated=True, source_type=source_type, source_reference=source_reference).first()
    values = {"item_type": PayrollLineItemType.DEDUCTION, "code": code, "description": description, "amount": amount, "metadata": metadata}
    if line is None:
        PayrollLineItem.objects.create(payroll=payroll, source_type=source_type, source_reference=source_reference, is_system_generated=True, **values)
        summary.created += 1
        if kind == "absence": summary.absence_deductions_created += 1
        elif kind == "unpaid_leave": summary.unpaid_leave_deductions_created += 1
        elif kind == "late": summary.lateness_deductions_created += 1
        else: summary.early_departure_deductions_created += 1
        summary.total_deduction_amount += amount
        return True
    if any(getattr(line, key) != value for key, value in values.items()):
        for key, value in values.items(): setattr(line, key, value)
        line.save(update_fields=[*values.keys()])
        summary.updated += 1
        summary.total_deduction_amount += amount
        return True
    summary.existing += 1
    if kind == "absence": summary.absence_deductions_existing += 1
    elif kind == "unpaid_leave": summary.unpaid_leave_deductions_existing += 1
    elif kind == "late": summary.lateness_deductions_existing += 1
    else: summary.early_departure_deductions_existing += 1
    return False


def attendance_exception_deduction(exception, rate):
    """Apply the approved fixed-band policy without recalculating attendance facts."""
    minutes = exception.minutes_affected
    if minutes <= 0:
        return None
    if minutes <= 15:
        return Decimal("300.00"), "1_15", {}
    if minutes <= 60:
        return Decimal("500.00"), "16_60", {}
    half_day_rate = (rate / Decimal("2")).quantize(MONEY, rounding=ROUND_HALF_UP)
    return half_day_rate, "over_60_half_day", {"daily_rate": str(rate), "half_day_rate": str(half_day_rate)}


def _sync_employee_payroll(payroll, summary):
    period, employee = payroll.payroll_period, payroll.employee
    attendance = employee_expected_attendance(employee, period)
    if not attendance["complete"]:
        summary.employees_skipped_incomplete_roster += 1
        summary.incomplete_roster_employees.append({"id": employee.id, "employee_id": employee.employee_id, "name": employee.full_name, "missing_dates": attendance["missing_dates"]})
        return
    expected_days = attendance["expected_days"]
    if not expected_days:
        summary.skipped += 1
        summary.errors.append({"employee_id": employee.employee_id, "detail": "Roster contains no scheduled work days."})
        return
    summary.employees_processed += 1
    summary.expected_attendance_days += expected_days
    rate, work_dates = daily_rate(payroll.basic_salary, expected_days), _work_dates(employee, period)
    leave_dates, unpaid_candidates = _leave_impacts(employee, period, work_dates, summary)
    eligible_sources, changed = set(), False
    for leave_request, work_date, factor in unpaid_candidates:
        reference, amount = f"{leave_request.pk}:{work_date.isoformat()}", (rate * factor).quantize(MONEY, rounding=ROUND_HALF_UP)
        eligible_sources.add(("leave_request_day", reference))
        changed |= _sync_system_line(payroll, source_type="leave_request_day", source_reference=reference, code="UNPAID_LEAVE", description=f"Unpaid Leave - {work_date.isoformat()}", amount=amount, metadata={"leave_request_id": leave_request.pk, "date": work_date.isoformat(), "expected_attendance_days": expected_days, "daily_rate": str(rate), "day_fraction": str(factor)}, summary=summary, kind="unpaid_leave")
    approved_absence_dates = set()
    for exception in period_exceptions(period).filter(attendance__employee=employee, status="approved", exception_type="absence"):
        work_date = exception.attendance.date
        if work_date not in work_dates:
            summary.rest_days_ignored += 1
            continue
        if work_date in leave_dates:
            continue
        reference = str(exception.pk)
        eligible_sources.add(("attendance_exception", reference))
        approved_absence_dates.add(work_date)
        changed |= _sync_system_line(payroll, source_type="attendance_exception", source_reference=reference, code="ATTENDANCE_ABSENCE", description=f"Unauthorized Absence - {work_date.isoformat()}", amount=rate, metadata={"attendance_exception_id": exception.pk, "attendance_date": work_date.isoformat(), "expected_attendance_days": expected_days, "daily_rate": str(rate)}, summary=summary, kind="absence")
    for exception in period_exceptions(period).filter(attendance__employee=employee, status="approved", exception_type__in=["late", "early_departure"]):
        work_date = exception.attendance.date
        if work_date not in work_dates:
            summary.rest_days_ignored += 1
            continue
        # Full-day absence and approved leave are the dominant date-level outcomes.
        if work_date in leave_dates or work_date in approved_absence_dates:
            continue
        deduction = attendance_exception_deduction(exception, rate)
        if deduction is None:
            continue
        amount, policy_band, rate_metadata = deduction
        is_late = exception.exception_type == "late"
        code = "ATTENDANCE_LATE" if is_late else "ATTENDANCE_EARLY_DEPARTURE"
        label = "Late Arrival" if is_late else "Early Departure"
        reference = str(exception.pk)
        eligible_sources.add(("attendance_exception", reference))
        changed |= _sync_system_line(
            payroll,
            source_type="attendance_exception",
            source_reference=reference,
            code=code,
            description=f"{label} - {work_date.isoformat()}",
            amount=amount,
            metadata={
                "attendance_exception_id": exception.pk,
                "attendance_date": work_date.isoformat(),
                "minutes_affected": exception.minutes_affected,
                "policy_band": policy_band,
                **rate_metadata,
            },
            summary=summary,
            kind="late" if is_late else "early_departure",
        )
    for line in PayrollLineItem.objects.filter(payroll=payroll, is_system_generated=True, source_type__in=SYSTEM_SOURCE_TYPES):
        if (line.source_type, line.source_reference) not in eligible_sources:
            line.delete(); summary.deleted += 1; changed = True
    if changed:
        recalculate_employee_payroll(payroll)


def sync_attendance_deductions_for_period(period, *, actor=None):
    if period.status in LOCKED_PERIOD_STATUSES:
        raise ValueError("Approved payroll periods cannot be synchronized.")
    summary = build_attendance_summary(period)
    with transaction.atomic():
        payrolls = EmployeePayroll.objects.select_for_update().filter(payroll_period=period).select_related("employee", "payroll_period")
        for payroll in payrolls:
            _sync_employee_payroll(payroll, summary)
        if summary.created or summary.updated or summary.deleted:
            AuditService.log(event_type="payroll.attendance_deductions_synced", module="payroll", actor=actor, object=period, severity=AuditSeverity.SUCCESS, title="Payroll attendance deductions synchronized", description=f"Attendance and leave deductions were synchronized for {period.display_name}.", metadata={"period": period.display_name, "employees_processed": summary.employees_processed, "absence_deductions_created": summary.absence_deductions_created, "lateness_deductions_created": summary.lateness_deductions_created, "early_departure_deductions_created": summary.early_departure_deductions_created, "unpaid_leave_deductions_created": summary.unpaid_leave_deductions_created, "total_deduction_amount": str(summary.total_deduction_amount)})
    return summary

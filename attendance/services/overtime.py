"""Approval-based overtime candidates and next-day payment records."""

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP

from django.db import transaction
from django.utils import timezone

from attendance.models import DailyAttendance, OvertimePaymentStatus, OvertimeRecord, OvertimeStatus
from attendance.services.roster import IncompleteRosterError, expected_attendance_days
from audit.models import AuditSeverity
from audit.services import AuditService
from payroll.models import EmployeePayroll, PayrollSetting


MONEY = Decimal("0.01")
DEFAULT_THRESHOLD_MINUTES = 60
DEFAULT_MULTIPLIER = Decimal("1.50")


def _setting_decimal(key, default):
    setting = PayrollSetting.objects.filter(key=key).first()
    if not setting:
        return default
    value = setting.value.get("value", setting.value) if isinstance(setting.value, dict) else setting.value
    try:
        return Decimal(str(value))
    except Exception:
        return default


def overtime_threshold_minutes():
    return max(0, int(_setting_decimal("overtime_threshold_minutes", Decimal(DEFAULT_THRESHOLD_MINUTES))))


def overtime_multiplier():
    return _setting_decimal("overtime_multiplier", DEFAULT_MULTIPLIER)


def scheduled_shift_minutes(attendance):
    return int((attendance.scheduled_end - attendance.scheduled_start).total_seconds() // 60)


@dataclass
class OvertimeSyncSummary:
    created: int = 0
    existing: int = 0
    changed_after_review: int = 0


def sync_overtime_for_date(work_date: date):
    """Create candidates only; reviewed financial decisions are deliberately immutable."""
    summary = OvertimeSyncSummary()
    threshold = overtime_threshold_minutes()
    attendance_rows = DailyAttendance.objects.filter(date=work_date, actual_clock_out__isnull=False, scheduled_end__isnull=False, shift__isnull=False).select_related("employee", "shift")
    for attendance in attendance_rows:
        after_shift = max(0, int((attendance.actual_clock_out - attendance.scheduled_end).total_seconds() // 60))
        potential = max(after_shift - threshold, 0)
        if potential <= 0:
            continue
        defaults = {
            "employee": attendance.employee,
            "shift": attendance.shift,
            "work_date": attendance.date,
            "scheduled_end": attendance.scheduled_end,
            "actual_clock_out": attendance.actual_clock_out,
            "threshold_minutes_snapshot": threshold,
            "potential_overtime_minutes": potential,
            "payment_due_date": attendance.date + timedelta(days=1),
        }
        record, created = OvertimeRecord.objects.get_or_create(attendance=attendance, defaults=defaults)
        if created:
            summary.created += 1
        elif record.status == OvertimeStatus.PENDING:
            summary.existing += 1
        elif (record.actual_clock_out, record.scheduled_end, record.potential_overtime_minutes) != (attendance.actual_clock_out, attendance.scheduled_end, potential):
            summary.changed_after_review += 1
    return summary


def _salary_snapshot(record):
    employee = record.employee
    payroll = EmployeePayroll.objects.filter(payroll_period__year=record.work_date.year, payroll_period__month=record.work_date.month, employee=employee).first()
    basic_salary = payroll.basic_salary if payroll else employee.basic_salary
    try:
        work_days = expected_attendance_days(employee, record.work_date.replace(day=1), (record.work_date.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1))
    except IncompleteRosterError as error:
        raise ValueError(f"Cannot calculate overtime payment: roster is incomplete ({len(error.missing_dates)} date(s) missing).")
    if not work_days:
        raise ValueError("Cannot calculate overtime payment: roster contains no work days.")
    daily_rate = (basic_salary / Decimal(work_days)).quantize(MONEY, rounding=ROUND_HALF_UP)
    shift_minutes = scheduled_shift_minutes(record.attendance)
    normal_hourly = (daily_rate / (Decimal(shift_minutes) / Decimal(60))).quantize(MONEY, rounding=ROUND_HALF_UP)
    multiplier = overtime_multiplier()
    overtime_hourly = (normal_hourly * multiplier).quantize(MONEY, rounding=ROUND_HALF_UP)
    payable = (overtime_hourly * Decimal(record.approved_overtime_minutes) / Decimal(60)).quantize(MONEY, rounding=ROUND_HALF_UP)
    return basic_salary, work_days, daily_rate, shift_minutes, normal_hourly, multiplier, overtime_hourly, payable


def approve_overtime(record_id, *, actor, approved_minutes=None, comment=""):
    with transaction.atomic():
        record = OvertimeRecord.objects.select_for_update().select_related("employee", "attendance").get(pk=record_id)
        if record.status != OvertimeStatus.PENDING or record.payment_status == OvertimePaymentStatus.PAID:
            raise ValueError("This overtime record can no longer be reviewed.")
        minutes = record.potential_overtime_minutes if approved_minutes is None else approved_minutes
        if minutes <= 0 or minutes > record.potential_overtime_minutes:
            raise ValueError("Approved overtime minutes must be greater than zero and cannot exceed the potential overtime minutes.")
        record.approved_overtime_minutes = minutes
        fields = _salary_snapshot(record)
        (record.basic_salary_snapshot, record.roster_work_days_snapshot, record.daily_rate_snapshot, record.scheduled_shift_minutes_snapshot, record.normal_hourly_rate_snapshot, record.overtime_multiplier_snapshot, record.overtime_hourly_rate_snapshot, record.payable_amount) = fields
        record.status, record.reviewed_by, record.reviewed_at, record.review_comment = OvertimeStatus.APPROVED, actor, timezone.now(), comment
        record.save()
        AuditService.log(event_type="attendance.overtime_approved", module="attendance", employee=record.employee, actor=actor, object=record, severity=AuditSeverity.SUCCESS, title="Overtime approved", description=f"Overtime approved for {record.employee.full_name}.", metadata={"work_date": record.work_date.isoformat(), "potential_minutes": record.potential_overtime_minutes, "approved_minutes": minutes, "payable_amount": str(record.payable_amount)})
    return record


def reject_overtime(record_id, *, actor, comment):
    if not comment.strip():
        raise ValueError("A rejection reason is required.")
    with transaction.atomic():
        record = OvertimeRecord.objects.select_for_update().get(pk=record_id)
        if record.status != OvertimeStatus.PENDING or record.payment_status == OvertimePaymentStatus.PAID:
            raise ValueError("This overtime record can no longer be reviewed.")
        record.status, record.reviewed_by, record.reviewed_at, record.review_comment = OvertimeStatus.REJECTED, actor, timezone.now(), comment.strip()
        record.save()
        AuditService.log(event_type="attendance.overtime_rejected", module="attendance", employee=record.employee, actor=actor, object=record, severity=AuditSeverity.WARNING, title="Overtime rejected", description=f"Overtime rejected for {record.employee.full_name}.", metadata={"work_date": record.work_date.isoformat(), "potential_minutes": record.potential_overtime_minutes, "comment": record.review_comment})
    return record


def mark_overtime_paid(record_id, *, actor, note=""):
    with transaction.atomic():
        record = OvertimeRecord.objects.select_for_update().get(pk=record_id)
        if record.status != OvertimeStatus.APPROVED or record.payment_status == OvertimePaymentStatus.PAID:
            raise ValueError("Only approved unpaid overtime can be marked paid.")
        record.payment_status, record.paid_at, record.paid_by, record.payment_note = OvertimePaymentStatus.PAID, timezone.now(), actor, note
        record.save()
        AuditService.log(event_type="attendance.overtime_paid", module="attendance", employee=record.employee, actor=actor, object=record, severity=AuditSeverity.SUCCESS, title="Overtime payment recorded", description=f"Overtime payment recorded for {record.employee.full_name}.", metadata={"work_date": record.work_date.isoformat(), "approved_minutes": record.approved_overtime_minutes, "payable_amount": str(record.payable_amount)})
    return record

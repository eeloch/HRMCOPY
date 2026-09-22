"""Aggregates one week's HR numbers - workforce, attendance, leave, new hires/exits, and meals - into the
data a weekly report (on screen or as a slide deck) needs. Read-only: nothing here writes to the database.
"""

from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal

from django.db.models import Count, Q
from django.utils import timezone

from attendance.models import DailyAttendance
from employees.models import Employee, EmploymentType
from leave.models import LeaveRequest, LeaveStatus
from meals.models import MealCollectionStatus
from meals.services import MealCollection


def monday_of(day):
    return day - timedelta(days=day.weekday())


def iso_week_number(day):
    return day.isocalendar()[1]


@dataclass
class DepartmentCount:
    name: str
    count: int


@dataclass
class DayAttendance:
    date: object
    present: int
    late: int
    absent: int


@dataclass
class PersonEntry:
    employee_id: str
    name: str
    department: str
    date: object


@dataclass
class WeeklyReportData:
    week_start: object
    week_end: object
    week_number: int
    generated_at: object

    # Workforce, as of the end of the week.
    total_employees: int = 0
    active_employees: int = 0
    inactive_employees: int = 0
    by_department: list = field(default_factory=list)
    by_employment_type: list = field(default_factory=list)
    new_hires: list = field(default_factory=list)
    exits: list = field(default_factory=list)

    # Attendance, Monday-Saturday of the week (the working days the roster expects).
    attendance_present: int = 0
    attendance_late: int = 0
    attendance_absent: int = 0
    attendance_on_leave: int = 0
    night_shift_records: int = 0
    overtime_records: int = 0
    attendance_by_day: list = field(default_factory=list)
    top_absentee_departments: list = field(default_factory=list)

    # Leave.
    leave_submitted: int = 0
    leave_approved: int = 0
    leave_rejected: int = 0
    leave_pending: int = 0
    leave_cancelled: int = 0
    leave_days_approved: Decimal = Decimal("0")
    leave_by_type: list = field(default_factory=list)

    # Meals.
    meal_collections: int = 0
    meal_excess: int = 0
    meal_within_entitlement: int = 0


def build_weekly_report(week_start=None):
    """Everything for the week starting on week_start (a Monday; defaults to the current week)."""
    today = timezone.localdate()
    week_start = monday_of(week_start or today)
    week_end = week_start + timedelta(days=6)
    working_end = week_start + timedelta(days=5)  # Saturday: the last working day most departments roster

    data = WeeklyReportData(
        week_start=week_start,
        week_end=week_end,
        week_number=iso_week_number(week_start),
        generated_at=timezone.now(),
    )

    # --- Workforce ---
    all_employees = Employee.objects.all()
    data.total_employees = all_employees.count()
    active = all_employees.filter(status="active")
    data.active_employees = active.count()
    data.inactive_employees = data.total_employees - data.active_employees

    data.by_department = [
        DepartmentCount(row["department__name"] or "No department", row["c"])
        for row in active.values("department__name").annotate(c=Count("id")).order_by("-c")
    ][:8]

    data.by_employment_type = [
        DepartmentCount(label, active.filter(employment_type=value).count())
        for value, label in EmploymentType.choices
    ]
    data.by_employment_type = [row for row in data.by_employment_type if row.count > 0]

    hires = Employee.objects.filter(employment_date__gte=week_start, employment_date__lte=week_end).select_related("department")
    data.new_hires = [
        PersonEntry(e.employee_id, e.full_name, e.department.name if e.department else "-", e.employment_date)
        for e in hires
    ]
    exits = Employee.objects.filter(exit_date__gte=week_start, exit_date__lte=week_end).select_related("department")
    data.exits = [
        PersonEntry(e.employee_id, e.full_name, e.department.name if e.department else "-", e.exit_date)
        for e in exits
    ]

    # --- Attendance ---
    week_attendance = DailyAttendance.objects.filter(date__range=(week_start, working_end)).select_related("employee", "employee__department", "shift")
    data.attendance_present = week_attendance.filter(status="present").count()
    data.attendance_late = week_attendance.filter(status="late").count()
    data.attendance_absent = week_attendance.filter(status="absent").count()
    data.attendance_on_leave = week_attendance.filter(status="leave").count()
    data.night_shift_records = week_attendance.filter(shift__is_overnight=True).count()
    data.overtime_records = week_attendance.filter(overtime_minutes__gt=0).count()

    for offset in range(6):
        day = week_start + timedelta(days=offset)
        day_rows = week_attendance.filter(date=day)
        data.attendance_by_day.append(DayAttendance(
            date=day,
            present=day_rows.filter(status="present").count(),
            late=day_rows.filter(status="late").count(),
            absent=day_rows.filter(status="absent").count(),
        ))

    data.top_absentee_departments = [
        DepartmentCount(row["employee__department__name"] or "No department", row["c"])
        for row in week_attendance.filter(status="absent").values("employee__department__name").annotate(c=Count("id")).order_by("-c")
    ][:5]

    # --- Leave ---
    submitted = LeaveRequest.objects.filter(created_at__date__range=(week_start, week_end))
    data.leave_submitted = submitted.count()
    data.leave_approved = submitted.filter(status__in=[LeaveStatus.APPROVED, LeaveStatus.PARTIALLY_APPROVED]).count()
    data.leave_rejected = submitted.filter(status=LeaveStatus.REJECTED).count()
    data.leave_pending = submitted.filter(status=LeaveStatus.PENDING).count()
    data.leave_cancelled = submitted.filter(status=LeaveStatus.CANCELLED).count()

    decided_this_week = LeaveRequest.objects.filter(
        approved_at__date__range=(week_start, week_end),
        status__in=[LeaveStatus.APPROVED, LeaveStatus.PARTIALLY_APPROVED],
    )
    data.leave_days_approved = sum((r.approved_days or r.total_days for r in decided_this_week), Decimal("0"))
    data.leave_by_type = [
        DepartmentCount(row["leave_type__name"], row["c"])
        for row in submitted.values("leave_type__name").annotate(c=Count("id")).order_by("-c")
    ]

    # --- Meals ---
    week_meals = MealCollection.objects.filter(work_date__range=(week_start, week_end), voided_at__isnull=True)
    data.meal_collections = week_meals.count()
    data.meal_excess = week_meals.filter(status=MealCollectionStatus.EXCESS).count()
    data.meal_within_entitlement = week_meals.filter(status=MealCollectionStatus.WITHIN).count()

    return data

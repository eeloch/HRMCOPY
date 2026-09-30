"""Aggregates one week's HR numbers - workforce, attendance, leave, new hires/exits, and meals - into the
data a weekly report (on screen or as a slide deck) needs. Read-only: nothing here writes to the database.
"""

from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal

from django.db.models import Count, Q, Sum
from django.utils import timezone

from accommodation.models import BuildingKind
from accommodation.services import build_overview as build_accommodation_overview
from attendance.models import DailyAttendance
from employees.models import Department, Employee, EmploymentType
from leave.models import LeaveRequest, LeaveStatus
from meals.models import MealCollectionStatus
from meals.services import MealCollection
from offences.models import EmployeeOffence, EmployeeOffenceStatus


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
class ComparisonRow:
    """One metric, the week before vs. the reported week - the shape both the headline chart and the
    department/leave-type breakdowns share, so growth% is computed in one place."""
    label: str
    previous: float
    current: float

    @property
    def growth_pct(self):
        if self.previous == 0:
            return 0.0 if self.current == 0 else 100.0
        return (self.current - self.previous) / self.previous * 100


@dataclass
class DepartmentApproved:
    """One department's actual headcount against its approved target (Department.required_staff)."""
    name: str
    current: int
    approved: int

    @property
    def diff(self):
        return self.current - self.approved

    @property
    def status(self):
        if self.approved == 0:
            return "No target set"
        if self.diff < 0:
            return "Understaffed"
        if self.diff > 0:
            return "Surplus"
        return "At target"


@dataclass
class GenderDepartmentRow:
    name: str
    male: int
    female: int


@dataclass
class BuildingOccupancy:
    name: str
    kind: str
    capacity: int
    occupied: int

    @property
    def vacant(self):
        return max(self.capacity - self.occupied, 0)

    @property
    def occupancy_rate(self):
        return (self.occupied / self.capacity * 100) if self.capacity else 0.0


@dataclass
class MealDayShift:
    date: object
    day_shift: int
    night_shift: int


@dataclass
class _PeriodMetrics:
    """The subset of a week's numbers needed only for week-over-week comparison - cheaper than the full
    WeeklyReportData, and computed identically for both the reported week and the one before it."""
    new_hires: int
    exits: int
    leave_submitted: int
    meal_collections: int
    attendance_present: int
    attendance_late: int
    attendance_absent: int
    department_present: dict
    leave_by_type: dict


def _attendance_rate(present, late, absent):
    total = present + late + absent
    if total == 0:
        return 0.0
    return (present + late) / total * 100


def _period_metrics(week_start):
    week_end = week_start + timedelta(days=6)
    working_end = week_start + timedelta(days=5)

    new_hires = Employee.objects.filter(employment_date__gte=week_start, employment_date__lte=week_end).count()
    exits = Employee.objects.filter(exit_date__gte=week_start, exit_date__lte=week_end).count()

    week_attendance = DailyAttendance.objects.filter(date__range=(week_start, working_end))
    department_present = {
        row["employee__department__name"] or "No department": row["c"]
        for row in week_attendance.filter(status="present").values("employee__department__name").annotate(c=Count("id"))
    }

    submitted = LeaveRequest.objects.filter(created_at__date__range=(week_start, week_end))
    leave_by_type = {
        row["leave_type__name"]: row["c"]
        for row in submitted.values("leave_type__name").annotate(c=Count("id"))
    }

    meal_collections = MealCollection.objects.filter(work_date__range=(week_start, week_end), voided_at__isnull=True).count()

    return _PeriodMetrics(
        new_hires=new_hires,
        exits=exits,
        leave_submitted=submitted.count(),
        meal_collections=meal_collections,
        attendance_present=week_attendance.filter(status="present").count(),
        attendance_late=week_attendance.filter(status="late").count(),
        attendance_absent=week_attendance.filter(status="absent").count(),
        department_present=department_present,
        leave_by_type=leave_by_type,
    )


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
    meal_total_cost: Decimal = Decimal("0")
    previous_meal_total_cost: Decimal = Decimal("0")
    meal_cost_per_ticket: Decimal = Decimal("0")
    meal_by_day_and_shift: list = field(default_factory=list)
    meal_cost_trend: list = field(default_factory=list)  # [(label, cost), ...] most recent 3 weeks

    # Employee overview rate formulas (opening/closing headcount already tracked as
    # previous_active_employees/active_employees below).
    retention_rate: float = 0.0
    hire_rate: float = 0.0
    attrition_rate: float = 0.0
    net_movement: int = 0

    # Department headcount vs Department.required_staff ("approved headcount").
    department_approved: list = field(default_factory=list)
    approved_headcount_total: int = 0
    pending_hires_total: int = 0
    surplus_employees_total: int = 0
    key_vacancy_departments: list = field(default_factory=list)  # top understaffed DepartmentApproved rows

    # Gender - a snapshot of the active workforce as of report generation, not week-scoped.
    gender_male: int = 0
    gender_female: int = 0
    gender_unspecified: int = 0
    gender_by_department: list = field(default_factory=list)

    # Accommodation - a live snapshot (who lives where right now), not week-scoped.
    accommodation_company: BuildingOccupancy = None
    accommodation_external: BuildingOccupancy = None
    accommodation_company_male: int = 0
    accommodation_company_female: int = 0
    accommodation_by_building: list = field(default_factory=list)

    # Disciplinary - from the Offences module (fines/incidents), the one part of "disciplinary actions"
    # this system actually tracks. Grievances, warnings and suspensions are not modelled here.
    offence_count: int = 0
    offence_total_amount: Decimal = Decimal("0")
    offence_by_status: list = field(default_factory=list)
    offence_by_type: list = field(default_factory=list)

    # Week-over-week comparison, against the week immediately before this one.
    previous_week_start: object = None
    previous_week_end: object = None
    previous_active_employees: int = 0
    attendance_rate: float = 0.0
    previous_attendance_rate: float = 0.0
    headline_comparison: list = field(default_factory=list)
    department_comparison: list = field(default_factory=list)
    leave_type_comparison: list = field(default_factory=list)


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
    week_meals = MealCollection.objects.filter(work_date__range=(week_start, week_end), voided_at__isnull=True).select_related("shift")
    data.meal_collections = week_meals.count()
    data.meal_excess = week_meals.filter(status=MealCollectionStatus.EXCESS).count()
    data.meal_within_entitlement = week_meals.filter(status=MealCollectionStatus.WITHIN).count()
    data.meal_total_cost = week_meals.aggregate(total=Sum("rate_snapshot"))["total"] or Decimal("0")
    data.meal_cost_per_ticket = (data.meal_total_cost / data.meal_collections) if data.meal_collections else Decimal("0")

    for offset in range(7):
        day = week_start + timedelta(days=offset)
        day_meals = week_meals.filter(work_date=day)
        data.meal_by_day_and_shift.append(MealDayShift(
            date=day,
            day_shift=day_meals.filter(shift__is_overnight=False).count(),
            night_shift=day_meals.filter(shift__is_overnight=True).count(),
        ))

    data.meal_cost_trend = []
    for weeks_back in (2, 1, 0):
        trend_start = week_start - timedelta(days=7 * weeks_back)
        trend_end = trend_start + timedelta(days=6)
        trend_cost = MealCollection.objects.filter(
            work_date__range=(trend_start, trend_end), voided_at__isnull=True,
        ).aggregate(total=Sum("rate_snapshot"))["total"] or Decimal("0")
        data.meal_cost_trend.append((f"Wk {iso_week_number(trend_start)}", trend_cost))
    data.previous_meal_total_cost = data.meal_cost_trend[1][1] if len(data.meal_cost_trend) > 1 else Decimal("0")

    # --- Department headcount vs approved target (Department.required_staff) ---
    current_department_headcount = {
        row["department__name"] or "No department": row["c"]
        for row in active.values("department__name").annotate(c=Count("id"))
    }
    for department in Department.objects.filter(required_staff__gt=0).order_by("-required_staff"):
        current = current_department_headcount.get(department.name, 0)
        data.department_approved.append(DepartmentApproved(department.name, current, department.required_staff))
    data.approved_headcount_total = sum(row.approved for row in data.department_approved)
    data.pending_hires_total = sum(-row.diff for row in data.department_approved if row.diff < 0)
    data.surplus_employees_total = sum(row.diff for row in data.department_approved if row.diff > 0)
    data.key_vacancy_departments = sorted(
        (row for row in data.department_approved if row.diff < 0), key=lambda row: row.diff,
    )[:5]

    # --- Gender - current snapshot, not week-scoped ---
    data.gender_male = active.filter(gender="male").count()
    data.gender_female = active.filter(gender="female").count()
    data.gender_unspecified = data.active_employees - data.gender_male - data.gender_female
    gender_department_rows = active.values("department__name").annotate(
        male=Count("id", filter=Q(gender="male")), female=Count("id", filter=Q(gender="female")),
    ).order_by("-male", "-female")
    data.gender_by_department = [
        GenderDepartmentRow(row["department__name"] or "No department", row["male"], row["female"])
        for row in gender_department_rows
    ][:10]

    # --- Accommodation - a live snapshot of who lives where right now ---
    overview = build_accommodation_overview()
    company_buildings = [b for b in overview["buildings"] if b["kind"] == BuildingKind.COMPANY]
    external_buildings = [b for b in overview["buildings"] if b["kind"] == BuildingKind.EXTERNAL]
    data.accommodation_company = BuildingOccupancy(
        "Company Accommodation", BuildingKind.COMPANY,
        sum(b["capacity"] for b in company_buildings), sum(b["occupied"] for b in company_buildings),
    )
    data.accommodation_external = BuildingOccupancy(
        "External Accommodation", BuildingKind.EXTERNAL,
        sum(b["capacity"] for b in external_buildings), sum(b["occupied"] for b in external_buildings),
    )
    data.accommodation_company_male = overview["summary"]["by_gender"].get("male", {}).get("occupied", 0)
    data.accommodation_company_female = overview["summary"]["by_gender"].get("female", {}).get("occupied", 0)
    data.accommodation_by_building = [
        BuildingOccupancy(b["name"], b["kind"], b["capacity"], b["occupied"])
        for b in overview["buildings"] if b["capacity"] or b["occupied"]
    ]

    # --- Disciplinary (Offences module - fines/incidents; no separate grievance tracking exists) ---
    week_offences = EmployeeOffence.objects.filter(created_at__date__range=(week_start, week_end)).select_related("offence_type")
    data.offence_count = week_offences.count()
    data.offence_total_amount = week_offences.aggregate(total=Sum("amount"))["total"] or Decimal("0")
    data.offence_by_status = [
        DepartmentCount(label, week_offences.filter(status=value).count())
        for value, label in EmployeeOffenceStatus.choices
    ]
    data.offence_by_status = [row for row in data.offence_by_status if row.count > 0]
    data.offence_by_type = [
        DepartmentCount(row["offence_type__name"], row["c"])
        for row in week_offences.values("offence_type__name").annotate(c=Count("id")).order_by("-c")
    ]

    # --- Week-over-week comparison ---
    data.previous_week_start = week_start - timedelta(days=7)
    data.previous_week_end = data.previous_week_start + timedelta(days=6)
    previous = _period_metrics(data.previous_week_start)

    data.attendance_rate = _attendance_rate(data.attendance_present, data.attendance_late, data.attendance_absent)
    data.previous_attendance_rate = _attendance_rate(previous.attendance_present, previous.attendance_late, previous.attendance_absent)
    # No historical headcount snapshot exists, so "active as of last week" is approximated from this
    # week's movements: undo the hires and restore the exits.
    data.previous_active_employees = max(data.active_employees - len(data.new_hires) + len(data.exits), 0)

    # Employee overview rate formulas, matching the reference report's own definitions.
    data.net_movement = len(data.new_hires) - len(data.exits)
    opening_headcount = data.previous_active_employees
    closing_headcount = data.active_employees
    data.retention_rate = ((closing_headcount - len(data.exits)) / closing_headcount * 100) if closing_headcount else 0.0
    data.hire_rate = (len(data.new_hires) / closing_headcount * 100) if closing_headcount else 0.0
    data.attrition_rate = (len(data.exits) / opening_headcount * 100) if opening_headcount else 0.0

    data.headline_comparison = [
        ComparisonRow("New Hires", previous.new_hires, len(data.new_hires)),
        ComparisonRow("Leave Requests", previous.leave_submitted, data.leave_submitted),
        ComparisonRow("Meal Tickets", previous.meal_collections, data.meal_collections),
        ComparisonRow("Present Records", previous.attendance_present, data.attendance_present),
        ComparisonRow("Absent Records", previous.attendance_absent, data.attendance_absent),
    ]

    current_department_present = {
        row["employee__department__name"] or "No department": row["c"]
        for row in week_attendance.filter(status="present").values("employee__department__name").annotate(c=Count("id"))
    }
    dept_names = set(current_department_present) | set(previous.department_present)
    data.department_comparison = sorted(
        (ComparisonRow(name, previous.department_present.get(name, 0), current_department_present.get(name, 0)) for name in dept_names),
        key=lambda row: row.current, reverse=True,
    )[:8]

    current_leave_by_type = {row.name: row.count for row in data.leave_by_type}
    leave_type_names = set(current_leave_by_type) | set(previous.leave_by_type)
    data.leave_type_comparison = sorted(
        (ComparisonRow(name, previous.leave_by_type.get(name, 0), current_leave_by_type.get(name, 0)) for name in leave_type_names),
        key=lambda row: row.current, reverse=True,
    )

    return data

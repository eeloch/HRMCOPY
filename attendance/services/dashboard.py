from datetime import timedelta

from django.db.models import Q
from django.utils import timezone

from attendance.models import (
    DailyAttendance,
    AttendanceException,
    AttendanceEvent,
    BiometricDevice,
    EmployeeRosterDay,
)

from employees.models import Department, Employee
from attendance.services.leave import approved_leave_employee_ids


# Every shift is either a day (morning) shift or an overnight (night) shift - Shift.is_overnight
# is the one field this whole page's two-column split is built on.
SHIFT_PERIODS = (("morning", False), ("night", True))


class DashboardService:
    """
    Workforce Operations Dashboard service.
    """

    @staticmethod
    def live_records(date, today):
        """This date's records. For today only, last night's still-running overnight shift is
        carried in too, so a night-shift worker who hasn't clocked out yet still shows as present."""
        if date != today:
            return DailyAttendance.objects.filter(date=date)
        now = timezone.now()
        return DailyAttendance.objects.filter(
            Q(date=date) | Q(date=date - timedelta(days=1), shift__is_overnight=True, scheduled_end__gt=now)
        )

    @staticmethod
    def get_dashboard(date=None):
        today = timezone.localdate()
        date = date or today

        by_period, late_employees, not_yet_in_employees, absent_employees = (
            DashboardService.get_period_breakdown(date, today)
        )

        return {
            "date": date.isoformat(),
            "is_today": date == today,
            "morning": by_period["morning"],
            "night": by_period["night"],
            "late_employees": late_employees,
            "not_yet_in_employees": not_yet_in_employees,
            "workforce_action_center": absent_employees,
            "department_readiness": DashboardService.get_department_readiness(date, today),
            "hostel_absentees": DashboardService.get_hostel_absentees(date),
            "recent_events": DashboardService.get_recent_events(),
            "device_status": DashboardService.get_device_status(),
        }

    @staticmethod
    def expected_ids(date, today, leave_employee_ids, is_overnight):
        """Employees rostered to work this shift period.

        For today, only those whose shift has actually started (and, for an overnight shift, not
        yet ended) count as expected "right now". For any other date - past or future - there is no
        "right now" to compare against, so the whole day's roster for that period is what applies.
        """
        base = EmployeeRosterDay.objects.filter(
            status="work", employee__status="active", shift__isnull=False, shift__is_overnight=is_overnight,
        )
        if date == today:
            clock = timezone.localtime().time()
            if is_overnight:
                query = base.filter(
                    Q(date=date, shift__start_time__lte=clock)
                    | Q(date=date - timedelta(days=1), shift__end_time__gt=clock)
                )
            else:
                query = base.filter(date=date, shift__start_time__lte=clock)
        else:
            query = base.filter(date=date)
        ids = query.values_list("employee_id", flat=True)
        return set(ids) - set(leave_employee_ids)

    @staticmethod
    def get_period_breakdown(date, today):
        """One pass building the morning/night summary cards and the three detail lists
        (late, not-yet-in, absent) that the cards drill into - each row tagged with its
        shift_period so the page can show only the period whose card was clicked."""
        leave_employee_ids = set(approved_leave_employee_ids(date))
        stored_leave_ids = set(
            DailyAttendance.objects.filter(date=date, status="leave").values_list("employee_id", flat=True)
        )

        # A leave day doesn't always leave a DailyAttendance row behind (see process_employee_attendance),
        # so on-leave people are attributed to a shift period from the roster, not the attendance record.
        roster_shift_map = dict(
            EmployeeRosterDay.objects.filter(date=date, shift__isnull=False)
            .values_list("employee_id", "shift__is_overnight")
        )

        records = DashboardService.live_records(date, today).select_related("employee", "employee__department", "shift")

        by_period = {}
        late_employees = []
        not_yet_in_employees = []
        absent_employees = []

        for period, is_overnight in SHIFT_PERIODS:
            period_records = records.filter(shift__is_overnight=is_overnight)

            conflict_ids = set(
                AttendanceException.objects.filter(
                    attendance__date=date,
                    exception_type="leave_punch_conflict",
                    attendance__shift__is_overnight=is_overnight,
                ).values_list("attendance__employee_id", flat=True)
            )

            on_leave_ids = {
                employee_id
                for employee_id in (leave_employee_ids | stored_leave_ids) - conflict_ids
                if roster_shift_map.get(employee_id) == is_overnight
            }

            expected_ids = DashboardService.expected_ids(date, today, leave_employee_ids, is_overnight)
            in_ids = set(
                period_records.filter(status__in=["present", "late", "incomplete"]).values_list("employee_id", flat=True)
            )
            # Once the day is judged in full, a missing punch becomes "absent", not "still to come" -
            # so a closed-out day never shows the same person in both counts.
            settled_absent_ids = set(
                period_records.filter(status="absent").values_list("employee_id", flat=True)
            )
            not_yet_in_ids = expected_ids - in_ids - settled_absent_ids

            by_period[period] = {
                "expected": len(expected_ids),
                "not_yet_in": len(not_yet_in_ids),
                "present": period_records.filter(status="present").exclude(employee_id__in=conflict_ids).count(),
                "late": period_records.filter(status="late").exclude(employee_id__in=conflict_ids).count(),
                "absent": period_records.filter(status="absent").exclude(employee_id__in=leave_employee_ids).count(),
                "conflicts": len(conflict_ids),
                "overtime": period_records.filter(overtime_minutes__gt=0).count(),
                "on_leave": len(on_leave_ids),
            }

            for record in period_records.filter(status="late").exclude(employee_id__in=conflict_ids).order_by("employee__first_name"):
                late_employees.append({
                    "shift_period": period,
                    "employee_id": record.employee.id,
                    "employee_number": record.employee.employee_id,
                    "employee_name": record.employee.full_name,
                    "department": record.employee.department.name if record.employee.department else None,
                    "shift": record.shift.name if record.shift else None,
                    "actual_clock_in": record.actual_clock_in,
                    "late_minutes": record.late_minutes,
                })

            for record in period_records.filter(status="absent").exclude(employee_id__in=leave_employee_ids).order_by("employee__first_name"):
                employee = record.employee
                absent_employees.append({
                    "shift_period": period,
                    "employee_id": employee.id,
                    "employee_number": employee.employee_id,
                    "employee_name": employee.full_name,
                    "department": employee.department.name if employee.department else None,
                    "shift": record.shift.name if record.shift else None,
                    "hostel": employee.lives_in_company_hostel,
                    "room": employee.hostel_room_number,
                    "status": record.status,
                })

            for employee in (
                Employee.objects.filter(id__in=not_yet_in_ids)
                .select_related("department")
                .order_by("department__name", "hostel_room_number", "first_name")
            ):
                not_yet_in_employees.append({
                    "shift_period": period,
                    "employee_id": employee.id,
                    "employee_number": employee.employee_id,
                    "employee_name": employee.full_name,
                    "department": employee.department.name if employee.department else None,
                    "hostel": employee.lives_in_company_hostel,
                    "room": employee.hostel_room_number,
                })

        return by_period, late_employees, not_yet_in_employees, absent_employees

    @staticmethod
    def get_department_readiness(date, today):

        departments = Department.objects.all().order_by("name")
        leave_ids = set(approved_leave_employee_ids(date))
        expected = (
            DashboardService.expected_ids(date, today, leave_ids, False)
            | DashboardService.expected_ids(date, today, leave_ids, True)
        )
        expected_by_department = {}
        for department_id in Employee.objects.filter(id__in=expected).values_list("department_id", flat=True):
            expected_by_department[department_id] = expected_by_department.get(department_id, 0) + 1

        results = []
        records = DashboardService.live_records(date, today)

        for department in departments:

            present = (
                records.filter(
                    employee__department=department,
                    status__in=["present", "late"],
                ).count()
            )

            # Departments with no target set are measured against who the roster expects at work right now.
            required = department.required_staff or expected_by_department.get(department.id, 0)

            short = max(required - present, 0)

            readiness = (
                round((present / required) * 100, 1)
                if required > 0
                else 0
            )

            results.append(
                {
                    "department": department.name,
                    "required": required,
                    "present": present,
                    "short": short,
                    "readiness": readiness,
                }
            )

        return results

    @staticmethod
    def get_hostel_absentees(date):

        leave_employee_ids = approved_leave_employee_ids(date)

        attendance = (
            DailyAttendance.objects
            .select_related(
                "employee",
                "employee__department",
            )
            .filter(
                date=date,
                status="absent",
                employee__lives_in_company_hostel=True,
            )
            .exclude(employee_id__in=leave_employee_ids)
            .order_by(
                "employee__first_name",
            )
        )

        results = []

        for record in attendance:

            employee = record.employee

            results.append(
                {
                    "employee_id": employee.id,
                    "employee_name": employee.full_name,
                    "department": (
                        employee.department.name
                        if employee.department
                        else None
                    ),
                    "room": employee.hostel_room_number,
                }
            )

        return results

    @staticmethod
    def get_recent_events():
        """The latest punches, newest first, so a test at the terminal shows up straight away.
        This is a live feed, not scoped to the dashboard's date filter."""
        events = AttendanceEvent.objects.select_related("employee", "employee__department", "device").order_by("-timestamp")[:20]
        return [
            {
                "employee_number": e.employee.employee_id,
                "employee_name": e.employee.full_name,
                "department": e.employee.department.name if e.employee.department else None,
                "device": e.device.name if e.device else None,
                "timestamp": e.timestamp,
            }
            for e in events
        ]

    @staticmethod
    def get_device_status():
        return [
            {"name": d.name, "purpose": d.purpose, "online": d.is_reachable, "last_sync_at": d.last_sync_at}
            for d in BiometricDevice.objects.order_by("name")
        ]

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


class DashboardService:
    """
    Workforce Operations Dashboard service.
    """

    @staticmethod
    def live_records(today):
        """Today's records plus last night's overnight shift while it is still running."""
        now = timezone.now()
        return DailyAttendance.objects.filter(
            Q(date=today) | Q(date=today - timedelta(days=1), shift__is_overnight=True, scheduled_end__gt=now)
        )

    @staticmethod
    def get_dashboard():
        today = timezone.localdate()

        return {
            "summary": DashboardService.get_summary(today),
            "department_readiness": DashboardService.get_department_readiness(today),
            "workforce_action_center": DashboardService.get_absent_employees(today),
            "not_yet_in_by_department": DashboardService.get_not_yet_in_by_department(today),
            "not_yet_in_employees": DashboardService.get_not_yet_in_employees(today),
            "late_employees": DashboardService.get_late_employees(today),
            "recent_events": DashboardService.get_recent_events(),
            "device_status": DashboardService.get_device_status(),
            "hostel_absentees": DashboardService.get_hostel_absentees(today),
        }

    @staticmethod
    def get_summary(today):
        attendance = DashboardService.live_records(today).select_related("shift")

        leave_employee_ids = set(approved_leave_employee_ids(today))
        conflict_employee_ids = set(AttendanceException.objects.filter(attendance__date=today, exception_type="leave_punch_conflict").values_list("attendance__employee_id", flat=True))
        stored_leave_employee_ids = set(
            attendance.filter(status="leave").values_list("employee_id", flat=True)
        )

        return {
            "present": attendance.filter(
                status="present"
            ).exclude(employee_id__in=conflict_employee_ids).count(),

            "late": attendance.filter(
                status="late"
            ).exclude(employee_id__in=conflict_employee_ids).count(),

            "absent": attendance.filter(
                status="absent"
            ).exclude(employee_id__in=leave_employee_ids).count(),

            "on_leave": len((leave_employee_ids | stored_leave_employee_ids) - conflict_employee_ids),
            "conflicts": len(conflict_employee_ids),

            "night_shift": attendance.filter(
                shift__is_overnight=True
            ).count(),

            **DashboardService.expected_now(today, attendance, leave_employee_ids),

            "overtime": attendance.filter(
                overtime_minutes__gt=0
            ).count(),
        }
    @staticmethod
    def expected_ids(today, leave_employee_ids):
        """Employees rostered to be at work right now (their shift has started and not yet ended)."""
        clock = timezone.localtime().time()
        started = EmployeeRosterDay.objects.filter(status="work", employee__status="active", shift__isnull=False).filter(
            Q(date=today, shift__start_time__lte=clock)
            | Q(date=today - timedelta(days=1), shift__is_overnight=True, shift__end_time__gt=clock)
        ).values_list("employee_id", flat=True)
        return set(started) - set(leave_employee_ids)

    @staticmethod
    def get_not_yet_in_by_department(today):
        """Per department: how many are expected now, how many are in, how many still to come."""
        leave_ids = set(approved_leave_employee_ids(today))
        expected = DashboardService.expected_ids(today, leave_ids)
        in_ids = set(
            DashboardService.live_records(today)
            .filter(status__in=["present", "late", "incomplete"])
            .values_list("employee_id", flat=True)
        )
        names = dict(Employee.objects.filter(id__in=expected).values_list("id", "department__name"))
        rows = {}
        for employee_id in expected:
            name = names.get(employee_id) or "No department"
            row = rows.setdefault(name, {"department": name, "expected": 0, "in": 0, "not_yet_in": 0})
            row["expected"] += 1
            if employee_id in in_ids:
                row["in"] += 1
            else:
                row["not_yet_in"] += 1
        return sorted(rows.values(), key=lambda r: (-r["not_yet_in"], r["department"]))

    @staticmethod
    def get_not_yet_in_employees(today):
        """Who is expected at work right now but hasn't punched in - with room numbers, so a physical
        search of the hostel is possible."""
        leave_ids = set(approved_leave_employee_ids(today))
        expected = DashboardService.expected_ids(today, leave_ids)
        in_ids = set(
            DashboardService.live_records(today)
            .filter(status__in=["present", "late", "incomplete"])
            .values_list("employee_id", flat=True)
        )
        missing_ids = expected - in_ids
        employees = (
            Employee.objects.filter(id__in=missing_ids)
            .select_related("department")
            .order_by("department__name", "hostel_room_number", "first_name")
        )
        return [
            {
                "employee_id": employee.id,
                "employee_number": employee.employee_id,
                "employee_name": employee.full_name,
                "department": employee.department.name if employee.department else None,
                "hostel": employee.lives_in_company_hostel,
                "room": employee.hostel_room_number,
            }
            for employee in employees
        ]

    @staticmethod
    def get_late_employees(today):
        """Everyone counted in the 'Late' summary card, one row each - for the on-page table, not a report."""
        conflict_employee_ids = set(
            AttendanceException.objects.filter(
                attendance__date=today, exception_type="leave_punch_conflict"
            ).values_list("attendance__employee_id", flat=True)
        )
        attendance = (
            DashboardService.live_records(today)
            .select_related("employee", "employee__department", "shift")
            .filter(status="late")
            .exclude(employee_id__in=conflict_employee_ids)
            .order_by("employee__first_name")
        )
        return [
            {
                "employee_id": record.employee.id,
                "employee_number": record.employee.employee_id,
                "employee_name": record.employee.full_name,
                "department": record.employee.department.name if record.employee.department else None,
                "shift": record.shift.name if record.shift else None,
                "actual_clock_in": record.actual_clock_in,
                "late_minutes": record.late_minutes,
            }
            for record in attendance
        ]

    @staticmethod
    def expected_now(today, attendance, leave_employee_ids):
        """Who is rostered to be at work right now, and who of them has not punched in yet."""
        expected = DashboardService.expected_ids(today, leave_employee_ids)
        in_ids = set(attendance.filter(status__in=["present", "late", "incomplete"]).values_list("employee_id", flat=True))
        return {"expected": len(expected), "not_yet_in": len(expected - in_ids)}

    @staticmethod
    def get_absent_employees(today):

        leave_employee_ids = approved_leave_employee_ids(today)

        attendance = (
            DailyAttendance.objects
            .select_related(
                "employee",
                "employee__department",
                "shift",
            )
            .filter(
                date=today,
                status="absent",
            )
            .exclude(employee_id__in=leave_employee_ids)
            .order_by(
                "employee__first_name",
            )
        )

        results = []

        for record in attendance:

            employee = record.employee

            results.append({
                "employee_id": employee.id,
                "employee_number": employee.employee_id,
                "employee_name": employee.full_name,
                "department": (
                    employee.department.name
                    if employee.department
                    else None
                ),
                "shift": (
                    record.shift.name
                    if record.shift
                    else None
                ),
                "hostel": employee.lives_in_company_hostel,
                "room": employee.hostel_room_number,
                "status": record.status,
            })

        return results


    @staticmethod
    def get_department_readiness(today):

        departments = Department.objects.all().order_by("name")
        expected = DashboardService.expected_ids(today, set(approved_leave_employee_ids(today)))
        expected_by_department = {}
        for department_id in Employee.objects.filter(id__in=expected).values_list("department_id", flat=True):
            expected_by_department[department_id] = expected_by_department.get(department_id, 0) + 1

        results = []

        for department in departments:

            present = (
                DashboardService.live_records(today).filter(
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
    def get_hostel_absentees(today):

        leave_employee_ids = approved_leave_employee_ids(today)

        attendance = (
            DailyAttendance.objects
            .select_related(
                "employee",
                "employee__department",
            )
            .filter(
                date=today,
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
        """The latest punches, newest first, so a test at the terminal shows up straight away."""
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

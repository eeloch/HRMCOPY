from datetime import date

from attendance.models import (
    DailyAttendance,
    AttendanceException,
    AttendanceEvent,
    BiometricDevice,
)

from employees.models import Department
from attendance.services.leave import approved_leave_employee_ids


class DashboardService:
    """
    Workforce Operations Dashboard service.
    """

    @staticmethod
    def get_dashboard():
        today = date.today()

        return {
            "summary": DashboardService.get_summary(today),
            "department_readiness": DashboardService.get_department_readiness(today),
            "workforce_action_center": DashboardService.get_absent_employees(today),
            "late_employees": DashboardService.get_late_employees(today),
            "attendance_exceptions": DashboardService.get_exceptions(today),
            "recent_events": DashboardService.get_recent_events(),
            "device_status": DashboardService.get_device_status(),
            "hostel_absentees": DashboardService.get_hostel_absentees(today),
        }

    @staticmethod
    def get_summary(today):
        attendance = DailyAttendance.objects.select_related(
            "shift"
        ).filter(
            date=today
        )

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

            "overtime": attendance.filter(
                overtime_minutes__gt=0
            ).count(),
        }
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

        results = []

        for department in departments:

            present = (
                DailyAttendance.objects.filter(
                    date=today,
                    employee__department=department,
                    status="present",
                ).count()
            )

            required = department.required_staff

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
    def get_late_employees(today):
        return []

    @staticmethod
    def get_exceptions(today):
        return []

    @staticmethod
    def get_recent_events():
        return []

    @staticmethod
    def get_device_status():
        return []

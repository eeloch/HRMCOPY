from django.utils import timezone

from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from django.db.models import Prefetch

from attendance.models import AttendanceException, DailyAttendance
from attendance.serializers import DailyAttendanceSerializer, EmployeeAttendanceHistorySerializer
from attendance.services.leave import approved_leave_employee_ids
from employees.models import Employee


class TodayAttendanceAPIView(APIView):

    permission_classes = [
        IsAuthenticated,
    ]

    def get(self, request):

        today = timezone.localdate()

        records = (
            DailyAttendance.objects
            .filter(date=today)
            .select_related(
                "employee",
                "employee__department",
                "shift",
            )
            .order_by(
                "employee__first_name",
                "employee__last_name",
            )
        )

        leave_employee_ids = set(approved_leave_employee_ids(today))
        serializer = DailyAttendanceSerializer(
            records,
            many=True,
        )
        results = serializer.data
        record_employee_ids = set(records.values_list("employee_id", flat=True))
        leave_employee_numbers = set(
            Employee.objects.filter(pk__in=leave_employee_ids).values_list(
                "employee_id",
                flat=True,
            )
        )

        conflict_employee_numbers = set(AttendanceException.objects.filter(attendance__date=today, exception_type="leave_punch_conflict").values_list("attendance__employee__employee_id", flat=True))
        # Preserve stored punch facts while exposing leave and punch conflicts operationally.
        for record in results:
            if record["employee_id"] in conflict_employee_numbers:
                record["status"] = "leave_punch_conflict"
            elif record["employee_id"] in leave_employee_numbers:
                record["status"] = "leave"

        leave_only_employees = Employee.objects.filter(
            pk__in=leave_employee_ids - record_employee_ids
        ).select_related("department").order_by("first_name", "last_name")
        results.extend(
            {
                "id": None,
                "employee_id": employee.employee_id,
                "employee_name": employee.full_name,
                "date": today,
                "shift_name": None,
                "scheduled_start": None,
                "scheduled_end": None,
                "actual_clock_in": None,
                "actual_clock_out": None,
                "late_minutes": 0,
                "early_departure_minutes": 0,
                "worked_minutes": 0,
                "overtime_minutes": 0,
                "status": "leave",
            }
            for employee in leave_only_employees
        )

        return Response({
            "date": today,
            "count": len(results),
            "results": results,
        })


class EmployeeAttendanceHistoryAPIView(APIView):
    """Read-only processed attendance history for one employee profile."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        employee_id = request.query_params.get("employee")
        if not employee_id:
            return Response(
                {"detail": "An employee query parameter is required."},
                status=400,
            )

        records = (
            DailyAttendance.objects.filter(employee_id=employee_id)
            .select_related("employee", "shift")
            .prefetch_related(
                Prefetch(
                    "exceptions",
                    queryset=AttendanceException.objects.filter(
                        exception_type="leave_punch_conflict"
                    ),
                    to_attr="leave_punch_conflicts",
                )
            )
            .order_by("-date", "-id")
        )
        return Response(
            {
                "count": records.count(),
                "results": EmployeeAttendanceHistorySerializer(records, many=True).data,
            }
        )

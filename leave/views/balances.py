from django.shortcuts import get_object_or_404

from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from employees.models import Employee
from leave.models import LeaveType
from leave.serializers import LeaveTypeSerializer
from leave.services.balance import LeaveBalanceService


class LeaveTypeListAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        leave_types = LeaveType.objects.filter(is_active=True).order_by("name")
        return Response(
            {
                "count": leave_types.count(),
                "results": LeaveTypeSerializer(leave_types, many=True).data,
            }
        )


class LeaveBalanceAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, employee_id):
        employee = get_object_or_404(
            Employee.objects.select_related("department", "position"),
            pk=employee_id,
        )
        leave_type_id = request.query_params.get("leave_type")
        year = request.query_params.get("year")

        try:
            year = int(year) if year else None
        except ValueError:
            return Response(
                {"detail": "Year must be a valid number."},
                status=400,
            )

        leave_types = LeaveType.objects.filter(is_active=True).order_by("name")
        if leave_type_id:
            leave_types = leave_types.filter(pk=leave_type_id)

        balances = [
            self._serialize_balance(
                leave_type,
                LeaveBalanceService.get_balance(employee, leave_type, year=year),
            )
            for leave_type in leave_types
        ]

        return Response(
            {
                "employee_id": employee.pk,
                "employee_number": employee.employee_id,
                "count": len(balances),
                "results": balances,
            }
        )

    @staticmethod
    def _serialize_balance(leave_type, balance):
        policy = balance["matched_policy"]
        return {
            "leave_type": leave_type.pk,
            "leave_type_name": leave_type.name,
            "allocated_days": balance["allocated_days"],
            "used_days": balance["used_days"],
            "remaining_days": balance["remaining_days"],
            "matched_policy": policy.pk if policy else None,
        }

from django.shortcuts import get_object_or_404

from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from leave.models import LeaveRequest, LeaveStatus
from leave.serializers import LeaveRequestCreateSerializer, LeaveRequestSerializer
from leave.services.request import LeaveRequestService
from audit.models import AuditSeverity
from audit.services import AuditService


class LeaveRequestCreateAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = LeaveRequestCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        result = LeaveRequestService.create_request(
            **serializer.validated_data,
            requested_by=request.user,
        )
        if not result["success"]:
            return Response(
                {
                    "success": False,
                    "errors": result["errors"],
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        leave_request = result["leave_request"]
        AuditService.log(
            event_type="leave.requested",
            module="leave",
            employee=leave_request.employee,
            actor=request.user,
            object=leave_request,
            severity=AuditSeverity.SUCCESS,
            title="Leave requested",
            description=(
                f"{leave_request.employee.full_name} requested "
                f"{leave_request.leave_type.name} leave."
            ),
            metadata={
                "request_number": leave_request.request_number,
                "start_date": leave_request.start_date.isoformat(),
                "end_date": leave_request.end_date.isoformat(),
                "total_days": str(leave_request.total_days),
            },
        )

        return Response(
            {
                "success": True,
                "result": LeaveRequestSerializer(leave_request).data,
            },
            status=status.HTTP_201_CREATED,
        )


class LeaveRequestListAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        leave_requests = self._get_queryset(request)
        return Response(
            {
                "count": leave_requests.count(),
                "results": LeaveRequestSerializer(leave_requests, many=True).data,
            }
        )

    @staticmethod
    def _get_queryset(request):
        leave_requests = LeaveRequest.objects.select_related(
            "employee",
            "employee__department",
            "leave_type",
            "requested_by",
            "approved_by",
        ).order_by("-created_at")

        employee_id = request.query_params.get("employee_id")
        status_filter = request.query_params.get("status")
        if employee_id:
            leave_requests = leave_requests.filter(employee_id=employee_id)
        if status_filter:
            leave_requests = leave_requests.filter(status=status_filter)

        return leave_requests


class LeaveRequestDetailAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, request_id):
        leave_request = get_object_or_404(
            LeaveRequest.objects.select_related(
                "employee",
                "employee__department",
                "leave_type",
                "requested_by",
                "approved_by",
            ),
            pk=request_id,
        )
        return Response({"result": LeaveRequestSerializer(leave_request).data})


class PendingLeaveRequestListAPIView(LeaveRequestListAPIView):
    def get(self, request):
        leave_requests = self._get_queryset(request).filter(status=LeaveStatus.PENDING)
        return Response(
            {
                "count": leave_requests.count(),
                "results": LeaveRequestSerializer(leave_requests, many=True).data,
            }
        )

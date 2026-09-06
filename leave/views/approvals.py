from django.http import Http404

from rest_framework import status
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from audit.models import AuditSeverity
from audit.services import AuditService
from leave.models import LeaveRequest, LeaveStatus
from leave.serializers import (
    LeaveRejectionSerializer,
    LeaveRequestSerializer,
    PartialLeaveApprovalSerializer,
)
from leave.services.approval import LeaveApprovalError, LeaveApprovalService
from notifications.models import NotificationSeverity
from notifications.services import NotificationService


class CanApproveLeave(BasePermission):
    def has_permission(self, request, view):
        return request.user.has_perm("leave.approve_leave")


class LeaveDecisionAPIView(APIView):
    permission_classes = [IsAuthenticated, CanApproveLeave]
    decision = None

    def post(self, request, request_id):
        serializer = self.get_input_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            leave_request = self.decide(request_id, request.user, serializer.validated_data)
        except LeaveRequest.DoesNotExist as error:
            raise Http404 from error
        except LeaveApprovalError as error:
            return Response({"detail": str(error)}, status=status.HTTP_400_BAD_REQUEST)

        self._record_outcome(leave_request, request.user)
        return Response({"result": LeaveRequestSerializer(leave_request).data})

    def _record_outcome(self, leave_request, actor):
        event_type, severity, title = {
            LeaveStatus.APPROVED: ("leave.approved", AuditSeverity.SUCCESS, "Leave approved"),
            LeaveStatus.PARTIALLY_APPROVED: ("leave.partially_approved", AuditSeverity.SUCCESS, "Leave partially approved"),
            LeaveStatus.REJECTED: ("leave.rejected", AuditSeverity.WARNING, "Leave rejected"),
        }[leave_request.status]
        metadata = {
            "request_number": leave_request.request_number,
            "leave_type": leave_request.leave_type.name,
            "original_total_days": str(leave_request.total_days),
            "approved_days": str(leave_request.approved_days) if leave_request.approved_days is not None else None,
            "start_date": leave_request.start_date.isoformat(),
            "end_date": leave_request.end_date.isoformat(),
            "approved_start_date": leave_request.approved_start_date.isoformat() if leave_request.approved_start_date else None,
            "approved_end_date": leave_request.approved_end_date.isoformat() if leave_request.approved_end_date else None,
        }
        AuditService.log(event_type=event_type, module="leave", employee=leave_request.employee, actor=actor, object=leave_request, severity=severity, title=title, description=f"{leave_request.leave_type.name} request {leave_request.request_number} was {leave_request.get_status_display().lower()}.", metadata=metadata)
        if leave_request.requested_by:
            NotificationService.create(recipient=leave_request.requested_by, event_type=event_type, title=f"{leave_request.leave_type.name} {leave_request.get_status_display()}", message=f"Your leave request {leave_request.request_number} was {leave_request.get_status_display().lower()}.", severity=NotificationSeverity.WARNING if leave_request.status == LeaveStatus.REJECTED else NotificationSeverity.SUCCESS, employee=leave_request.employee, related_url="/leave/requests", metadata=metadata)


class LeaveApproveAPIView(LeaveDecisionAPIView):
    def get_input_serializer(self, *args, **kwargs):
        from rest_framework import serializers
        return serializers.Serializer(*args, **kwargs)

    def decide(self, request_id, user, validated_data):
        return LeaveApprovalService.approve(request_id, user)


class LeavePartialApproveAPIView(LeaveDecisionAPIView):
    def get_input_serializer(self, *args, **kwargs):
        return PartialLeaveApprovalSerializer(*args, **kwargs)

    def decide(self, request_id, user, validated_data):
        return LeaveApprovalService.partially_approve(request_id, user, **validated_data)


class LeaveRejectAPIView(LeaveDecisionAPIView):
    def get_input_serializer(self, *args, **kwargs):
        return LeaveRejectionSerializer(*args, **kwargs)

    def decide(self, request_id, user, validated_data):
        return LeaveApprovalService.reject(request_id, user, **validated_data)

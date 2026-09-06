from django.db import transaction

from rest_framework import status
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from attendance.models import AttendanceException
from attendance.serializers import (
    AttendanceExceptionSerializer,
    ExceptionDecisionSerializer,
)
from audit.models import AuditSeverity
from audit.services import AuditService
from meals.services import MealService


class CanReviewAttendanceExceptions(BasePermission):
    def has_permission(self, request, view):
        return request.user.has_perm("attendance.review_attendanceexception")


class AttendanceExceptionListAPIView(APIView):

    permission_classes = [
        IsAuthenticated,
    ]

    def get(self, request):

        queryset = (
            AttendanceException.objects
            .select_related(
                "attendance",
                "attendance__employee",
                "attendance__employee__department",
                "attendance__shift",
            )
            .order_by(
                "-created_at",
            )
        )

        exception_status = request.query_params.get(
            "status"
        )

        exception_type = request.query_params.get(
            "type"
        )

        if exception_status:
            queryset = queryset.filter(
                status=exception_status
            )

        if exception_type:
            queryset = queryset.filter(
                exception_type=exception_type
            )

        serializer = AttendanceExceptionSerializer(
            queryset,
            many=True,
        )

        return Response({
            "count": queryset.count(),
            "results": serializer.data,
        })


class PendingExceptionAPIView(APIView):

    permission_classes = [
        IsAuthenticated,
    ]

    def get(self, request):

        queryset = (
            AttendanceException.objects
            .filter(status="pending")
            .select_related(
                "attendance",
                "attendance__employee",
                "attendance__employee__department",
                "attendance__shift",
            )
            .order_by(
                "attendance__date",
                "attendance__employee__first_name",
            )
        )

        serializer = AttendanceExceptionSerializer(
            queryset,
            many=True,
        )

        total_proposed_deduction = sum(
            item.proposed_deduction
            for item in queryset
        )

        return Response({
            "count": queryset.count(),
            "total_proposed_deduction": total_proposed_deduction,
            "results": serializer.data,
        })


class ExceptionDecisionAPIView(APIView):

    permission_classes = [
        IsAuthenticated,
        CanReviewAttendanceExceptions,
    ]

    def post(self, request, exception_id):

        try:
            with transaction.atomic():
                exception = (
                    AttendanceException.objects
                    .select_for_update()
                    .select_related(
                        "attendance",
                        "attendance__employee",
                    )
                    .get(id=exception_id)
                )

                serializer = ExceptionDecisionSerializer(
                    exception,
                    data=request.data,
                    context={
                        "request": request,
                    },
                )

                serializer.is_valid(
                    raise_exception=True
                )

                serializer.save()
                if (
                    exception.exception_type == "absence"
                    and exception.status == "approved"
                ):
                    MealService.sync_absence_penalties(
                        exception.attendance.employee,
                        exception.attendance.date,
                        actor=request.user,
                    )
                self._log_decision(exception, request.user)

        except AttendanceException.DoesNotExist:

            return Response(
                {
                    "detail": "Attendance exception not found."
                },
                status=status.HTTP_404_NOT_FOUND,
            )

        result = AttendanceExceptionSerializer(
            exception
        )

        return Response({
            "message": "Attendance exception reviewed successfully.",
            "exception": result.data,
        })

    @staticmethod
    def _log_decision(exception, reviewer):
        decision = exception.status
        severity = (
            AuditSeverity.SUCCESS
            if decision == "approved"
            else AuditSeverity.WARNING
        )
        AuditService.log(
            event_type=f"attendance.exception_{decision}",
            module="attendance",
            employee=exception.attendance.employee,
            actor=reviewer,
            object=exception,
            severity=severity,
            title=f"Attendance exception {decision}",
            description=(
                f"{exception.get_exception_type_display()} was {decision} "
                f"by {exception.reviewed_by}."
            ),
            metadata={
                "exception_id": exception.pk,
                "exception_type": exception.exception_type,
                "attendance_date": exception.attendance.date.isoformat(),
                "decision": decision,
                "reviewer": exception.reviewed_by,
                "proposed_deduction": str(exception.proposed_deduction),
                "comment": exception.admin_comment,
            },
        )

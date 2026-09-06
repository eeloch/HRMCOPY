from datetime import timedelta

from django.db import transaction

from rest_framework import status
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from attendance.models import Shift, ShiftAssignment
from attendance.serializers import (
    ShiftAssignmentSerializer,
    ShiftChangeSerializer,
    ShiftSerializer,
)


class CanManageShifts(BasePermission):
    def has_permission(self, request, view):
        return request.user.has_perm("attendance.manage_shifts")


class ShiftListAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        shifts = Shift.objects.filter(active=True).order_by("start_time", "name")
        return Response({"count": shifts.count(), "results": ShiftSerializer(shifts, many=True).data})


class ShiftAssignmentListCreateAPIView(APIView):
    def get_permissions(self):
        if self.request.method == "POST":
            return [IsAuthenticated(), CanManageShifts()]
        return [IsAuthenticated()]

    def get(self, request):
        assignments = ShiftAssignment.objects.select_related(
            "employee",
            "employee__department",
            "shift",
        ).order_by("-start_date", "-created_at")
        employee_id = request.query_params.get("employee")
        if employee_id:
            assignments = assignments.filter(employee_id=employee_id)

        return Response(
            {"count": assignments.count(), "results": ShiftAssignmentSerializer(assignments, many=True).data}
        )

    def post(self, request):
        with transaction.atomic():
            # Lock a person's existing assignments while validating a new range.
            employee_id = request.data.get("employee")
            if employee_id:
                ShiftAssignment.objects.select_for_update().filter(employee_id=employee_id).exists()

            serializer = ShiftAssignmentSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            assignment = serializer.save(assigned_by=request.user.get_username())

        return Response(ShiftAssignmentSerializer(assignment).data, status=status.HTTP_201_CREATED)


class ShiftAssignmentDetailAPIView(APIView):
    permission_classes = [IsAuthenticated, CanManageShifts]

    def patch(self, request, assignment_id):
        try:
            with transaction.atomic():
                assignment = ShiftAssignment.objects.select_for_update().select_related(
                    "employee",
                    "employee__department",
                    "shift",
                ).get(pk=assignment_id)
                ShiftAssignment.objects.select_for_update().filter(
                    employee=assignment.employee
                ).exclude(pk=assignment.pk).exists()

                serializer = ShiftAssignmentSerializer(assignment, data=request.data, partial=True)
                serializer.is_valid(raise_exception=True)
                assignment = serializer.save()
        except ShiftAssignment.DoesNotExist:
            return Response({"detail": "Shift assignment not found."}, status=status.HTTP_404_NOT_FOUND)

        return Response(ShiftAssignmentSerializer(assignment).data)


class ShiftAssignmentChangeAPIView(APIView):
    """End an indefinite assignment and start its replacement atomically."""

    permission_classes = [IsAuthenticated, CanManageShifts]

    def post(self, request):
        change_serializer = ShiftChangeSerializer(data=request.data)
        change_serializer.is_valid(raise_exception=True)
        data = change_serializer.validated_data

        with transaction.atomic():
            assignments = ShiftAssignment.objects.select_for_update().filter(
                employee_id=data["employee"]
            ).order_by("-start_date")
            ongoing_assignment = assignments.filter(end_date__isnull=True).first()

            if ongoing_assignment:
                if data["start_date"] <= ongoing_assignment.start_date:
                    return Response(
                        {
                            "start_date": (
                                "The new shift must start after the current assignment begins."
                            )
                        },
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                ongoing_assignment.end_date = data["start_date"] - timedelta(days=1)
                ongoing_assignment.save(update_fields=["end_date"])

            assignment_serializer = ShiftAssignmentSerializer(
                data={
                    "employee": data["employee"],
                    "shift": data["shift"].pk,
                    "start_date": data["start_date"],
                }
            )
            assignment_serializer.is_valid(raise_exception=True)
            assignment = assignment_serializer.save(assigned_by=request.user.get_username())

        return Response(ShiftAssignmentSerializer(assignment).data, status=status.HTTP_201_CREATED)

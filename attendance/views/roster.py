from datetime import date

from django.db import transaction

from rest_framework import status
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from attendance.models import EmployeeRosterDay, RosterDaySource
from attendance.serializers import EmployeeRosterDaySerializer, RotationGenerationSerializer, RosterGenerationSerializer, RosterOverrideSerializer
from attendance.services.roster import RosterGenerationConflict, generate_rotation_roster, generate_roster, roster_completeness
from audit.models import AuditSeverity
from audit.services import AuditService
from employees.models import Employee


class CanManageRoster(BasePermission):
    def has_permission(self, request, view):
        return request.user.has_perm("attendance.manage_roster")


class EmployeeRosterListAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        employee_id = request.query_params.get("employee")
        start_date = request.query_params.get("start_date")
        end_date = request.query_params.get("end_date")
        if not employee_id or not start_date or not end_date:
            return Response({"detail": "employee, start_date, and end_date are required."}, status=status.HTTP_400_BAD_REQUEST)
        try:
            start_date = date.fromisoformat(start_date)
            end_date = date.fromisoformat(end_date)
        except ValueError:
            return Response({"detail": "start_date and end_date must use YYYY-MM-DD."}, status=status.HTTP_400_BAD_REQUEST)
        if end_date < start_date:
            return Response({"detail": "end_date cannot be before start_date."}, status=status.HTTP_400_BAD_REQUEST)
        employee = Employee.objects.filter(pk=employee_id).first()
        if not employee:
            return Response({"detail": "Employee not found."}, status=status.HTTP_404_NOT_FOUND)
        rows = EmployeeRosterDay.objects.filter(employee=employee, date__range=(start_date, end_date)).select_related("employee", "shift", "updated_by")
        completeness = roster_completeness(employee, start_date, end_date)
        return Response({"count": rows.count(), "results": EmployeeRosterDaySerializer(rows, many=True).data, "completeness": {**completeness, "missing_dates": [value.isoformat() for value in completeness["missing_dates"]]}})


class RosterGenerationAPIView(APIView):
    permission_classes = [IsAuthenticated, CanManageRoster]

    def post(self, request):
        serializer = RosterGenerationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        employee = Employee.objects.get(pk=data["employee"])
        summary = generate_roster(employee, data["start_date"], data["end_date"], data["shift"], data["work_days"], data["rest_days"], actor=request.user, notes=data.get("notes", ""), working_weekdays=data.get("working_weekdays"))
        return Response({"summary": summary.as_dict()})


class RotationRosterGenerationAPIView(APIView):
    permission_classes = [IsAuthenticated, CanManageRoster]

    def post(self, request):
        serializer = RotationGenerationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        employee = Employee.objects.get(pk=data["employee"])
        starting_shift = data["day_shift"] if data["starting_shift"] == "day" else data["night_shift"]
        try:
            summary = generate_rotation_roster(
                employee,
                data["start_date"],
                data["end_date"],
                starting_shift,
                data["day_shift"],
                data["night_shift"],
                actor=request.user,
                notes=data.get("notes", ""),
            )
        except RosterGenerationConflict as conflict:
            return Response({"detail": str(conflict), "conflicts": conflict.conflicts}, status=status.HTTP_409_CONFLICT)
        return Response({"summary": summary.as_dict()})


class RosterOverrideAPIView(APIView):
    permission_classes = [IsAuthenticated, CanManageRoster]

    def post(self, request):
        serializer = RosterOverrideSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        employee = Employee.objects.get(pk=data["employee"])
        with transaction.atomic():
            roster_day, created = EmployeeRosterDay.objects.select_for_update().get_or_create(employee=employee, date=data["date"], defaults={"status": data["status"], "shift": data.get("shift"), "source": RosterDaySource.MANUAL, "notes": data.get("notes", ""), "created_by": request.user, "updated_by": request.user})
            if not created:
                roster_day.status = data["status"]
                roster_day.shift = data.get("shift")
                roster_day.source = RosterDaySource.OVERRIDE
                roster_day.notes = data.get("notes", "")
                roster_day.updated_by = request.user
                roster_day.save()
            AuditService.log(event_type="attendance.roster_day_overridden", module="attendance", employee=employee, actor=request.user, object=roster_day, severity=AuditSeverity.SUCCESS, title="Roster day updated", description=f"Roster for {employee.full_name} was manually set to {roster_day.status} on {roster_day.date}.", metadata={"date": roster_day.date.isoformat(), "status": roster_day.status, "shift": roster_day.shift.name if roster_day.shift else None, "source": roster_day.source, "created": created})
        return Response(EmployeeRosterDaySerializer(roster_day).data, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)

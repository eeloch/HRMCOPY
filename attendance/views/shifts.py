from datetime import timedelta

from django.db import transaction
from django.http import HttpResponse
from django.shortcuts import get_object_or_404

from rest_framework import status
from rest_framework.parsers import FormParser, MultiPartParser
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


class ShiftPlanListAPIView(APIView):
    """The shift plans, how many people are on each, and which rotation group is on Day this week."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        from datetime import timedelta

        from django.db.models import Count, Q
        from django.utils import timezone

        from attendance.models import ShiftPlan, ShiftPlanAssignment
        from attendance.services.shift_plans import day_group_for_week, monday_of

        today = timezone.localdate()
        current = ShiftPlanAssignment.objects.filter(employee__status="active", start_date__lte=today).filter(Q(end_date__isnull=True) | Q(end_date__gte=today))
        counts = {row["plan"]: row for row in current.values("plan").annotate(total=Count("id"), a=Count("id", filter=Q(group="A")), b=Count("id", filter=Q(group="B")))}
        results = []
        for plan in ShiftPlan.objects.select_related("shift", "day_shift", "night_shift").order_by("id"):
            row = counts.get(plan.pk, {})
            item = {"id": plan.pk, "name": plan.name, "kind": plan.kind, "description": plan.description, "active": plan.active,
                    "members": row.get("total", 0), "group_a": row.get("a", 0), "group_b": row.get("b", 0),
                    "shift": plan.shift.name if plan.shift else None, "working_weekdays": plan.working_weekdays}
            if plan.kind == "rotation" and plan.anchor_monday:
                monday = monday_of(today)
                item["this_week"] = {"monday": monday, "day_group": day_group_for_week(plan, monday), "next_monday": monday + timedelta(days=7), "next_day_group": day_group_for_week(plan, monday + timedelta(days=7))}
            results.append(item)
        from employees.models import Employee

        on_a_plan = current.values("employee").distinct().count()
        return Response({"results": results, "active_employees": Employee.objects.filter(status="active").count(), "on_a_plan": on_a_plan})


class ShiftPlanAssignAPIView(APIView):
    """Put many people on a plan at once. Pick them by department and/or by the plan they are on now, or list their ids.
    For a rotation choose group A, group B, or "split" to divide them evenly between the two."""

    permission_classes = [IsAuthenticated, CanManageShifts]

    def post(self, request):
        from datetime import date

        from django.db.models import Q
        from django.utils import timezone

        from attendance.models import ShiftPlan, ShiftPlanAssignment
        from attendance.services.shift_plans import assign_plan, split_groups
        from employees.models import Employee

        plan = get_object_or_404(ShiftPlan, pk=request.data.get("plan"))
        employees = Employee.objects.filter(status="active").order_by("employee_id")
        if request.data.get("employee_ids"):
            employees = employees.filter(pk__in=request.data["employee_ids"])
        if request.data.get("department_ids"):
            employees = employees.filter(department_id__in=request.data["department_ids"])
        today = timezone.localdate()
        if request.data.get("current_plan"):
            on_plan = ShiftPlanAssignment.objects.filter(plan_id=request.data["current_plan"], start_date__lte=today).filter(Q(end_date__isnull=True) | Q(end_date__gte=today)).values_list("employee_id", flat=True)
            employees = employees.filter(pk__in=list(on_plan))
        if not (request.data.get("employee_ids") or request.data.get("department_ids") or request.data.get("current_plan") or request.data.get("everyone")):
            return Response({"detail": "Choose who to assign: a department, people on another plan, or everyone."}, status=status.HTTP_400_BAD_REQUEST)
        people = list(employees)
        if not people:
            return Response({"detail": "Nobody matches that selection."}, status=status.HTTP_400_BAD_REQUEST)
        try:
            start = date.fromisoformat(request.data["start_date"]) if request.data.get("start_date") else today
        except ValueError:
            return Response({"detail": "The start date is not valid."}, status=status.HTTP_400_BAD_REQUEST)
        group = str(request.data.get("group", "")).upper()
        if request.data.get("dry_run"):
            split = {"A": len(split_groups(people)[0]), "B": len(split_groups(people)[1])} if group == "SPLIT" else None
            return Response({"people": len(people), "split": split, "sample": [e.full_name for e in people[:5]]})
        with transaction.atomic():
            try:
                if plan.kind == "rotation" and group == "SPLIT":
                    group_a, group_b = split_groups(people)
                    _, first = assign_plan(group_a, plan, group="A", start_date=start, actor=request.user.get_username())
                    _, second = assign_plan(group_b, plan, group="B", start_date=start, actor=request.user.get_username()) if group_b else (None, None)
                    summary = {"created": first.created + (second.created if second else 0), "updated": first.updated + (second.updated if second else 0)}
                else:
                    _, done = assign_plan(people, plan, group=group, start_date=start, actor=request.user.get_username())
                    summary = {"created": done.created, "updated": done.updated}
            except ValueError as error:
                return Response({"detail": str(error)}, status=status.HTTP_400_BAD_REQUEST)
        return Response({"people": len(people), "roster_days_created": summary["created"], "roster_days_changed": summary["updated"], "start_date": start})


class ShiftPlanFlipAPIView(APIView):
    """Swap which rotation group is on Day (moves the reference week on by one)."""

    permission_classes = [IsAuthenticated, CanManageShifts]

    def post(self, request, plan_id):
        from attendance.models import ShiftPlan
        from attendance.services.shift_plans import flip_rotation_week

        plan = get_object_or_404(ShiftPlan, pk=plan_id)
        try:
            summary = flip_rotation_week(plan)
        except ValueError as error:
            return Response({"detail": str(error)}, status=status.HTTP_400_BAD_REQUEST)
        return Response({"roster_days_changed": summary.updated + summary.created})


class ShiftRosterTemplateAPIView(APIView):
    """Download the roster upload template: every active employee, with their current plan already filled in."""

    permission_classes = [IsAuthenticated, CanManageShifts]

    def get(self, request):
        from attendance.services.roster_upload import build_template_workbook

        content = build_template_workbook()
        response = HttpResponse(
            content,
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        response["Content-Disposition"] = 'attachment; filename="Roster Upload Template.xlsx"'
        return response


class ShiftRosterUploadAPIView(APIView):
    """Upload the filled-in roster template. dry_run=true (default) only reports what would change."""

    permission_classes = [IsAuthenticated, CanManageShifts]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        from attendance.services.roster_upload import apply_roster_upload

        upload = request.FILES.get("file")
        if upload is None or not upload.name.lower().endswith(".xlsx"):
            return Response({"detail": "Upload the roster template as an .xlsx file."}, status=status.HTTP_400_BAD_REQUEST)
        dry_run = str(request.data.get("dry_run", "true")).lower() != "false"
        try:
            report = apply_roster_upload(upload, dry_run=dry_run, actor=request.user.get_username())
        except ValueError as error:
            return Response({"detail": str(error)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception:
            return Response({"detail": "That file could not be read. Check it is the roster upload template."}, status=status.HTTP_400_BAD_REQUEST)
        return Response(report.as_dict())


class EmployeeShiftPlanAPIView(APIView):
    """One employee's real, current shift plan (and group, for a rotation plan) plus today's actual roster
    shift - the accurate replacement for the old static per-employee Shift Assignment, which never reflects
    a rotation's weekly Day/Night swap."""

    permission_classes = [IsAuthenticated]

    def get(self, request, employee_id):
        from django.db.models import Q
        from django.utils import timezone

        from attendance.models import EmployeeRosterDay, ShiftPlanAssignment
        from employees.models import Employee

        employee = get_object_or_404(Employee, pk=employee_id)
        today = timezone.localdate()
        assignment = (
            ShiftPlanAssignment.objects.select_related("plan")
            .filter(employee=employee, start_date__lte=today)
            .filter(Q(end_date__isnull=True) | Q(end_date__gte=today))
            .order_by("-start_date")
            .first()
        )
        roster_today = EmployeeRosterDay.objects.select_related("shift").filter(employee=employee, date=today).first()
        return Response({
            "plan": {
                "id": assignment.plan_id,
                "name": assignment.plan.name,
                "kind": assignment.plan.kind,
                "group": assignment.group,
                "start_date": assignment.start_date,
            } if assignment else None,
            "today": {
                "date": today,
                "status": roster_today.status,
                "shift": {
                    "name": roster_today.shift.name,
                    "start_time": roster_today.shift.start_time,
                    "end_time": roster_today.shift.end_time,
                } if roster_today.shift else None,
            } if roster_today else None,
        })

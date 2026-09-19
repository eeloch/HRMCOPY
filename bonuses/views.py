from decimal import Decimal

from django.db.models import Sum
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from employees.models import Department, Employee

from .models import Bonus, BonusStatus, EmployeeOfTheMonth, EotmStatus
from .serializers import BonusCreateSerializer, BonusSerializer, DecisionSerializer, EmployeeOfTheMonthSerializer, EotmCreateSerializer
from .services import BonusService, EmployeeOfTheMonthService

PERMS = {"view": "bonuses.view_bonuses", "record": "bonuses.record_bonus", "approve": "bonuses.approve_bonus"}


def allow(*keys):
    """Anyone holding any one of these capabilities gets through."""

    class Allowed(BasePermission):
        def has_permission(self, request, view):
            return any(request.user.has_perm(PERMS[key]) for key in keys)

    return Allowed


def fail(error):
    return Response({"detail": str(error)}, status=status.HTTP_400_BAD_REQUEST)


def bonus_queryset():
    return Bonus.objects.select_related("employee", "employee__department", "recorded_by", "decided_by")


def eotm_queryset():
    return EmployeeOfTheMonth.objects.select_related("employee", "department", "recorded_by", "decided_by", "bonus")


class PeopleAPIView(APIView):
    """Active staff to choose from when recording a bonus or picking an Employee of the Month."""

    permission_classes = [IsAuthenticated, allow("record", "approve")]

    def get(self, request):
        people = Employee.objects.filter(status="active").select_related("department").order_by("first_name", "last_name")
        return Response({"results": [{"id": e.pk, "employee_id": e.employee_id, "name": e.full_name, "department": e.department_id, "department_name": e.department.name if e.department else None} for e in people]})


class BonusListCreateAPIView(APIView):
    def get_permissions(self):
        return [IsAuthenticated(), allow("record")() if self.request.method == "POST" else allow("view", "record", "approve")()]

    def get(self, request):
        bonuses = bonus_queryset()
        year, month = request.query_params.get("year"), request.query_params.get("month")
        if year and month:
            bonuses = bonuses.filter(performance_year=year, performance_month=month)
        if value := request.query_params.get("status"):
            bonuses = bonuses.filter(status=value)
        bonuses = list(bonuses)
        live = [b for b in bonuses if b.status in (BonusStatus.APPROVED, BonusStatus.PAID)]
        total = lambda rows: str(sum((b.amount for b in rows), Decimal("0.00")))  # noqa: E731
        pending = [b for b in bonuses if b.status == BonusStatus.PROPOSED]
        return Response({
            "summary": {"pending_count": len(pending), "pending_total": total(pending), "approved_count": len(live), "approved_total": total(live), "paid_total": total([b for b in bonuses if b.status == BonusStatus.PAID])},
            "results": BonusSerializer(bonuses, many=True).data,
        })

    def post(self, request):
        serializer = BonusCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        employee = get_object_or_404(Employee, pk=data.pop("employee"))
        try:
            bonus = BonusService.record(employee=employee, actor=request.user, **data)
        except ValueError as error:
            return fail(error)
        return Response(BonusSerializer(bonus_queryset().get(pk=bonus.pk)).data, status=status.HTTP_201_CREATED)


class BonusActionAPIView(APIView):
    action, needs = "", ()

    def get_permissions(self):
        return [IsAuthenticated(), allow(*self.needs)()]

    def post(self, request, bonus_id):
        serializer = DecisionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        bonus = get_object_or_404(bonus_queryset(), pk=bonus_id)
        try:
            if self.action == "approve":
                BonusService.approve(bonus, actor=request.user, comment=data.get("comment", ""), pay_year=data.get("pay_year"), pay_month=data.get("pay_month"))
            elif self.action == "decline":
                BonusService.decline(bonus, actor=request.user, comment=data.get("comment", ""))
            else:
                BonusService.cancel(bonus, actor=request.user)
        except ValueError as error:
            return fail(error)
        return Response(BonusSerializer(bonus_queryset().get(pk=bonus_id)).data)


class BonusApproveAPIView(BonusActionAPIView):
    action, needs = "approve", ("approve",)


class BonusDeclineAPIView(BonusActionAPIView):
    action, needs = "decline", ("approve",)


class BonusCancelAPIView(BonusActionAPIView):
    action, needs = "cancel", ("record", "approve")


class EotmOverviewAPIView(APIView):
    """Every department for one month with its Employee of the Month (or none yet), plus the recent winners."""

    permission_classes = [IsAuthenticated, allow("view", "record", "approve")]

    def get(self, request):
        try:
            year, month = int(request.query_params["year"]), int(request.query_params["month"])
        except (KeyError, ValueError):
            return fail("Give the year and month.")
        entries = {e.department_id: e for e in eotm_queryset().filter(year=year, month=month, status__in=[EotmStatus.PROPOSED, EotmStatus.APPROVED])}
        recent = eotm_queryset().filter(status=EotmStatus.APPROVED).order_by("-year", "-month", "department__name")[:60]
        departments = Department.objects.order_by("name")
        return Response({
            "year": year, "month": month,
            "departments": [{"id": d.pk, "name": d.name, "entry": EmployeeOfTheMonthSerializer(entries[d.pk]).data if d.pk in entries else None} for d in departments],
            "chosen": sum(1 for e in entries.values() if e.status == EotmStatus.APPROVED),
            "awaiting": sum(1 for e in entries.values() if e.status == EotmStatus.PROPOSED),
            "recent": EmployeeOfTheMonthSerializer(recent, many=True).data,
        })


class EotmCreateAPIView(APIView):
    permission_classes = [IsAuthenticated, allow("record")]

    def post(self, request):
        serializer = EotmCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        department = get_object_or_404(Department, pk=data["department"])
        employee = get_object_or_404(Employee, pk=data["employee"])
        try:
            entry = EmployeeOfTheMonthService.propose(department=department, employee=employee, year=data["year"], month=data["month"], reason=data["reason"], reward_amount=data.get("reward_amount"), actor=request.user)
        except ValueError as error:
            return fail(error)
        return Response(EmployeeOfTheMonthSerializer(eotm_queryset().get(pk=entry.pk)).data, status=status.HTTP_201_CREATED)


class EotmActionAPIView(APIView):
    action, needs = "", ()

    def get_permissions(self):
        return [IsAuthenticated(), allow(*self.needs)()]

    def post(self, request, entry_id):
        serializer = DecisionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        entry = get_object_or_404(eotm_queryset(), pk=entry_id)
        try:
            if self.action == "approve":
                EmployeeOfTheMonthService.approve(entry, actor=request.user, comment=data.get("comment", ""), pay_year=data.get("pay_year"), pay_month=data.get("pay_month"))
            elif self.action == "decline":
                EmployeeOfTheMonthService.decline(entry, actor=request.user, comment=data.get("comment", ""))
            else:
                EmployeeOfTheMonthService.cancel(entry, actor=request.user)
        except ValueError as error:
            return fail(error)
        return Response(EmployeeOfTheMonthSerializer(eotm_queryset().get(pk=entry_id)).data)


class EotmApproveAPIView(EotmActionAPIView):
    action, needs = "approve", ("approve",)


class EotmDeclineAPIView(EotmActionAPIView):
    action, needs = "decline", ("approve",)


class EotmCancelAPIView(EotmActionAPIView):
    action, needs = "cancel", ("record", "approve")

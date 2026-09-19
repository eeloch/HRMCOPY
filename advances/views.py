from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import SalaryAdvance
from .serializers import AdvanceDecisionSerializer, SalaryAdvanceCreateSerializer, SalaryAdvanceSerializer
from .services import AdvanceService

PERMS = {"record": "advances.record_salary_advance", "approve": "advances.approve_salary_advance", "pay": "advances.pay_salary_advance"}


def _has_any(user, *keys):
    return any(user.has_perm(PERMS[key]) for key in keys)


class CanViewAdvances(BasePermission):
    def has_permission(self, request, view):
        return _has_any(request.user, "record", "approve", "pay")


class CanRecordAdvance(BasePermission):
    def has_permission(self, request, view):
        return _has_any(request.user, "record")


class AdvanceQuerysetMixin:
    @staticmethod
    def queryset():
        return SalaryAdvance.objects.select_related("employee", "employee__department", "recorded_by", "decided_by", "paid_by")


def _serialize(advance, request, many=False):
    return SalaryAdvanceSerializer(advance, many=many, context={"request": request}).data


class AdvanceListCreateAPIView(AdvanceQuerysetMixin, APIView):
    def get_permissions(self):
        return [IsAuthenticated(), CanRecordAdvance() if self.request.method == "POST" else CanViewAdvances()]

    def get(self, request):
        advances = self.queryset()
        if value := request.query_params.get("status"):
            advances = advances.filter(status=value)
        if value := request.query_params.get("employee"):
            advances = advances.filter(employee_id=value)
        return Response({"count": advances.count(), "results": _serialize(advances, request, many=True)})

    def post(self, request):
        serializer = SalaryAdvanceCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            advance = AdvanceService.record(**serializer.validated_data, actor=request.user)
        except ValueError as error:
            return Response({"detail": str(error)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(_serialize(self.queryset().get(pk=advance.pk), request), status=status.HTTP_201_CREATED)


class AdvanceActionAPIView(AdvanceQuerysetMixin, APIView):
    action = ""
    needs = ()

    def get_permissions(self):
        needs = self.needs

        class Allowed(BasePermission):
            def has_permission(self, request, view):
                return _has_any(request.user, *needs)

        return [IsAuthenticated(), Allowed()]

    def post(self, request, advance_id):
        serializer = AdvanceDecisionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        advance = get_object_or_404(self.queryset(), pk=advance_id)
        try:
            if self.action == "approve":
                AdvanceService.approve(advance, actor=request.user, comment=data.get("comment", ""))
            elif self.action == "decline":
                AdvanceService.decline(advance, actor=request.user, comment=data.get("comment", ""))
            elif self.action == "cancel":
                AdvanceService.cancel(advance, actor=request.user, comment=data.get("comment", ""))
            else:
                AdvanceService.pay(advance, actor=request.user, paid_on=data.get("paid_on"), reference=data.get("reference", ""))
        except ValueError as error:
            return Response({"detail": str(error)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(_serialize(self.queryset().get(pk=advance_id), request))


class AdvanceApproveAPIView(AdvanceActionAPIView):
    action, needs = "approve", ("approve",)


class AdvanceDeclineAPIView(AdvanceActionAPIView):
    action, needs = "decline", ("approve",)


class AdvanceCancelAPIView(AdvanceActionAPIView):
    action, needs = "cancel", ("record", "approve")


class AdvancePayAPIView(AdvanceActionAPIView):
    action, needs = "pay", ("pay",)

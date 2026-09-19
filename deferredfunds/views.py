from decimal import Decimal

from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from employees.models import Employee, EmploymentType

from .models import DeferredFundAccount, DeferredFundEntry, DeferredFundWithdrawal
from .serializers import AccountSerializer, AdjustSerializer, EnrolSerializer, EntrySerializer, PercentSerializer, WithdrawalActionSerializer, WithdrawalCreateSerializer, WithdrawalSerializer
from .services import DeferredFundService

PERMS = {
    "view": "deferredfunds.view_deferred_funds",
    "manage": "deferredfunds.manage_deferred_funds",
    "approve": "deferredfunds.approve_deferred_withdrawal",
    "pay": "deferredfunds.pay_deferred_withdrawal",
}


def allow(*keys):
    """A permission class letting through anyone holding any one of these capabilities."""

    class Allowed(BasePermission):
        def has_permission(self, request, view):
            return any(request.user.has_perm(PERMS[key]) for key in keys)

    return Allowed


def accounts_queryset():
    return DeferredFundAccount.objects.select_related("employee", "employee__department", "employee__position")


def fail(error):
    return Response({"detail": str(error)}, status=status.HTTP_400_BAD_REQUEST)


class OverviewAPIView(APIView):
    permission_classes = [IsAuthenticated, allow("view", "manage", "approve", "pay")]

    def get(self, request):
        accounts = list(accounts_queryset())
        held = sum((account.balance for account in accounts), Decimal("0.00"))
        enrolled_ids = {account.employee_id for account in accounts}
        unenrolled = Employee.objects.filter(employment_type=EmploymentType.CONTRACT, status="active").exclude(pk__in=enrolled_ids).select_related("department", "position").order_by("employee_id")
        return Response({
            "total_held": str(held),
            "enrolled_count": sum(1 for account in accounts if account.active),
            "accounts": AccountSerializer(accounts, many=True).data,
            "not_enrolled": [{"id": e.pk, "employee_id": e.employee_id, "full_name": e.full_name, "department_name": e.department.name if e.department else None, "position_name": e.position.name if e.position else None} for e in unenrolled],
        })


class EnrolAPIView(APIView):
    permission_classes = [IsAuthenticated, allow("manage")]

    def post(self, request):
        serializer = EnrolSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            account = DeferredFundService.enrol(**serializer.validated_data, actor=request.user)
        except ValueError as error:
            return fail(error)
        return Response(AccountSerializer(accounts_queryset().get(pk=account.pk)).data, status=status.HTTP_201_CREATED)


class AccountAPIView(APIView):
    permission_classes = [IsAuthenticated, allow("manage")]

    def patch(self, request, account_id):
        serializer = PercentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        account = get_object_or_404(accounts_queryset(), pk=account_id)
        try:
            DeferredFundService.set_percent(account, serializer.validated_data["percent"], actor=request.user)
        except ValueError as error:
            return fail(error)
        return Response(AccountSerializer(accounts_queryset().get(pk=account_id)).data)


class LedgerAPIView(APIView):
    permission_classes = [IsAuthenticated, allow("view", "manage", "approve", "pay")]

    def get(self, request, account_id):
        account = get_object_or_404(accounts_queryset(), pk=account_id)
        entries = account.entries.select_related("payroll_period")
        return Response({"account": AccountSerializer(account).data, "entries": EntrySerializer(entries, many=True).data})


class AdjustAPIView(APIView):
    permission_classes = [IsAuthenticated, allow("manage")]

    def post(self, request, account_id):
        serializer = AdjustSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        account = get_object_or_404(accounts_queryset(), pk=account_id)
        try:
            DeferredFundService.adjust(account, **serializer.validated_data, actor=request.user)
        except ValueError as error:
            return fail(error)
        return Response(AccountSerializer(accounts_queryset().get(pk=account_id)).data)


def withdrawals_queryset():
    return DeferredFundWithdrawal.objects.select_related("account", "account__employee", "recorded_by", "decided_by", "paid_by")


class WithdrawalListCreateAPIView(APIView):
    def get_permissions(self):
        return [IsAuthenticated(), allow("manage")() if self.request.method == "POST" else allow("view", "manage", "approve", "pay")()]

    def get(self, request):
        return Response({"results": WithdrawalSerializer(withdrawals_queryset(), many=True).data})

    def post(self, request):
        serializer = WithdrawalCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            withdrawal = DeferredFundService.request_withdrawal(data["account"], kind=data["kind"], amount=data.get("amount"), reason=data.get("reason", ""), actor=request.user)
        except ValueError as error:
            return fail(error)
        return Response(WithdrawalSerializer(withdrawals_queryset().get(pk=withdrawal.pk)).data, status=status.HTTP_201_CREATED)


class WithdrawalActionAPIView(APIView):
    action = ""
    needs = ()

    def get_permissions(self):
        return [IsAuthenticated(), allow(*self.needs)()]

    def post(self, request, withdrawal_id):
        serializer = WithdrawalActionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        withdrawal = get_object_or_404(withdrawals_queryset(), pk=withdrawal_id)
        try:
            if self.action == "approve":
                DeferredFundService.approve(withdrawal, actor=request.user, comment=data.get("comment", ""))
            elif self.action == "decline":
                DeferredFundService.decline(withdrawal, actor=request.user, comment=data.get("comment", ""))
            elif self.action == "cancel":
                DeferredFundService.cancel(withdrawal, actor=request.user)
            else:
                DeferredFundService.pay(withdrawal, actor=request.user, paid_on=data.get("paid_on"), reference=data.get("reference", ""))
        except ValueError as error:
            return fail(error)
        return Response(WithdrawalSerializer(withdrawals_queryset().get(pk=withdrawal_id)).data)


class WithdrawalApproveAPIView(WithdrawalActionAPIView):
    action, needs = "approve", ("approve",)


class WithdrawalDeclineAPIView(WithdrawalActionAPIView):
    action, needs = "decline", ("approve",)


class WithdrawalCancelAPIView(WithdrawalActionAPIView):
    action, needs = "cancel", ("manage", "approve")


class WithdrawalPayAPIView(WithdrawalActionAPIView):
    action, needs = "pay", ("pay",)

from decimal import Decimal

from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from audit.models import AuditSeverity
from audit.services import AuditService
from employees.models import Employee, EmploymentType
from payroll.services.bank_upload import BANK_UPLOAD_NARRATION, bank_file, split_payable

from .models import DeferredFundAccount, DeferredFundEntry, DeferredFundWithdrawal, WithdrawalKind, WithdrawalStatus
from .serializers import POLICY, ForfeitSerializer, AccountSerializer, AdjustSerializer, EnrolSerializer, EntrySerializer, PercentSerializer, WithdrawalActionSerializer, WithdrawalCreateSerializer, WithdrawalSerializer
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
            "policy": POLICY,
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


class ForfeitAPIView(APIView):
    """Management only: the company keeps the whole fund (dismissal for theft or serious misconduct)."""

    permission_classes = [IsAuthenticated, allow("approve")]

    def post(self, request, account_id):
        serializer = ForfeitSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        account = get_object_or_404(accounts_queryset(), pk=account_id)
        try:
            DeferredFundService.forfeit(account, actor=request.user, reason=serializer.validated_data["reason"])
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
            withdrawal = DeferredFundService.request_withdrawal(data["account"], kind=data["kind"], amount=data.get("amount"), reason=data.get("reason", ""), notice_given_on=data.get("notice_given_on"), leaving_on=data.get("leaving_on"), actor=request.user)
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


class BulkWithdrawalMixin:
    """The ticked, approved withdrawals as a bank file, and marking them paid in one go."""

    def get_permissions(self):
        return [IsAuthenticated(), allow("pay")()]

    def selected(self, request):
        ids = request.data.get("ids")
        if not isinstance(ids, list) or not ids or not all(isinstance(item, int) for item in ids):
            return None, fail("Select at least one withdrawal.")
        withdrawals = list(withdrawals_queryset().filter(pk__in=ids, status=WithdrawalStatus.APPROVED).order_by("account__employee__employee_id", "id"))
        if not withdrawals:
            return None, fail("None of the selected withdrawals is awaiting payment.")
        return withdrawals, None

    @staticmethod
    def amount_of(withdrawal):
        # A full release pays whatever is held at the time of payment.
        return withdrawal.account.balance if withdrawal.kind == WithdrawalKind.FINAL else withdrawal.amount

    def payable(self, withdrawals):
        return split_payable((w.account.employee, self.amount_of(w)) for w in withdrawals)


class WithdrawalBankPreviewAPIView(BulkWithdrawalMixin, APIView):
    def post(self, request):
        withdrawals, error = self.selected(request)
        if error:
            return error
        rows, issues = self.payable(withdrawals)
        return Response({
            "narration": BANK_UPLOAD_NARRATION,
            "ready_count": len(rows),
            "ready_total": str(sum((row.amount for row in rows), Decimal("0.00"))),
            "padded": [{"employee_id": r.employee_id, "employee_name": r.employee_name, "account_number": r.account_number} for r in rows if r.padded],
            "issues": [{**issue.__dict__, "net_pay": str(issue.net_pay)} for issue in issues],
        })


class WithdrawalBankDownloadAPIView(BulkWithdrawalMixin, APIView):
    def post(self, request):
        withdrawals, error = self.selected(request)
        if error:
            return error
        rows, issues = self.payable(withdrawals)
        if not rows:
            return fail("None of the selected people can be included yet - fix their bank details first.")
        filename, content_type, content = bank_file(f"Deferred Fund {timezone.localdate().strftime('%d %b %Y')}", rows)
        total = sum((row.amount for row in rows), Decimal("0.00"))
        AuditService.log(
            event_type="deferred_fund.bank_upload_exported", module="deferred_funds", actor=request.user, severity=AuditSeverity.INFO,
            title="Deferred fund bank file created", description=f"Bank file for {len(rows)} deferred fund withdrawal(s), {total:,.2f} total; {len(issues)} left out.",
            metadata={"withdrawal_ids": [w.pk for w in withdrawals], "payments": len(rows), "total": str(total), "left_out": len(issues)},
        )
        response = HttpResponse(content, content_type=content_type)
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response


class WithdrawalBulkPayAPIView(BulkWithdrawalMixin, APIView):
    def post(self, request):
        withdrawals, error = self.selected(request)
        if error:
            return error
        serializer = WithdrawalActionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        paid = 0
        for withdrawal in withdrawals:
            # Only people who were in the bank file: anyone left out stays awaiting payment.
            if not split_payable([(withdrawal.account.employee, self.amount_of(withdrawal))])[0]:
                continue
            try:
                DeferredFundService.pay(withdrawal, actor=request.user, paid_on=data.get("paid_on"), reference=data.get("reference", ""))
                paid += 1
            except ValueError:
                continue
        return Response({"paid": paid})

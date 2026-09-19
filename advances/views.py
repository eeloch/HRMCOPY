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
from payroll.services.bank_upload import BANK_UPLOAD_NARRATION, bank_file, split_payable

from .models import AdvanceStatus, SalaryAdvance
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


class BulkAdvanceMixin(AdvanceQuerysetMixin):
    """Shared by the bank file and bulk-pay endpoints: work on the ticked, approved advances."""

    permission_classes = [IsAuthenticated]

    def get_permissions(self):
        class CanPay(BasePermission):
            def has_permission(self, request, view):
                return _has_any(request.user, "pay")

        return [IsAuthenticated(), CanPay()]

    def selected(self, request):
        ids = request.data.get("ids")
        if not isinstance(ids, list) or not ids or not all(isinstance(item, int) for item in ids):
            return None, Response({"detail": "Select at least one advance."}, status=status.HTTP_400_BAD_REQUEST)
        advances = list(self.queryset().filter(pk__in=ids, status=AdvanceStatus.APPROVED).order_by("employee__employee_id", "id"))
        if not advances:
            return None, Response({"detail": "None of the selected advances is awaiting payment."}, status=status.HTTP_400_BAD_REQUEST)
        return advances, None

    @staticmethod
    def payable(advances):
        rows, issues = split_payable((advance.employee, advance.amount) for advance in advances)
        return rows, issues


class AdvanceBankPreviewAPIView(BulkAdvanceMixin, APIView):
    def post(self, request):
        advances, error = self.selected(request)
        if error:
            return error
        rows, issues = self.payable(advances)
        return Response({
            "narration": BANK_UPLOAD_NARRATION,
            "ready_count": len(rows),
            "ready_total": str(sum((row.amount for row in rows), Decimal("0.00"))),
            "padded": [{"employee_id": r.employee_id, "employee_name": r.employee_name, "account_number": r.account_number} for r in rows if r.padded],
            "issues": [{**issue.__dict__, "net_pay": str(issue.net_pay)} for issue in issues],
        })


class AdvanceBankDownloadAPIView(BulkAdvanceMixin, APIView):
    def post(self, request):
        advances, error = self.selected(request)
        if error:
            return error
        rows, issues = self.payable(advances)
        if not rows:
            return Response({"detail": "None of the selected people can be included yet - fix their bank details first."}, status=status.HTTP_400_BAD_REQUEST)
        title = f"Salary Advances {timezone.localdate().strftime('%d %b %Y')}"
        filename, content_type, content = bank_file(title, rows)
        total = sum((row.amount for row in rows), Decimal("0.00"))
        AuditService.log(
            event_type="advance.bank_upload_exported", module="advances", actor=request.user, severity=AuditSeverity.INFO,
            title="Salary advance bank file created", description=f"Bank file for {len(rows)} salary advance(s), {total:,.2f} total; {len(issues)} left out.",
            metadata={"advance_ids": [advance.pk for advance in advances], "payments": len(rows), "total": str(total), "left_out": len(issues)},
        )
        response = HttpResponse(content, content_type=content_type)
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response


class AdvanceBulkPayAPIView(BulkAdvanceMixin, APIView):
    def post(self, request):
        advances, error = self.selected(request)
        if error:
            return error
        serializer = AdvanceDecisionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        paid = 0
        for advance in advances:
            # Only people who were in the bank file: anyone left out (bad bank details) stays awaiting payment.
            if not split_payable([(advance.employee, advance.amount)])[0]:
                continue
            try:
                AdvanceService.pay(advance, actor=request.user, paid_on=data.get("paid_on"), reference=data.get("reference", ""))
                paid += 1
            except ValueError:
                continue
        return Response({"paid": paid})

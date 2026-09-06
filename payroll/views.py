from decimal import Decimal

from django.db import transaction
from django.db.models import Count, DecimalField, Sum, Value
from django.db.models.functions import Coalesce
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from audit.models import AuditSeverity
from audit.services import AuditService
from payroll.models import EmployeePayroll, PayrollLineItem, PayrollPeriod, PayrollPeriodStatus
from payroll.serializers import EmployeePayrollDetailSerializer, EmployeePayrollListSerializer, PayrollLineItemCreateSerializer, PayrollLineItemSerializer, PayrollPeriodCreateSerializer, PayrollPeriodSerializer, PayrollPeriodTransitionSerializer
from payroll.services import (
    generate_payroll_for_period,
    pending_exception_count,
    recalculate_employee_payroll,
    sync_attendance_deductions_for_period,
)


class CanViewPayroll(BasePermission):
    def has_permission(self, request, view):
        return request.user.has_perm("payroll.view_payroll") or request.user.has_perm("payroll.manage_payroll")


class CanManagePayroll(BasePermission):
    def has_permission(self, request, view):
        return request.user.has_perm("payroll.manage_payroll")


def period_queryset():
    money = DecimalField(max_digits=16, decimal_places=2)
    zero = Value(Decimal("0.00"), output_field=money)
    return PayrollPeriod.objects.annotate(
        employee_count=Count("employee_payrolls"),
        total_basic_salary=Coalesce(Sum("employee_payrolls__basic_salary"), zero),
        total_gross_earnings=Coalesce(Sum("employee_payrolls__gross_earnings"), zero),
        total_deductions=Coalesce(Sum("employee_payrolls__total_deductions"), zero),
        total_net_pay=Coalesce(Sum("employee_payrolls__net_pay"), zero),
    )


def period_is_locked(period):
    return period.status in {PayrollPeriodStatus.APPROVED, PayrollPeriodStatus.PAID, PayrollPeriodStatus.CLOSED}


class PayrollPeriodListCreateAPIView(APIView):
    def get_permissions(self):
        return [IsAuthenticated(), CanManagePayroll() if self.request.method == "POST" else CanViewPayroll()]

    def get(self, request):
        return Response({"results": PayrollPeriodSerializer(period_queryset(), many=True).data})

    def post(self, request):
        serializer = PayrollPeriodCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        values = serializer.validated_data
        if PayrollPeriod.objects.filter(year=values["year"], month=values["month"]).exists():
            return Response({"detail": "A payroll period already exists for this month."}, status=status.HTTP_400_BAD_REQUEST)
        period = serializer.save(created_by=request.user)
        return Response(PayrollPeriodSerializer(period_queryset().get(pk=period.pk)).data, status=status.HTTP_201_CREATED)


class PayrollPeriodDetailAPIView(APIView):
    permission_classes = [IsAuthenticated, CanViewPayroll]

    def get(self, request, period_id):
        return Response(PayrollPeriodSerializer(get_object_or_404(period_queryset(), pk=period_id)).data)


class PayrollPeriodGenerateAPIView(APIView):
    permission_classes = [IsAuthenticated, CanManagePayroll]

    def post(self, request, period_id):
        period = get_object_or_404(PayrollPeriod, pk=period_id)
        if period_is_locked(period):
            return Response({"detail": "Approved payroll periods cannot be regenerated."}, status=status.HTTP_400_BAD_REQUEST)
        summary = generate_payroll_for_period(period, actor=request.user)
        return Response({"period": PayrollPeriodSerializer(period_queryset().get(pk=period.pk)).data, "summary": summary.__dict__})


class PayrollPeriodTransitionAPIView(APIView):
    permission_classes = [IsAuthenticated, CanManagePayroll]
    transitions = {PayrollPeriodStatus.DRAFT: PayrollPeriodStatus.PROCESSING, PayrollPeriodStatus.PROCESSING: PayrollPeriodStatus.REVIEW, PayrollPeriodStatus.REVIEW: PayrollPeriodStatus.APPROVED}

    def post(self, request, period_id):
        serializer = PayrollPeriodTransitionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        with transaction.atomic():
            period = get_object_or_404(PayrollPeriod.objects.select_for_update(), pk=period_id)
            target = serializer.validated_data["status"]
            if self.transitions.get(period.status) != target:
                return Response({"detail": f"{period.get_status_display()} cannot transition to {target}."}, status=status.HTTP_400_BAD_REQUEST)
            if target == PayrollPeriodStatus.APPROVED:
                pending_exceptions = pending_exception_count(period)
                if pending_exceptions:
                    return Response({"detail": f"Payroll approval is blocked by {pending_exceptions} pending attendance exception(s)."}, status=status.HTTP_400_BAD_REQUEST)
            old_status = period.status
            period.status = target
            update_fields = ["status"]
            if target == PayrollPeriodStatus.APPROVED:
                period.approved_at = timezone.now()
                period.approved_by = request.user
                update_fields.extend(["approved_at", "approved_by"])
            period.save(update_fields=update_fields)
            AuditService.log(event_type="payroll.period_status_changed", module="payroll", actor=request.user, object=period, severity=AuditSeverity.SUCCESS, title="Payroll period status updated", description=f"{period.display_name} moved from {old_status} to {target}.", metadata={"period": period.display_name, "from_status": old_status, "to_status": target})
        return Response(PayrollPeriodSerializer(period_queryset().get(pk=period.pk)).data)


class PayrollPeriodAttendanceSyncAPIView(APIView):
    permission_classes = [IsAuthenticated, CanManagePayroll]

    def post(self, request, period_id):
        period = get_object_or_404(PayrollPeriod, pk=period_id)
        try:
            summary = sync_attendance_deductions_for_period(period, actor=request.user)
        except ValueError as error:
            return Response({"detail": str(error)}, status=status.HTTP_400_BAD_REQUEST)
        return Response({"summary": summary.as_dict()})


class PayrollPeriodEmployeeListAPIView(APIView):
    permission_classes = [IsAuthenticated, CanViewPayroll]

    def get(self, request, period_id):
        get_object_or_404(PayrollPeriod, pk=period_id)
        payrolls = EmployeePayroll.objects.filter(payroll_period_id=period_id).select_related("employee", "employee__department")
        search = request.query_params.get("search", "").strip()
        department = request.query_params.get("department", "").strip()
        if search:
            from django.db.models import Q
            payrolls = payrolls.filter(Q(employee__employee_id__icontains=search) | Q(employee__first_name__icontains=search) | Q(employee__last_name__icontains=search))
        if department:
            payrolls = payrolls.filter(employee__department_id=department)
        return Response({"results": EmployeePayrollListSerializer(payrolls, many=True).data})


class EmployeePayrollDetailAPIView(APIView):
    permission_classes = [IsAuthenticated, CanViewPayroll]

    def get(self, request, payroll_id):
        payroll = get_object_or_404(EmployeePayroll.objects.select_related("employee", "employee__department", "payroll_period").prefetch_related("line_items"), pk=payroll_id)
        return Response(EmployeePayrollDetailSerializer(payroll).data)


class EmployeePayrollHistoryAPIView(APIView):
    """Read-only payroll history scoped to one employee."""

    permission_classes = [IsAuthenticated, CanViewPayroll]

    def get(self, request):
        employee_id = request.query_params.get("employee")
        if not employee_id:
            return Response(
                {"detail": "An employee query parameter is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        payrolls = (
            EmployeePayroll.objects.filter(employee_id=employee_id)
            .select_related("employee", "employee__department", "payroll_period")
            .order_by("-payroll_period__year", "-payroll_period__month", "-id")
        )
        return Response({"count": payrolls.count(), "results": EmployeePayrollListSerializer(payrolls, many=True).data})


class PayrollLineItemListCreateAPIView(APIView):
    permission_classes = [IsAuthenticated, CanManagePayroll]

    def post(self, request, payroll_id):
        serializer = PayrollLineItemCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        with transaction.atomic():
            payroll = get_object_or_404(EmployeePayroll.objects.select_for_update().select_related("payroll_period", "employee"), pk=payroll_id)
            if period_is_locked(payroll.payroll_period):
                return Response({"detail": "Manual line items cannot be changed after payroll approval."}, status=status.HTTP_400_BAD_REQUEST)
            line_item = serializer.save(payroll=payroll, source_type="manual", source_reference="", is_system_generated=False)
            recalculate_employee_payroll(payroll)
            AuditService.log(event_type="payroll.line_item_added", module="payroll", employee=payroll.employee, actor=request.user, object=payroll, severity=AuditSeverity.SUCCESS, title="Manual payroll line item added", description=f"{line_item.description} was added to {payroll.employee.full_name}'s payroll.", metadata={"payroll_id": payroll.id, "line_item_id": line_item.id, "code": line_item.code, "amount": str(line_item.amount), "item_type": line_item.item_type})
        return Response(PayrollLineItemSerializer(line_item).data, status=status.HTTP_201_CREATED)


class PayrollLineItemDetailAPIView(APIView):
    permission_classes = [IsAuthenticated, CanManagePayroll]

    def delete(self, request, line_item_id):
        with transaction.atomic():
            line_item = get_object_or_404(PayrollLineItem.objects.select_for_update().select_related("payroll__payroll_period", "payroll__employee"), pk=line_item_id)
            payroll = line_item.payroll
            if line_item.is_system_generated:
                return Response({"detail": "System-generated line items cannot be deleted manually."}, status=status.HTTP_400_BAD_REQUEST)
            if period_is_locked(payroll.payroll_period):
                return Response({"detail": "Manual line items cannot be changed after payroll approval."}, status=status.HTTP_400_BAD_REQUEST)
            metadata = {"payroll_id": payroll.id, "line_item_id": line_item.id, "code": line_item.code, "amount": str(line_item.amount), "item_type": line_item.item_type}
            description = line_item.description
            line_item.delete()
            recalculate_employee_payroll(payroll)
            AuditService.log(event_type="payroll.line_item_deleted", module="payroll", employee=payroll.employee, actor=request.user, object=payroll, severity=AuditSeverity.SUCCESS, title="Manual payroll line item removed", description=f"{description} was removed from {payroll.employee.full_name}'s payroll.", metadata=metadata)
        return Response(status=status.HTTP_204_NO_CONTENT)

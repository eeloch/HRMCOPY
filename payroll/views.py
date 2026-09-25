from decimal import Decimal

from django.db import transaction
from django.http import HttpResponse
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
from employees.banking import to_account_number, to_bank_code
from payroll.models import EmployeePayroll, EmployeePayrollStatus, PayrollLineItem, PayrollPeriod, PayrollPeriodStatus
from payroll.serializers import EmployeePayrollDetailSerializer, EmployeePayrollListSerializer, PayrollLineItemCreateSerializer, PayrollLineItemSerializer, PayrollPeriodCreateSerializer, PayrollPeriodSerializer, PayrollPeriodTransitionSerializer
from payroll.services.bank_upload import BANK_UPLOAD_NARRATION, bank_upload_download, prepare_bank_upload
from payroll.services.attendance import validate_attendance_for_approval
from payroll.services.generation import employees_for_period
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
        try:
            summary = generate_payroll_for_period(period, actor=request.user)
        except ValueError as error:
            return Response({"detail": str(error)}, status=status.HTTP_400_BAD_REQUEST)
        return Response({"period": PayrollPeriodSerializer(period_queryset().get(pk=period.pk)).data, "summary": summary.__dict__})


class PayrollBankUploadPreviewAPIView(APIView):
    """What the bank upload file for an approved period would contain, and who's left out and why."""

    permission_classes = [IsAuthenticated, CanManagePayroll]

    def get(self, request, period_id):
        period = get_object_or_404(PayrollPeriod, pk=period_id)
        if not period_is_locked(period):
            return Response({"detail": "Approve this payroll before creating the bank upload file."}, status=status.HTTP_400_BAD_REQUEST)
        try:
            rows, issues = prepare_bank_upload(period)
        except ValueError as error:
            return Response({"detail": str(error)}, status=status.HTTP_400_BAD_REQUEST)
        return Response({
            "period": period.display_name,
            "narration": BANK_UPLOAD_NARRATION,
            "ready_count": len(rows),
            "ready_total": str(sum((row.amount for row in rows), Decimal("0.00"))),
            "padded": [{"employee_id": r.employee_id, "employee_name": r.employee_name, "account_number": r.account_number} for r in rows if r.padded],
            "issues": [{**issue.__dict__, "net_pay": str(issue.net_pay)} for issue in issues],
        })


class PayrollBankUploadDownloadAPIView(APIView):
    """The bank upload spreadsheet itself. Optional ?batch_size=N splits it into a zip of "Batch N.xlsx" files."""

    permission_classes = [IsAuthenticated, CanManagePayroll]

    def get(self, request, period_id):
        period = get_object_or_404(PayrollPeriod, pk=period_id)
        if not period_is_locked(period):
            return Response({"detail": "Approve this payroll before creating the bank upload file."}, status=status.HTTP_400_BAD_REQUEST)
        try:
            batch_size = int(request.query_params["batch_size"]) if request.query_params.get("batch_size") else None
        except ValueError:
            return Response({"detail": "batch_size must be a whole number."}, status=status.HTTP_400_BAD_REQUEST)
        if batch_size is not None and not 1 <= batch_size <= 5000:
            return Response({"detail": "batch_size must be between 1 and 5000."}, status=status.HTTP_400_BAD_REQUEST)
        try:
            rows, issues = prepare_bank_upload(period)
        except ValueError as error:
            return Response({"detail": str(error)}, status=status.HTTP_400_BAD_REQUEST)
        if not rows:
            return Response({"detail": "Nobody in this payroll can be included in a bank upload file yet."}, status=status.HTTP_400_BAD_REQUEST)
        filename, content_type, content = bank_upload_download(period, rows, batch_size)
        total = sum((row.amount for row in rows), Decimal("0.00"))
        AuditService.log(
            event_type="payroll.bank_upload_exported", module="payroll", actor=request.user, object=period, severity=AuditSeverity.INFO,
            title="Bank upload file created", description=f"Bank upload file for {period.display_name}: {len(rows)} payment(s), {total:,.2f} total; {len(issues)} left out.",
            metadata={"period": period.display_name, "payments": len(rows), "total": str(total), "left_out": len(issues), "batch_size": batch_size},
        )
        response = HttpResponse(content, content_type=content_type)
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response


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
                expected_ids = set(employees_for_period(period).values_list("id", flat=True))
                payroll_ids = set(period.employee_payrolls.values_list("employee_id", flat=True))
                if payroll_ids != expected_ids:
                    return Response({"detail": "Payroll records do not match the employees employed during this period. Review and regenerate the payroll before approval."}, status=status.HTTP_400_BAD_REQUEST)
                try:
                    validate_attendance_for_approval(period)
                except ValueError as error:
                    return Response({"detail": str(error)}, status=status.HTTP_400_BAD_REQUEST)
                payrolls = list(period.employee_payrolls.select_related("employee").select_for_update())
                invalid_bank_count = sum(
                    1 for payroll in payrolls if payroll.net_pay > 0 and (
                        to_account_number(payroll.employee.account_number)[2]
                        or to_bank_code(payroll.employee.bank_code)[1]
                    )
                )
                if invalid_bank_count:
                    return Response({"detail": f"Payroll approval is blocked by {invalid_bank_count} employee(s) with incomplete bank details."}, status=status.HTTP_400_BAD_REQUEST)
                for payroll in payrolls:
                    employee = payroll.employee
                    payroll.bank_details_snapshot = {
                        "bank_name": employee.bank_name,
                        "account_number": employee.account_number,
                        "bank_code": employee.bank_code,
                    }
                if payrolls:
                    EmployeePayroll.objects.bulk_update(payrolls, ["bank_details_snapshot"])
            old_status = period.status
            period.status = target
            update_fields = ["status"]
            if target == PayrollPeriodStatus.APPROVED:
                period.approved_at = timezone.now()
                period.approved_by = request.user
                update_fields.extend(["approved_at", "approved_by"])
            period.save(update_fields=update_fields)
            if target == PayrollPeriodStatus.APPROVED:
                # The period is the gate, but each employee's record (and so their
                # payslip) must read approved too, not stay "Draft".
                period.employee_payrolls.filter(status__in=[EmployeePayrollStatus.DRAFT, EmployeePayrollStatus.REVIEW]).update(status=EmployeePayrollStatus.APPROVED)
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


class PayrollPeriodPayslipsAPIView(APIView):
    """Every payslip of a period in one call, for bulk printing."""

    permission_classes = [IsAuthenticated, CanViewPayroll]

    def get(self, request, period_id):
        period = get_object_or_404(PayrollPeriod, pk=period_id)
        payrolls = (
            EmployeePayroll.objects.filter(payroll_period=period)
            .select_related("employee", "employee__department", "employee__position", "payroll_period")
            .prefetch_related("line_items")
            .order_by("employee__department__name", "employee__employee_id")
        )
        department = request.query_params.get("department", "").strip()
        if department:
            payrolls = payrolls.filter(employee__department_id=department)
        if request.query_params.get("include_zero") != "1":
            payrolls = payrolls.filter(net_pay__gt=0)
        return Response({"period": period.display_name, "results": EmployeePayrollDetailSerializer(payrolls, many=True).data})


class EmployeePayrollDetailAPIView(APIView):
    permission_classes = [IsAuthenticated, CanViewPayroll]

    def get(self, request, payroll_id):
        payroll = get_object_or_404(EmployeePayroll.objects.select_related("employee", "employee__department", "employee__position", "payroll_period").prefetch_related("line_items"), pk=payroll_id)
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

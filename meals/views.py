from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.utils import timezone

from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from employees.models import Employee

from .models import (
    EmployeeMealEntitlement,
    MealCollection,
    MealDevice,
    MealExcessException,
    MealTicketRate,
)
from .serializers import (
    EmployeeMealEntitlementSerializer,
    MealDeviceSerializer,
    MealTicketRateSerializer,
)
from .services import MealService
from payroll.models import PayrollPeriod


class CanViewMealOperations(BasePermission):
    def has_permission(self, request, view):
        return (
            request.user.has_perm("meals.record_meal_operations")
            or request.user.has_perm("meals.review_meal_excess")
            or request.user.has_perm("meals.manage_meal_configuration")
        )



class CanReviewMealExcess(BasePermission):
    def has_permission(self, request, view):
        return request.user.has_perm("meals.review_meal_excess")


class CanManageMealConfiguration(BasePermission):
    def has_permission(self, request, view):
        return request.user.has_perm("meals.manage_meal_configuration")


class MealEntitlementListCreateAPIView(APIView):
    def get_permissions(self):
        permission = (
            CanManageMealConfiguration
            if self.request.method == "POST"
            else CanViewMealOperations
        )
        return [IsAuthenticated(), permission()]

    def get(self, request):
        entitlements = EmployeeMealEntitlement.objects.select_related(
            "employee", "set_by"
        ).order_by("employee_id", "-effective_from", "-id")
        if employee := request.query_params.get("employee"):
            entitlements = entitlements.filter(employee_id=employee)
        return Response({
            "count": entitlements.count(),
            "results": EmployeeMealEntitlementSerializer(entitlements, many=True).data,
        })

    def post(self, request):
        serializer = EmployeeMealEntitlementSerializer(
            data=request.data,
            context={"request": request},
        )
        serializer.is_valid(raise_exception=True)
        entitlement = serializer.save(set_by=request.user)
        return Response(
            EmployeeMealEntitlementSerializer(entitlement).data,
            status=201,
        )


class MealTicketRateListCreateAPIView(APIView):
    def get_permissions(self):
        permission = (
            CanManageMealConfiguration
            if self.request.method == "POST"
            else CanViewMealOperations
        )
        return [IsAuthenticated(), permission()]

    def get(self, request):
        rates = MealTicketRate.objects.order_by("-effective_from", "-id")
        return Response({
            "count": rates.count(),
            "results": MealTicketRateSerializer(rates, many=True).data,
        })

    def post(self, request):
        serializer = MealTicketRateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(
            MealTicketRateSerializer(serializer.save()).data,
            status=201,
        )


class MealDeviceListCreateAPIView(APIView):
    def get_permissions(self):
        permission = (
            CanManageMealConfiguration
            if self.request.method == "POST"
            else CanViewMealOperations
        )
        return [IsAuthenticated(), permission()]

    def get(self, request):
        devices = MealDevice.objects.order_by("name")
        return Response({
            "count": devices.count(),
            "results": MealDeviceSerializer(devices, many=True).data,
        })

    def post(self, request):
        serializer = MealDeviceSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(
            MealDeviceSerializer(serializer.save()).data,
            status=201,
        )


class MealDeviceDetailAPIView(APIView):
    permission_classes = [IsAuthenticated, CanManageMealConfiguration]

    def patch(self, request, pk):
        device = get_object_or_404(MealDevice, pk=pk)
        serializer = MealDeviceSerializer(device, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        return Response(MealDeviceSerializer(serializer.save()).data)


class MealExcessCancelAPIView(APIView):
    permission_classes = [IsAuthenticated, CanReviewMealExcess]

    def post(self, request, pk):
        exception = get_object_or_404(
            MealExcessException,
            pk=pk,
        )

        reason = str(request.data.get("reason", "")).strip()

        if not reason:
            return Response(
                {"detail": "A cancellation reason is required."},
                status=400,
            )

        try:
            MealService.cancel(
                exception,
                request.user,
                reason,
            )
        except ValueError as exc:
            return Response(
                {"detail": str(exc)},
                status=400,
            )

        return Response(
            {
                "id": exception.pk,
                "status": exception.status,
                "reviewer": exception.reviewer_id,
                "reviewed_at": exception.reviewed_at,
                "comment": exception.comment,
            }
        )


class MealExcessApproveAPIView(APIView):
    permission_classes = [IsAuthenticated, CanReviewMealExcess]

    def post(self, request, pk):
        exception = get_object_or_404(
            MealExcessException,
            pk=pk,
        )

        payroll_period_id = request.data.get("payroll_period")

        if not payroll_period_id:
            return Response(
                {"detail": "Payroll period is required."},
                status=400,
            )

        period = get_object_or_404(
            PayrollPeriod,
            pk=payroll_period_id,
        )

        comment = str(
            request.data.get("comment", "")
        ).strip()

        try:
            MealService.approve(
                exception,
                period,
                request.user,
                comment,
            )
        except ValueError as exc:
            return Response(
                {"detail": str(exc)},
                status=400,
            )

        return Response(
            {
                "id": exception.pk,
                "status": exception.status,
                "reviewer": exception.reviewer_id,
                "reviewed_at": exception.reviewed_at,
                "comment": exception.comment,
                "payroll_period": exception.payroll_period_id,
                "payroll": exception.payroll_id,
                "payroll_line_item": (
                    exception.payroll_line_item_id
                ),
                "proposed_deduction": (
                    exception.proposed_deduction
                ),
            }
        )   

class MealOperationsAPIView(APIView):
    permission_classes = [IsAuthenticated, CanViewMealOperations]

    def get(self, request):
        employee = request.query_params.get("employee")

        collections = (
            MealCollection.objects
            .select_related("employee", "event__device")
            .order_by("-event__timestamp")
        )

        if employee:
            collections = collections.filter(employee_id=employee)

        exceptions = (
            MealExcessException.objects
            .select_related("employee", "reviewer")
            .filter(status="pending")
            .order_by("-created_at")
        )

        return Response(
            {
                "collections": [
                    {
                        "id": x.pk,
                        "employee": x.employee_id,
                        "employee_name": x.employee.full_name,
                        "work_date": x.work_date,
                        "timestamp": x.event.timestamp,
                        "device": x.event.device.name,
                        "entitlement": x.entitlement_snapshot,
                        "sequence": x.sequence_number,
                        "rate": x.rate_snapshot,
                        "status": x.status,
                    }
                    for x in collections[:100]
                ],
                "exceptions": [
                    {
                        "id": x.pk,
                        "employee": x.employee_id,
                        "employee_name": x.employee.full_name,
                        "work_date": x.work_date,
                        "entitlement": x.entitlement_snapshot,
                        "collected_quantity": x.collected_quantity,
                        "excess_quantity": x.excess_quantity,
                        "rate": x.rate_snapshot,
                        "proposed_deduction": x.proposed_deduction,
                        "status": x.status,
                        "reviewer": x.reviewer_id,
                        "reviewed_at": x.reviewed_at,
                        "comment": x.comment,
                    }
                    for x in exceptions
                ],
            }
        )


class EmployeeMealsProfileAPIView(APIView):
    permission_classes = [IsAuthenticated, CanViewMealOperations]

    def get(self, request, pk):
        employee = get_object_or_404(Employee, pk=pk)

        collections = (
            MealCollection.objects
            .filter(employee=employee)
            .select_related("event__device", "shift")
            .order_by("-event__timestamp")[:20]
        )

        exceptions = (
            MealExcessException.objects
            .filter(employee=employee)
            .select_related("reviewer", "payroll_period", "payroll")
            .order_by("-work_date", "-created_at")[:20]
        )
        reference_date = timezone.localdate()

        approved_entitlement = MealService.approved_entitlement(
            employee,
            reference_date,
        )

        suggested_entitlement, suggested_rule = (
            MealService.suggested_entitlement(
                employee,
                reference_date,
            )
        )

        active_entitlement = (
            employee.meal_entitlements
            .filter(effective_from__lte=reference_date)
            .filter(
                Q(effective_to__isnull=True)
                | Q(effective_to__gte=reference_date)
            )
            .order_by("-effective_from", "-id")
            .first()
        )

        return Response(
            {
                "employee": {
                    "id": employee.pk,
                    "employee_id": employee.employee_id,
                    "name": employee.full_name,
                },
                "entitlement": {
                    "approved": approved_entitlement,
                    "effective_from": (
                        active_entitlement.effective_from
                        if active_entitlement
                        else None
                    ),
                    "effective_to": (
                        active_entitlement.effective_to
                        if active_entitlement
                        else None
                    ),
                    "reason": (
                        active_entitlement.reason
                        if active_entitlement
                        else ""
                    ),
                    "is_exceptional_override": (
                        active_entitlement.is_exceptional_override
                        if active_entitlement
                        else False
                    ),
                },
                "suggestion": {
                    "tickets_per_work_day": suggested_entitlement,
                    "rule_id": (
                        suggested_rule.pk
                        if suggested_rule
                        else None
                    ),
                    "reason": (
                        suggested_rule.description
                        if suggested_rule
                        else ""
                    ),
                },
                "collections": [
                    {
                        "id": x.pk,
                        "work_date": x.work_date,
                        "timestamp": x.event.timestamp,
                        "device": x.event.device.name,
                        "shift": (
                            x.shift.name
                            if x.shift
                            else None
                        ),
                        "sequence": x.sequence_number,
                        "entitlement": x.entitlement_snapshot,
                        "rate": x.rate_snapshot,
                        "status": x.status,
                    }
                    for x in collections
                ],
                "exceptions": [
                    {
                        "id": x.pk,
                        "work_date": x.work_date,
                        "entitlement": x.entitlement_snapshot,
                        "collected_quantity": x.collected_quantity,
                        "excess_quantity": x.excess_quantity,
                        "rate": x.rate_snapshot,
                        "proposed_deduction": x.proposed_deduction,
                        "status": x.status,
                        "reviewer": x.reviewer_id,
                        "reviewed_at": x.reviewed_at,
                        "comment": x.comment,
                        "payroll_period": x.payroll_period_id,
                        "payroll": x.payroll_id,
                    }
                    for x in exceptions
                ],
            }
        )

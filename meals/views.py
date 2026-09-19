from django.db.models import Q, Sum
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
    MealEntitlementRule,
    MealExcessException,
    MealTicketRate,
    MealVendorPayment,
)
from .serializers import (
    EmployeeMealEntitlementSerializer,
    MealDeviceSerializer,
    MealEntitlementRuleSerializer,
    MealTicketRateSerializer,
    MealVendorPaymentSerializer,
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


class MealEntitlementRuleListCreateAPIView(APIView):
    def get_permissions(self):
        permission = (
            CanManageMealConfiguration
            if self.request.method == "POST"
            else CanViewMealOperations
        )
        return [IsAuthenticated(), permission()]

    def get(self, request):
        rules = MealEntitlementRule.objects.select_related("position").order_by("priority", "id")
        return Response({
            "count": rules.count(),
            "results": MealEntitlementRuleSerializer(rules, many=True).data,
        })

    def post(self, request):
        serializer = MealEntitlementRuleSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(
            MealEntitlementRuleSerializer(serializer.save()).data,
            status=201,
        )


class MealEntitlementRuleDetailAPIView(APIView):
    permission_classes = [IsAuthenticated, CanManageMealConfiguration]

    def patch(self, request, pk):
        rule = get_object_or_404(MealEntitlementRule, pk=pk)
        serializer = MealEntitlementRuleSerializer(rule, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        return Response(MealEntitlementRuleSerializer(serializer.save()).data)


class MealReviewRemindersAPIView(APIView):
    permission_classes = [IsAuthenticated, CanManageMealConfiguration]

    def get(self, request):
        employees = MealService.employees_due_for_meal_review(timezone.localdate())
        return Response({
            "count": len(employees),
            "results": [
                {
                    "id": employee.pk,
                    "employee_id": employee.employee_id,
                    "name": employee.full_name,
                    "employment_date": employee.employment_date,
                    "position": employee.position.name if employee.position_id else "",
                }
                for employee in employees
            ],
        })


class MealVendorPeriodAPIView(APIView):
    permission_classes = [IsAuthenticated, CanViewMealOperations]

    def get(self, request, payroll_period_id):
        period = get_object_or_404(PayrollPeriod, pk=payroll_period_id)

        collections = MealCollection.objects.filter(
            work_date__gte=period.start_date,
            work_date__lte=period.end_date,
            voided_at__isnull=True,
        )
        tickets_issued = collections.count()
        amount_owed = collections.aggregate(total=Sum("rate_snapshot"))["total"] or 0

        payments = MealVendorPayment.objects.filter(payroll_period=period).select_related("recorded_by")
        total_paid = payments.aggregate(total=Sum("amount"))["total"] or 0

        return Response({
            "payroll_period": period.pk,
            "tickets_issued": tickets_issued,
            "amount_owed": amount_owed,
            "total_paid": total_paid,
            "balance": amount_owed - total_paid,
            "payments": MealVendorPaymentSerializer(payments, many=True).data,
        })


class MealVendorPaymentListCreateAPIView(APIView):
    permission_classes = [IsAuthenticated, CanManageMealConfiguration]

    def post(self, request):
        serializer = MealVendorPaymentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        payment = serializer.save(recorded_by=request.user)
        return Response(
            MealVendorPaymentSerializer(payment).data,
            status=201,
        )


class MealDeviceListAPIView(APIView):
    """Read-only: devices are registered on the Biometric Devices page
    (purpose=meal_ticket) and mirrored here automatically - see
    attendance.views.devices.sync_meal_device. There's deliberately no
    create/update here, since a MealDevice with no matching BiometricDevice
    is one the AiFace gateway can't route scans to.
    """

    permission_classes = [IsAuthenticated, CanViewMealOperations]

    def get(self, request):
        devices = MealDevice.objects.order_by("name")
        return Response({
            "count": devices.count(),
            "results": MealDeviceSerializer(devices, many=True).data,
        })


class MealExcessCancelAPIView(APIView):
    permission_classes = [IsAuthenticated, CanReviewMealExcess]

    def post(self, request, pk):
        exception = get_object_or_404(
            MealExcessException,
            pk=pk,
        )

        reason = str(request.data.get("reason", "")).strip()

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
        period = get_object_or_404(PayrollPeriod, pk=payroll_period_id) if payroll_period_id else None

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
            .select_related("employee", "event__device", "excess_exception")
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

        shown = list(collections[:100])

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
                        "voided": x.voided_at is not None,
                        "void_reason": x.void_reason,
                        "excess_id": x.excess_exception_id,
                        "excess_status": x.excess_exception.status if x.excess_exception_id else None,
                    }
                    for x in shown
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


class MealExcessDeclineAPIView(APIView):
    permission_classes = [IsAuthenticated, CanReviewMealExcess]

    def post(self, request, pk):
        exception = get_object_or_404(MealExcessException, pk=pk)
        try:
            MealService.decline(exception, request.user, str(request.data.get("reason", "")))
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=400)
        return Response({"id": exception.pk, "status": exception.status, "reviewer": exception.reviewer_id, "reviewed_at": exception.reviewed_at, "comment": exception.comment})


class MealCollectionVoidAPIView(APIView):
    permission_classes = [IsAuthenticated, CanReviewMealExcess]

    def post(self, request, pk):
        collection = get_object_or_404(MealCollection.objects.select_related("employee"), pk=pk)
        try:
            MealService.void_collection(collection, request.user, str(request.data.get("reason", "")))
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=400)
        return Response({"id": collection.pk, "voided": True, "void_reason": collection.void_reason})


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

        today_date, today_roster = MealService.resolve_work_day(employee, timezone.now())
        return Response(
            {
                "employee": {
                    "id": employee.pk,
                    "employee_id": employee.employee_id,
                    "name": employee.full_name,
                },
                # A scan only earns a ticket on a rostered WORK day, so show what today looks like.
                "today": {
                    "work_date": today_date,
                    "roster_status": today_roster.status if today_roster else "none",
                    "shift": today_roster.shift.name if today_roster and today_roster.shift else None,
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
                        "voided": x.voided_at is not None,
                        "void_reason": x.void_reason,
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

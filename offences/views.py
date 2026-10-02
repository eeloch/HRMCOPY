from django.http import Http404
from django.shortcuts import get_object_or_404

from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from employees.models import Employee
from .models import EmployeeOffence, EmployeeOffenceStatus, OffenceType, RewardType
from audit.models import AuditSeverity
from audit.services import AuditService
from .serializers import EmployeeOffenceSerializer, OffenceTypeSerializer, RewardTypeSerializer
from .services import OffenceService


class CanRecordOffences(BasePermission):
    def has_permission(self, request, view):
        return request.user.has_perm("offences.record_employee_offences")


class CanReviewOffences(BasePermission):
    def has_permission(self, request, view):
        return request.user.has_perm("offences.review_employee_offences")


class CanManageOffenceConfiguration(BasePermission):
    def has_permission(self, request, view):
        return request.user.has_perm("offences.manage_offence_configuration")


class CanViewOffences(BasePermission):
    def has_permission(self, request, view):
        return (
            request.user.has_perm("offences.record_employee_offences")
            or request.user.has_perm("offences.review_employee_offences")
            or request.user.has_perm("offences.manage_offence_configuration")
        )


class OffenceTypeListCreateAPIView(APIView):
    def get_permissions(self):
        permission = (
            CanManageOffenceConfiguration
            if self.request.method == "POST"
            else CanViewOffences
        )
        return [IsAuthenticated(), permission()]

    def get(self, request):
        offence_types = OffenceType.objects.order_by("sort_order", "category", "name")
        return Response({
            "count": offence_types.count(),
            "results": OffenceTypeSerializer(offence_types, many=True).data,
        })

    def post(self, request):
        serializer = OffenceTypeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(
            OffenceTypeSerializer(serializer.save()).data,
            status=201,
        )


POLICY_FIELDS = {
    "offence": ("category", "name", "penalty_first", "penalty_second", "penalty_third", "amount_first", "amount_second", "amount_third", "active"),
    "reward": ("category", "name", "reward_first", "reward_second", "amount_first", "amount_second", "active"),
}


def _log_policy_change(request, kind, instance, before):
    """Who changed which penalty or amount in the policy, from what to what - these set what staff are fined or paid."""
    changes = {
        field: {"from": str(before[field]) if before[field] is not None else None, "to": str(getattr(instance, field)) if getattr(instance, field) is not None else None}
        for field in POLICY_FIELDS[kind]
        if before[field] != getattr(instance, field)
    }
    if not changes:
        return
    AuditService.log(
        event_type=f"offences.{kind}_policy_changed",
        module="offences",
        actor=request.user,
        object=instance,
        severity=AuditSeverity.WARNING,
        title=f"{'Offence' if kind == 'offence' else 'Reward'} policy changed",
        description=f"{instance.name}: " + ", ".join(f"{field.replace('_', ' ')}" for field in changes) + " changed.",
        metadata={"id": instance.pk, "name": instance.name, "changes": changes},
    )


class OffenceTypeDetailAPIView(APIView):
    permission_classes = [IsAuthenticated, CanManageOffenceConfiguration]

    def patch(self, request, pk):
        offence_type = get_object_or_404(OffenceType, pk=pk)
        before = {field: getattr(offence_type, field) for field in POLICY_FIELDS["offence"]}
        serializer = OffenceTypeSerializer(offence_type, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        saved = serializer.save()
        _log_policy_change(request, "offence", saved, before)
        return Response(OffenceTypeSerializer(saved).data)


class RewardTypeDetailAPIView(APIView):
    permission_classes = [IsAuthenticated, CanManageOffenceConfiguration]

    def patch(self, request, pk):
        reward_type = get_object_or_404(RewardType, pk=pk)
        before = {field: getattr(reward_type, field) for field in POLICY_FIELDS["reward"]}
        serializer = RewardTypeSerializer(reward_type, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        saved = serializer.save()
        _log_policy_change(request, "reward", saved, before)
        return Response(RewardTypeSerializer(saved).data)


class EmployeeOffenceListCreateAPIView(APIView):
    def get_permissions(self):
        permission = (
            CanRecordOffences
            if self.request.method == "POST"
            else CanViewOffences
        )
        return [IsAuthenticated(), permission()]

    def get(self, request):
        offences = EmployeeOffence.objects.select_related(
            "employee", "offence_type", "recorded_by", "reviewer"
        ).order_by("-incident_date", "-id")
        if status_filter := request.query_params.get("status"):
            offences = offences.filter(status=status_filter)
        if employee_id := request.query_params.get("employee"):
            offences = offences.filter(employee_id=employee_id)
        return Response({
            "count": offences.count(),
            "results": EmployeeOffenceSerializer(offences, many=True).data,
        })

    def post(self, request):
        data = request.data.copy()
        if data.get("amount") in ("", None):
            data.pop("amount", None)
        serializer = EmployeeOffenceSerializer(data=data)
        serializer.is_valid(raise_exception=True)
        offence_type = serializer.validated_data["offence_type"]
        employee = serializer.validated_data["employee"]
        occurrence = _occurrence_for(employee, offence_type)
        penalty_text, tier_amount = offence_type.penalty_for(occurrence)
        extra = {"occurrence": occurrence, "penalty_text": penalty_text}
        if "amount" not in serializer.validated_data:
            extra["amount"] = tier_amount or 0
        offence = serializer.save(recorded_by=request.user, **extra)
        OffenceService.notify_reviewers_of_pending_offence(offence)
        return Response(
            EmployeeOffenceSerializer(offence).data,
            status=201,
        )


def _occurrence_for(employee, offence_type):
    """Which time this is: one more than the offences of this kind already logged for the person (not rejected)."""
    earlier = EmployeeOffence.objects.filter(employee=employee, offence_type=offence_type).exclude(status=EmployeeOffenceStatus.REJECTED).count()
    return earlier + 1


class OffencePenaltyPreviewAPIView(APIView):
    """What the policy says should happen if this person does this offence now - shown before it is logged."""

    permission_classes = [IsAuthenticated, CanRecordOffences]

    def get(self, request):
        employee = get_object_or_404(Employee, pk=_int_or_404(request.query_params.get("employee")))
        offence_type = get_object_or_404(OffenceType, pk=_int_or_404(request.query_params.get("offence_type")))
        occurrence = _occurrence_for(employee, offence_type)
        text, amount = offence_type.penalty_for(occurrence)
        return Response({"occurrence": occurrence, "penalty_text": text, "amount": str(amount) if amount is not None else None})


def _int_or_404(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        raise Http404


class RewardTypeListAPIView(APIView):
    permission_classes = [IsAuthenticated, CanViewOffences]

    def get(self, request):
        rewards = RewardType.objects.order_by("sort_order", "category", "name")
        return Response({"count": rewards.count(), "results": RewardTypeSerializer(rewards, many=True).data})


class EmployeeOffenceApproveAPIView(APIView):
    permission_classes = [IsAuthenticated, CanReviewOffences]

    def post(self, request, pk):
        offence = get_object_or_404(EmployeeOffence, pk=pk)
        comment = str(request.data.get("comment", "")).strip()
        try:
            OffenceService.approve(offence, request.user, comment)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=400)
        return Response(EmployeeOffenceSerializer(offence).data)


class EmployeeOffenceRejectAPIView(APIView):
    permission_classes = [IsAuthenticated, CanReviewOffences]

    def post(self, request, pk):
        offence = get_object_or_404(EmployeeOffence, pk=pk)
        reason = str(request.data.get("reason", "")).strip()
        if not reason:
            return Response({"detail": "A rejection reason is required."}, status=400)
        try:
            OffenceService.reject(offence, request.user, reason)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=400)
        return Response(EmployeeOffenceSerializer(offence).data)

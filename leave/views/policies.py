from decimal import Decimal, InvalidOperation

from rest_framework import status
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from audit.models import AuditSeverity
from audit.services import AuditService
from employees.models import EmploymentType
from leave.models import LeavePolicy, LeaveType


class CanManageLeavePolicy(BasePermission):
    def has_permission(self, request, view):
        return request.user.has_perm("leave.manage_leave_policy")


class LeavePolicyMatrixAPIView(APIView):
    """The full leave-entitlement matrix: every leave type against every employment type, so a gap (an
    employment type nobody ever set an entitlement for) is visible instead of silently blocking requests."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        leave_types = LeaveType.objects.filter(is_active=True).order_by("name")
        policies = LeavePolicy.objects.filter(leave_type__in=leave_types).select_related("leave_type")
        return Response({
            "leave_types": [
                {"id": lt.pk, "code": lt.code, "name": lt.name, "default_days": lt.default_days}
                for lt in leave_types
            ],
            "employment_types": [{"value": value, "label": label} for value, label in EmploymentType.choices],
            "policies": [
                {
                    "id": policy.pk,
                    "leave_type": policy.leave_type_id,
                    "employment_type": policy.employment_type,
                    "allocated_days": policy.allocated_days,
                    "is_paid": policy.is_paid,
                    "requires_approval": policy.requires_approval,
                    "is_active": policy.is_active,
                }
                for policy in policies
            ],
        })


class LeavePolicySetAPIView(APIView):
    """Create or update the entitlement for one (leave type, employment type) cell. This is the only way
    that combination becomes requestable/approvable - a combination with no row here has no entitlement."""

    permission_classes = [IsAuthenticated, CanManageLeavePolicy]

    def post(self, request):
        leave_type_id = request.data.get("leave_type")
        employment_type = request.data.get("employment_type")
        valid_employment_types = {value for value, _ in EmploymentType.choices}

        if employment_type not in valid_employment_types:
            return Response({"detail": "Choose a valid employment type."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            leave_type = LeaveType.objects.get(pk=leave_type_id, is_active=True)
        except (LeaveType.DoesNotExist, ValueError, TypeError):
            return Response({"detail": "Choose a valid leave type."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            allocated_days = Decimal(str(request.data.get("allocated_days", "0")))
        except InvalidOperation:
            return Response({"detail": "Allocated days must be a number."}, status=status.HTTP_400_BAD_REQUEST)
        if allocated_days < 0:
            return Response({"detail": "Allocated days cannot be negative."}, status=status.HTTP_400_BAD_REQUEST)

        is_paid = bool(request.data.get("is_paid", True))
        requires_approval = bool(request.data.get("requires_approval", True))
        is_active = bool(request.data.get("is_active", True))

        policy, created = LeavePolicy.objects.update_or_create(
            leave_type=leave_type,
            employment_type=employment_type,
            defaults={
                "allocated_days": allocated_days,
                "is_paid": is_paid,
                "requires_approval": requires_approval,
                "is_active": is_active,
            },
        )

        AuditService.log(
            event_type="leave.policy_set",
            module="leave",
            actor=request.user,
            object=policy,
            severity=AuditSeverity.INFO,
            title="Leave policy set" if created else "Leave policy updated",
            description=f"{leave_type.name} for {policy.get_employment_type_display()}: {allocated_days} day(s).",
            metadata={
                "leave_type": leave_type.name,
                "employment_type": employment_type,
                "allocated_days": str(allocated_days),
                "is_paid": is_paid,
                "is_active": is_active,
            },
        )

        return Response({
            "id": policy.pk,
            "leave_type": policy.leave_type_id,
            "employment_type": policy.employment_type,
            "allocated_days": policy.allocated_days,
            "is_paid": policy.is_paid,
            "requires_approval": policy.requires_approval,
            "is_active": policy.is_active,
        }, status=status.HTTP_200_OK)

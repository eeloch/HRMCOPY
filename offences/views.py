from django.shortcuts import get_object_or_404

from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import EmployeeOffence, OffenceType
from .serializers import EmployeeOffenceSerializer, OffenceTypeSerializer
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
        offence_types = OffenceType.objects.order_by("name")
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


class OffenceTypeDetailAPIView(APIView):
    permission_classes = [IsAuthenticated, CanManageOffenceConfiguration]

    def patch(self, request, pk):
        offence_type = get_object_or_404(OffenceType, pk=pk)
        serializer = OffenceTypeSerializer(offence_type, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        return Response(OffenceTypeSerializer(serializer.save()).data)


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
        if not data.get("amount"):
            offence_type = get_object_or_404(OffenceType, pk=data.get("offence_type"))
            data["amount"] = offence_type.default_amount
        serializer = EmployeeOffenceSerializer(data=data)
        serializer.is_valid(raise_exception=True)
        offence = serializer.save(recorded_by=request.user)
        OffenceService.notify_reviewers_of_pending_offence(offence)
        return Response(
            EmployeeOffenceSerializer(offence).data,
            status=201,
        )


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

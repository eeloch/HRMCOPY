"""Biometric device fleet management: list, register, and edit terminals."""

from rest_framework import status
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from attendance.models import BiometricDevice
from attendance.serializers import BiometricDeviceSerializer


class CanManageDevices(BasePermission):
    def has_permission(self, request, view):
        return request.user.has_perm("attendance.manage_devices")


class BiometricDeviceListCreateAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get_permissions(self):
        if self.request.method == "POST":
            return [IsAuthenticated(), CanManageDevices()]
        return super().get_permissions()

    def get(self, request):
        devices = BiometricDevice.objects.order_by("name")
        if purpose := request.query_params.get("purpose"):
            devices = devices.filter(purpose=purpose)
        return Response({"count": devices.count(), "results": BiometricDeviceSerializer(devices, many=True).data})

    def post(self, request):
        serializer = BiometricDeviceSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class BiometricDeviceDetailAPIView(APIView):
    permission_classes = [IsAuthenticated, CanManageDevices]

    def patch(self, request, device_id):
        try:
            device = BiometricDevice.objects.get(pk=device_id)
        except BiometricDevice.DoesNotExist:
            return Response({"detail": "Device not found."}, status=status.HTTP_404_NOT_FOUND)
        serializer = BiometricDeviceSerializer(device, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)

    def delete(self, request, device_id):
        deleted, _ = BiometricDevice.objects.filter(pk=device_id).delete()
        if not deleted:
            return Response({"detail": "Device not found."}, status=status.HTTP_404_NOT_FOUND)
        return Response(status=status.HTTP_204_NO_CONTENT)

"""Biometric device fleet management: list, register, and edit terminals."""

from rest_framework import status
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from attendance.integrations.aiface_protocol import IDENTITY_SYSTEM
from attendance.models import BiometricDevice, DeviceCommand
from attendance.serializers import BiometricDeviceSerializer, DeviceCommandSerializer
from employees.models import BiometricIdentity, Employee

BIOMETRIC_TYPES = {"face", "fingerprint"}


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


class DeviceCommandListCreateAPIView(APIView):
    """Queue admin actions (enroll/delete/refresh enrolled ids) for a device.

    The AiFace gateway process (a separate long-running command, not this
    request/response view) polls for "pending" rows here and delivers them to
    the device's live connection - see run_aiface_gateway's `_poll_commands`.
    """

    permission_classes = [IsAuthenticated]

    def get_permissions(self):
        if self.request.method == "POST":
            return [IsAuthenticated(), CanManageDevices()]
        return super().get_permissions()

    def get(self, request, device_id):
        commands = DeviceCommand.objects.filter(device_id=device_id).order_by("-created_at")[:20]
        return Response({"results": DeviceCommandSerializer(commands, many=True).data})

    def post(self, request, device_id):
        try:
            device = BiometricDevice.objects.get(pk=device_id)
        except BiometricDevice.DoesNotExist:
            return Response({"detail": "Device not found."}, status=status.HTTP_404_NOT_FOUND)

        if DeviceCommand.objects.filter(device=device, status__in=("pending", "sent")).exists():
            return Response(
                {"detail": "A command is already queued or in progress for this device. Wait for it to finish first."},
                status=status.HTTP_409_CONFLICT,
            )

        command_type = request.data.get("command_type")
        if command_type == "refresh_enrolled_ids":
            payload = {}
        elif command_type in ("enroll_user", "delete_user"):
            payload_or_error = self._build_membership_payload(request, device, command_type)
            if isinstance(payload_or_error, Response):
                return payload_or_error
            payload = payload_or_error
        else:
            return Response({"detail": "command_type must be one of enroll_user, delete_user, refresh_enrolled_ids."}, status=status.HTTP_400_BAD_REQUEST)

        command = DeviceCommand.objects.create(
            device=device, command_type=command_type, payload=payload,
            requested_by=request.user if request.user.is_authenticated else None,
        )
        return Response(DeviceCommandSerializer(command).data, status=status.HTTP_201_CREATED)

    @staticmethod
    def _build_membership_payload(request, device, command_type):
        employee_id = request.data.get("employee")
        if not employee_id:
            return Response({"detail": "employee is required."}, status=status.HTTP_400_BAD_REQUEST)
        try:
            employee = Employee.objects.get(pk=employee_id)
        except Employee.DoesNotExist:
            return Response({"detail": "Employee not found."}, status=status.HTTP_404_NOT_FOUND)

        existing_identity = BiometricIdentity.objects.filter(
            employee=employee, system=IDENTITY_SYSTEM, source_identifier=device.serial_number,
        ).first()

        if command_type == "delete_user":
            if not existing_identity:
                return Response({"detail": "This employee is not enrolled on this device."}, status=status.HTTP_400_BAD_REQUEST)
            return {"enrollid": int(existing_identity.external_user_id), "employee_id": employee.id}

        biometric_type = request.data.get("biometric_type", "face")
        if biometric_type not in BIOMETRIC_TYPES:
            return Response({"detail": "biometric_type must be 'face' or 'fingerprint'."}, status=status.HTTP_400_BAD_REQUEST)
        enrollid = int(existing_identity.external_user_id) if existing_identity else DeviceCommandListCreateAPIView._next_free_enrollid(device)
        return {"enrollid": enrollid, "employee_id": employee.id, "name": employee.full_name, "biometric_type": biometric_type}

    @staticmethod
    def _next_free_enrollid(device):
        used_ids = BiometricIdentity.objects.filter(
            system=IDENTITY_SYSTEM, source_identifier=device.serial_number,
        ).values_list("external_user_id", flat=True)
        numeric_ids = [int(value) for value in used_ids if value.isdigit()]
        return max(numeric_ids, default=0) + 1

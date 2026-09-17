"""Biometric device fleet management: list, register, and edit terminals."""

from rest_framework import status
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from attendance.integrations.aiface_protocol import IDENTITY_SYSTEM
from attendance.models import BiometricDevice, DeviceCommand
from attendance.serializers import BiometricDeviceSerializer, DeviceCommandSerializer
from audit.models import AuditSeverity
from audit.services import AuditService
from employees.models import BiometricIdentity, Employee

BIOMETRIC_TYPES = {"face", "fingerprint"}

YUNATT_SYSTEM = "yunatt"
YUNATT_SOURCE = "cloud"


def sync_meal_device(biometric_device):
    """Mirror a meal_ticket-purpose BiometricDevice into meals.MealDevice.

    meals.MealEvent has its own FK to a separate MealDevice row (kept
    distinct rather than migrated, to avoid touching existing meal
    history), so registering a device once here is enough for both the
    attendance gateway routing and meal-ticket ingestion to recognize it.
    Deferred import: attendance and meals would otherwise import each
    other at module load time (meals.models already imports from
    attendance.models).
    """
    from meals.models import MealDevice

    if biometric_device.purpose == "meal_ticket":
        MealDevice.objects.update_or_create(
            serial_number=biometric_device.serial_number,
            defaults={"name": biometric_device.name, "active": True},
        )
    else:
        MealDevice.objects.filter(serial_number=biometric_device.serial_number).update(active=False)


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
        device = serializer.save()
        sync_meal_device(device)
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
        sync_meal_device(device)
        return Response(serializer.data)

    def delete(self, request, device_id):
        serial_number = BiometricDevice.objects.filter(pk=device_id).values_list("serial_number", flat=True).first()
        deleted, _ = BiometricDevice.objects.filter(pk=device_id).delete()
        if not deleted:
            return Response({"detail": "Device not found."}, status=status.HTTP_404_NOT_FOUND)
        if serial_number:
            from meals.models import MealDevice
            MealDevice.objects.filter(serial_number=serial_number).update(active=False)
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

        DeviceCommand.expire_stale(device=device)
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


class DeviceReconcileEnrolledIdsAPIView(APIView):
    """Bulk-link a device's already-enrolled IDs to employees by matching numbers.

    For a device that was enrolled before this system existed (staff scanned
    directly at the terminal, with the terminal's own enrollid set to the
    employee's numeric staff ID), this replaces enrolling everyone from
    scratch: it reads the most recent "Check Who's Enrolled" result and
    creates a BiometricIdentity for every enrollid that unambiguously matches
    exactly one employee's ID, leaving anything uncertain for manual review
    rather than guessing.
    """

    permission_classes = [IsAuthenticated, CanManageDevices]

    def post(self, request, device_id):
        try:
            device = BiometricDevice.objects.get(pk=device_id)
        except BiometricDevice.DoesNotExist:
            return Response({"detail": "Device not found."}, status=status.HTTP_404_NOT_FOUND)

        latest_refresh = DeviceCommand.objects.filter(
            device=device, command_type="refresh_enrolled_ids", status="acked",
        ).order_by("-id").first()
        if not latest_refresh:
            return Response(
                {"detail": "Run 'Check Who's Enrolled' for this device first, and wait for it to complete."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        device_ids = latest_refresh.result.get("record") or []
        numeric_map, ambiguous_ids = self._build_numeric_employee_map()

        linked = []
        already_linked = 0
        conflicts = []
        unmatched = []

        for device_id_value in device_ids:
            if BiometricIdentity.objects.filter(
                system=IDENTITY_SYSTEM, source_identifier=device.serial_number, external_user_id=device_id_value,
            ).exists():
                already_linked += 1
                continue

            normalized = str(int(device_id_value)) if device_id_value.isdigit() else None
            if normalized is None or normalized in ambiguous_ids:
                unmatched.append(device_id_value)
                continue

            employee = numeric_map.get(normalized)
            if employee is None:
                unmatched.append(device_id_value)
                continue

            existing_for_employee = BiometricIdentity.objects.filter(
                employee=employee, system=IDENTITY_SYSTEM, source_identifier=device.serial_number,
            ).first()
            if existing_for_employee:
                conflicts.append({
                    "device_id": device_id_value,
                    "employee_id": employee.employee_id,
                    "already_linked_to_device_id": existing_for_employee.external_user_id,
                })
                continue

            BiometricIdentity.objects.create(
                employee=employee, system=IDENTITY_SYSTEM,
                source_identifier=device.serial_number, external_user_id=device_id_value, is_active=True,
            )
            linked.append({"device_id": device_id_value, "employee_id": employee.employee_id, "employee_name": employee.full_name})

        return Response({
            "total_enrolled_on_device": len(device_ids),
            "linked": len(linked),
            "already_linked": already_linked,
            "unmatched": unmatched,
            "conflicts": conflicts,
            "linked_employees": linked,
        })

    @staticmethod
    def _build_numeric_employee_map():
        """{normalized numeric staff id: Employee}, excluding any id shared by more than one employee record."""
        groups = {}
        for employee in Employee.objects.only("id", "employee_id", "first_name", "middle_name", "last_name"):
            if not employee.employee_id.isdigit():
                continue
            key = str(int(employee.employee_id))
            groups.setdefault(key, []).append(employee)

        numeric_map = {key: matches[0] for key, matches in groups.items() if len(matches) == 1}
        ambiguous_ids = {key for key, matches in groups.items() if len(matches) > 1}
        return numeric_map, ambiguous_ids


class PersonInformationImportAPIView(APIView):
    """Bulk-link a Yunatt cloud "Person Information" export to employees.

    For staff already enrolled directly at a terminal before this system
    existed, this avoids re-enrolling everyone from scratch: it reads the
    vendor's "Person Information" spreadsheet export and creates a
    yunatt/cloud BiometricIdentity for every row that matches exactly one
    employee - by staff number when the sheet's "ID No" column was filled
    in at enrollment, otherwise by an unambiguous exact name match -
    leaving anything uncertain for manual review rather than guessing.
    """

    permission_classes = [IsAuthenticated, CanManageDevices]

    def post(self, request):
        upload = request.FILES.get("file")
        if not upload:
            return Response(
                {"detail": "A Person Information file is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            rows = self._read_person_information(upload)
        except Exception as exc:
            return Response(
                {"detail": f"Unable to read this file: {exc}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        employees_by_staff_id = {}
        name_groups = {}
        for employee in Employee.objects.only("id", "employee_id", "first_name", "middle_name", "last_name"):
            employees_by_staff_id[employee.employee_id.strip()] = employee
            name_groups.setdefault(employee.full_name.strip().lower(), []).append(employee)

        linked, conflicts, unmatched = [], [], []
        already_linked = 0

        for row in rows:
            external_id = self._normalize_id(row.get("User ID"))
            name = str(row.get("Name") or "").strip()
            id_no = str(row.get("ID No") or "").strip()
            department = str(row.get("Department") or "").strip()
            if not external_id or not name:
                continue

            if BiometricIdentity.objects.filter(
                system=YUNATT_SYSTEM, source_identifier=YUNATT_SOURCE, external_user_id=external_id,
            ).exists():
                already_linked += 1
                continue

            employee = employees_by_staff_id.get(id_no) if id_no else None
            if employee is None:
                candidates = name_groups.get(name.lower(), [])
                employee = candidates[0] if len(candidates) == 1 else None

            if employee is None:
                unmatched.append({"user_id": external_id, "name": name, "department": department})
                continue

            existing = BiometricIdentity.objects.filter(
                employee=employee, system=YUNATT_SYSTEM, source_identifier=YUNATT_SOURCE,
            ).first()
            if existing:
                conflicts.append({
                    "user_id": external_id,
                    "employee_id": employee.employee_id,
                    "employee_name": employee.full_name,
                    "already_linked_to_user_id": existing.external_user_id,
                })
                continue

            BiometricIdentity.objects.create(
                employee=employee, system=YUNATT_SYSTEM, source_identifier=YUNATT_SOURCE,
                external_user_id=external_id, is_active=True,
            )
            linked.append({"user_id": external_id, "employee_id": employee.employee_id, "employee_name": employee.full_name})

        AuditService.log(
            event_type="attendance.person_information_imported",
            module="attendance",
            actor=request.user,
            severity=AuditSeverity.SUCCESS,
            title="Person Information import",
            description=f"Linked {len(linked)} employee(s) from an uploaded Person Information export.",
            metadata={
                "total_rows": len(rows),
                "linked": len(linked),
                "already_linked": already_linked,
                "conflicts": len(conflicts),
                "unmatched": len(unmatched),
            },
        )

        return Response({
            "total_rows": len(rows),
            "linked": len(linked),
            "already_linked": already_linked,
            "conflicts": conflicts,
            "unmatched": unmatched,
            "linked_employees": linked,
        })

    @staticmethod
    def _normalize_id(value):
        if value is None:
            return ""
        if isinstance(value, float):
            return str(int(value)) if value.is_integer() else str(value)
        return str(value).strip()

    @staticmethod
    def _read_person_information(upload):
        filename = (upload.name or "").lower()
        rows = []

        if filename.endswith(".xls"):
            import xlrd

            book = xlrd.open_workbook(file_contents=upload.read())
            sheet = book.sheet_by_index(0)
            headers = [str(sheet.cell_value(0, c)).strip() for c in range(sheet.ncols)]
            for r in range(1, sheet.nrows):
                values = [sheet.cell_value(r, c) for c in range(sheet.ncols)]
                rows.append(dict(zip(headers, values)))
        elif filename.endswith(".xlsx"):
            import openpyxl

            workbook = openpyxl.load_workbook(upload, read_only=True, data_only=True)
            sheet = workbook.active
            rows_iter = sheet.iter_rows(values_only=True)
            headers = [str(h).strip() if h is not None else "" for h in next(rows_iter)]
            for values in rows_iter:
                rows.append(dict(zip(headers, values)))
        else:
            raise ValueError("Upload the .xls or .xlsx Person Information export from the terminal software.")

        return rows


class BiometricsOverviewAPIView(APIView):
    """Active staff vs. who actually has a working biometric identity.

    Enrollment happens outside this system (at a terminal, or via the
    Person Information import) - this just compares the two lists so
    nobody has to track it by memory or a stale one-off count.
    """

    permission_classes = [IsAuthenticated, CanManageDevices]

    def get(self, request):
        active_employees = (
            Employee.objects
            .filter(status="active")
            .select_related("department", "position")
            .order_by("department__name", "last_name", "first_name")
        )

        enrolled_employee_ids = set(
            BiometricIdentity.objects
            .filter(is_active=True, employee__status="active")
            .values_list("employee_id", flat=True)
            .distinct()
        )

        not_enrolled = []
        for employee in active_employees:
            if employee.id in enrolled_employee_ids:
                continue
            not_enrolled.append({
                "id": employee.pk,
                "employee_id": employee.employee_id,
                "name": employee.full_name,
                "department": employee.department.name if employee.department_id else "",
                "position": employee.position.name if employee.position_id else "",
                "employment_type": employee.employment_type,
            })

        total_active = active_employees.count()
        total_enrolled = total_active - len(not_enrolled)

        return Response({
            "total_active": total_active,
            "total_enrolled": total_enrolled,
            "total_not_enrolled": len(not_enrolled),
            "not_enrolled": not_enrolled,
        })

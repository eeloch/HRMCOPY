"""Biometric device fleet management: list, register, and edit terminals."""

from rest_framework import status
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from attendance.integrations.aiface_protocol import IDENTITY_SYSTEM
from attendance.models import BiometricDevice, DeviceCommand
from attendance.services.device_sync import queue_missing_clones
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
        # Background jobs (a Sync All or purge can queue dozens per device)
        # don't count: the gateway serves admin commands ahead of them, so they
        # shouldn't lock the panel until the whole backlog drains.
        if DeviceCommand.objects.filter(device=device, status__in=("pending", "sent")).exclude(command_type__in=DeviceCommand.BACKGROUND_TYPES).exists():
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

        response_data = DeviceCommandSerializer(command).data
        if command_type == "enroll_user":
            response_data["will_clone_to"] = self._devices_pending_clone(device, payload["employee_id"])
        return Response(response_data, status=status.HTTP_201_CREATED)

    @staticmethod
    def _devices_pending_clone(source_device, employee_id):
        """Informational only - lists the other devices that don't yet have
        this employee, which the gateway will automatically clone the
        enrollment to (via getuserinfo/setuserinfo) once this device's scan
        actually succeeds. See Command._link_biometric_identity in
        run_aiface_gateway.py, which is what actually queues the clone.
        """
        return [
            {"device_id": other_device.id, "device_name": other_device.name}
            for other_device in BiometricDevice.objects.exclude(pk=source_device.pk)
            if not BiometricIdentity.objects.filter(
                employee_id=employee_id, system=IDENTITY_SYSTEM, source_identifier=other_device.serial_number, is_active=True,
            ).exists()
        ]

    @staticmethod
    def _build_membership_payload(request, device, command_type):
        employee_id = request.data.get("employee")
        if not employee_id:
            return Response({"detail": "employee is required."}, status=status.HTTP_400_BAD_REQUEST)
        try:
            employee = Employee.objects.get(pk=employee_id)
        except Employee.DoesNotExist:
            return Response({"detail": "Employee not found."}, status=status.HTTP_404_NOT_FOUND)

        if command_type == "enroll_user" and employee.status != "active":
            return Response({"detail": f"{employee.full_name} is not an active employee, so they can't be enrolled on a device."}, status=status.HTTP_400_BAD_REQUEST)

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
        staff_number = int(employee.employee_id) if employee.employee_id.isdigit() else None
        enrollid = int(existing_identity.external_user_id) if existing_identity else device.free_enrollid(preferred=staff_number)
        return {"enrollid": enrollid, "employee_id": employee.id, "name": employee.full_name, "biometric_type": biometric_type}

    @staticmethod
    def _next_free_enrollid(device):
        return device.free_enrollid()


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


class DeviceSyncAllAPIView(APIView):
    """Fill in enrollment gaps between devices that were built up
    independently (e.g. one had more people pre-enrolled by the vendor than
    another), instead of only cloning new enrollments going forward.

    For every employee enrolled on at least one device, queues one
    clone_enrollment command (per source device that already has them) to
    every device they're missing from - reusing the exact same
    getuserinfo/setuserinfo relay as a normal enroll_user propagation
    (see run_aiface_gateway._run_clone_enrollment), so no new physical scan
    is needed. Safe to call repeatedly: an employee already present
    everywhere, or already waiting in the clone queue, is simply skipped.

    Only devices that are online right now take part (as source or target):
    a job aimed at an offline device can't finish and would just clog the
    queue. Run it again once that device is back.
    """

    permission_classes = [IsAuthenticated, CanManageDevices]

    def post(self, request):
        all_devices = list(BiometricDevice.objects.all())
        devices = [device for device in all_devices if device.is_reachable]
        offline_names = [device.name for device in all_devices if not device.is_reachable]
        if len(devices) < 2:
            return Response(
                {"detail": "Need at least two devices online to sync." + (f" Offline right now: {', '.join(offline_names)}." if offline_names else "")},
                status=status.HTTP_400_BAD_REQUEST,
            )

        queued = queue_missing_clones(devices, requested_by=request.user if request.user.is_authenticated else None)

        detail = f"Queued {len(queued)} sync operation(s). Each device works through its queue one relay at a time, so this runs in the background."
        if not queued:
            detail = "Nothing to sync - every online device already has the same people (or their sync is already queued)."
        if offline_names:
            detail += f" Skipped offline device(s): {', '.join(offline_names)} - run Sync All again once they're back."
        return Response({"queued": len(queued), "detail": detail, "operations": queued})


class DevicePurgeInactiveAPIView(APIView):
    """Remove staff who have left from the terminals themselves.

    A terminal keeps a person's face/fingerprint until told otherwise, so people
    marked inactive or terminated stay enrolled (inflating each terminal's
    count) even though HRM already ignores their scans. This queues a
    deleteuser for each of them on each terminal that still has them.

    Two steps: POST without `confirm` returns a preview and changes nothing;
    POST with `confirm: true` queues the removals. It is deliberately not
    reversible on the terminal - the template is deleted - so a rehire needs
    enrolling again. "suspended" staff are left alone since that's temporary.
    The removals run as background jobs, one at a time per terminal.
    """

    permission_classes = [IsAuthenticated, CanManageDevices]
    REMOVABLE_STATUSES = ("inactive", "terminated")

    def post(self, request):
        confirm = request.data.get("confirm") is True
        device_by_serial = {device.serial_number: device for device in BiometricDevice.objects.all()}
        already_queued = {
            (command.device_id, command.payload.get("enrollid"))
            for command in DeviceCommand.objects.filter(command_type__in=("purge_user", "delete_user"), status__in=("pending", "sent"))
        }

        removals = []
        for identity in BiometricIdentity.objects.filter(
            system=IDENTITY_SYSTEM, source_identifier__in=device_by_serial, employee__status__in=self.REMOVABLE_STATUSES,
        ).select_related("employee"):
            if not identity.external_user_id.isdigit():
                continue
            device = device_by_serial[identity.source_identifier]
            enrollid = int(identity.external_user_id)
            if (device.id, enrollid) in already_queued:
                continue
            removals.append((device, enrollid, identity.employee))

        per_device = {}
        for device, _enrollid, _employee in removals:
            per_device[device.name] = per_device.get(device.name, 0) + 1
        summary = {"total": len(removals), "devices": [{"device_name": name, "count": count} for name, count in sorted(per_device.items())]}

        if not confirm:
            return Response({**summary, "confirmed": False})

        for device, enrollid, employee in removals:
            DeviceCommand.objects.create(
                device=device, command_type="purge_user",
                payload={"enrollid": enrollid, "employee_id": employee.id},
                requested_by=request.user if request.user.is_authenticated else None,
            )
        return Response({**summary, "confirmed": True, "detail": f"Queued {len(removals)} removal(s). Each terminal works through them one at a time in the background."})


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

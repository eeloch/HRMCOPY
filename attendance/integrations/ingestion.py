"""Idempotent persistence of normalized biometric events without device APIs."""

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable, Mapping

from django.db import IntegrityError, transaction
from django.utils import timezone

from attendance.integrations.base import NormalizedBiometricPunch
from attendance.models import AttendanceEvent, BiometricDevice
from employees.models import BiometricIdentity, Employee


VALID_VERIFICATION_TYPES = {
    choice for choice, _ in AttendanceEvent.VERIFICATION_TYPES
}
TEMPLATE_PAYLOAD_KEYWORDS = {
    "template",
    "face_data",
    "fingerprint_data",
    "biometric_data",
}


@dataclass(frozen=True)
class IngestionResult:
    status: str
    reason: str = ""
    attendance_event_id: int | None = None


@dataclass
class IngestionSummary:
    created: int = 0
    duplicate: int = 0
    unmapped_employee: int = 0
    unknown_device: int = 0
    revoked_access: int = 0
    invalid: int = 0
    results: list[IngestionResult] = field(default_factory=list)

    def add(self, result: IngestionResult):
        setattr(self, result.status, getattr(self, result.status) + 1)
        self.results.append(result)


class BiometricIngestionService:
    """Persist normalized records; adapters remain responsible for provider I/O."""

    @classmethod
    def ingest(cls, record: NormalizedBiometricPunch | Mapping[str, Any]):
        try:
            normalized = cls._normalize(record)
            validation_error = cls._validate(normalized)
            if validation_error:
                return IngestionResult("invalid", validation_error)
        except (TypeError, ValueError) as error:
            return IngestionResult("invalid", str(error))

        try:
            device = BiometricDevice.objects.get(
                serial_number=normalized.device_serial_number
            )
        except BiometricDevice.DoesNotExist:
            return IngestionResult("unknown_device", "No configured biometric device matches this serial number.")

        employee, mapping_error, revoked_identity = cls._resolve_employee(normalized)
        if revoked_identity is not None:
            cls._notify_revoked_access_attempt(revoked_identity, device)
            return IngestionResult("revoked_access", mapping_error)
        if employee is None:
            return IngestionResult("unmapped_employee", mapping_error)

        existing = AttendanceEvent.objects.filter(
            device=device,
            external_event_id=normalized.external_event_id,
        ).first()
        if existing:
            return IngestionResult("duplicate", "Event already imported.", existing.pk)

        try:
            with transaction.atomic():
                event = AttendanceEvent.objects.create(
                    employee=employee,
                    device=device,
                    timestamp=normalized.timestamp,
                    verification_type=normalized.verification_type,
                    external_event_id=normalized.external_event_id,
                    raw_payload=cls._safe_raw_payload(normalized.raw_payload),
                )
        except IntegrityError:
            # PostgreSQL's unique constraint is the final idempotency guard under
            # concurrent imports. Existing events are deliberately never updated.
            existing = AttendanceEvent.objects.filter(
                device=device,
                external_event_id=normalized.external_event_id,
            ).first()
            if existing:
                return IngestionResult("duplicate", "Event already imported.", existing.pk)
            raise

        return IngestionResult("created", attendance_event_id=event.pk)

    @classmethod
    def ingest_many(cls, records: Iterable[NormalizedBiometricPunch | Mapping[str, Any]]):
        summary = IngestionSummary()
        for record in records:
            summary.add(cls.ingest(record))
        return summary

    @staticmethod
    def _normalize(record):
        if isinstance(record, NormalizedBiometricPunch):
            return record
        if isinstance(record, Mapping):
            return NormalizedBiometricPunch.from_mapping(record)
        raise TypeError("A normalized biometric punch record is required.")

    @staticmethod
    def _validate(record):
        required_values = {
            "system": record.system,
            "source_identifier": record.source_identifier,
            "external_user_id": record.external_user_id,
            "device_serial_number": record.device_serial_number,
            "external_event_id": record.external_event_id,
        }
        missing = [name for name, value in required_values.items() if not value]
        if missing:
            return f"Missing required field(s): {', '.join(missing)}."
        if not isinstance(record.timestamp, datetime) or not timezone.is_aware(record.timestamp):
            return "Timestamp must be a timezone-aware datetime."
        if record.verification_type not in VALID_VERIFICATION_TYPES:
            return "Verification type is not supported."
        if not isinstance(record.raw_payload, Mapping):
            return "Raw payload must be a mapping."
        try:
            json.dumps(record.raw_payload)
        except (TypeError, ValueError):
            return "Raw payload must be JSON serializable."
        return ""

    @staticmethod
    def _resolve_employee(record):
        """Returns (employee, error, revoked_identity) - revoked_identity is set only
        when the id matches someone whose access was explicitly revoked, so the
        caller can flag that distinctly from an id that was never assigned."""
        identity = BiometricIdentity.objects.filter(
            system=record.system,
            source_identifier=record.source_identifier,
            external_user_id=record.external_user_id,
            is_active=True,
        ).select_related("employee").first()
        if identity:
            return identity.employee, "", None

        revoked_identity = BiometricIdentity.objects.filter(
            system=record.system,
            source_identifier=record.source_identifier,
            external_user_id=record.external_user_id,
            is_active=False,
        ).select_related("employee").first()
        if revoked_identity:
            return None, "Access has been revoked for this identity.", revoked_identity

        legacy_matches = Employee.objects.filter(
            biometric_user_id=record.external_user_id,
        ).order_by("id")
        if legacy_matches.count() == 1:
            return legacy_matches.first(), "", None
        if legacy_matches.count() > 1:
            return None, "Legacy biometric user ID maps to multiple employees.", None
        return None, "No active biometric identity maps this external user ID.", None

    @staticmethod
    def _notify_revoked_access_attempt(revoked_identity, device):
        """One notification per employee per day, not one per scan - a revoked
        identity left on a device may be tried repeatedly before it's noticed."""
        from django.contrib.auth import get_user_model
        from notifications.models import Notification, NotificationSeverity
        from notifications.services import NotificationService

        employee = revoked_identity.employee
        already_notified_today = Notification.objects.filter(
            event_type="biometrics.revoked_access_attempt",
            employee=employee,
            created_at__date=timezone.now().date(),
        ).exists()
        if already_notified_today:
            return

        for user in get_user_model().objects.filter(is_superuser=True):
            NotificationService.create(
                recipient=user,
                event_type="biometrics.revoked_access_attempt",
                title="Revoked employee attempted to badge in",
                message=f"{employee.full_name} ({employee.employee_id}) scanned at {device.name}, but their access was revoked.",
                severity=NotificationSeverity.WARNING,
                employee=employee,
                related_url="/attendance/biometric",
            )

    @classmethod
    def _safe_raw_payload(cls, value):
        """Do not persist face or fingerprint template fields from provider payloads."""
        if isinstance(value, Mapping):
            return {
                str(key): cls._safe_raw_payload(item)
                for key, item in value.items()
                if not any(keyword in str(key).lower() for keyword in TEMPLATE_PAYLOAD_KEYWORDS)
            }
        if isinstance(value, list):
            return [cls._safe_raw_payload(item) for item in value]
        return value

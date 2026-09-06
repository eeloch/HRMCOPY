"""Vendor-neutral contracts for adapters that normalize biometric punch data."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping


@dataclass(frozen=True)
class NormalizedBiometricPunch:
    """A provider adapter's safe, normalized representation of one punch."""

    system: str
    source_identifier: str
    external_event_id: str | None
    external_user_id: str
    device_serial_number: str
    timestamp: datetime
    verification_type: str = "unknown"
    raw_payload: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]):
        return cls(
            system=str(value.get("system", "")).strip(),
            source_identifier=str(value.get("source_identifier", "")).strip(),
            external_event_id=(
                str(value["external_event_id"]).strip()
                if value.get("external_event_id") is not None
                else None
            ),
            external_user_id=str(value.get("external_user_id", "")).strip(),
            device_serial_number=str(value.get("device_serial_number", "")).strip(),
            timestamp=value.get("timestamp"),
            verification_type=str(value.get("verification_type", "unknown")).strip() or "unknown",
            raw_payload=value.get("raw_payload", {}),
        )

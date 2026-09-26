"""Authenticated vendor-gateway bridge for meal-ticket devices.

Mirrors attendance/views/bridge.py exactly (same wire format, same secret,
same gateway) since the AiFace gateway already produces the right shape for
either destination - only the routing target differs, chosen by the
connecting device's purpose. See run_aiface_gateway's _handle_sendlog.
"""

import hmac
from datetime import datetime

from django.conf import settings
from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from attendance.integrations.aiface_protocol import IDENTITY_SYSTEM

from .models import MealCollection, MealCollectionStatus
from .services import MealService


FORBIDDEN_BIOMETRIC_FIELDS = {"image", "photo", "face", "fingerprint", "template", "signature", "base64"}

CARD_VERIFICATION_MODE = 3  # confirmed 2026-09-23: a few staff use card verification at the meal terminal


def _notify_card_gating_bypass(collections):
    """Card-verified scans do not respect the terminal's enable/disable state, unlike face scans - so
    someone already past their entitlement can keep collecting by card indefinitely; gating.reconcile()
    still (correctly) tells the terminal to disable them, the terminal still acks it, but a card scan
    goes through anyway. One notification per employee per day, not one per scan, since a bypass slips
    through repeatedly before anyone notices."""
    from django.contrib.auth import get_user_model
    from notifications.models import Notification, NotificationSeverity
    from notifications.services import NotificationService

    today = timezone.localdate()
    for collection in collections:
        if collection.status == MealCollectionStatus.WITHIN:
            continue
        if collection.event.raw_payload.get("mode") != CARD_VERIFICATION_MODE:
            continue
        employee = collection.employee
        already_notified_today = Notification.objects.filter(
            event_type="meals.card_gating_bypass", employee=employee, created_at__date=today,
        ).exists()
        if already_notified_today:
            continue
        for user in get_user_model().objects.filter(is_superuser=True):
            NotificationService.create(
                recipient=user,
                event_type="meals.card_gating_bypass",
                title="Card scan bypassed meal gating",
                message=(
                    f"{employee.full_name} ({employee.employee_id}) collected an extra meal ticket by card "
                    "after their entitlement was used up. The terminal does not block card verification the "
                    "way it blocks face scans, so this can repeat - physical follow-up may be needed."
                ),
                severity=NotificationSeverity.WARNING,
                employee=employee,
                related_url="/meals",
            )


def terminal_reply(result):
    """What the meal terminal should show and do for one scan: allow (so its printer issues the
    ticket) with a short line for the person, or deny with the reason. Whether an extra ticket is
    charged is decided later by HR, so an extra or rest-day ticket is still handed over."""
    if result.status in {"created", "duplicate"}:
        collection = MealCollection.objects.filter(pk=result.collection_id).select_related("employee").first()
        if collection is None:
            return {"access": 1, "entitled": True, "message": "Meal ticket"}
        # The terminal's screen only fits about 28 characters, so lead with the ticket and keep the name short.
        first = (collection.employee.first_name or collection.employee.full_name).split()[0][:12]
        entitled = collection.status == MealCollectionStatus.WITHIN
        line = f"Ticket {collection.sequence_number} of {collection.entitlement_snapshot}" if entitled else "Not entitled"
        # access stays 1 (the scan is always recorded and decided later); `entitled` is what the gated mode uses.
        return {"access": 1, "entitled": entitled, "message": f"{line} - {first}"}
    reasons = {"unmapped_employee": "Not enrolled for meals", "revoked_access": "No meal access", "unknown_device": "Device not registered"}
    return {"access": 0, "entitled": False, "message": reasons.get(result.status, "Scan not recognised")}


class MealVendorGatewayPunchBridgeAPIView(APIView):
    authentication_classes = []
    permission_classes = []

    def post(self, request):
        configured_secret = settings.BIOMETRIC_BRIDGE_SECRET
        supplied_secret = request.headers.get("X-Biometric-Bridge-Key", "")
        if not configured_secret:
            return Response({"detail": "Biometric bridge is not configured."}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        if not hmac.compare_digest(supplied_secret, configured_secret):
            return Response({"detail": "Bridge authentication failed."}, status=status.HTTP_401_UNAUTHORIZED)
        if not isinstance(request.data, dict) or not isinstance(request.data.get("records"), list):
            return Response({"detail": "A JSON object containing a records list is required."}, status=status.HTTP_400_BAD_REQUEST)

        normalized = []
        refused = []
        for item in request.data["records"]:
            if not isinstance(item, dict):
                normalized.append((None, {"invalid": "Record must be an object."}))
                continue
            forbidden = FORBIDDEN_BIOMETRIC_FIELDS.intersection(key.lower() for key in item)
            if forbidden:
                normalized.append((item.get("gateway_record_id"), {"invalid": f"Biometric payload fields are not accepted: {', '.join(sorted(forbidden))}."}))
                continue
            if item.get("event") not in (None, 0, "0"):
                # A terminal that logs refused verifications (Meal Ticket 2 does: "Access denied no LOG" is off) sends
                # them as records with a non-zero event, 104 for a switched-off person. Every genuine scan is event 0.
                # A refusal is not a ticket: counting it charged people 700 for a ticket that was never issued
                # (Egbo Sussan Chidimma, 25 Sep 2026).
                refused.append((item.get("gateway_record_id"), item.get("event")))
                continue
            try:
                timestamp = timezone.make_aware(datetime.strptime(str(item["timestamp"]), "%Y-%m-%d %H:%M:%S"), timezone.get_current_timezone())
                gateway_id = int(item["gateway_record_id"])
                if gateway_id < 1:
                    raise ValueError("gateway_record_id must be positive.")
                device_serial_number = str(item["device_serial_number"])
                normalized.append((gateway_id, {
                    "system": IDENTITY_SYSTEM,
                    "source_identifier": device_serial_number,
                    "device_serial_number": device_serial_number,
                    "external_user_id": str(item["enroll_id"]),
                    "external_event_id": f"vendor-flask-record:{gateway_id}",
                    "timestamp": timestamp,
                    "verification_type": "unknown",
                    "raw_payload": {"gateway_record_id": gateway_id, "mode": item.get("mode"), "inout": item.get("inout"), "event": item.get("event"), "temperature": item.get("temperature")},
                }))
            except (KeyError, TypeError, ValueError) as error:
                normalized.append((item.get("gateway_record_id"), {"invalid": str(error)}))

        valid = [(gateway_id, record) for gateway_id, record in normalized if "invalid" not in record]
        summary = MealService.ingest_many(record for _, record in valid)
        try:
            from .gating import reconcile

            scanned = {r.collection_id for r in summary.results if getattr(r, "collection_id", None)}
            scanned_collections = list(MealCollection.objects.filter(pk__in=scanned).select_related("employee", "event")) if scanned else []
            people = {c.employee_id for c in scanned_collections}
            if people:
                reconcile(employees=people)  # e.g. switch someone off at the terminal the moment they have had their last ticket
            _notify_card_gating_bypass(scanned_collections)
        except Exception:  # a problem here must never lose a scan
            pass
        invalid = [(gateway_id, record) for gateway_id, record in normalized if "invalid" in record]
        summary.invalid += len(invalid)
        results = [{"gateway_record_id": gateway_id, "status": result.status, "reason": result.reason, **terminal_reply(result)} for (gateway_id, _), result in zip(valid, summary.results)]
        results.extend({"gateway_record_id": gateway_id, "status": "invalid", "reason": record["invalid"], "access": 0, "entitled": False, "message": "Scan not recognised"} for gateway_id, record in invalid)
        results.extend({"gateway_record_id": gateway_id, "status": "refused_by_terminal", "reason": f"The terminal refused this verification (event {event}); it is not a ticket.", "access": 0, "entitled": False, "message": "Access denied"} for gateway_id, event in refused)
        return Response({"received": len(normalized) + len(refused), "refused_by_terminal": len(refused), "created": summary.created, "duplicate": summary.duplicate, "unmapped_employee": summary.unmapped_employee, "unknown_device": summary.unknown_device, "revoked_access": summary.revoked_access, "invalid": summary.invalid, "results": results})

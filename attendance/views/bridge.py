"""Authenticated vendor-gateway bridge; device protocol remains outside Django."""

import hmac
from datetime import datetime

from django.conf import settings
from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from attendance.integrations import BiometricIngestionService, NormalizedBiometricPunch


FORBIDDEN_BIOMETRIC_FIELDS = {"image", "photo", "face", "fingerprint", "template", "signature", "base64"}
BRIDGE_SYSTEM = "vendor_flask_gateway"
BRIDGE_SOURCE_IDENTIFIER = "gateway"


class VendorGatewayPunchBridgeAPIView(APIView):
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
        for item in request.data["records"]:
            if not isinstance(item, dict):
                normalized.append((None, {"invalid": "Record must be an object."}))
                continue
            forbidden = FORBIDDEN_BIOMETRIC_FIELDS.intersection(key.lower() for key in item)
            if forbidden:
                normalized.append((item.get("gateway_record_id"), {"invalid": f"Biometric payload fields are not accepted: {', '.join(sorted(forbidden))}."}))
                continue
            try:
                timestamp = timezone.make_aware(datetime.strptime(str(item["timestamp"]), "%Y-%m-%d %H:%M:%S"), timezone.get_current_timezone())
                gateway_id = int(item["gateway_record_id"])
                if gateway_id < 1:
                    raise ValueError("gateway_record_id must be positive.")
                normalized.append((gateway_id, NormalizedBiometricPunch(system=BRIDGE_SYSTEM, source_identifier=BRIDGE_SOURCE_IDENTIFIER, external_event_id=f"vendor-flask-record:{gateway_id}", external_user_id=str(item["enroll_id"]), device_serial_number=str(item["device_serial_number"]), timestamp=timestamp, verification_type="unknown", raw_payload={"gateway_record_id": gateway_id, "mode": item.get("mode"), "inout": item.get("inout"), "event": item.get("event"), "temperature": item.get("temperature")})))
            except (KeyError, TypeError, ValueError) as error:
                normalized.append((item.get("gateway_record_id"), {"invalid": str(error)}))

        valid = [(gateway_id, record) for gateway_id, record in normalized if not isinstance(record, dict)]
        summary = BiometricIngestionService.ingest_many(record for _, record in valid)
        invalid = [(gateway_id, record) for gateway_id, record in normalized if isinstance(record, dict)]
        summary.invalid += len(invalid)
        results = [{"gateway_record_id": gateway_id, "status": result.status, "reason": result.reason} for (gateway_id, _), result in zip(valid, summary.results)]
        results.extend({"gateway_record_id": gateway_id, "status": "invalid", "reason": record["invalid"]} for gateway_id, record in invalid)
        return Response({"received": len(normalized), "created": summary.created, "duplicate": summary.duplicate, "unmapped_employee": summary.unmapped_employee, "unknown_device": summary.unknown_device, "invalid": summary.invalid, "results": results})

"""Read-only operational feed of immutable biometric punch facts."""

from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from attendance.models import AttendanceEvent
from attendance.serializers import AttendanceEventSerializer


class BiometricEventListAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        events = AttendanceEvent.objects.select_related(
            "employee", "employee__department", "device"
        ).order_by("-timestamp", "-id")
        if date_value := request.query_params.get("date"):
            events = events.filter(timestamp__date=date_value)
        if employee_id := request.query_params.get("employee"):
            events = events.filter(employee_id=employee_id)
        if device := request.query_params.get("device"):
            events = events.filter(device__serial_number=device)
        return Response({"count": events.count(), "results": AttendanceEventSerializer(events, many=True).data})

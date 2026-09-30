from datetime import date as date_cls

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework import status

from attendance.services.dashboard import DashboardService


class AttendanceDashboardAPIView(APIView):
    permission_classes = [
        IsAuthenticated,
    ]

    def get(self, request):
        date_param = request.query_params.get("date")
        target_date = None
        if date_param:
            try:
                target_date = date_cls.fromisoformat(date_param)
            except ValueError:
                return Response(
                    {"detail": "date must be an ISO date (YYYY-MM-DD)."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        return Response(
            DashboardService.get_dashboard(target_date)
        )

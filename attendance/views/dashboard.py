from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from attendance.services.dashboard import DashboardService


class AttendanceDashboardAPIView(APIView):
    permission_classes = [
        IsAuthenticated,
    ]

    def get(self, request):
        return Response(
            DashboardService.get_dashboard()
        )
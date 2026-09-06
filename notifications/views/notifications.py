from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from notifications.models import Notification, NotificationSeverity
from notifications.serializers import NotificationSerializer
from notifications.services import NotificationService


class NotificationListAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        notifications = Notification.objects.filter(
            recipient=request.user,
        ).select_related("employee").order_by("-created_at")

        unread = request.query_params.get("unread")
        severity = request.query_params.get("severity")
        if unread is not None:
            if unread.lower() not in {"true", "false"}:
                return Response(
                    {"detail": "Unread must be true or false."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            notifications = notifications.filter(is_read=unread.lower() == "false")

        if severity:
            valid_severities = {value for value, _ in NotificationSeverity.choices}
            if severity not in valid_severities:
                return Response(
                    {"detail": "Severity must be a valid notification severity."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            notifications = notifications.filter(severity=severity)

        page, page_size = self._get_pagination(request)
        count = notifications.count()
        start = (page - 1) * page_size
        results = notifications[start : start + page_size]

        return Response(
            {
                "count": count,
                "page": page,
                "page_size": page_size,
                "total_pages": max(1, (count + page_size - 1) // page_size),
                "results": NotificationSerializer(results, many=True).data,
            }
        )

    @staticmethod
    def _get_pagination(request):
        try:
            page = max(1, int(request.query_params.get("page", 1)))
            page_size = int(request.query_params.get("page_size", 20))
        except ValueError:
            return 1, 20

        return page, min(max(page_size, 1), 100)


class UnreadNotificationCountAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(
            {
                "count": Notification.objects.filter(
                    recipient=request.user,
                    is_read=False,
                ).count()
            }
        )


class NotificationMarkReadAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, notification_id):
        try:
            notification = Notification.objects.get(
                pk=notification_id,
                recipient=request.user,
            )
        except Notification.DoesNotExist:
            return Response(
                {"detail": "Notification not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        NotificationService.mark_read(notification, request.user)
        return Response({"result": NotificationSerializer(notification).data})


class NotificationReadAllAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        updated = NotificationService.mark_all_read(request.user)
        return Response({"updated": updated})

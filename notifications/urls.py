from django.urls import path

from notifications.views import (
    NotificationListAPIView,
    NotificationMarkReadAPIView,
    NotificationReadAllAPIView,
    UnreadNotificationCountAPIView,
)


urlpatterns = [
    path("", NotificationListAPIView.as_view(), name="notifications-list"),
    path(
        "unread-count/",
        UnreadNotificationCountAPIView.as_view(),
        name="notifications-unread-count",
    ),
    path(
        "<int:notification_id>/read/",
        NotificationMarkReadAPIView.as_view(),
        name="notifications-mark-read",
    ),
    path("read-all/", NotificationReadAllAPIView.as_view(), name="notifications-read-all"),
]

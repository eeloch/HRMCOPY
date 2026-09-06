from django.urls import path

from audit.views import AuditActivityAPIView


urlpatterns = [
    path("activity/", AuditActivityAPIView.as_view(), name="audit-activity"),
]

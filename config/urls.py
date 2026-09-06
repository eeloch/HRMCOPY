from django.contrib import admin
from django.urls import include, path
from django.conf import settings
from django.conf.urls.static import static

from rest_framework_simplejwt.views import (
    TokenRefreshView,
)

from core.views import CurrentUserAPIView, LogoutAPIView, ThrottledTokenObtainPairView


urlpatterns = [

    path(
        "admin/",
        admin.site.urls,
    ),

    path(
        "api/auth/login/",
        ThrottledTokenObtainPairView.as_view(),
        name="token_obtain_pair",
    ),

    path(
        "api/auth/refresh/",
        TokenRefreshView.as_view(),
        name="token_refresh",
    ),

    path(
        "api/auth/logout/",
        LogoutAPIView.as_view(),
        name="token_logout",
    ),

    path(
        "api/auth/me/",
        CurrentUserAPIView.as_view(),
        name="current_user",
    ),

    path(
        "api/attendance/",
        include("attendance.urls"),
    ),

    path(
        "api/employees/",
        include("employees.urls"),
    ),

    path(
        "api/documents/",
        include("documents.urls"),
    ),

    path(
        "api/leave/",
        include("leave.urls"),
    ),

    path(
        "api/payroll/",
        include("payroll.urls"),
    ),

    path(
        "api/workflow/",
        include("workflow.urls"),
    ),

    path(
        "api/audit/",
        include("audit.urls"),
    ),

    path(
        "api/notifications/",
        include("notifications.urls"),
    ),

    path(
        "api/ppe/",
        include("ppe.urls"),
    ),
    path("api/meals/", include("meals.urls")),

    
]

if settings.DEBUG:
    urlpatterns += static(
        settings.MEDIA_URL,
        document_root=settings.MEDIA_ROOT,
    )

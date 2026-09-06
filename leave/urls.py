from django.urls import path

from leave.views import (
    LeaveBalanceAPIView,
    LeaveApproveAPIView,
    LeavePartialApproveAPIView,
    LeaveRejectAPIView,
    LeaveRequestCreateAPIView,
    LeaveRequestDetailAPIView,
    LeaveRequestListAPIView,
    LeaveTypeListAPIView,
    PendingLeaveRequestListAPIView,
)


urlpatterns = [
    path("types/", LeaveTypeListAPIView.as_view(), name="leave-types"),
    path(
        "balance/<int:employee_id>/",
        LeaveBalanceAPIView.as_view(),
        name="leave-balance",
    ),
    path("request/", LeaveRequestCreateAPIView.as_view(), name="leave-request-create"),
    path("requests/", LeaveRequestListAPIView.as_view(), name="leave-requests"),
    path(
        "requests/<int:request_id>/",
        LeaveRequestDetailAPIView.as_view(),
        name="leave-request-detail",
    ),
    path("pending/", PendingLeaveRequestListAPIView.as_view(), name="leave-requests-pending"),
    path("requests/<int:request_id>/approve/", LeaveApproveAPIView.as_view(), name="leave-request-approve"),
    path("requests/<int:request_id>/partial-approve/", LeavePartialApproveAPIView.as_view(), name="leave-request-partial-approve"),
    path("requests/<int:request_id>/reject/", LeaveRejectAPIView.as_view(), name="leave-request-reject"),
]

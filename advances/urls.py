from django.urls import path

from .views import AdvanceApproveAPIView, AdvanceCancelAPIView, AdvanceDeclineAPIView, AdvanceListCreateAPIView, AdvancePayAPIView

urlpatterns = [
    path("", AdvanceListCreateAPIView.as_view(), name="advances"),
    path("<int:advance_id>/approve/", AdvanceApproveAPIView.as_view(), name="advance-approve"),
    path("<int:advance_id>/decline/", AdvanceDeclineAPIView.as_view(), name="advance-decline"),
    path("<int:advance_id>/cancel/", AdvanceCancelAPIView.as_view(), name="advance-cancel"),
    path("<int:advance_id>/pay/", AdvancePayAPIView.as_view(), name="advance-pay"),
]

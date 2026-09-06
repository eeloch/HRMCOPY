from django.urls import path

from .views import PPEApproveAPIView, PPEDeferAPIView, PPEHoldAPIView, PPEIssueDetailAPIView, PPEIssueListCreateAPIView, PPETypeListAPIView


urlpatterns = [
    path("types/", PPETypeListAPIView.as_view(), name="ppe-types"),
    path("issues/", PPEIssueListCreateAPIView.as_view(), name="ppe-issues"),
    path("issues/<int:issue_id>/", PPEIssueDetailAPIView.as_view(), name="ppe-issue-detail"),
    path("issues/<int:issue_id>/approve/", PPEApproveAPIView.as_view(), name="ppe-issue-approve"),
    path("issues/<int:issue_id>/hold/", PPEHoldAPIView.as_view(), name="ppe-issue-hold"),
    path("issues/<int:issue_id>/defer/", PPEDeferAPIView.as_view(), name="ppe-issue-defer"),
]

from django.urls import path

from .views import (
    EmployeeOffenceApproveAPIView,
    EmployeeOffenceListCreateAPIView,
    EmployeeOffenceRejectAPIView,
    OffenceTypeDetailAPIView,
    OffencePenaltyPreviewAPIView,
    OffenceTypeListCreateAPIView,
    RewardTypeDetailAPIView,
    RewardTypeListAPIView,
)

urlpatterns = [
    path(
        "types/",
        OffenceTypeListCreateAPIView.as_view(),
        name="offence-types",
    ),
    path(
        "types/<int:pk>/",
        OffenceTypeDetailAPIView.as_view(),
        name="offence-type-detail",
    ),
    path("penalty-preview/", OffencePenaltyPreviewAPIView.as_view(), name="offence-penalty-preview"),
    path("reward-types/", RewardTypeListAPIView.as_view(), name="reward-types"),
    path("reward-types/<int:pk>/", RewardTypeDetailAPIView.as_view(), name="reward-type-detail"),
    path(
        "",
        EmployeeOffenceListCreateAPIView.as_view(),
        name="employee-offences",
    ),
    path(
        "<int:pk>/approve/",
        EmployeeOffenceApproveAPIView.as_view(),
        name="employee-offence-approve",
    ),
    path(
        "<int:pk>/reject/",
        EmployeeOffenceRejectAPIView.as_view(),
        name="employee-offence-reject",
    ),
]

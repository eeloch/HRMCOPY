from django.urls import path

from .views import (
    EmployeeOffenceApproveAPIView,
    EmployeeOffenceListCreateAPIView,
    EmployeeOffenceRejectAPIView,
    OffenceTypeDetailAPIView,
    OffenceTypeListCreateAPIView,
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

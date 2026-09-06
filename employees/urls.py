from django.urls import path

from .views import (
    DepartmentListAPIView,
    PositionListAPIView,
    EmployeeListCreateAPIView,
    EmployeeDetailAPIView,
    EmployeeImportAPIView,
    EmployeeImportPreviewAPIView,
    EmployeeImportOrganizationAPIView,
    EmployeeImportOrganizationCreateAPIView,
    EmployeeProfileAPIView
)


urlpatterns = [

    path(
        "departments/",
        DepartmentListAPIView.as_view(),
        name="departments",
    ),

    path(
        "import/organization/create/",
        EmployeeImportOrganizationCreateAPIView.as_view(),
        name="employee-import-organization-create",
    ),

    path(
        "positions/",
        PositionListAPIView.as_view(),
        name="positions",
    ),

    path(
        "import/organization/",
        EmployeeImportOrganizationAPIView.as_view(),
        name="employee-import-organization",
    ),

    path(
        "",
        EmployeeListCreateAPIView.as_view(),
        name="employees",
    ),


    path(
        "import/preview/",
        EmployeeImportPreviewAPIView.as_view(),
        name="employee-import-preview",
    ),

    path(
        "import/",
        EmployeeImportAPIView.as_view(),
        name="employee-import",
    ),

    path(
        "<int:employee_id>/",
        EmployeeDetailAPIView.as_view(),
        name="employee-detail",
    ),

    path(
        "<int:pk>/profile/",
        EmployeeProfileAPIView.as_view(),
        name="employee-profile",
    ),
]

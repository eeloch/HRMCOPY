from django.urls import path

from .views import (
    AccommodationStatusReportAPIView,
    DepartmentListAPIView,
    EmployeeDirectorySummaryAPIView,
    EmployeeHiresExitsAPIView,
    PositionListAPIView,
    EmployeeListCreateAPIView,
    EmployeeDetailAPIView,
    EmployeeImportAPIView,
    EmployeeImportPreviewAPIView,
    EmployeeImportOrganizationAPIView,
    EmployeeImportOrganizationCreateAPIView,
    EmployeeProfileAPIView,
    EmployeeSalaryImportPreviewAPIView,
    EmployeeSalaryImportAPIView,
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
        "accommodation-report/",
        AccommodationStatusReportAPIView.as_view(),
        name="employee-accommodation-report",
    ),

    path(
        "summary/",
        EmployeeDirectorySummaryAPIView.as_view(),
        name="employee-directory-summary",
    ),

    path(
        "hires-exits/",
        EmployeeHiresExitsAPIView.as_view(),
        name="employee-hires-exits",
    ),


    path(
        "import/preview/",
        EmployeeImportPreviewAPIView.as_view(),
        name="employee-import-preview",
    ),

    path(
        "salary-import/preview/",
        EmployeeSalaryImportPreviewAPIView.as_view(),
        name="employee-salary-import-preview",
    ),

    path(
        "salary-import/",
        EmployeeSalaryImportAPIView.as_view(),
        name="employee-salary-import",
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

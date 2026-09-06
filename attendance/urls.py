from django.urls import path
from .views import (
    AttendanceExceptionListAPIView,
    AttendanceDashboardAPIView,
    ExceptionDecisionAPIView,
    PendingExceptionAPIView,
    ShiftAssignmentDetailAPIView,
    ShiftAssignmentChangeAPIView,
    ShiftAssignmentListCreateAPIView,
    ShiftListAPIView,
    EmployeeRosterListAPIView,
    RosterGenerationAPIView,
    RotationRosterGenerationAPIView,
    RosterOverrideAPIView,
    TodayAttendanceAPIView,
    EmployeeAttendanceHistoryAPIView,
    OvertimeDecisionAPIView,
    OvertimeMarkPaidAPIView,
    OvertimeRecordListAPIView,
    VendorGatewayPunchBridgeAPIView,
    BiometricEventListAPIView,
)


urlpatterns = [
    path("integrations/vendor-gateway/punches/", VendorGatewayPunchBridgeAPIView.as_view(), name="vendor-gateway-punches"),
    path("biometric-events/", BiometricEventListAPIView.as_view(), name="biometric-event-list"),
    path(
        "today/",
        TodayAttendanceAPIView.as_view(),
        name="attendance-today",
    ),
    path(
        "records/",
        EmployeeAttendanceHistoryAPIView.as_view(),
        name="attendance-records",
    ),

    path(
        "exceptions/",
        AttendanceExceptionListAPIView.as_view(),
        name="attendance-exceptions",
    ),

    path(
        "exceptions/pending/",
        PendingExceptionAPIView.as_view(),
        name="attendance-exceptions-pending",
    ),

    path(
        "exceptions/<int:exception_id>/decision/",
        ExceptionDecisionAPIView.as_view(),
        name="attendance-exception-decision",
    ),

    path(
        "dashboard/",
        AttendanceDashboardAPIView.as_view(),
        name="attendance-dashboard",
    ),
    path("overtime/", OvertimeRecordListAPIView.as_view(), name="overtime-list"),
    path("overtime/<int:record_id>/mark-paid/", OvertimeMarkPaidAPIView.as_view(), name="overtime-mark-paid"),
    path("overtime/<int:record_id>/<str:decision>/", OvertimeDecisionAPIView.as_view(), name="overtime-decision"),
    path("shifts/", ShiftListAPIView.as_view(), name="shift-list"),
    path("roster/", EmployeeRosterListAPIView.as_view(), name="employee-roster-list"),
    path("roster/generate/", RosterGenerationAPIView.as_view(), name="roster-generate"),
    path("roster/rotation/generate/", RotationRosterGenerationAPIView.as_view(), name="roster-rotation-generate"),
    path("roster/override/", RosterOverrideAPIView.as_view(), name="roster-override"),
    path(
        "shift-assignments/",
        ShiftAssignmentListCreateAPIView.as_view(),
        name="shift-assignment-list-create",
    ),
    path(
        "shift-assignments/<int:assignment_id>/",
        ShiftAssignmentDetailAPIView.as_view(),
        name="shift-assignment-detail",
    ),
    path(
        "shift-assignments/change/",
        ShiftAssignmentChangeAPIView.as_view(),
        name="shift-assignment-change",
    ),
]

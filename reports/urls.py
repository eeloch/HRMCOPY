from django.urls import path

from .views import EmployeeStatementAPIView, WeeklyReportPresentationAPIView, WeeklyReportSummaryAPIView

urlpatterns = [
    path("weekly/", WeeklyReportSummaryAPIView.as_view(), name="weekly-report-summary"),
    path("employee-statement/", EmployeeStatementAPIView.as_view(), name="employee-statement"),
    path("weekly/presentation/", WeeklyReportPresentationAPIView.as_view(), name="weekly-report-presentation"),
]

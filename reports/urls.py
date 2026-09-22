from django.urls import path

from .views import WeeklyReportPresentationAPIView, WeeklyReportSummaryAPIView

urlpatterns = [
    path("weekly/", WeeklyReportSummaryAPIView.as_view(), name="weekly-report-summary"),
    path("weekly/presentation/", WeeklyReportPresentationAPIView.as_view(), name="weekly-report-presentation"),
]

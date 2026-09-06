from django.urls import path

from payroll.views import EmployeePayrollDetailAPIView, EmployeePayrollHistoryAPIView, PayrollLineItemDetailAPIView, PayrollLineItemListCreateAPIView, PayrollPeriodAttendanceSyncAPIView, PayrollPeriodDetailAPIView, PayrollPeriodEmployeeListAPIView, PayrollPeriodGenerateAPIView, PayrollPeriodListCreateAPIView, PayrollPeriodTransitionAPIView


urlpatterns = [
    path("periods/", PayrollPeriodListCreateAPIView.as_view(), name="payroll-periods"),
    path("periods/<int:period_id>/", PayrollPeriodDetailAPIView.as_view(), name="payroll-period-detail"),
    path("periods/<int:period_id>/generate/", PayrollPeriodGenerateAPIView.as_view(), name="payroll-period-generate"),
    path("periods/<int:period_id>/sync-attendance/", PayrollPeriodAttendanceSyncAPIView.as_view(), name="payroll-period-sync-attendance"),
    path("periods/<int:period_id>/transition/", PayrollPeriodTransitionAPIView.as_view(), name="payroll-period-transition"),
    path("periods/<int:period_id>/employees/", PayrollPeriodEmployeeListAPIView.as_view(), name="payroll-period-employees"),
    path("employee-payrolls/<int:payroll_id>/", EmployeePayrollDetailAPIView.as_view(), name="employee-payroll-detail"),
    path("employee-payrolls/", EmployeePayrollHistoryAPIView.as_view(), name="employee-payroll-history"),
    path("employee-payrolls/<int:payroll_id>/line-items/", PayrollLineItemListCreateAPIView.as_view(), name="payroll-line-items"),
    path("line-items/<int:line_item_id>/", PayrollLineItemDetailAPIView.as_view(), name="payroll-line-item-detail"),
]

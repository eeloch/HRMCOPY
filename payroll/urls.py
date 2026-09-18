from django.urls import path

from payroll.views import PayrollBankUploadDownloadAPIView, PayrollPeriodPayslipsAPIView, PayrollBankUploadPreviewAPIView, EmployeePayrollDetailAPIView, EmployeePayrollHistoryAPIView, PayrollLineItemDetailAPIView, PayrollLineItemListCreateAPIView, PayrollPeriodAttendanceSyncAPIView, PayrollPeriodDetailAPIView, PayrollPeriodEmployeeListAPIView, PayrollPeriodGenerateAPIView, PayrollPeriodListCreateAPIView, PayrollPeriodTransitionAPIView


urlpatterns = [
    path("periods/", PayrollPeriodListCreateAPIView.as_view(), name="payroll-periods"),
    path("periods/<int:period_id>/", PayrollPeriodDetailAPIView.as_view(), name="payroll-period-detail"),
    path("periods/<int:period_id>/generate/", PayrollPeriodGenerateAPIView.as_view(), name="payroll-period-generate"),
    path("periods/<int:period_id>/sync-attendance/", PayrollPeriodAttendanceSyncAPIView.as_view(), name="payroll-period-sync-attendance"),
    path("periods/<int:period_id>/bank-upload/preview/", PayrollBankUploadPreviewAPIView.as_view(), name="payroll-bank-upload-preview"),
    path("periods/<int:period_id>/bank-upload/", PayrollBankUploadDownloadAPIView.as_view(), name="payroll-bank-upload"),
    path("periods/<int:period_id>/transition/", PayrollPeriodTransitionAPIView.as_view(), name="payroll-period-transition"),
    path("periods/<int:period_id>/employees/", PayrollPeriodEmployeeListAPIView.as_view(), name="payroll-period-employees"),
    path("periods/<int:period_id>/payslips/", PayrollPeriodPayslipsAPIView.as_view(), name="payroll-period-payslips"),
    path("employee-payrolls/<int:payroll_id>/", EmployeePayrollDetailAPIView.as_view(), name="employee-payroll-detail"),
    path("employee-payrolls/", EmployeePayrollHistoryAPIView.as_view(), name="employee-payroll-history"),
    path("employee-payrolls/<int:payroll_id>/line-items/", PayrollLineItemListCreateAPIView.as_view(), name="payroll-line-items"),
    path("line-items/<int:line_item_id>/", PayrollLineItemDetailAPIView.as_view(), name="payroll-line-item-detail"),
]

from django.urls import path

from .views import ERPEmployeeDetailAPIView, ERPEmployeeListAPIView

urlpatterns = [
    path("employees/", ERPEmployeeListAPIView.as_view(), name="erp-employees"),
    path("employees/<str:staff_number>/", ERPEmployeeDetailAPIView.as_view(), name="erp-employee"),
]

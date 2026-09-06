from django.urls import path

from .views import (
    EmployeeMealsProfileAPIView,
    MealDeviceDetailAPIView,
    MealDeviceListCreateAPIView,
    MealEntitlementListCreateAPIView,
    MealExcessApproveAPIView,
    MealExcessCancelAPIView,
    MealOperationsAPIView,
    MealTicketRateListCreateAPIView,
)

urlpatterns = [
    path(
        "entitlements/",
        MealEntitlementListCreateAPIView.as_view(),
        name="meal-entitlements",
    ),
    path(
        "rates/",
        MealTicketRateListCreateAPIView.as_view(),
        name="meal-rates",
    ),
    path(
        "devices/",
        MealDeviceListCreateAPIView.as_view(),
        name="meal-devices",
    ),
    path(
        "devices/<int:pk>/",
        MealDeviceDetailAPIView.as_view(),
        name="meal-device-detail",
    ),
    path(
        "operations/",
        MealOperationsAPIView.as_view(),
        name="meal-operations",
    ),
    path(
        "employees/<int:pk>/profile/",
        EmployeeMealsProfileAPIView.as_view(),
        name="employee-meals-profile",
    ),
    path(
        "excess/<int:pk>/approve/",
        MealExcessApproveAPIView.as_view(),
        name="meal-excess-approve",
    ),
    path(
        "excess/<int:pk>/cancel/",
        MealExcessCancelAPIView.as_view(),
        name="meal-excess-cancel",
    ),
]

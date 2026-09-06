from django.contrib import admin

from .models import (
    Department,
    Position,
    Employee,
    BiometricIdentity,
)


@admin.register(Department)
class DepartmentAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "required_staff",
    )

    search_fields = (
        "name",
    )


@admin.register(Position)
class PositionAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "department",
    )

    search_fields = (
        "name",
    )

    list_filter = (
        "department",
    )


@admin.register(Employee)
class EmployeeAdmin(admin.ModelAdmin):
    list_display = (
        "employee_id",
        "full_name",
        "department",
        "position",
        "employment_type",
        "employment_category",
        "status",
    )

    search_fields = (
        "employee_id",
        "first_name",
        "last_name",
    )

    list_filter = (
        "department",
        "employment_type",
        "employment_category",
        "status",
    )


@admin.register(BiometricIdentity)
class BiometricIdentityAdmin(admin.ModelAdmin):
    list_display = (
        "employee",
        "system",
        "external_user_id",
        "is_active",
    )

    search_fields = (
        "employee__employee_id",
        "employee__first_name",
        "employee__last_name",
        "external_user_id",
    )

    list_filter = (
        "system",
        "is_active",
    )

from django.contrib import admin

from .models import EmployeeOffence, OffenceType


@admin.register(OffenceType)
class OffenceTypeAdmin(admin.ModelAdmin):
    list_display = ("name", "default_amount", "active")
    list_filter = ("active",)
    search_fields = ("name",)


@admin.register(EmployeeOffence)
class EmployeeOffenceAdmin(admin.ModelAdmin):
    list_display = ("employee", "offence_type", "amount", "incident_date", "status")
    list_filter = ("status",)
    search_fields = ("employee__employee_id", "employee__first_name", "employee__last_name")
    readonly_fields = ("created_at", "updated_at")

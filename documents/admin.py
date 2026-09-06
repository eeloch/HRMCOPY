from django.contrib import admin

from .models import EmployeeDocument


@admin.register(EmployeeDocument)
class EmployeeDocumentAdmin(admin.ModelAdmin):

    list_display = (
        "employee",
        "document_type",
        "title",
        "uploaded_at",
        "expiry_date",
        "is_active",
    )

    list_filter = (
        "document_type",
        "is_active",
    )

    search_fields = (
        "employee__employee_id",
        "employee__first_name",
        "employee__last_name",
        "title",
    )
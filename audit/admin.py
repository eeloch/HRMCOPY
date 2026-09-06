from django.contrib import admin

from audit.models import AuditEvent


@admin.register(AuditEvent)
class AuditEventAdmin(admin.ModelAdmin):
    list_display = (
        "created_at",
        "module",
        "event_type",
        "severity",
        "title",
        "employee",
        "actor",
    )
    list_filter = (
        "module",
        "event_type",
        "severity",
        "created_at",
    )
    search_fields = (
        "title",
        "employee__employee_id",
        "employee__first_name",
        "employee__last_name",
        "actor__username",
        "actor__first_name",
        "actor__last_name",
    )
    readonly_fields = ("created_at",)

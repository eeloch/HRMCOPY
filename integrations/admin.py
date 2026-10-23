from django.contrib import admin

from .models import IntegrationKey


@admin.register(IntegrationKey)
class IntegrationKeyAdmin(admin.ModelAdmin):
    list_display = ("name", "prefix", "active", "allowed_ips", "last_used_at", "last_used_ip", "request_count", "created_at")
    readonly_fields = ("prefix", "key_hash", "created_by", "created_at", "last_used_at", "last_used_ip", "request_count", "revoked_at")
    fields = ("name", "active", "allowed_ips") + readonly_fields

    def has_add_permission(self, request):
        return False  # keys are made with: manage.py integration_key create "<name>" (the key is shown once there)

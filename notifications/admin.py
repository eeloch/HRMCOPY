from django.contrib import admin

from notifications.models import Notification


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = (
        "recipient",
        "title",
        "event_type",
        "severity",
        "is_read",
        "created_at",
    )
    list_filter = ("severity", "is_read", "event_type", "created_at")
    search_fields = (
        "recipient__username",
        "recipient__email",
        "title",
        "message",
    )
    readonly_fields = ("created_at", "read_at")

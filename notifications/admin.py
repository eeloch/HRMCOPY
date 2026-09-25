from django.contrib import admin
from django.utils import timezone

from audit.admin_support import SEVERITY_TONES, badge, employee_columns, employee_search_fields, log_admin_event, perm_checker, pretty_json
from notifications.models import Notification


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    staff_number, employee_name, department = employee_columns("employee")
    list_display = ("created_at", "recipient_name", "title", "event_type", "severity_badge", "staff_number", "employee_name", "is_read", "read_at")
    list_display_links = ("created_at", "title")
    list_filter = ("is_read", "severity", "event_type", "recipient", "created_at")
    search_fields = ("title", "message", "recipient__username", "recipient__email", "recipient__first_name", "recipient__last_name", *employee_search_fields("employee"))
    list_select_related = ("recipient", "employee", "employee__department")
    date_hierarchy = "created_at"
    ordering = ("-created_at", "-id")
    list_per_page = 50
    show_full_result_count = False
    save_on_top = True
    actions = ["mark_read", "mark_unread"]
    fields = ("recipient_name", "created_at", "severity_badge", "event_type", "title", "message", "staff_number", "employee_name", "related_url", "metadata_table", "is_read", "read_at")
    readonly_fields = fields  # a notification is system-written; read state changes only through the two actions

    has_mark_permission = perm_checker("notifications.change_notification")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    @admin.display(description="Recipient", ordering="recipient__username")
    def recipient_name(self, obj):
        return obj.recipient.get_full_name() or obj.recipient.get_username()

    @admin.display(description="Severity", ordering="severity")
    def severity_badge(self, obj):
        return badge(obj.get_severity_display(), SEVERITY_TONES.get(obj.severity, "grey"))

    @admin.display(description="Metadata")
    def metadata_table(self, obj):
        return pretty_json(obj.metadata)

    def _set_read(self, request, queryset, read):
        # NotificationService.mark_read only lets a user mark their own notifications, so a superuser fixing
        # other people's inboxes updates the rows directly.
        pending = queryset.filter(is_read=not read)
        ids = list(pending.values_list("pk", flat=True))
        pending.update(is_read=read, read_at=timezone.now() if read else None)
        word = "read" if read else "unread"
        self.message_user(request, f"Marked {len(ids)} notification(s) as {word}.")
        log_admin_event(
            request, module="notifications", event_type=f"admin.notifications.mark_{word}",
            title=f"Notifications marked {word} from Django admin", description=f"{len(ids)} notification(s) marked {word}.",
            metadata={"notification_ids": ids[:200], "count": len(ids)},
        )

    @admin.action(description="Mark selected notifications as read", permissions=["mark"])
    def mark_read(self, request, queryset):
        self._set_read(request, queryset, True)

    @admin.action(description="Mark selected notifications as unread", permissions=["mark"])
    def mark_unread(self, request, queryset):
        self._set_read(request, queryset, False)

from django.contrib import admin
from django.contrib.admin import RelatedOnlyFieldListFilter
from django.urls import NoReverseMatch, reverse
from django.utils.html import format_html

from audit.admin_support import SEVERITY_TONES, badge, employee_columns, employee_search_fields, pretty_json
from audit.models import AuditEvent


@admin.register(AuditEvent)
class AuditEventAdmin(admin.ModelAdmin):
    """The trail is evidence: it can be searched and read here but never added to, edited or deleted."""

    staff_number, employee_name, department = employee_columns("employee")
    list_display = ("created_at", "module", "event_type", "severity_badge", "title", "staff_number", "employee_name", "actor_name", "target")
    list_display_links = ("created_at", "title")
    list_filter = ("module", "severity", "event_type", ("actor", RelatedOnlyFieldListFilter), "employee__department", "created_at")
    search_fields = (
        "title",
        "description",
        "event_type",
        "module",
        *employee_search_fields("employee"),
        "actor__username",
        "actor__first_name",
        "actor__last_name",
    )
    list_select_related = ("employee", "employee__department", "actor", "content_type")
    date_hierarchy = "created_at"
    ordering = ("-created_at", "-id")
    list_per_page = 50
    show_full_result_count = False  # the trail is large; do not count every row on each page
    save_on_top = True
    actions = None

    fieldsets = (
        ("When and who", {"fields": ("created_at", "actor_name", "staff_number", "employee_name")}),
        ("What happened", {"fields": ("module", "event_type", "severity_badge", "title", "description")}),
        ("About", {"fields": ("target",)}),
        ("Details", {"fields": ("metadata_table",)}),
    )
    readonly_fields = ("created_at", "actor_name", "staff_number", "employee_name", "module", "event_type", "severity_badge", "title", "description", "target", "metadata_table")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.display(description="Severity", ordering="severity")
    def severity_badge(self, obj):
        return badge(obj.get_severity_display(), SEVERITY_TONES.get(obj.severity, "grey"))

    @admin.display(description="Actor", ordering="actor__username")
    def actor_name(self, obj):
        if not obj.actor_id:
            return "-"
        return obj.actor.get_full_name() or obj.actor.get_username()

    @admin.display(description="Record")
    def target(self, obj):
        if not obj.content_type_id or not obj.object_id:
            return "-"
        content_type = obj.content_type
        label = f"{content_type.app_label}.{content_type.model} #{obj.object_id}"
        try:
            url = reverse(f"admin:{content_type.app_label}_{content_type.model}_change", args=[obj.object_id])
        except NoReverseMatch:
            return label
        return format_html('<a href="{}">{}</a>', url, label)

    @admin.display(description="Metadata")
    def metadata_table(self, obj):
        return pretty_json(obj.metadata)

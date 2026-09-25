import os
from datetime import timedelta

from django.contrib import admin
from django.db import transaction
from django.utils import timezone

from audit.models import AuditSeverity
from audit.services import AuditService
from employees.admin_common import (
    EMPLOYEE_SEARCH_FIELDS,
    AuditedAdminMixin,
    EmployeeColumnsMixin,
    HRAdminMixin,
    badge,
    confirm_step,
    log_action,
    report,
)

from .models import EmployeeDocument


class ExpiryFilter(admin.SimpleListFilter):
    title = "expiry"
    parameter_name = "expiry"

    def lookups(self, request, model_admin):
        return [
            ("expired", "Expired"),
            ("30", "Expires within 30 days"),
            ("90", "Expires within 90 days"),
            ("later", "Expires later"),
            ("none", "No expiry date"),
        ]

    def queryset(self, request, queryset):
        today = timezone.localdate()
        value = self.value()
        if value == "expired":
            return queryset.filter(expiry_date__lt=today)
        if value in ("30", "90"):
            return queryset.filter(expiry_date__gte=today, expiry_date__lte=today + timedelta(days=int(value)))
        if value == "later":
            return queryset.filter(expiry_date__gt=today + timedelta(days=90))
        if value == "none":
            return queryset.filter(expiry_date__isnull=True)
        return queryset


@admin.register(EmployeeDocument)
class EmployeeDocumentAdmin(HRAdminMixin, EmployeeColumnsMixin, AuditedAdminMixin, admin.ModelAdmin):
    audit_module = "documents"
    audit_prefix = "document"
    no_bulk_delete = True  # documents are evidence; archive them instead

    actions = ["archive_documents", "restore_documents"]
    list_display = (
        "staff_number",
        "employee_name",
        "department",
        "document_type",
        "title",
        "file_name",
        "uploaded_at",
        "uploaded_by",
        "expiry",
        "is_active",
    )
    list_display_links = ("staff_number", "employee_name", "title")
    list_select_related = ("employee", "employee__department", "uploaded_by")
    list_filter = (
        "document_type",
        "is_active",
        ExpiryFilter,
        "employee__department",
    )
    search_fields = (*EMPLOYEE_SEARCH_FIELDS, "title", "description", "file")
    date_hierarchy = "uploaded_at"
    autocomplete_fields = ("employee",)
    ordering = ("-uploaded_at", "-id")
    readonly_fields = ("file_name", "uploaded_by", "uploaded_at", "created_at", "updated_at")

    def get_fields(self, request, obj=None):
        # The file can only be chosen when adding. Downloads go through the authenticated API endpoint (the media
        # folder is never served directly in production), so the admin shows the file name only, and a wrong file is
        # fixed by adding the right document and archiving or deleting this one.
        if obj is None:
            return ("employee", "document_type", "title", "file", "description", "expiry_date", "is_active")
        return ("employee", "document_type", "title", "file_name", "description", "expiry_date", "is_active", "uploaded_by", "uploaded_at")

    @admin.display(description="File")
    def file_name(self, obj):
        return os.path.basename(obj.file.name) if obj.file else "-"

    @admin.display(description="Expiry", ordering="expiry_date")
    def expiry(self, obj):
        if not obj.expiry_date:
            return "-"
        days = (obj.expiry_date - timezone.localdate()).days
        if days < 0:
            return badge(f"{obj.expiry_date:%d %b %Y} (expired)", "red")
        if days <= 30:
            return badge(f"{obj.expiry_date:%d %b %Y} ({days}d)", "amber")
        return f"{obj.expiry_date:%d %b %Y}"

    def has_manage_permission(self, request):
        return request.user.has_perm("documents.manage_documents")

    def save_model(self, request, obj, form, change):
        if change:
            return super().save_model(request, obj, form, change)
        # Same as the upload API: who uploaded it, and the "document.uploaded" audit event.
        obj.uploaded_by = request.user
        obj.save()
        AuditService.log(
            event_type="document.uploaded",
            module="documents",
            employee=obj.employee,
            actor=request.user,
            object=obj,
            severity=AuditSeverity.SUCCESS,
            title="Document uploaded",
            description=f"{obj.title} was uploaded for {obj.employee.full_name} (Django admin).",
            metadata={"document_type": obj.document_type, "document_id": obj.pk, "via": "admin"},
        )

    def delete_model(self, request, obj):
        # Same as the delete API: audit event, the file goes with the record.
        file = obj.file
        super().delete_model(request, obj)  # writes "document.admin_deleted", then deletes the row
        if file:
            transaction.on_commit(lambda: file.delete(save=False))

    def _set_active(self, request, queryset, *, active, action, title, verb):
        documents = [document for document in queryset if document.is_active != active]
        unchanged = [f"{document} (already {'active' if active else 'archived'})" for document in queryset if document.is_active == active]
        _, response = confirm_step(
            self, request, queryset, action=action, title=title,
            question=f"{verb.capitalize()} {len(documents)} document(s)?",
            notes=["Only the active flag changes. The file and the record stay exactly as they are, and this can be undone."],
            confirm_label=f"Yes, {verb}",
        )
        if response is not None:
            return response
        done = []
        with transaction.atomic():
            for document in documents:
                document.is_active = active
                document.save(update_fields=["is_active", "updated_at"])
                log_action(
                    request, event_type=f"document.admin_{verb}d", module="documents", employee=document.employee, obj=document,
                    severity=AuditSeverity.INFO, title=f"Document {verb}d in the admin",
                    description=f"{document} was {verb}d.", metadata={"document_id": document.pk},
                )
                done.append(str(document))
        report(self, request, done, skipped=unchanged, verb=f"{verb}d")

    @admin.action(description="Archive selected documents (mark inactive)", permissions=["manage"])
    def archive_documents(self, request, queryset):
        return self._set_active(request, queryset, active=False, action="archive_documents", title="Archive documents", verb="archive")

    @admin.action(description="Restore selected documents (mark active)", permissions=["manage"])
    def restore_documents(self, request, queryset):
        return self._set_active(request, queryset, active=True, action="restore_documents", title="Restore documents", verb="restore")

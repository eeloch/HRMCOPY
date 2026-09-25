from django.contrib import admin
from django.db.models import Count

from employees.admin_common import (
    EMPLOYEE_SEARCH_FIELDS,
    AuditedAdminMixin,
    ConfirmField,
    EmployeeColumnsMixin,
    HRAdminMixin,
    confirm_step,
    report,
    status_badge,
)

from .models import EmployeeOffence, EmployeeOffenceStatus, OffenceType
from .services import OffenceService

OFFENCE_STATUS_TONES = {
    EmployeeOffenceStatus.PENDING: "amber",
    EmployeeOffenceStatus.APPROVED: "blue",
    EmployeeOffenceStatus.REJECTED: "red",
    EmployeeOffenceStatus.DEDUCTED: "green",
}


@admin.register(OffenceType)
class OffenceTypeAdmin(HRAdminMixin, AuditedAdminMixin, admin.ModelAdmin):
    audit_module = "offences"
    audit_prefix = "offence_type"

    list_display = ("name", "default_amount", "active", "description", "offence_count")
    list_filter = ("active",)
    search_fields = ("name", "description")
    ordering = ("name",)

    def audit_employee(self, obj):
        return None

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(offences_total=Count("offences"))

    @admin.display(description="Recorded", ordering="offences_total")
    def offence_count(self, obj):
        return obj.offences_total


@admin.register(EmployeeOffence)
class EmployeeOffenceAdmin(HRAdminMixin, EmployeeColumnsMixin, AuditedAdminMixin, admin.ModelAdmin):
    audit_module = "offences"
    audit_prefix = "offence"
    no_bulk_delete = True  # offences are evidence and, once approved, payroll history

    actions = ["approve_offences", "reject_offences"]
    list_display = (
        "staff_number",
        "employee_name",
        "department",
        "offence_type",
        "amount",
        "incident_date",
        "state",
        "reviewer",
        "reviewed_at",
        "payroll_period",
    )
    list_display_links = ("staff_number", "employee_name")
    list_select_related = ("employee", "employee__department", "offence_type", "reviewer", "payroll_period")
    list_filter = ("status", "offence_type", "employee__department", ("incident_date", admin.DateFieldListFilter))
    search_fields = (*EMPLOYEE_SEARCH_FIELDS, "offence_type__name", "notes", "comment")
    date_hierarchy = "incident_date"
    autocomplete_fields = ("employee",)
    ordering = ("-incident_date", "-id")
    # Set by the approve / reject services and by payroll, never by hand.
    base_readonly = ("recorded_by", "reviewer", "reviewed_at", "payroll_period", "payroll", "payroll_line_item", "created_at", "updated_at")
    fieldsets = (
        ("Offence", {"fields": ("employee", "offence_type", "amount", "incident_date", "notes")}),
        ("Review", {
            "fields": ("status", "reviewer", "reviewed_at", "comment"),
            "description": "Changing the status here does not touch payroll. For pending offences use the Approve / Reject actions on the list.",
        }),
        ("Payroll", {"fields": ("payroll_period", "payroll", "payroll_line_item"), "classes": ("collapse",)}),
        ("Record", {"fields": ("recorded_by", "created_at", "updated_at"), "classes": ("collapse",)}),
    )

    def get_readonly_fields(self, request, obj=None):
        if obj and obj.status == EmployeeOffenceStatus.DEDUCTED:
            # Already on a payslip: payroll history, read-only (same rule as PPE issues).
            return tuple(field.name for field in obj._meta.fields)
        return self.base_readonly

    def has_delete_permission(self, request, obj=None):
        if obj and obj.status == EmployeeOffenceStatus.DEDUCTED:
            return False
        return super().has_delete_permission(request, obj)

    def has_review_permission(self, request):
        return request.user.has_perm("offences.review_employee_offences")

    def save_model(self, request, obj, form, change):
        created = obj.pk is None
        if created and obj.recorded_by_id is None:
            obj.recorded_by = request.user
        super().save_model(request, obj, form, change)
        if created and obj.status == EmployeeOffenceStatus.PENDING:
            OffenceService.notify_reviewers_of_pending_offence(obj)

    @admin.display(description="Status", ordering="status")
    def state(self, obj):
        return status_badge(obj, OFFENCE_STATUS_TONES)

    def _decide(self, request, queryset, *, action, title, question, notes, run, verb, fields=(), danger=False):
        pending = [offence for offence in queryset if offence.status == EmployeeOffenceStatus.PENDING]
        others = [offence for offence in queryset if offence.status != EmployeeOffenceStatus.PENDING]
        values, response = confirm_step(
            self, request, queryset, action=action, title=title, question=question.format(count=len(pending)),
            notes=notes, fields=fields, confirm_label=f"Yes, {verb}", danger=danger,
        )
        if response is not None:
            return response
        done, errors = [], []
        for offence in pending:
            try:
                run(offence, values)  # the service writes the audit event and notifies the person who recorded it
            except ValueError as error:
                errors.append(f"{offence}: {error}")
                continue
            done.append(str(offence))
        report(self, request, done, skipped=[f"{offence} ({offence.get_status_display().lower()})" for offence in others], errors=errors, verb=verb)

    @admin.action(description="Approve selected pending offences (deduct from salary)", permissions=["review"])
    def approve_offences(self, request, queryset):
        return self._decide(
            request, queryset, action="approve_offences", title="Approve offences", verb="approved",
            question="Approve {count} pending offence(s)? The amounts come out of the employees' salaries.",
            notes=["If the employee's payroll for the open period exists the deduction is applied now; otherwise it is deducted when payroll is generated.",
                   "A payroll record that is already approved or paid is never altered - those offences are reported and left pending."],
            fields=[ConfirmField("comment", "Comment (optional)", kind="textarea")],
            run=lambda offence, values: OffenceService.approve(offence, request.user, values.get("comment", "")),
        )

    @admin.action(description="Reject selected pending offences", permissions=["review"])
    def reject_offences(self, request, queryset):
        return self._decide(
            request, queryset, action="reject_offences", title="Reject offences", verb="rejected", danger=True,
            question="Reject {count} pending offence(s)? No deduction is made.",
            notes=["The reason is stored on each offence and sent to the person who recorded it."],
            fields=[ConfirmField("reason", "Rejection reason", kind="textarea", required=True)],
            run=lambda offence, values: OffenceService.reject(offence, request.user, values["reason"]),
        )

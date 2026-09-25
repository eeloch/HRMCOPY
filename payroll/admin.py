from django.contrib import admin
from django.db.models import Count, F, Sum
from django.urls import reverse
from django.utils.html import format_html

from audit.admin_support import (
    AdminAuditMixin,
    NoBulkDeleteMixin,
    ReadOnlyAdminMixin,
    badge,
    confirm_and_run,
    employee_columns,
    employee_search_fields,
    log_admin_event,
    naira,
    perm_checker,
    run_bulk,
)
from audit.models import AuditSeverity

from .models import (
    EmployeePayroll,
    EmployeePayrollStatus,
    PayrollLineItem,
    PayrollLineItemType,
    PayrollPeriod,
    PayrollPeriodStatus,
    PayrollSetting,
)
from .services import generate_payroll_for_period, recalculate_employee_payroll, sync_attendance_deductions_for_period

LOCKED_PERIODS = (PayrollPeriodStatus.APPROVED, PayrollPeriodStatus.PAID, PayrollPeriodStatus.CLOSED)
LOCKED_RECORDS = (EmployeePayrollStatus.APPROVED, EmployeePayrollStatus.PAID)

PERIOD_TONES = {"draft": "grey", "processing": "blue", "review": "amber", "approved": "green", "paid": "green", "closed": "grey"}
RECORD_TONES = {"draft": "grey", "review": "amber", "approved": "green", "paid": "green"}


def _period_locked(period):
    return period.status in LOCKED_PERIODS


def _line_item_editable(item):
    """Same rule as the API: only a manual line in a payroll that is not approved can be changed."""
    return not item.is_system_generated and not _period_locked(item.payroll.payroll_period) and item.payroll.status not in LOCKED_RECORDS


class PayrollLineItemInline(admin.TabularInline):
    """What makes up a payslip, read-only, on the employee's payroll page."""

    model = PayrollLineItem
    extra = 0
    can_delete = False
    fields = ("item_type", "code", "description", "amount_naira", "source_type", "is_system_generated")
    readonly_fields = fields
    ordering = ("item_type", "code", "id")

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    @admin.display(description="Amount")
    def amount_naira(self, obj):
        return naira(obj.amount)


@admin.register(PayrollPeriod)
class PayrollPeriodAdmin(AdminAuditMixin, NoBulkDeleteMixin, admin.ModelAdmin):
    """Approved / paid / closed periods are fully read-only. Status changes, approval and the totals belong to the
    payroll screens (which check pending exceptions and lock the records), never to a raw form."""

    audit_module = "payroll"
    list_display = ("period", "status_badge", "records", "basic_total", "gross_total", "deductions_total", "net_total", "approved_by", "approved_at", "paid_at", "records_link")
    list_display_links = ("period",)
    list_filter = ("status", "year", "month")
    search_fields = ("notes",)
    list_select_related = ("approved_by",)
    ordering = ("-year", "-month")
    list_per_page = 50
    save_on_top = True
    actions = ["regenerate_draft_periods", "resync_draft_periods"]

    ALWAYS_READONLY = ("status", "created_by", "created_at", "approved_by", "approved_at", "paid_at")
    fields = ("year", "month", "status", "notes", "created_by", "created_at", "approved_by", "approved_at", "paid_at")

    has_manage_payroll_permission = perm_checker("payroll.manage_payroll")

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(
            _records=Count("employee_payrolls"),
            _basic=Sum("employee_payrolls__basic_salary"),
            _gross=Sum("employee_payrolls__gross_earnings"),
            _deductions=Sum("employee_payrolls__total_deductions"),
            _net=Sum("employee_payrolls__net_pay"),
        )

    # ---- read-only rules
    def get_readonly_fields(self, request, obj=None):
        if obj is not None and _period_locked(obj):
            return self.fields  # approved / paid / closed: every field
        if obj is not None:
            return ("year", "month", *self.ALWAYS_READONLY)  # a draft: only the notes can be edited
        return self.ALWAYS_READONLY

    def has_change_permission(self, request, obj=None):
        if obj is not None and _period_locked(obj):
            return False
        return super().has_change_permission(request, obj)

    def can_delete_object(self, request, obj):
        return obj.status == PayrollPeriodStatus.DRAFT and not obj.employee_payrolls.exists()

    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)

    # ---- columns
    @admin.display(description="Period", ordering="year")
    def period(self, obj):
        return obj.display_name

    @admin.display(description="Status", ordering="status")
    def status_badge(self, obj):
        return badge(obj.get_status_display(), PERIOD_TONES.get(obj.status, "grey"))

    @admin.display(description="Records", ordering="_records")
    def records(self, obj):
        return obj._records

    @admin.display(description="Basic", ordering="_basic")
    def basic_total(self, obj):
        return naira(obj._basic or 0)

    @admin.display(description="Gross", ordering="_gross")
    def gross_total(self, obj):
        return naira(obj._gross or 0)

    @admin.display(description="Deductions", ordering="_deductions")
    def deductions_total(self, obj):
        return naira(obj._deductions or 0)

    @admin.display(description="Net pay", ordering="_net")
    def net_total(self, obj):
        return naira(obj._net or 0)

    @admin.display(description="Employees")
    def records_link(self, obj):
        return format_html('<a href="{}?payroll_period__id__exact={}">View records</a>', reverse("admin:payroll_employeepayroll_changelist"), obj.pk)

    # ---- actions (draft periods only; they call the same services as the payroll screens)
    @admin.action(description="Regenerate selected DRAFT periods (add missing records, re-apply deductions)", permissions=["manage_payroll"])
    def regenerate_draft_periods(self, request, queryset):
        def one(period):
            if period.status != PayrollPeriodStatus.DRAFT:
                raise ValueError(f"{period.display_name} is {period.get_status_display().lower()}; only draft payroll can be regenerated here.")
            s = generate_payroll_for_period(period, actor=request.user)
            return f"{s.created} new record(s), {s.existing} already there, {s.deductions_applied} deduction/bonus line(s) applied."

        return confirm_and_run(
            self, request, queryset,
            action="regenerate_draft_periods",
            title="Regenerate draft payroll",
            intro="Adds a payroll record for anyone missing and re-applies approved deductions and bonuses. Existing salary snapshots are never overwritten. Periods that are not draft are skipped.",
            submit_label="Regenerate",
            run=lambda text: run_bulk(self, request, queryset, module="payroll", name="regenerate_periods", verb="Payroll regenerated", fn=one),
        )

    @admin.action(description="Re-sync attendance and leave deductions for selected DRAFT periods", permissions=["manage_payroll"])
    def resync_draft_periods(self, request, queryset):
        def one(period):
            if period.status != PayrollPeriodStatus.DRAFT:
                raise ValueError(f"{period.display_name} is {period.get_status_display().lower()}; only draft payroll can be re-synced here.")
            s = sync_attendance_deductions_for_period(period, actor=request.user)
            return f"{s.created} added, {s.updated} changed, {s.deleted} removed; {s.employees_skipped_incomplete_roster} skipped (incomplete roster)."

        return confirm_and_run(
            self, request, queryset,
            action="resync_draft_periods",
            title="Re-sync attendance deductions",
            intro="Recalculates absence, lateness, early-departure and unpaid-leave deductions from the reviewed attendance. Periods that are not draft are skipped.",
            submit_label="Re-sync",
            run=lambda text: run_bulk(self, request, queryset, module="payroll", name="resync_attendance", verb="Attendance deductions re-synced", fn=one),
        )


@admin.register(EmployeePayroll)
class EmployeePayrollAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    """A payslip record is a snapshot: look, follow the line items, recalculate a draft. Nothing is edited by hand."""

    staff_number, employee_name, department = employee_columns("employee")
    list_display = ("staff_number", "employee_name", "department", "period", "basic", "gross", "deductions", "net", "status_badge")
    list_display_links = ("staff_number", "employee_name")
    list_filter = ("status", "payroll_period", "employee__department")
    search_fields = employee_search_fields("employee")
    list_select_related = ("employee", "employee__department", "payroll_period")
    date_hierarchy = "generated_at"
    raw_id_fields = ("employee", "payroll_period")
    list_per_page = 50
    save_on_top = True
    inlines = [PayrollLineItemInline]
    actions = ["recalculate_draft_records"]
    # bank_details_snapshot is deliberately not shown: bank details stay in the payroll screens.
    fields = ("staff_number", "employee_name", "department", "payroll_period", "status_badge", "basic", "gross", "deductions", "net", "generated_at", "updated_at")
    readonly_fields = (*fields, "status")  # every field, the raw status included

    has_manage_payroll_permission = perm_checker("payroll.manage_payroll")

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(_period_key=F("payroll_period__year") * 100 + F("payroll_period__month")).order_by("-_period_key", "employee__employee_id")

    @admin.display(description="Period", ordering="_period_key")
    def period(self, obj):
        return obj.payroll_period.display_name

    @admin.display(description="Status", ordering="status")
    def status_badge(self, obj):
        return badge(obj.get_status_display(), RECORD_TONES.get(obj.status, "grey"))

    @admin.display(description="Basic", ordering="basic_salary")
    def basic(self, obj):
        return naira(obj.basic_salary)

    @admin.display(description="Gross", ordering="gross_earnings")
    def gross(self, obj):
        return naira(obj.gross_earnings)

    @admin.display(description="Deductions", ordering="total_deductions")
    def deductions(self, obj):
        return naira(obj.total_deductions)

    @admin.display(description="Net pay", ordering="net_pay")
    def net(self, obj):
        return naira(obj.net_pay)

    @admin.action(description="Regenerate selected draft payroll records (recalculate from line items)", permissions=["manage_payroll"])
    def recalculate_draft_records(self, request, queryset):
        def one(payroll):
            payroll = EmployeePayroll.objects.select_for_update().select_related("employee", "payroll_period").get(pk=payroll.pk)
            if payroll.payroll_period.status != PayrollPeriodStatus.DRAFT:
                raise ValueError(f"{payroll.payroll_period.display_name} is {payroll.payroll_period.get_status_display().lower()}; only draft payroll can be recalculated here.")
            if payroll.status in LOCKED_RECORDS:
                raise ValueError("this record is already approved.")
            before = payroll.net_pay
            recalculate_employee_payroll(payroll)
            return "net pay unchanged." if before == payroll.net_pay else f"net pay {naira(before)} -> {naira(payroll.net_pay)}."

        return confirm_and_run(
            self, request, queryset,
            action="recalculate_draft_records",
            title="Recalculate draft payroll records",
            intro="Recomputes gross, deductions and net pay from each record's salary snapshot and its line items (the payroll service does the maths). Only records in a draft period are touched; the before and after net pay is shown and logged.",
            submit_label="Recalculate",
            run=lambda text: run_bulk(self, request, queryset, module="payroll", name="recalculate_records", verb="Payroll records recalculated", fn=one),
        )


@admin.register(PayrollLineItem)
class PayrollLineItemAdmin(AdminAuditMixin, NoBulkDeleteMixin, admin.ModelAdmin):
    """Line items explain every naira of a payslip. Only a manual line in an unapproved payroll can be corrected or
    removed here - exactly what the API allows - and the payslip is recalculated straight away."""

    audit_module = "payroll"
    staff_number, employee_name, department = employee_columns("payroll__employee")
    list_display = ("staff_number", "employee_name", "period", "type_badge", "code", "description", "amount_naira", "source", "is_system_generated")
    list_display_links = ("staff_number", "employee_name")
    list_filter = ("item_type", "is_system_generated", "payroll__payroll_period", "code")
    search_fields = (*employee_search_fields("payroll__employee"), "code", "description")
    list_select_related = ("payroll", "payroll__employee", "payroll__employee__department", "payroll__payroll_period")
    date_hierarchy = "created_at"
    ordering = ("-created_at", "-id")
    list_per_page = 50
    save_on_top = True
    fields = ("payroll", "item_type", "code", "description", "amount", "source_type", "source_reference", "metadata_pretty", "is_system_generated", "created_at")
    readonly_fields = ("payroll", "source_type", "source_reference", "metadata_pretty", "is_system_generated", "created_at")

    @admin.display(description="Period", ordering="payroll__payroll_period__year")
    def period(self, obj):
        return obj.payroll.payroll_period.display_name

    @admin.display(description="Type", ordering="item_type")
    def type_badge(self, obj):
        return badge(obj.get_item_type_display(), "green" if obj.item_type == PayrollLineItemType.EARNING else "red")

    @admin.display(description="Amount", ordering="amount")
    def amount_naira(self, obj):
        return naira(obj.amount)

    @admin.display(description="Source")
    def source(self, obj):
        return "system" if obj.is_system_generated else "manual"

    @admin.display(description="Details")
    def metadata_pretty(self, obj):
        from audit.admin_support import pretty_json

        return pretty_json(obj.metadata)

    def get_readonly_fields(self, request, obj=None):
        if obj is not None and not _line_item_editable(obj):
            return self.fields
        return self.readonly_fields

    def has_add_permission(self, request):
        return False  # a new line goes through the payroll screen, which checks the lock and recalculates

    def has_change_permission(self, request, obj=None):
        if obj is not None and not _line_item_editable(obj):
            return False
        return super().has_change_permission(request, obj)

    def can_delete_object(self, request, obj):
        return _line_item_editable(obj)

    def audit_employee(self, obj):
        return obj.payroll.employee

    def save_model(self, request, obj, form, change):
        before = {name: str(form.initial.get(name)) for name in form.changed_data}
        admin.ModelAdmin.save_model(self, request, obj, form, change)  # this admin writes its own, richer audit event
        old_net = obj.payroll.net_pay
        recalculate_employee_payroll(obj.payroll)
        if form.changed_data:
            log_admin_event(
                request, module="payroll", event_type="payroll.line_item_admin_changed", title="Payroll line item corrected in Django admin",
                description=f"{obj} was corrected; net pay {naira(old_net)} -> {naira(obj.payroll.net_pay)}.",
                object=obj.payroll, employee=obj.payroll.employee, severity=AuditSeverity.WARNING,
                metadata={"line_item_id": obj.pk, "before": before, "after": {n: str(form.cleaned_data.get(n)) for n in form.changed_data}, "net_before": str(old_net), "net_after": str(obj.payroll.net_pay)},
            )

    def delete_model(self, request, obj):
        payroll, employee, label, pk, amount = obj.payroll, obj.payroll.employee, str(obj), obj.pk, obj.amount
        old_net = payroll.net_pay
        obj.delete()
        recalculate_employee_payroll(payroll)
        log_admin_event(
            request, module="payroll", event_type="payroll.line_item_admin_deleted", title="Payroll line item removed in Django admin",
            description=f"{label} was removed; net pay {naira(old_net)} -> {naira(payroll.net_pay)}.",
            object=payroll, employee=employee, severity=AuditSeverity.WARNING,
            metadata={"line_item_id": pk, "amount": str(amount), "net_before": str(old_net), "net_after": str(payroll.net_pay)},
        )


@admin.register(PayrollSetting)
class PayrollSettingAdmin(AdminAuditMixin, NoBulkDeleteMixin, admin.ModelAdmin):
    audit_module = "payroll"
    list_display = ("key", "short_value", "description", "updated_at")
    search_fields = ("key", "description")
    ordering = ("key",)
    list_per_page = 50
    save_on_top = True

    @admin.display(description="Value")
    def short_value(self, obj):
        text = str(obj.value)
        return text if len(text) <= 80 else text[:77] + "..."

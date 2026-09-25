from django.contrib import admin
from django.db.models import Sum

from audit.admin_support import (
    ReadOnlyAdminMixin,
    any_perm_checker,
    badge,
    employee_columns,
    employee_search_fields,
    naira,
    perm_checker,
    service_action,
)
from audit.models import AuditSeverity

from .models import DeferredFundAccount, DeferredFundEntry, DeferredFundWithdrawal, EntryType, WithdrawalStatus
from .services import DeferredFundService

WITHDRAWAL_TONES = {"requested": "amber", "approved": "blue", "declined": "red", "paid": "green", "cancelled": "red"}
ENTRY_TONES = {"contribution": "green", "opening": "blue", "adjustment": "amber", "withdrawal": "grey", "release": "grey", "forfeiture": "red"}


class EntryInline(admin.TabularInline):
    model = DeferredFundEntry
    extra = 0
    can_delete = False
    fields = ("entry_date", "entry_type", "amount_naira", "payroll_period", "note")
    readonly_fields = fields
    ordering = ("-entry_date", "-id")

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    @admin.display(description="Amount")
    def amount_naira(self, obj):
        return naira(obj.amount, signed=True)


@admin.register(DeferredFundAccount)
class DeferredFundAccountAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    """Read-only. Enrolling, changing the percentage, adjusting, withdrawing and forfeiting all go through the Deferred Funds
    screen (the service), which checks the policy and writes the audit trail."""

    staff_number, employee_name, department = employee_columns("employee")
    list_display = ("staff_number", "employee_name", "department", "percent_label", "active_badge", "balance_naira", "saving_since", "enrolled_on")
    list_display_links = ("staff_number", "employee_name")
    list_filter = ("active", "employee__department", "enrolled_on")
    search_fields = employee_search_fields("employee")
    list_select_related = ("employee", "employee__department")
    date_hierarchy = "enrolled_on"
    ordering = ("employee__employee_id",)
    raw_id_fields = ("employee", "created_by")
    list_per_page = 50
    save_on_top = True
    inlines = [EntryInline]
    fields = ("staff_number", "employee_name", "percent_label", "active", "balance_naira", "saving_since", "enrolled_on", "created_by", "updated_at")
    readonly_fields = fields

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(_balance=Sum("entries__amount"))

    @admin.display(description="Percent", ordering="percent")
    def percent_label(self, obj):
        return f"{obj.percent.normalize():f}%"

    @admin.display(description="Enrolled", ordering="active")
    def active_badge(self, obj):
        return badge("Active" if obj.active else "Stopped", "green" if obj.active else "grey")

    @admin.display(description="Balance held", ordering="_balance")
    def balance_naira(self, obj):
        return naira(obj._balance or 0)


@admin.register(DeferredFundEntry)
class DeferredFundEntryAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    """The ledger. Every line is written by a service (a payroll contribution, a payout, an audited adjustment), so it is never edited here."""

    staff_number, employee_name, department = employee_columns("account__employee")
    list_display = ("entry_date", "staff_number", "employee_name", "type_badge", "amount_naira", "period", "note", "created_by")
    list_display_links = ("entry_date", "staff_number", "employee_name")
    list_filter = ("entry_type", "payroll_period", "account__employee__department", "entry_date")
    search_fields = (*employee_search_fields("account__employee"), "note")
    list_select_related = ("account", "account__employee", "account__employee__department", "payroll_period", "created_by")
    date_hierarchy = "entry_date"
    ordering = ("-entry_date", "-id")
    raw_id_fields = ("account", "payroll_period", "line_item", "created_by")
    list_per_page = 50
    save_on_top = True

    @admin.display(description="Type", ordering="entry_type")
    def type_badge(self, obj):
        return badge(obj.get_entry_type_display(), ENTRY_TONES.get(obj.entry_type, "grey"))

    @admin.display(description="Amount", ordering="amount")
    def amount_naira(self, obj):
        return naira(obj.amount, signed=True)

    @admin.display(description="Payroll", ordering="payroll_period__year")
    def period(self, obj):
        return obj.payroll_period.display_name if obj.payroll_period_id else "-"


@admin.register(DeferredFundWithdrawal)
class DeferredFundWithdrawalAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    staff_number, employee_name, department = employee_columns("account__employee")
    list_display = ("staff_number", "employee_name", "kind", "amount_naira", "status_badge", "policy", "created_at", "decided_by", "paid_on")
    list_display_links = ("staff_number", "employee_name")
    list_filter = ("status", "kind", "account__employee__department", "created_at")
    search_fields = (*employee_search_fields("account__employee"), "reason", "payment_reference")
    list_select_related = ("account", "account__employee", "account__employee__department", "decided_by")
    date_hierarchy = "created_at"
    ordering = ("-created_at", "-id")
    raw_id_fields = ("account", "recorded_by", "decided_by", "paid_by")
    list_per_page = 50
    save_on_top = True
    actions = ["approve_withdrawals", "decline_withdrawals", "cancel_withdrawals"]

    has_approve_permission = perm_checker("deferredfunds.approve_deferred_withdrawal")
    has_cancel_permission = any_perm_checker("deferredfunds.manage_deferred_funds", "deferredfunds.approve_deferred_withdrawal")

    @admin.display(description="Amount", ordering="amount")
    def amount_naira(self, obj):
        return naira(obj.amount) if obj.amount is not None else "Whole balance"

    @admin.display(description="Status", ordering="status")
    def status_badge(self, obj):
        return badge(obj.get_status_display(), WITHDRAWAL_TONES.get(obj.status, "grey"))

    @admin.display(description="Policy")
    def policy(self, obj):
        return badge("Outside policy", "red") if obj.rule_exceptions else badge("Within policy", "green")

    approve_withdrawals = service_action(
        name="approve_withdrawals", label="Approve selected withdrawals", perm="approve", module="deferred_funds", verb="Withdrawals approved", severity=AuditSeverity.SUCCESS,
        intro="Approves requests that are awaiting approval. Nothing is paid out here: finance pays from the Deferred Funds screen. A request outside policy needs your reason.",
        reason="optional", reason_label="Comment", reason_help="Required for any request marked 'Outside policy'.",
        call=lambda withdrawal, actor, text: (DeferredFundService.approve(withdrawal, actor=actor, comment=text), "approved.")[1],
    )
    decline_withdrawals = service_action(
        name="decline_withdrawals", label="Decline selected withdrawals", perm="approve", module="deferred_funds", verb="Withdrawals declined", severity=AuditSeverity.WARNING,
        intro="Declines requests that are awaiting approval.",
        reason="required", reason_label="Reason for declining",
        call=lambda withdrawal, actor, text: (DeferredFundService.decline(withdrawal, actor=actor, comment=text), "declined.")[1],
    )
    cancel_withdrawals = service_action(
        name="cancel_withdrawals", label="Cancel selected withdrawals (not yet paid)", perm="cancel", module="deferred_funds", verb="Withdrawals cancelled", severity=AuditSeverity.WARNING,
        intro="Cancels requests that are awaiting approval or approved but not yet paid.",
        call=lambda withdrawal, actor, text: (DeferredFundService.cancel(withdrawal, actor=actor), "cancelled.")[1],
    )

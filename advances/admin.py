from django.contrib import admin
from django.db.models import F, Sum

from audit.admin_support import (
    ReadOnlyAdminMixin,
    any_perm_checker,
    badge,
    employee_columns,
    employee_search_fields,
    month_label,
    naira,
    perm_checker,
    service_action,
)
from audit.models import AuditSeverity

from .models import AdvanceRepayment, AdvanceStatus, SalaryAdvance
from .services import AdvanceService

STATUS_TONES = {"requested": "amber", "approved": "blue", "declined": "red", "paid": "green", "repaid": "grey", "cancelled": "red"}


class AdvanceRepaymentInline(admin.TabularInline):
    model = AdvanceRepayment
    extra = 0
    can_delete = False
    fields = ("payroll_period", "amount_naira", "created_at")
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    @admin.display(description="Amount")
    def amount_naira(self, obj):
        return naira(obj.amount)


@admin.register(SalaryAdvance)
class SalaryAdvanceAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    """Read-only: an advance is money out of the company, so amounts and months are never edited by hand. To fix a wrong
    one, cancel it (before it is paid) and record it again on the Advances screen. Approve / decline / cancel here use the
    same service as the screen, so they are audited and follow the same rules."""

    staff_number, employee_name, department = employee_columns("employee")
    list_display = ("staff_number", "employee_name", "department", "amount_naira", "repayment_months", "instalment_naira", "first_deduction", "status_badge", "repaid_naira", "balance_naira", "created_at")
    list_display_links = ("staff_number", "employee_name")
    list_filter = ("status", "employee__department", "deduct_from_year", "created_at")
    search_fields = (*employee_search_fields("employee"), "reason", "payment_reference")
    list_select_related = ("employee", "employee__department", "recorded_by", "decided_by", "paid_by")
    date_hierarchy = "created_at"
    ordering = ("-created_at", "-id")
    raw_id_fields = ("employee", "recorded_by", "decided_by", "paid_by")
    list_per_page = 50
    save_on_top = True
    inlines = [AdvanceRepaymentInline]
    actions = ["approve_advances", "decline_advances", "cancel_advances"]

    has_approve_permission = perm_checker("advances.approve_salary_advance")
    has_cancel_permission = any_perm_checker("advances.record_salary_advance", "advances.approve_salary_advance")

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(_repaid=Sum("repayments__amount"))

    def get_fields(self, request, obj=None):
        return (
            "staff_number", "employee_name", "amount_naira", "reason", "repayment_months", "instalment_naira", "first_deduction", "status_badge",
            "repaid_naira", "balance_naira", "recorded_by", "created_at", "decided_by", "decided_at", "decision_comment", "paid_by", "paid_on", "payment_reference",
        )

    def get_readonly_fields(self, request, obj=None):
        return self.get_fields(request, obj)

    @admin.display(description="Amount", ordering="amount")
    def amount_naira(self, obj):
        return naira(obj.amount)

    @admin.display(description="Instalment")
    def instalment_naira(self, obj):
        return naira(obj.instalment)

    @admin.display(description="First deduction", ordering="deduct_from_year")
    def first_deduction(self, obj):
        return month_label(obj.deduct_from_year, obj.deduct_from_month)

    @admin.display(description="Status", ordering="status")
    def status_badge(self, obj):
        return badge(obj.get_status_display(), STATUS_TONES.get(obj.status, "grey"))

    @admin.display(description="Repaid", ordering="_repaid")
    def repaid_naira(self, obj):
        if obj.status not in (AdvanceStatus.PAID, AdvanceStatus.REPAID):
            return "-"
        return naira(obj._repaid or 0)

    @admin.display(description="Still owed")
    def balance_naira(self, obj):
        if obj.status not in (AdvanceStatus.PAID, AdvanceStatus.REPAID):
            return "-"
        return naira(obj.amount - (obj._repaid or 0))

    approve_advances = service_action(
        name="approve_advances", label="Approve selected advances", perm="approve", module="advances", verb="Advances approved", severity=AuditSeverity.SUCCESS,
        intro="Approves the advances that are awaiting approval. Nothing is paid out: finance still pays them from the Advances screen. Others are skipped.",
        reason="optional", reason_label="Comment",
        call=lambda advance, actor, text: (AdvanceService.approve(advance, actor=actor, comment=text), "approved.")[1],
    )
    decline_advances = service_action(
        name="decline_advances", label="Decline selected advances", perm="approve", module="advances", verb="Advances declined", severity=AuditSeverity.WARNING,
        intro="Declines the advances that are awaiting approval. Others are skipped.",
        reason="required", reason_label="Reason for declining",
        call=lambda advance, actor, text: (AdvanceService.decline(advance, actor=actor, comment=text), "declined.")[1],
    )
    cancel_advances = service_action(
        name="cancel_advances", label="Cancel selected advances (not yet paid)", perm="cancel", module="advances", verb="Advances cancelled", severity=AuditSeverity.WARNING,
        intro="Cancels advances that are awaiting approval or approved but not yet paid. A paid advance cannot be cancelled: it is being repaid through payroll.",
        reason="optional", reason_label="Comment",
        call=lambda advance, actor, text: (AdvanceService.cancel(advance, actor=actor, comment=text), "cancelled.")[1],
    )


@admin.register(AdvanceRepayment)
class AdvanceRepaymentAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    """Repayments are created by payroll only; this list is for finding what was taken from whom and when."""

    staff_number, employee_name, department = employee_columns("advance__employee")
    list_display = ("staff_number", "employee_name", "advance_amount", "period", "amount_naira", "created_at")
    list_display_links = ("staff_number", "employee_name")
    list_filter = ("payroll_period", "advance__employee__department", "created_at")
    search_fields = employee_search_fields("advance__employee")
    list_select_related = ("advance", "advance__employee", "advance__employee__department", "payroll_period")
    date_hierarchy = "created_at"
    raw_id_fields = ("advance", "payroll_period", "payroll", "line_item")
    list_per_page = 50
    save_on_top = True

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(_period_key=F("payroll_period__year") * 100 + F("payroll_period__month")).order_by("-_period_key", "-id")

    @admin.display(description="Advance", ordering="advance__amount")
    def advance_amount(self, obj):
        return naira(obj.advance.amount)

    @admin.display(description="Period", ordering="_period_key")
    def period(self, obj):
        return obj.payroll_period.display_name

    @admin.display(description="Taken", ordering="amount")
    def amount_naira(self, obj):
        return naira(obj.amount)

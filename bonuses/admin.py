from django.contrib import admin

from audit.admin_support import (
    ReadOnlyAdminMixin,
    badge,
    employee_columns,
    employee_search_fields,
    month_label,
    naira,
    perm_checker,
    service_action,
)
from audit.models import AuditSeverity

from .models import Bonus, EmployeeOfTheMonth
from .services import BonusService, EmployeeOfTheMonthService

BONUS_TONES = {"proposed": "amber", "approved": "blue", "paid": "green", "declined": "red", "cancelled": "red"}
EOTM_TONES = {"proposed": "amber", "approved": "green", "declined": "red", "cancelled": "red"}


@admin.register(Bonus)
class BonusAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    """Read-only: an approved bonus becomes an earning in payroll, so it is decided through the service (approve /
    decline / cancel below), never edited by hand."""

    staff_number, employee_name, department = employee_columns("employee")
    list_display = ("staff_number", "employee_name", "department", "kind", "amount_naira", "for_month", "paid_with", "status_badge", "decided_by", "created_at")
    list_display_links = ("staff_number", "employee_name")
    list_filter = ("status", "kind", "pay_year", "pay_month", "employee__department", "created_at")
    search_fields = (*employee_search_fields("employee"), "reason")
    list_select_related = ("employee", "employee__department", "decided_by")
    date_hierarchy = "created_at"
    ordering = ("-created_at", "-id")
    raw_id_fields = ("employee", "recorded_by", "decided_by", "payroll", "payroll_line_item")
    list_per_page = 50
    save_on_top = True
    actions = ["approve_bonuses", "decline_bonuses", "cancel_bonuses"]

    has_approve_permission = perm_checker("bonuses.approve_bonus")

    @admin.display(description="Amount", ordering="amount")
    def amount_naira(self, obj):
        return naira(obj.amount)

    @admin.display(description="For month", ordering="performance_year")
    def for_month(self, obj):
        return month_label(obj.performance_year, obj.performance_month)

    @admin.display(description="Paid with", ordering="pay_year")
    def paid_with(self, obj):
        return month_label(obj.pay_year, obj.pay_month)

    @admin.display(description="Status", ordering="status")
    def status_badge(self, obj):
        return badge(obj.get_status_display(), BONUS_TONES.get(obj.status, "grey"))

    approve_bonuses = service_action(
        name="approve_bonuses", label="Approve selected bonuses", perm="approve", module="bonuses", verb="Bonuses approved", severity=AuditSeverity.SUCCESS,
        intro="Approves bonuses awaiting approval. If that month's payroll already exists and is not approved, the bonus is added to it as an earning straight away; otherwise it is added when payroll is generated. A month whose payroll is approved refuses it.",
        reason="optional", reason_label="Comment",
        call=lambda bonus, actor, text: (BonusService.approve(bonus, actor=actor, comment=text), "approved.")[1],
    )
    decline_bonuses = service_action(
        name="decline_bonuses", label="Decline selected bonuses", perm="approve", module="bonuses", verb="Bonuses declined", severity=AuditSeverity.WARNING,
        intro="Declines bonuses awaiting approval.", reason="required", reason_label="Reason for declining",
        call=lambda bonus, actor, text: (BonusService.decline(bonus, actor=actor, comment=text), "declined.")[1],
    )
    cancel_bonuses = service_action(
        name="cancel_bonuses", label="Cancel selected bonuses (not yet in payroll)", perm="approve", module="bonuses", verb="Bonuses cancelled", severity=AuditSeverity.WARNING,
        intro="Cancels bonuses that are proposed or approved but not yet in payroll. A bonus already in payroll must be reversed there.",
        call=lambda bonus, actor, text: (BonusService.cancel(bonus, actor=actor), "cancelled.")[1],
    )


@admin.register(EmployeeOfTheMonth)
class EmployeeOfTheMonthAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    staff_number, employee_name, department = employee_columns("employee")
    list_display = ("award_month", "award_department", "staff_number", "employee_name", "reward_naira", "status_badge", "decided_by")
    list_display_links = ("award_month", "staff_number", "employee_name")
    list_filter = ("status", "year", "month", "department")
    search_fields = (*employee_search_fields("employee"), "reason", "department__name")
    list_select_related = ("employee", "department", "decided_by")
    ordering = ("-year", "-month", "department__name")
    raw_id_fields = ("employee", "bonus", "recorded_by", "decided_by")
    list_per_page = 50
    save_on_top = True
    actions = ["approve_awards", "decline_awards", "cancel_awards"]

    has_approve_permission = perm_checker("bonuses.approve_bonus")

    @admin.display(description="Month", ordering="year")
    def award_month(self, obj):
        return month_label(obj.year, obj.month)

    @admin.display(description="Award department", ordering="department__name")
    def award_department(self, obj):
        return obj.department.name

    @admin.display(description="Reward", ordering="reward_amount")
    def reward_naira(self, obj):
        return naira(obj.reward_amount) if obj.reward_amount else "-"

    @admin.display(description="Status", ordering="status")
    def status_badge(self, obj):
        return badge(obj.get_status_display(), EOTM_TONES.get(obj.status, "grey"))

    approve_awards = service_action(
        name="approve_awards", label="Approve selected awards", perm="approve", module="bonuses", verb="Awards approved", severity=AuditSeverity.SUCCESS,
        intro="Approves the awards awaiting approval. An award with a reward becomes an approved bonus in that month's payroll (added at once if payroll exists and is open).",
        reason="optional", reason_label="Comment",
        call=lambda entry, actor, text: (EmployeeOfTheMonthService.approve(entry, actor=actor, comment=text), "approved.")[1],
    )
    decline_awards = service_action(
        name="decline_awards", label="Decline selected awards", perm="approve", module="bonuses", verb="Awards declined", severity=AuditSeverity.WARNING,
        intro="Declines awards awaiting approval.", reason="required", reason_label="Reason for declining",
        call=lambda entry, actor, text: (EmployeeOfTheMonthService.decline(entry, actor=actor, comment=text), "declined.")[1],
    )
    cancel_awards = service_action(
        name="cancel_awards", label="Cancel selected awards (reward not yet in payroll)", perm="approve", module="bonuses", verb="Awards cancelled", severity=AuditSeverity.WARNING,
        intro="Cancels awards that are proposed or approved. If the reward is already in payroll it is refused: reverse it in payroll first.",
        call=lambda entry, actor, text: (EmployeeOfTheMonthService.cancel(entry, actor=actor), "cancelled.")[1],
    )

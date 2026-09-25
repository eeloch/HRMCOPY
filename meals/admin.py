from django.contrib import admin

from audit.admin_support import (
    AdminAuditMixin,
    NoBulkDeleteMixin,
    ReadOnlyAdminMixin,
    badge,
    employee_columns,
    employee_search_fields,
    log_admin_event,
    naira,
    perm_checker,
    service_action,
)
from audit.models import AuditSeverity

from . import authorizations, gating
from .models import (
    EmployeeMealEntitlement,
    MealAbsencePenalty,
    MealCollection,
    MealDevice,
    MealEntitlementRule,
    MealEvent,
    MealExcessException,
    MealExtraAuthorization,
    MealTerminalUserState,
    MealTicketRate,
    MealVendorPayment,
)
from .services import MealService

COLLECTION_TONES = {"within_entitlement": "green", "excess": "amber", "rest_day": "red"}
EXCESS_TONES = {"pending": "amber", "approved": "blue", "deducted": "green", "cancelled": "grey", "declined": "red"}
SEARCH = employee_search_fields("employee")


class RecheckTerminalMixin:
    """'Re-check terminal access' for lists of rows that concern an employee (meals.gating.refresh)."""

    has_terminal_permission = perm_checker("meals.manage_meal_configuration")

    @admin.action(description="Re-check terminal access for selected people", permissions=["terminal"])
    def recheck_terminal_access(self, request, queryset):
        ids = sorted({obj.employee_id for obj in queryset})
        managed = gating.gated_employees(only=ids).count()
        queued = sum(gating.refresh(employee_id) for employee_id in ids)
        if not managed:
            self.message_user(request, f"Checked {len(ids)} people, but none of them is managed by terminal gating (MEAL_GATING_EMPLOYEE_IDS), so nothing was sent to the terminals.", "warning")
        else:
            self.message_user(request, f"Re-checked {len(ids)} people ({managed} managed by terminal gating): {queued} switch command(s) queued for the terminals.")
        log_admin_event(
            request, module="meals", event_type="admin.meals.recheck_terminal_access",
            title="Terminal access re-checked from Django admin", description=f"{len(ids)} people re-checked, {queued} terminal switch command(s) queued.",
            metadata={"employee_ids": ids[:200], "people": len(ids), "managed": managed, "queued": queued},
        )


class VoidedFilter(admin.SimpleListFilter):
    title = "voided"
    parameter_name = "voided"

    def lookups(self, request, model_admin):
        return (("no", "Counting"), ("yes", "Voided"))

    def queryset(self, request, queryset):
        if self.value() == "yes":
            return queryset.filter(voided_at__isnull=False)
        if self.value() == "no":
            return queryset.filter(voided_at__isnull=True)
        return queryset


@admin.register(MealDevice)
class MealDeviceAdmin(admin.ModelAdmin):
    """Read-only: devices are registered from the Biometric Devices page
    (purpose=meal_ticket) and mirrored here automatically - see
    attendance.views.devices.sync_meal_device. Adding or editing a row
    directly here would create one with no matching BiometricDevice, which
    the AiFace gateway can't route scans to.
    """

    list_display = ("name", "serial_number", "active", "created_at")
    list_filter = ("active",)
    search_fields = ("name", "serial_number")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(MealTicketRate)
class MealTicketRateAdmin(AdminAuditMixin, NoBulkDeleteMixin, admin.ModelAdmin):
    """The price of one ticket. Past tickets keep the rate they were issued at, so changing this only affects new ones.
    Rates are never deleted: switch one off (untick Active) or give it an end date."""

    audit_module = "meals"
    list_display = ("amount_naira", "effective_from", "effective_to", "active")
    list_display_links = ("amount_naira",)
    list_filter = ("active",)
    date_hierarchy = "effective_from"
    ordering = ("-effective_from", "-id")
    list_per_page = 50
    save_on_top = True

    @admin.display(description="Price per ticket", ordering="amount")
    def amount_naira(self, obj):
        return naira(obj.amount)


@admin.register(MealEntitlementRule)
class MealEntitlementRuleAdmin(AdminAuditMixin, NoBulkDeleteMixin, admin.ModelAdmin):
    audit_module = "meals"
    list_display = ("priority", "description", "tickets_per_work_day", "employment_type", "employment_category", "position", "minimum_years_of_service", "minimum_months_of_service", "active")
    list_display_links = ("priority", "description")
    list_filter = ("active", "employment_type", "employment_category")
    search_fields = ("description", "position__name")
    list_select_related = ("position",)
    raw_id_fields = ("position",)
    ordering = ("priority", "id")
    list_per_page = 50
    save_on_top = True

    def audit_employee(self, obj):
        return None

    def can_delete_object(self, request, obj):
        return True


@admin.register(EmployeeMealEntitlement)
class EmployeeMealEntitlementAdmin(RecheckTerminalMixin, AdminAuditMixin, NoBulkDeleteMixin, admin.ModelAdmin):
    """Tickets a person may collect per work day. Editing or removing one is logged with before and after values; use
    'Re-check terminal access' afterwards so the terminal follows the new entitlement."""

    audit_module = "meals"
    staff_number, employee_name, department = employee_columns("employee")
    list_display = ("staff_number", "employee_name", "department", "tickets_per_work_day", "effective_from", "effective_to", "override_badge", "reason", "set_by")
    list_display_links = ("staff_number", "employee_name")
    list_filter = ("is_exceptional_override", "tickets_per_work_day", "employee__department", "effective_from")
    search_fields = (*SEARCH, "reason")
    list_select_related = ("employee", "employee__department", "set_by")
    date_hierarchy = "effective_from"
    raw_id_fields = ("employee",)
    ordering = ("-effective_from", "-id")
    readonly_fields = ("set_by", "created_at")
    list_per_page = 50
    save_on_top = True
    actions = ["recheck_terminal_access"]

    @admin.display(description="Exceptional", ordering="is_exceptional_override")
    def override_badge(self, obj):
        return badge("Override", "amber") if obj.is_exceptional_override else "-"

    def can_delete_object(self, request, obj):
        return True

    def save_model(self, request, obj, form, change):
        if not change:
            obj.set_by = request.user
        super().save_model(request, obj, form, change)


class _EmployeeColumnsMixin:
    """Staff number and name as their own columns, so a row says who it is about instead of 'object (771)'."""

    staff_number, employee_name, department = employee_columns("employee")


@admin.register(MealCollection)
class MealCollectionAdmin(RecheckTerminalMixin, ReadOnlyAdminMixin, _EmployeeColumnsMixin, admin.ModelAdmin):
    """Read-only. A wrong ticket is never edited or deleted: void it, which takes it off the vendor's bill and out of the
    person's daily count and shrinks or cancels its excess decision (MealService.void_collection)."""

    list_display = ("staff_number", "employee_name", "work_date", "ticket", "status_badge", "amount_naira", "collected_at", "device", "voided_badge")
    list_display_links = ("staff_number", "employee_name")
    list_filter = ("status", VoidedFilter, "work_date", "event__device", "employee__department")
    search_fields = (*SEARCH, "void_reason")
    list_select_related = ("employee", "employee__department", "event", "event__device", "voided_by")
    date_hierarchy = "work_date"
    raw_id_fields = ("event", "employee", "excess_exception", "voided_by")
    ordering = ("-event__timestamp",)
    list_per_page = 50
    show_full_result_count = False
    save_on_top = True
    actions = ["void_collections", "recheck_terminal_access"]

    has_review_permission = perm_checker("meals.review_meal_excess")

    @admin.display(description="Ticket", ordering="sequence_number")
    def ticket(self, obj):
        return f"{obj.sequence_number} of {obj.entitlement_snapshot}"

    @admin.display(description="Status", ordering="status")
    def status_badge(self, obj):
        return badge(obj.get_status_display(), COLLECTION_TONES.get(obj.status, "grey"))

    @admin.display(description="Rate", ordering="rate_snapshot")
    def amount_naira(self, obj):
        return naira(obj.rate_snapshot)

    @admin.display(description="Collected at", ordering="event__timestamp")
    def collected_at(self, obj):
        return obj.event.timestamp

    @admin.display(description="Terminal", ordering="event__device__name")
    def device(self, obj):
        return obj.event.device.name

    @admin.display(description="Voided", ordering="voided_at")
    def voided_badge(self, obj):
        return badge("Voided", "red") if obj.voided_at else "-"

    void_collections = service_action(
        name="void_collections", label="Void selected collections", perm="review", module="meals", verb="Tickets voided", severity=AuditSeverity.WARNING,
        intro="A voided ticket stays on record but stops counting: it comes off the vendor's amount owed and the person's daily count, and its own excess decision is shrunk or cancelled. A ticket whose excess was already deducted in payroll is refused.",
        reason="required", reason_label="Reason for voiding", reason_help="Recorded on every ticket and in the audit trail (for example: test scan, wrong person scanned).",
        call=lambda collection, actor, text: (MealService.void_collection(collection, actor, text), "voided.")[1],
    )


@admin.register(MealEvent)
class MealEventAdmin(RecheckTerminalMixin, ReadOnlyAdminMixin, _EmployeeColumnsMixin, admin.ModelAdmin):
    """The raw scan as the terminal reported it: evidence, never edited."""

    list_display = ("staff_number", "employee_name", "timestamp", "device", "verification_type", "external_event_id")
    list_display_links = ("staff_number", "employee_name")
    list_filter = ("device", "verification_type", "employee__department")
    search_fields = (*SEARCH, "external_event_id")
    list_select_related = ("employee", "employee__department", "device")
    date_hierarchy = "timestamp"
    raw_id_fields = ("employee",)
    list_per_page = 50
    show_full_result_count = False
    save_on_top = True
    actions = ["recheck_terminal_access"]


@admin.register(MealExcessException)
class MealExcessExceptionAdmin(RecheckTerminalMixin, ReadOnlyAdminMixin, _EmployeeColumnsMixin, admin.ModelAdmin):
    """Read-only; decide with the actions (the same MealService calls the Meals screen makes)."""

    list_display = ("staff_number", "employee_name", "work_date", "collected_quantity", "excess_quantity", "deduction_naira", "status_badge", "reviewer", "reviewed_at", "payroll_period")
    list_display_links = ("staff_number", "employee_name")
    list_filter = ("status", "work_date", "payroll_period", "employee__department")
    search_fields = (*SEARCH, "comment")
    list_select_related = ("employee", "employee__department", "reviewer", "payroll_period")
    date_hierarchy = "work_date"
    ordering = ("-work_date", "-id")
    raw_id_fields = ("employee", "reviewer", "payroll_period", "payroll", "payroll_line_item")
    list_per_page = 50
    save_on_top = True
    actions = ["accept_excess", "waive_excess", "decline_excess", "recheck_terminal_access"]

    has_review_permission = perm_checker("meals.review_meal_excess")

    @admin.display(description="Deduction", ordering="proposed_deduction")
    def deduction_naira(self, obj):
        return naira(obj.proposed_deduction)

    @admin.display(description="Status", ordering="status")
    def status_badge(self, obj):
        return badge(obj.get_status_display(), EXCESS_TONES.get(obj.status, "grey"))

    accept_excess = service_action(
        name="accept_excess", label="Accept selected excess (employee pays)", perm="review", module="meals", verb="Excess accepted", severity=AuditSeverity.SUCCESS,
        intro="The employee is charged the deduction shown, in the payroll of the ticket's month: at once if that payroll record exists and is not approved, otherwise when payroll is generated. Approved payroll is never altered (those are refused).",
        reason="optional", reason_label="Comment",
        call=lambda exception, actor, text: (MealService.approve(exception, None, actor, text), "accepted.")[1],
    )
    waive_excess = service_action(
        name="waive_excess", label="Waive selected excess (company pays)", perm="review", module="meals", verb="Excess waived", severity=AuditSeverity.WARNING,
        intro="No deduction is made. The tickets stand, so the company still pays the vendor for them.",
        reason="required", reason_label="Reason for waiving",
        call=lambda exception, actor, text: (MealService.cancel(exception, actor, text), "waived.")[1],
    )
    decline_excess = service_action(
        name="decline_excess", label="Decline selected excess (void the tickets)", perm="review", module="meals", verb="Excess declined", severity=AuditSeverity.WARNING,
        intro="The tickets beyond the entitlement are voided (the vendor is not billed) and nothing is deducted from the employee.",
        reason="required", reason_label="Reason for declining",
        call=lambda exception, actor, text: (MealService.decline(exception, actor, text), "declined.")[1],
    )


@admin.register(MealExtraAuthorization)
class MealExtraAuthorizationAdmin(RecheckTerminalMixin, ReadOnlyAdminMixin, _EmployeeColumnsMixin, admin.ModelAdmin):
    list_display = ("staff_number", "employee_name", "work_date", "quantity", "used", "pays", "state", "reason", "authorised_by")
    list_display_links = ("staff_number", "employee_name")
    list_filter = ("pays", "work_date", "employee__department")
    search_fields = (*SEARCH, "reason")
    list_select_related = ("employee", "employee__department", "authorised_by")
    date_hierarchy = "work_date"
    raw_id_fields = ("employee", "authorised_by")
    list_per_page = 50
    save_on_top = True
    actions = ["withdraw_authorisations", "recheck_terminal_access"]

    has_review_permission = perm_checker("meals.review_meal_excess")

    @admin.display(description="State")
    def state(self, obj):
        label = authorizations.status_of(obj)
        return badge(label, {"waiting": "amber", "collected": "green", "not used": "grey", "cancelled": "red"}.get(label, "grey"))

    withdraw_authorisations = service_action(
        name="withdraw_authorisations", label="Withdraw selected extra-ticket authorisations", perm="review", module="meals", verb="Authorisations withdrawn", severity=AuditSeverity.WARNING,
        intro="Withdraws the unused part of each authorisation (tickets already collected stand). Finished ones are skipped.",
        call=lambda authorization, actor, text: (authorizations.cancel(authorization, actor=actor), "withdrawn.")[1],
    )


@admin.register(MealTerminalUserState)
class MealTerminalUserStateAdmin(RecheckTerminalMixin, ReadOnlyAdminMixin, _EmployeeColumnsMixin, admin.ModelAdmin):
    """Whether each person is switched on at each meal terminal, as the terminal last confirmed."""

    list_display = ("staff_number", "employee_name", "device_serial", "on_badge", "updated_at")
    list_display_links = ("staff_number", "employee_name")
    list_filter = ("enabled", "device_serial", "employee__department")
    search_fields = (*SEARCH, "device_serial")
    list_select_related = ("employee", "employee__department")
    ordering = ("-updated_at",)
    raw_id_fields = ("employee",)
    list_per_page = 50
    save_on_top = True
    actions = ["recheck_terminal_access"]

    @admin.display(description="At terminal", ordering="enabled")
    def on_badge(self, obj):
        return badge("On", "green") if obj.enabled else badge("Off", "red")


@admin.register(MealAbsencePenalty)
class MealAbsencePenaltyAdmin(ReadOnlyAdminMixin, _EmployeeColumnsMixin, admin.ModelAdmin):
    """Why someone has fewer tickets: generated from approved absences, so only viewed here."""

    list_display = ("staff_number", "employee_name", "penalty_type", "tickets_to_reduce_per_work_day", "work_days_to_apply", "work_days_applied", "status", "approved_at")
    list_display_links = ("staff_number", "employee_name")
    list_filter = ("status", "penalty_type", "employee__department")
    search_fields = SEARCH
    list_select_related = ("employee", "employee__department")
    date_hierarchy = "created_at"
    ordering = ("-created_at", "-id")
    raw_id_fields = ("employee", "approved_by")
    list_per_page = 50
    save_on_top = True


@admin.register(MealVendorPayment)
class MealVendorPaymentAdmin(AdminAuditMixin, NoBulkDeleteMixin, admin.ModelAdmin):
    """What was paid to the meal vendor. A wrong entry can be corrected or removed one at a time; each change is
    logged with before and after values."""

    audit_module = "meals"
    list_display = ("payment_date", "payroll_period", "amount_naira", "reference", "recorded_by")
    list_display_links = ("payment_date", "payroll_period")
    list_filter = ("payroll_period", "payment_date")
    search_fields = ("reference", "notes")
    list_select_related = ("payroll_period", "recorded_by")
    date_hierarchy = "payment_date"
    ordering = ("-payment_date", "-id")
    readonly_fields = ("recorded_by", "created_at")
    list_per_page = 50
    save_on_top = True

    @admin.display(description="Amount", ordering="amount")
    def amount_naira(self, obj):
        return naira(obj.amount)

    def audit_employee(self, obj):
        return None

    def can_delete_object(self, request, obj):
        return True

    def save_model(self, request, obj, form, change):
        if not change:
            obj.recorded_by = request.user
        super().save_model(request, obj, form, change)

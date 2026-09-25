import re
from datetime import timedelta

from django import forms
from django.contrib import admin, messages
from django.db.models import Count, IntegerField, OuterRef, Q, Subquery, Value
from django.db.models.functions import Coalesce
from django.utils import timezone
from django.utils.html import format_html

from employees.admin_common import (
    EMPLOYEE_SEARCH_FIELDS,
    AuditedAdminMixin,
    ConfirmField,
    EmployeeColumnsMixin,
    HRAdminMixin,
    badge,
    confirm_step,
    report,
    status_badge,
)
from employees.models import Employee

from .models import LeaveBalance, LeavePolicy, LeaveRequest, LeaveStatus, LeaveType
from .services.approval import LeaveApprovalError, LeaveApprovalService
from .services.balance import LeaveBalanceService
from .services.request import LeaveRequestService

LEAVE_STATUS_TONES = {
    LeaveStatus.PENDING: "amber",
    LeaveStatus.APPROVED: "green",
    LeaveStatus.PARTIALLY_APPROVED: "blue",
    LeaveStatus.REJECTED: "red",
    LeaveStatus.CANCELLED: "grey",
}

EMPLOYEE_ROW_SELECT = ("employee", "employee__department")


@admin.register(LeaveType)
class LeaveTypeAdmin(HRAdminMixin, AuditedAdminMixin, admin.ModelAdmin):
    audit_module = "leave"
    audit_prefix = "leave_type"

    list_display = (
        "name",
        "code",
        "swatch",
        "default_days",
        "requires_approval",
        "is_paid",
        "is_active",
        "request_count",
    )
    list_filter = (
        "requires_approval",
        "is_paid",
        "is_active",
    )
    search_fields = (
        "name",
        "code",
        "description",
    )
    list_editable = (
        "default_days",
        "requires_approval",
        "is_paid",
        "is_active",
    )
    ordering = ("name",)

    def audit_employee(self, obj):
        return None

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(requests_total=Count("requests"))

    @admin.display(description="Colour")
    def swatch(self, obj):
        if not re.fullmatch(r"#[0-9a-fA-F]{3,8}", obj.color or ""):
            return "-"
        return format_html('<span style="display:inline-block;width:14px;height:14px;border-radius:3px;background:{};vertical-align:middle"></span> {}', obj.color, obj.color)

    @admin.display(description="Requests", ordering="requests_total")
    def request_count(self, obj):
        return obj.requests_total


@admin.register(LeavePolicy)
class LeavePolicyAdmin(HRAdminMixin, AuditedAdminMixin, admin.ModelAdmin):
    audit_module = "leave"
    audit_prefix = "leave_policy"

    list_display = (
        "leave_type",
        "employment_type",
        "allocated_days",
        "requires_approval",
        "is_paid",
        "is_active",
        "active_staff",
    )
    list_select_related = ("leave_type",)
    list_filter = (
        "employment_type",
        "leave_type",
        "is_active",
    )
    search_fields = (
        "leave_type__name",
        "leave_type__code",
    )
    list_editable = (
        "allocated_days",
        "requires_approval",
        "is_paid",
        "is_active",
    )
    ordering = ("leave_type__name", "employment_type")

    def audit_employee(self, obj):
        return None

    def get_queryset(self, request):
        covered = (
            Employee.objects.filter(employment_type=OuterRef("employment_type"), status="active")
            .order_by()
            .values("employment_type")
            .annotate(c=Count("pk"))
            .values("c")
        )
        return super().get_queryset(request).annotate(
            covered=Coalesce(Subquery(covered), Value(0), output_field=IntegerField()),
        )

    @admin.display(description="Active staff on this policy", ordering="covered")
    def active_staff(self, obj):
        return obj.covered


@admin.register(LeaveBalance)
class LeaveBalanceAdmin(HRAdminMixin, EmployeeColumnsMixin, admin.ModelAdmin):
    """Read-only. Nothing in the app reads this table: the live balance is computed from the leave policy and the
    approved requests (leave.services.balance.LeaveBalanceService), so editing a stored row would change nothing
    and only mislead. See a person's real balance on their leave request page."""

    list_display = (
        "staff_number",
        "employee_name",
        "department",
        "leave_type",
        "year",
        "allocated_days",
        "used_days",
        "remaining",
    )
    list_display_links = ("staff_number", "employee_name")
    list_select_related = (*EMPLOYEE_ROW_SELECT, "leave_type")
    list_filter = (
        "leave_type",
        "year",
        "employee__department",
    )
    search_fields = EMPLOYEE_SEARCH_FIELDS
    ordering = ("-year", "employee__employee_id")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def changelist_view(self, request, extra_context=None):
        if request.method == "GET":
            messages.info(
                request,
                "These stored balances are not used by leave approval; live balances are computed from the leave policy "
                "and approved requests. Open a leave request to see the live balance.",
            )
        return super().changelist_view(request, extra_context)

    @admin.display(description="Remaining (stored)")
    def remaining(self, obj):
        return obj.remaining_days


class TimingFilter(admin.SimpleListFilter):
    title = "when"
    parameter_name = "when"

    def lookups(self, request, model_admin):
        return [
            ("now", "On leave today"),
            ("soon", "Starts within 14 days"),
            ("waiting", "Pending and already started"),
        ]

    def queryset(self, request, queryset):
        today = timezone.localdate()
        if self.value() == "now":
            return queryset.filter(
                status__in=[LeaveStatus.APPROVED, LeaveStatus.PARTIALLY_APPROVED],
                approved_start_date__lte=today,
                approved_end_date__gte=today,
            )
        if self.value() == "soon":
            return queryset.filter(
                status__in=[LeaveStatus.PENDING, LeaveStatus.APPROVED, LeaveStatus.PARTIALLY_APPROVED],
                start_date__gt=today,
                start_date__lte=today + timedelta(days=14),
            )
        if self.value() == "waiting":
            return queryset.filter(status=LeaveStatus.PENDING, start_date__lte=today)
        return queryset


class LeaveRequestAdminForm(forms.ModelForm):
    """Keeps the request's day count in step with its dates, using the same service function that created it."""

    class Meta:
        model = LeaveRequest
        fields = "__all__"

    def clean(self):
        cleaned = super().clean()
        start, end = cleaned.get("start_date"), cleaned.get("end_date")
        duration = cleaned.get("duration_type")
        return_date = cleaned.get("return_date")
        if start and end and duration:
            try:
                self.instance.total_days = LeaveRequestService.calculate_leave_days(start, end, duration)
            except ValueError as error:
                self.add_error("end_date", str(error))
        if return_date and end and return_date <= end:
            self.add_error("return_date", "Return date must be after the leave end date.")
        return cleaned


@admin.register(LeaveRequest)
class LeaveRequestAdmin(HRAdminMixin, EmployeeColumnsMixin, AuditedAdminMixin, admin.ModelAdmin):
    audit_module = "leave"
    audit_prefix = "leave_request"
    no_bulk_delete = True  # leave requests are evidence (they also drive the balances)

    form = LeaveRequestAdminForm
    actions = ["approve_requests", "reject_requests", "cancel_requests"]

    list_display = (
        "request_number",
        "staff_number",
        "employee_name",
        "department",
        "leave_type",
        "period",
        "days",
        "state",
        "decided_by",
        "approved_at",
        "created_at",
    )
    list_display_links = ("request_number", "staff_number", "employee_name")
    list_select_related = (*EMPLOYEE_ROW_SELECT, "leave_type", "requested_by", "approved_by")
    list_filter = (
        "status",
        "leave_type",
        "employee__department",
        TimingFilter,
        ("start_date", admin.DateFieldListFilter),
        "duration_type",
    )
    search_fields = (
        "request_number",
        *EMPLOYEE_SEARCH_FIELDS,
        "leave_type__name",
        "reason",
    )
    date_hierarchy = "start_date"
    autocomplete_fields = ("employee",)
    ordering = ("-created_at", "-id")
    readonly_fields = (
        "request_number",
        "total_days",
        "live_balance",
        "requested_by",
        "approved_by",
        "approved_at",
        "created_at",
        "updated_at",
    )
    fieldsets = (
        ("Request", {"fields": ("request_number", "employee", "leave_type", "duration_type", ("start_date", "end_date"), "return_date", "total_days", "reason", "live_balance")}),
        ("Decision", {
            "fields": ("status", "approved_by", "approved_at", ("approved_start_date", "approved_end_date"), "approved_days", "rejection_reason"),
            "description": "Changing the status here skips the approval checks (balance, policy). For pending requests use the "
                           "Approve / Reject / Cancel actions on the list. If you change the dates of an approved request, fix the approved dates and days too.",
        }),
        ("Record", {"fields": ("requested_by", "created_at", "updated_at"), "classes": ("collapse",)}),
    )

    # Requests are created through LeaveRequestService (balance, overlap and numbering rules), never from here.
    def has_add_permission(self, request):
        return False

    def has_approve_permission(self, request):
        return request.user.has_perm("leave.approve_leave")

    @admin.display(description="Live balance")
    def live_balance(self, obj):
        if not obj.pk:
            return "-"
        balance = LeaveBalanceService.get_balance(obj.employee, obj.leave_type, year=obj.start_date.year, as_of=obj.start_date)
        policy = balance["matched_policy"]
        return (
            f"{obj.leave_type.name} {obj.start_date.year}: allocated {balance['allocated_days']}, used {balance['used_days']}, "
            f"remaining {balance['remaining_days']} ({policy if policy else 'no active policy matches this employee'})"
        )

    @admin.display(description="Dates", ordering="start_date")
    def period(self, obj):
        if obj.start_date == obj.end_date:
            return f"{obj.start_date:%d %b %Y}"
        return f"{obj.start_date:%d %b} - {obj.end_date:%d %b %Y}"

    @admin.display(description="Days", ordering="total_days")
    def days(self, obj):
        if obj.approved_days is not None and obj.approved_days != obj.total_days:
            return f"{obj.approved_days} of {obj.total_days}"
        return obj.total_days

    @admin.display(description="Status", ordering="status")
    def state(self, obj):
        return status_badge(obj, LEAVE_STATUS_TONES)

    @admin.display(description="Decided by", ordering="approved_by__username")
    def decided_by(self, obj):
        return obj.approved_by.get_username() if obj.approved_by else "-"

    # ---- actions: the same service functions and the same audit / notification as the API views

    def _decide(self, request, queryset, *, action, title, question, notes, run, verb, fields=(), danger=False):
        from .views.approvals import LeaveDecisionAPIView

        pending = [leave for leave in queryset if leave.status == LeaveStatus.PENDING]
        others = [leave for leave in queryset if leave.status != LeaveStatus.PENDING]
        values, response = confirm_step(
            self, request, queryset, action=action, title=title, question=question.format(count=len(pending)),
            notes=notes, fields=fields, confirm_label=f"Yes, {verb}", danger=danger,
        )
        if response is not None:
            return response
        done, errors = [], []
        for leave in pending:
            try:
                decided = run(leave, values)
            except (LeaveApprovalError, LeaveRequest.DoesNotExist) as error:
                errors.append(f"{leave}: {error}")
                continue
            LeaveDecisionAPIView()._record_outcome(decided, request.user)
            done.append(str(decided))
        report(self, request, done, skipped=[f"{leave} ({leave.get_status_display().lower()})" for leave in others], errors=errors, verb=verb)

    @admin.action(description="Approve selected pending requests", permissions=["approve"])
    def approve_requests(self, request, queryset):
        return self._decide(
            request, queryset, action="approve_requests", title="Approve leave requests", verb="approved",
            question="Approve {count} pending leave request(s) in full?",
            notes=["Each request is checked against the employee's live balance and policy; any that fail are reported and left pending.",
                   "Only pending requests are decided. Use the API / Leave page for a partial approval."],
            run=lambda leave, values: LeaveApprovalService.approve(leave.pk, request.user),
        )

    @admin.action(description="Reject selected pending requests", permissions=["approve"])
    def reject_requests(self, request, queryset):
        return self._decide(
            request, queryset, action="reject_requests", title="Reject leave requests", verb="rejected", danger=True,
            question="Reject {count} pending leave request(s)?",
            notes=["The reason is stored on each request and shown to the employee."],
            fields=[ConfirmField("reason", "Rejection reason", kind="textarea", required=True)],
            run=lambda leave, values: LeaveApprovalService.reject(leave.pk, request.user, values["reason"]),
        )

    @admin.action(description="Cancel selected pending requests", permissions=["approve"])
    def cancel_requests(self, request, queryset):
        return self._decide(
            request, queryset, action="cancel_requests", title="Cancel leave requests", verb="cancelled", danger=True,
            question="Withdraw {count} pending leave request(s)?",
            notes=["A cancellation is not a decision on the merits; no reason is needed."],
            run=lambda leave, values: LeaveApprovalService.cancel(leave.pk, request.user),
        )

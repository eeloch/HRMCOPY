from django.contrib import admin
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Count, Sum

from employees.admin_common import (
    EMPLOYEE_SEARCH_FIELDS,
    AuditedAdminMixin,
    ConfirmField,
    EmployeeColumnsMixin,
    HRAdminMixin,
    adopt,
    confirm_step,
    report,
    status_badge,
)
from payroll.models import PayrollPeriod

from .models import EmployeePPEIssue, PPEDeductionStatus, PPEType
from .services import PPEDeductionService

DEDUCTION_TONES = {
    PPEDeductionStatus.NOT_DEDUCTIBLE: "grey",
    PPEDeductionStatus.PENDING: "amber",
    PPEDeductionStatus.APPROVED: "blue",
    PPEDeductionStatus.HELD: "red",
    PPEDeductionStatus.DEFERRED: "amber",
    PPEDeductionStatus.DEDUCTED: "green",
}


@admin.register(PPEType)
class PPETypeAdmin(HRAdminMixin, AuditedAdminMixin, admin.ModelAdmin):
    audit_module = "ppe"
    audit_prefix = "ppe_type"

    list_display = ("name", "code", "potentially_employee_deductible", "default_cost", "active", "issues", "units_issued")
    list_filter = ("potentially_employee_deductible", "active")
    search_fields = ("name", "code", "description")
    ordering = ("name",)

    def audit_employee(self, obj):
        return None

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(issue_total=Count("issues"), unit_total=Sum("issues__quantity"))

    @admin.display(description="Issues recorded", ordering="issue_total")
    def issues(self, obj):
        return obj.issue_total

    @admin.display(description="Units issued", ordering="unit_total")
    def units_issued(self, obj):
        return obj.unit_total or 0


@admin.register(EmployeePPEIssue)
class EmployeePPEIssueAdmin(HRAdminMixin, EmployeeColumnsMixin, AuditedAdminMixin, admin.ModelAdmin):
    audit_module = "ppe"
    audit_prefix = "ppe_issue"
    no_bulk_delete = True  # issues are records of what was handed out and, once deducted, payroll history

    actions = ["approve_deductions", "hold_deductions", "defer_deductions"]
    list_display = (
        "staff_number",
        "employee_name",
        "department",
        "ppe_type",
        "issue_date",
        "quantity",
        "unit_cost",
        "total_cost",
        "employee_deductible",
        "state",
        "target_payroll_period",
        "reviewed_by",
    )
    list_display_links = ("staff_number", "employee_name")
    list_select_related = ("employee", "employee__department", "ppe_type", "target_payroll_period", "reviewed_by")
    list_filter = ("deduction_status", "employee_deductible", "ppe_type", "employee__department", ("issue_date", admin.DateFieldListFilter))
    search_fields = (*EMPLOYEE_SEARCH_FIELDS, "ppe_type__name", "ppe_type__code", "notes", "review_comment")
    date_hierarchy = "issue_date"
    autocomplete_fields = ("employee",)
    ordering = ("-issue_date", "-id")
    readonly_fields = ("total_cost", "issued_by", "reviewed_by", "reviewed_at", "payroll_line_item", "deducted_payroll")

    def get_readonly_fields(self, request, obj=None):
        if obj and obj.deduction_status == PPEDeductionStatus.DEDUCTED:
            return tuple(field.name for field in obj._meta.fields)
        if obj is None:
            # A new issue goes through PPEDeductionService.issue, which derives these from the PPE type.
            return (*super().get_readonly_fields(request, obj), "employee_deductible", "deduction_status", "target_payroll_period")
        return super().get_readonly_fields(request, obj)

    def has_delete_permission(self, request, obj=None):
        if obj and obj.deduction_status == PPEDeductionStatus.DEDUCTED:
            return False
        return super().has_delete_permission(request, obj)

    def has_review_permission(self, request):
        return request.user.has_perm("ppe.review_ppe_deduction") or request.user.has_perm("payroll.manage_payroll")

    def save_model(self, request, obj, form, change):
        if change and obj.pk and EmployeePPEIssue.objects.filter(
            pk=obj.pk,
            deduction_status=PPEDeductionStatus.DEDUCTED,
        ).exists():
            raise PermissionDenied("Deducted PPE issues are read-only payroll history.")
        if not change:
            issue = PPEDeductionService.issue(
                employee=obj.employee, ppe_type=obj.ppe_type, quantity=obj.quantity, issue_date=obj.issue_date,
                unit_cost=obj.unit_cost, notes=obj.notes, actor=request.user,
            )
            adopt(obj, issue)  # the service already wrote the "ppe.issued" audit event
            return
        super().save_model(request, obj, form, change)

    def delete_queryset(self, request, queryset):
        if queryset.filter(deduction_status=PPEDeductionStatus.DEDUCTED).exists():
            raise PermissionDenied("Deducted PPE issues cannot be deleted.")
        super().delete_queryset(request, queryset)

    @admin.display(description="Deduction", ordering="deduction_status")
    def state(self, obj):
        return status_badge(obj, DEDUCTION_TONES, field="deduction_status")

    # ---- actions: PPEDeductionService (audit events are written by the service)

    def _review(self, request, queryset, *, action, title, question, notes, run, verb, fields=(), danger=False):
        open_issues = [
            issue for issue in queryset
            if issue.employee_deductible and issue.deduction_status != PPEDeductionStatus.DEDUCTED
        ]
        closed = [issue for issue in queryset if issue not in open_issues]
        values, response = confirm_step(
            self, request, queryset, action=action, title=title, question=question.format(count=len(open_issues)),
            notes=notes, fields=fields, confirm_label=f"Yes, {verb}", danger=danger,
        )
        if response is not None:
            return response
        period = PayrollPeriod.objects.filter(pk=values["period"]).first() if values.get("period") else None
        done, errors = [], []
        for issue in open_issues:
            try:
                with transaction.atomic():
                    run(issue, period, values)
            except ValueError as error:
                errors.append(f"{issue}: {error}")
                continue
            done.append(str(issue))
        skipped = [
            f"{issue} ({'already deducted' if issue.deduction_status == PPEDeductionStatus.DEDUCTED else 'not employee-deductible'})"
            for issue in closed
        ]
        report(self, request, done, skipped=skipped, errors=errors, verb=verb)

    @staticmethod
    def _period_choices():
        return [(period.pk, period.display_name) for period in PayrollPeriod.objects.order_by("-year", "-month")[:24]]

    @admin.action(description="Approve deduction of selected issues", permissions=["review"])
    def approve_deductions(self, request, queryset):
        return self._review(
            request, queryset, action="approve_deductions", title="Approve PPE deductions", verb="approved",
            question="Approve {count} PPE deduction(s)? The cost comes out of the employees' salaries.",
            notes=["Deducted now if the employee's payroll for that month exists, otherwise when payroll is generated.",
                   "A payroll period or record that is already approved or paid is never altered - those are reported and left as they are."],
            fields=[
                ConfirmField("period", "Payroll month (optional; default is the deferred month, else the month issued)", kind="select", choices=self._period_choices()),
                ConfirmField("comment", "Comment (optional)", kind="textarea"),
            ],
            run=lambda issue, period, values: PPEDeductionService.approve(issue, payroll_period=period, actor=request.user, comment=values.get("comment", "")),
        )

    @admin.action(description="Hold deduction of selected issues", permissions=["review"])
    def hold_deductions(self, request, queryset):
        return self._review(
            request, queryset, action="hold_deductions", title="Hold PPE deductions", verb="held", danger=True,
            question="Hold {count} PPE deduction(s)? Nothing is deducted until HR approves them.",
            notes=[],
            fields=[ConfirmField("comment", "Reason for holding", kind="textarea", required=True)],
            run=lambda issue, period, values: PPEDeductionService.hold(issue, actor=request.user, comment=values["comment"]),
        )

    @admin.action(description="Defer deduction of selected issues to a later month", permissions=["review"])
    def defer_deductions(self, request, queryset):
        return self._review(
            request, queryset, action="defer_deductions", title="Defer PPE deductions", verb="deferred",
            question="Defer {count} PPE deduction(s) to the month you choose?",
            notes=["Deferred issues are not deducted until HR approves them."],
            fields=[
                ConfirmField("period", "Defer to payroll month", kind="select", required=True, choices=self._period_choices()),
                ConfirmField("comment", "Comment (optional)", kind="textarea"),
            ],
            run=lambda issue, period, values: PPEDeductionService.defer(issue, payroll_period=period, actor=request.user, comment=values.get("comment", "")),
        )

from django.contrib import admin
from django.core.exceptions import PermissionDenied

from .models import EmployeePPEIssue, PPEDeductionStatus, PPEType


@admin.register(PPEType)
class PPETypeAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "potentially_employee_deductible", "default_cost", "active")
    list_filter = ("potentially_employee_deductible", "active")
    search_fields = ("name", "code", "description")


@admin.register(EmployeePPEIssue)
class EmployeePPEIssueAdmin(admin.ModelAdmin):
    list_display = ("employee", "ppe_type", "issue_date", "quantity", "total_cost", "employee_deductible", "deduction_status", "target_payroll_period", "deducted_payroll")
    list_filter = ("deduction_status", "employee_deductible", "ppe_type")
    search_fields = ("employee__employee_id", "employee__first_name", "employee__last_name", "ppe_type__name")
    readonly_fields = ("total_cost", "issued_by", "reviewed_by", "reviewed_at", "payroll_line_item", "deducted_payroll")

    def get_readonly_fields(self, request, obj=None):
        if obj and obj.deduction_status == PPEDeductionStatus.DEDUCTED:
            return tuple(field.name for field in obj._meta.fields)
        return super().get_readonly_fields(request, obj)

    def has_delete_permission(self, request, obj=None):
        if obj and obj.deduction_status == PPEDeductionStatus.DEDUCTED:
            return False
        return super().has_delete_permission(request, obj)

    def save_model(self, request, obj, form, change):
        if change and obj.pk and EmployeePPEIssue.objects.filter(
            pk=obj.pk,
            deduction_status=PPEDeductionStatus.DEDUCTED,
        ).exists():
            raise PermissionDenied("Deducted PPE issues are read-only payroll history.")
        super().save_model(request, obj, form, change)

    def delete_queryset(self, request, queryset):
        if queryset.filter(deduction_status=PPEDeductionStatus.DEDUCTED).exists():
            raise PermissionDenied("Deducted PPE issues cannot be deleted.")
        super().delete_queryset(request, queryset)

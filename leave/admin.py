from django.contrib import admin

from .models import LeaveBalance, LeavePolicy, LeaveRequest, LeaveType


@admin.register(LeaveType)
class LeaveTypeAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "code",
        "default_days",
        "requires_approval",
        "is_paid",
        "is_active",
    )
    list_filter = (
        "requires_approval",
        "is_paid",
        "is_active",
    )
    search_fields = (
        "name",
        "code",
    )
    list_editable = (
        "default_days",
        "requires_approval",
        "is_paid",
        "is_active",
    )


@admin.register(LeavePolicy)
class LeavePolicyAdmin(admin.ModelAdmin):
    list_display = (
        "leave_type",
        "employment_type",
        "allocated_days",
        "requires_approval",
        "is_paid",
        "is_active",
    )
    list_filter = (
        "employment_type",
        "leave_type",
        "is_active",
    )
    search_fields = (
        "leave_type__name",
    )
    list_editable = (
        "allocated_days",
        "requires_approval",
        "is_paid",
        "is_active",
    )


@admin.register(LeaveBalance)
class LeaveBalanceAdmin(admin.ModelAdmin):
    list_display = (
        "employee",
        "leave_type",
        "year",
        "allocated_days",
        "used_days",
        "remaining_days",
    )
    list_filter = (
        "leave_type",
        "year",
    )
    search_fields = (
        "employee__employee_id",
        "employee__first_name",
        "employee__last_name",
    )


@admin.register(LeaveRequest)
class LeaveRequestAdmin(admin.ModelAdmin):
    list_display = (
        "request_number",
        "employee",
        "leave_type",
        "start_date",
        "end_date",
        "total_days",
        "status",
        "requested_by",
        "approved_by",
        "approved_at",
        "approved_start_date",
        "approved_end_date",
        "approved_days",
    )
    list_filter = (
        "status",
        "duration_type",
        "leave_type",
        "start_date",
    )
    search_fields = (
        "request_number",
        "employee__employee_id",
        "employee__first_name",
        "employee__last_name",
        "reason",
    )

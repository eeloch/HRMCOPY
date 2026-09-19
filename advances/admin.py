from django.contrib import admin

from .models import AdvanceRepayment, SalaryAdvance


@admin.register(SalaryAdvance)
class SalaryAdvanceAdmin(admin.ModelAdmin):
    list_display = ("employee", "amount", "repayment_months", "status", "created_at")
    list_filter = ("status",)
    search_fields = ("employee__employee_id", "employee__first_name", "employee__last_name")
    readonly_fields = ("recorded_by", "decided_by", "decided_at", "paid_by", "paid_on")


@admin.register(AdvanceRepayment)
class AdvanceRepaymentAdmin(admin.ModelAdmin):
    list_display = ("advance", "payroll_period", "amount")

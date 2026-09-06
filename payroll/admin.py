from django.contrib import admin

from .models import EmployeePayroll, PayrollLineItem, PayrollPeriod, PayrollSetting


@admin.register(PayrollPeriod)
class PayrollPeriodAdmin(admin.ModelAdmin):
    list_display = ("display_name", "year", "month", "status", "created_at", "approved_at", "paid_at")
    list_filter = ("status", "year")
    search_fields = ("notes",)


@admin.register(EmployeePayroll)
class EmployeePayrollAdmin(admin.ModelAdmin):
    list_display = ("employee", "payroll_period", "basic_salary", "gross_earnings", "total_deductions", "net_pay", "status")
    list_filter = ("status", "payroll_period")
    search_fields = ("employee__employee_id", "employee__first_name", "employee__last_name")


@admin.register(PayrollLineItem)
class PayrollLineItemAdmin(admin.ModelAdmin):
    list_display = ("payroll", "item_type", "code", "description", "amount", "is_system_generated", "created_at")
    list_filter = ("item_type", "is_system_generated")
    search_fields = ("code", "description", "payroll__employee__employee_id")


@admin.register(PayrollSetting)
class PayrollSettingAdmin(admin.ModelAdmin):
    list_display = ("key", "updated_at")
    search_fields = ("key", "description")

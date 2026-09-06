from django.contrib import admin

from .models import MealDevice, MealTicketRate, MealEntitlementRule, EmployeeMealEntitlement, MealEvent, MealCollection, MealExcessException


@admin.register(MealDevice)
class MealDeviceAdmin(admin.ModelAdmin):
    list_display = ("name", "serial_number", "active", "created_at")
    list_filter = ("active",)
    search_fields = ("name", "serial_number")


@admin.register(MealTicketRate)
class MealTicketRateAdmin(admin.ModelAdmin):
    list_display = ("amount", "effective_from", "effective_to", "active")
    list_filter = ("active",)


@admin.register(EmployeeMealEntitlement)
class EmployeeMealEntitlementAdmin(admin.ModelAdmin):
    list_display = (
        "employee",
        "tickets_per_work_day",
        "effective_from",
        "effective_to",
        "is_exceptional_override",
        "set_by",
    )
    list_filter = ("is_exceptional_override",)
    search_fields = ("employee__employee_id", "employee__first_name", "employee__last_name")
    readonly_fields = ("created_at",)


admin.site.register(MealEntitlementRule)
admin.site.register(MealEvent)
admin.site.register(MealCollection)
admin.site.register(MealExcessException)

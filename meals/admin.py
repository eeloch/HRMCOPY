from django.contrib import admin

from .models import MealDevice, MealTicketRate, MealEntitlementRule, EmployeeMealEntitlement, MealEvent, MealCollection, MealExcessException


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


class _EmployeeColumnsMixin:
    """Staff number and name as their own columns, so a row says who it is about instead of 'object (771)'."""

    @admin.display(description="Staff No.", ordering="employee__employee_id")
    def staff_number(self, obj):
        return obj.employee.employee_id

    @admin.display(description="Employee", ordering="employee__first_name")
    def employee_name(self, obj):
        return obj.employee.full_name


@admin.register(MealCollection)
class MealCollectionAdmin(_EmployeeColumnsMixin, admin.ModelAdmin):
    list_display = ("id", "staff_number", "employee_name", "work_date", "ticket", "status", "collected_at", "device", "voided_at")
    list_filter = ("status", "work_date", "event__device")
    search_fields = ("employee__employee_id", "employee__first_name", "employee__last_name", "employee__middle_name")
    list_select_related = ("employee", "event", "event__device")
    date_hierarchy = "work_date"
    raw_id_fields = ("event", "employee", "excess_exception", "voided_by")
    ordering = ("-event__timestamp",)

    @admin.display(description="Ticket", ordering="sequence_number")
    def ticket(self, obj):
        return f"{obj.sequence_number} of {obj.entitlement_snapshot}"

    @admin.display(description="Collected at", ordering="event__timestamp")
    def collected_at(self, obj):
        return obj.event.timestamp

    @admin.display(description="Terminal", ordering="event__device__name")
    def device(self, obj):
        return obj.event.device.name


@admin.register(MealEvent)
class MealEventAdmin(_EmployeeColumnsMixin, admin.ModelAdmin):
    list_display = ("id", "staff_number", "employee_name", "timestamp", "device", "verification_type")
    list_filter = ("device", "verification_type")
    search_fields = ("employee__employee_id", "employee__first_name", "employee__last_name", "employee__middle_name")
    list_select_related = ("employee", "device")
    date_hierarchy = "timestamp"
    raw_id_fields = ("employee",)


@admin.register(MealExcessException)
class MealExcessExceptionAdmin(_EmployeeColumnsMixin, admin.ModelAdmin):
    list_display = ("id", "staff_number", "employee_name", "work_date", "excess_quantity", "proposed_deduction", "status", "reviewer")
    list_filter = ("status", "work_date")
    search_fields = ("employee__employee_id", "employee__first_name", "employee__last_name", "employee__middle_name")
    list_select_related = ("employee", "reviewer")
    raw_id_fields = ("employee",)


admin.site.register(MealEntitlementRule)

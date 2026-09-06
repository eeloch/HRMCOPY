from django.contrib import admin

from .models import (
    Shift,
    ShiftAssignment,
    BiometricDevice,
    AttendanceEvent,
    DailyAttendance,
    AttendanceException,
    EmployeeRosterDay,
    OvertimeRecord,
)


admin.site.register(Shift)
admin.site.register(ShiftAssignment)


@admin.register(EmployeeRosterDay)
class EmployeeRosterDayAdmin(admin.ModelAdmin):
    list_display = ("employee", "date", "status", "shift", "source", "updated_by", "updated_at")
    list_filter = ("status", "source", "shift")
    search_fields = ("employee__employee_id", "employee__first_name", "employee__last_name", "notes")
    date_hierarchy = "date"

@admin.register(BiometricDevice)
class BiometricDeviceAdmin(admin.ModelAdmin):
    list_display = ("name", "serial_number", "device_type", "location", "is_online", "last_sync_at")
    list_filter = ("device_type", "is_online")
    search_fields = ("name", "serial_number", "location")


@admin.register(AttendanceEvent)
class AttendanceEventAdmin(admin.ModelAdmin):
    list_display = ("employee", "device", "timestamp", "verification_type", "external_event_id", "imported_at")
    list_filter = ("verification_type", "device")
    search_fields = ("employee__employee_id", "employee__first_name", "employee__last_name", "external_event_id")
    readonly_fields = ("employee", "device", "timestamp", "verification_type", "external_event_id", "raw_payload", "imported_at")

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
admin.site.register(DailyAttendance)
admin.site.register(AttendanceException)


@admin.register(OvertimeRecord)
class OvertimeRecordAdmin(admin.ModelAdmin):
    list_display = ("employee", "work_date", "potential_overtime_minutes", "approved_overtime_minutes", "status", "payable_amount", "payment_due_date", "payment_status")
    list_filter = ("status", "payment_status", "work_date")
    search_fields = ("employee__employee_id", "employee__first_name", "employee__last_name")

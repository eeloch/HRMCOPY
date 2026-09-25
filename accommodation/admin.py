from django import forms
from django.contrib import admin
from django.db.models import Count, F, IntegerField, OuterRef, Subquery, Sum, Value
from django.db.models.functions import Coalesce

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

from .models import Building, Room, RoomAssignment
from .services import AccommodationService

EMPLOYEE_STATUS_TONES = {"active": "green", "inactive": "grey", "suspended": "amber", "terminated": "red"}


def _occupancy(occupied, capacity):
    if capacity is None:
        return f"{occupied} / ?"
    tone = "red" if occupied > capacity else "amber" if occupied == capacity else "green"
    return badge(f"{occupied} / {capacity}", tone)


class OccupantsInline(admin.TabularInline):
    """Who is in the room, read-only: people are moved with the actions on the Room assignments list (or on the
    Accommodation page), which enforce the gender / capacity / bed rules and keep the employee's hostel fields in step."""

    model = RoomAssignment
    extra = 0
    fields = ("staff_number", "employee_name", "employee_status", "bed_number", "assigned_on")
    readonly_fields = fields
    can_delete = False
    verbose_name_plural = "Occupants"

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("employee").order_by("bed_number", "employee__employee_id")

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.display(description="Staff No.")
    def staff_number(self, obj):
        return obj.employee.employee_id

    @admin.display(description="Employee")
    def employee_name(self, obj):
        return obj.employee.full_name

    @admin.display(description="Employee status")
    def employee_status(self, obj):
        return status_badge(obj.employee, EMPLOYEE_STATUS_TONES)


@admin.register(Building)
class BuildingAdmin(HRAdminMixin, AuditedAdminMixin, admin.ModelAdmin):
    audit_module = "accommodation"
    audit_prefix = "building"

    list_display = ("name", "kind", "address", "room_count", "occupied", "capacity", "sort_order")
    list_editable = ("sort_order",)
    list_filter = ("kind",)
    search_fields = ("name", "address", "notes")
    ordering = ("kind", "sort_order", "name")

    def audit_employee(self, obj):
        return None

    def get_queryset(self, request):
        capacity = (
            Room.objects.filter(building=OuterRef("pk"), active=True)
            .order_by()
            .values("building")
            .annotate(total=Sum("capacity"))
            .values("total")
        )
        return super().get_queryset(request).annotate(
            room_total=Count("rooms", distinct=True),
            occupied_total=Count("rooms__assignments", distinct=True),
            capacity_total=Coalesce(Subquery(capacity), Value(0), output_field=IntegerField()),
        )

    @admin.display(description="Rooms", ordering="room_total")
    def room_count(self, obj):
        return obj.room_total

    @admin.display(description="People", ordering="occupied_total")
    def occupied(self, obj):
        return obj.occupied_total

    @admin.display(description="Beds (active rooms)", ordering="capacity_total")
    def capacity(self, obj):
        return obj.capacity_total or "-"


class RoomAdminForm(forms.ModelForm):
    """The rules the Accommodation page applies when a room is edited (accommodation.views.RoomDetailAPIView)."""

    class Meta:
        model = Room
        fields = "__all__"

    def clean(self):
        cleaned = super().clean()
        if not self.instance.pk:
            return cleaned
        occupants = list(self.instance.assignments.select_related("employee"))
        capacity, gender = cleaned.get("capacity"), cleaned.get("gender")
        if capacity is not None and len(occupants) > capacity:
            self.add_error("capacity", f"{len(occupants)} people are already in this room, so it cannot take fewer than that.")
        wrong = [a.employee.full_name for a in occupants if gender and a.employee.gender and a.employee.gender != gender]
        if wrong:
            self.add_error("gender", f"{', '.join(wrong[:3])} {'is' if len(wrong) == 1 else 'are'} in this room and not {gender}. Move them first.")
        if cleaned.get("active") is False and occupants:
            self.add_error("active", "Move the people out of this room before closing it.")
        return cleaned


class OccupancyFilter(admin.SimpleListFilter):
    title = "occupancy"
    parameter_name = "occupancy"

    def lookups(self, request, model_admin):
        return [("empty", "Empty"), ("space", "Has free beds"), ("full", "Full"), ("over", "Over capacity")]

    def queryset(self, request, queryset):
        if self.value() == "empty":
            return queryset.filter(occupied=0)
        if self.value() == "space":
            return queryset.filter(capacity__isnull=False, occupied__lt=F("capacity"))
        if self.value() == "full":
            return queryset.filter(capacity__isnull=False, occupied__gte=F("capacity"))
        if self.value() == "over":
            return queryset.filter(capacity__isnull=False, occupied__gt=F("capacity"))
        return queryset


@admin.register(Room)
class RoomAdmin(HRAdminMixin, AuditedAdminMixin, admin.ModelAdmin):
    audit_module = "accommodation"
    audit_prefix = "room"

    form = RoomAdminForm
    inlines = [OccupantsInline]
    list_display = ("building", "name", "gender", "people", "free_beds", "active", "capacity_estimated", "notes")
    list_display_links = ("building", "name")
    list_select_related = ("building",)
    list_filter = ("building", "gender", "active", OccupancyFilter, "capacity_estimated")
    search_fields = ("name", "building__name", "notes")
    ordering = ("building__sort_order", "building__name", "name")

    def audit_employee(self, obj):
        return None

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("building").annotate(occupied=Count("assignments"))

    @admin.display(description="People", ordering="occupied")
    def people(self, obj):
        return _occupancy(obj.occupied, obj.capacity)

    @admin.display(description="Free beds")
    def free_beds(self, obj):
        return "?" if obj.capacity is None else max(obj.capacity - obj.occupied, 0)


@admin.register(RoomAssignment)
class RoomAssignmentAdmin(HRAdminMixin, EmployeeColumnsMixin, admin.ModelAdmin):
    """Read-only list: adding or editing a row directly would skip the gender, capacity and bed checks and leave the
    employee's hostel fields out of step. Use the actions (or the Accommodation page); both go through
    AccommodationService, which audits every move."""

    actions = ["vacate_rooms", "move_to_room"]
    list_display = ("staff_number", "employee_name", "department", "employee_status", "building", "room", "bed_number", "assigned_on", "assigned_by")
    list_display_links = ("staff_number", "employee_name")
    list_select_related = ("employee", "employee__department", "room", "room__building", "assigned_by")
    list_filter = ("room__building", "room__gender", "employee__status", "employee__department", "assigned_on")
    search_fields = (*EMPLOYEE_SEARCH_FIELDS, "room__name", "room__building__name")
    date_hierarchy = "assigned_on"
    ordering = ("-assigned_on", "-id")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_manage_permission(self, request):
        return request.user.has_perm("accommodation.manage_accommodation")

    @admin.display(description="Employee status", ordering="employee__status")
    def employee_status(self, obj):
        return status_badge(obj.employee, EMPLOYEE_STATUS_TONES)

    @admin.display(description="Building", ordering="room__building__name")
    def building(self, obj):
        return obj.room.building.name

    @admin.action(description="Take selected people out of their rooms", permissions=["manage"])
    def vacate_rooms(self, request, queryset):
        assignments = list(queryset)
        _, response = confirm_step(
            self, request, queryset, action="vacate_rooms", title="Take people out of their rooms",
            question=f"Vacate the room of {len(assignments)} person(s)?",
            notes=["Their room assignment is removed and their hostel fields on the employee record are cleared. They stay employed and active."],
            confirm_label="Yes, vacate", danger=True,
        )
        if response is not None:
            return response
        done = []
        for assignment in assignments:
            label = str(assignment)
            AccommodationService.unassign(assignment.employee, actor=request.user)
            done.append(label)
        report(self, request, done, verb="vacated")

    @admin.action(description="Move selected people to another room", permissions=["manage"])
    def move_to_room(self, request, queryset):
        assignments = list(queryset)
        rooms = Room.objects.filter(active=True).select_related("building").annotate(occupied=Count("assignments"))
        choices = [(room.pk, f"{room} ({room.occupied}/{room.capacity if room.capacity is not None else '?'})") for room in rooms]
        values, response = confirm_step(
            self, request, queryset, action="move_to_room", title="Move people to another room",
            question=f"Move {len(assignments)} person(s) to the room you choose?",
            notes=["Gender, capacity and 'room in use' rules apply per person; anyone who cannot be moved is reported and stays where they are.",
                   "No bed number is set - choose the bed afterwards on the Accommodation page."],
            fields=[ConfirmField("room", "New room", kind="select", required=True, choices=choices)],
            confirm_label="Yes, move",
        )
        if response is not None:
            return response
        room = Room.objects.select_related("building").get(pk=values["room"])
        done, errors = [], []
        for assignment in assignments:
            try:
                AccommodationService.assign(assignment.employee, room, actor=request.user)
            except ValueError as error:
                errors.append(f"{assignment.employee.employee_id} {assignment.employee.full_name}: {error}")
                continue
            done.append(f"{assignment.employee.employee_id} {assignment.employee.full_name}")
        report(self, request, done, errors=errors, verb=f"moved to {room}")

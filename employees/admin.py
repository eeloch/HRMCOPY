from django import forms
from django.contrib import admin
from django.db import transaction
from django.db.models import Count, ExpressionWrapper, F, IntegerField, OuterRef, Q, Subquery, Value
from django.db.models.functions import Coalesce

from attendance.models import BiometricDevice
from audit.models import AuditSeverity

from .admin_common import (
    AuditedAdminMixin,
    ConfirmField,
    HRAdminMixin,
    badge,
    confirm_step,
    log_action,
    report,
    status_badge,
)
from .models import (
    BiometricIdentity,
    Department,
    Employee,
    Position,
)

EMPLOYEE_STATUS_TONES = {"active": "green", "inactive": "grey", "suspended": "amber", "terminated": "red"}


def _digits(value):
    """'000684' -> '684'. The staff number without its leading zeros IS the terminal user id."""
    return str(value or "").strip().lstrip("0")


def _id_matches_staff_number(identity):
    return _digits(identity.employee.employee_id) == _digits(identity.external_user_id)


def _device_name_subquery():
    """BiometricIdentity.source_identifier holds a device serial (or 'cloud'); resolve it to the terminal's name in
    the same query instead of one lookup per row."""
    return Subquery(BiometricDevice.objects.filter(serial_number=OuterRef("source_identifier")).values("name")[:1])


def _terminal_label(identity):
    name = getattr(identity, "device_name", None)
    if name:
        return name
    if identity.source_identifier == "cloud":
        return "Yunatt cloud"
    return "unknown terminal"


# --------------------------------------------------------------------------------------------------------------------
# Department / Position


@admin.register(Department)
class DepartmentAdmin(HRAdminMixin, AuditedAdminMixin, admin.ModelAdmin):
    audit_module = "employees"
    audit_prefix = "department"
    no_bulk_delete = True  # deleting a department silently clears it from every employee and deletes its positions

    list_display = ("name", "required_staff", "active_staff", "gap", "position_count", "description")
    search_fields = ("name", "description")
    ordering = ("name",)

    def audit_employee(self, obj):
        return None

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(
            active_count=Count("employees", filter=Q(employees__status="active"), distinct=True),
            position_total=Count("positions", distinct=True),
        )

    @admin.display(description="Active staff", ordering="active_count")
    def active_staff(self, obj):
        return obj.active_count

    @admin.display(description="Against required")
    def gap(self, obj):
        if not obj.required_staff:
            return "-"
        difference = obj.active_count - obj.required_staff
        if difference < 0:
            return badge(f"short by {-difference}", "red")
        if difference > 0:
            return badge(f"{difference} over", "blue")
        return badge("full", "green")

    @admin.display(description="Positions", ordering="position_total")
    def position_count(self, obj):
        return obj.position_total


@admin.register(Position)
class PositionAdmin(HRAdminMixin, AuditedAdminMixin, admin.ModelAdmin):
    audit_module = "employees"
    audit_prefix = "position"
    no_bulk_delete = True

    list_display = ("name", "department", "active_staff")
    list_select_related = ("department",)
    list_filter = ("department",)
    search_fields = ("name", "department__name")
    autocomplete_fields = ("department",)
    ordering = ("department__name", "name")

    def audit_employee(self, obj):
        return None

    def get_queryset(self, request):
        # Position.__str__ shows the department, so every place that renders a position needs it loaded.
        return super().get_queryset(request).select_related("department").annotate(
            active_count=Count("employees", filter=Q(employees__status="active")),
        )

    @admin.display(description="Active staff", ordering="active_count")
    def active_staff(self, obj):
        return obj.active_count


# --------------------------------------------------------------------------------------------------------------------
# Biometric identities


class TerminalFilter(admin.SimpleListFilter):
    title = "terminal"
    parameter_name = "terminal"

    def lookups(self, request, model_admin):
        rows = [(device.serial_number, device.name) for device in BiometricDevice.objects.order_by("name")]
        return [*rows, ("cloud", "Yunatt cloud")]

    def queryset(self, request, queryset):
        if self.value():
            return queryset.filter(source_identifier=self.value())
        return queryset


class IdMatchesStaffNumberFilter(admin.SimpleListFilter):
    """Staff number (without leading zeros) IS the terminal id, so a row whose id is something else needs a look."""

    title = "terminal id vs staff number"
    parameter_name = "id_check"

    def lookups(self, request, model_admin):
        return [("differs", "Id differs from staff number"), ("matches", "Id equals staff number")]

    def queryset(self, request, queryset):
        if self.value() not in ("differs", "matches"):
            return queryset
        rows = queryset.values_list("pk", "employee__employee_id", "external_user_id")
        wanted = [pk for pk, staff, ext in rows if (_digits(staff) == _digits(ext)) == (self.value() == "matches")]
        return queryset.filter(pk__in=wanted)


class BiometricIdentityInline(admin.TabularInline):
    """The employee's terminal mappings. Staff number = terminal id: a row whose id differs is flagged."""

    model = BiometricIdentity
    extra = 0
    fields = ("system", "source_identifier", "device_name", "external_user_id", "id_check", "is_active", "updated_at")
    readonly_fields = ("device_name", "id_check", "updated_at")
    show_change_link = True

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("employee").annotate(device_name=_device_name_subquery()).order_by("source_identifier", "external_user_id")

    @admin.display(description="Terminal")
    def device_name(self, obj):
        return _terminal_label(obj) if obj.pk else "-"

    @admin.display(description="Id vs staff no.")
    def id_check(self, obj):
        if not obj.pk:
            return "-"
        return badge("= staff no.", "green") if _id_matches_staff_number(obj) else badge("differs", "red")


@admin.register(BiometricIdentity)
class BiometricIdentityAdmin(HRAdminMixin, AuditedAdminMixin, admin.ModelAdmin):
    """HRM's mapping from a terminal user id to a person. Nothing here ever touches a terminal: deactivating a row
    only stops HRM accepting scans under that id (attendance and meal tickets), it does not delete the user from the
    device."""

    audit_module = "employees"
    audit_prefix = "biometric_identity"
    no_bulk_delete = True  # a deleted mapping makes that terminal user "unmapped"; deactivate instead

    list_display = (
        "staff_number",
        "employee_name",
        "department",
        "employee_status",
        "terminal",
        "source_identifier",
        "external_user_id",
        "id_check",
        "system",
        "active",
        "updated_at",
    )
    list_display_links = ("staff_number", "employee_name")
    list_select_related = ("employee", "employee__department")
    list_filter = ("is_active", TerminalFilter, IdMatchesStaffNumberFilter, "system", "employee__status", "employee__department")
    search_fields = (
        "employee__employee_id",
        "employee__first_name",
        "employee__middle_name",
        "employee__last_name",
        "external_user_id",
        "source_identifier",
    )
    autocomplete_fields = ("employee",)
    readonly_fields = ("created_at", "updated_at")
    ordering = ("-updated_at", "-id")
    date_hierarchy = "updated_at"
    actions = ["deactivate_identities", "reactivate_identities"]

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(device_name=_device_name_subquery())

    @admin.display(description="Staff No.", ordering="employee__employee_id")
    def staff_number(self, obj):
        return obj.employee.employee_id

    @admin.display(description="Employee", ordering="employee__first_name")
    def employee_name(self, obj):
        return obj.employee.full_name

    @admin.display(description="Department", ordering="employee__department__name")
    def department(self, obj):
        return obj.employee.department.name if obj.employee.department else "-"

    @admin.display(description="Employee status", ordering="employee__status")
    def employee_status(self, obj):
        return status_badge(obj.employee, EMPLOYEE_STATUS_TONES)

    @admin.display(description="Terminal", ordering="device_name")
    def terminal(self, obj):
        return _terminal_label(obj)

    @admin.display(description="Id vs staff no.")
    def id_check(self, obj):
        if _id_matches_staff_number(obj):
            return badge("= staff no.", "green")
        # The staff-number rule is for terminals; a cloud / other system id that differs is only worth a glance.
        return badge("differs", "red" if obj.system == "vendor_flask_gateway" else "amber")

    @admin.display(description="Active", ordering="is_active")
    def active(self, obj):
        return badge("active", "green") if obj.is_active else badge("on hold", "grey")

    @admin.action(description="Deactivate selected identities (HRM mapping only - terminals are not touched)", permissions=["change"])
    def deactivate_identities(self, request, queryset):
        identities = [identity for identity in queryset.select_related("employee") if identity.is_active]
        still_active = sum(1 for identity in identities if identity.employee.status == "active")
        notes = [
            "Only HRM's mapping changes. Nothing is deleted from any terminal, and the person keeps their face and card there.",
            "Scans under a deactivated id are rejected as revoked access (a warning goes to the superusers).",
        ]
        if still_active:
            notes.append(
                f"{still_active} of these belong to people who are still ACTIVE. Sync treats a terminal without an active mapping as "
                "missing that person and may push them back onto it. To take a leaver off every terminal, mark the employee inactive instead."
            )
        _, response = confirm_step(
            self, request, queryset, action="deactivate_identities", title="Deactivate terminal identities",
            question=f"Put {len(identities)} identity mapping(s) on hold?", notes=notes, confirm_label="Yes, deactivate", danger=True,
        )
        if response is not None:
            return response
        done, skipped = [], [str(identity) for identity in queryset.select_related("employee") if not identity.is_active]
        with transaction.atomic():
            for identity in identities:
                identity.is_active = False
                identity.save(update_fields=["is_active", "updated_at"])
                log_action(
                    request, event_type="biometric_identity.admin_deactivated", module="employees", employee=identity.employee, obj=identity,
                    severity=AuditSeverity.WARNING, title="Terminal identity deactivated in the admin",
                    description=f"{identity} was put on hold (HRM mapping only; the terminal was not touched).",
                    metadata={"source_identifier": identity.source_identifier, "external_user_id": identity.external_user_id},
                )
                done.append(str(identity))
        report(self, request, done, skipped=[f"{name} (already on hold)" for name in skipped], verb="deactivated")

    @admin.action(description="Reactivate selected identities (active staff only)", permissions=["change"])
    def reactivate_identities(self, request, queryset):
        rows = list(queryset.select_related("employee"))
        eligible = [identity for identity in rows if not identity.is_active and identity.employee.status == "active"]
        _, response = confirm_step(
            self, request, queryset, action="reactivate_identities", title="Reactivate terminal identities",
            question=f"Accept scans again for {len(eligible)} identity mapping(s)?",
            notes=["Identities of people who are not active stay on hold: mark the employee active to restore them all."],
            confirm_label="Yes, reactivate",
        )
        if response is not None:
            return response
        done, skipped = [], []
        with transaction.atomic():
            for identity in rows:
                if identity not in eligible:
                    reason = "already active" if identity.is_active else f"employee is {identity.employee.status}"
                    skipped.append(f"{identity} ({reason})")
                    continue
                identity.is_active = True
                identity.save(update_fields=["is_active", "updated_at"])
                log_action(
                    request, event_type="biometric_identity.admin_reactivated", module="employees", employee=identity.employee, obj=identity,
                    severity=AuditSeverity.SUCCESS, title="Terminal identity reactivated in the admin",
                    description=f"{identity} accepts scans again.",
                    metadata={"source_identifier": identity.source_identifier, "external_user_id": identity.external_user_id},
                )
                done.append(str(identity))
        report(self, request, done, skipped=skipped, verb="reactivated")


# --------------------------------------------------------------------------------------------------------------------
# Employee


class TerminalIdentityFilter(admin.SimpleListFilter):
    title = "terminal identity"
    parameter_name = "identity"

    def lookups(self, request, model_admin):
        return [
            ("none_active", "No active identity (cannot scan anywhere)"),
            ("held", "Has identities on hold"),
            ("some", "Has an active identity"),
        ]

    def queryset(self, request, queryset):
        if self.value() == "none_active":
            return queryset.filter(active_ids=0)
        if self.value() == "held":
            return queryset.filter(held_ids__gt=0)
        if self.value() == "some":
            return queryset.filter(active_ids__gt=0)
        return queryset


class DataCheckFilter(admin.SimpleListFilter):
    """Presence checks only - the values themselves (bank details) are never exposed by a filter."""

    title = "data check"
    parameter_name = "check"

    def lookups(self, request, model_admin):
        return [
            ("no_bank", "Missing bank details"),
            ("no_department", "No department"),
            ("no_position", "No position"),
            ("no_phone", "No phone number"),
            ("no_date", "No employment date"),
        ]

    def queryset(self, request, queryset):
        value = self.value()
        if value == "no_bank":
            return queryset.filter(Q(bank_name="") | Q(account_number="") | Q(bank_code=""))
        if value == "no_department":
            return queryset.filter(department__isnull=True)
        if value == "no_position":
            return queryset.filter(position__isnull=True)
        if value == "no_phone":
            return queryset.filter(phone="")
        if value == "no_date":
            return queryset.filter(employment_date__isnull=True)
        return queryset


class EmployeeAdminForm(forms.ModelForm):
    """The same three consistency rules the API applies (employees.serializers.EmployeeCreateUpdateSerializer)."""

    class Meta:
        model = Employee
        fields = "__all__"

    def clean(self):
        cleaned = super().clean()
        department, position = cleaned.get("department"), cleaned.get("position")
        if position and department and position.department_id != department.pk:
            self.add_error("position", "Selected position does not belong to the selected department.")
        if cleaned.get("hostel_room_number") and not cleaned.get("lives_in_company_hostel"):
            self.add_error("hostel_room_number", "Room number can only be entered for employees living in company accommodation.")
        if cleaned.get("external_accommodation_address") and not cleaned.get("lives_in_external_accommodation"):
            self.add_error("external_accommodation_address", "Address can only be entered for employees living in company-arranged external accommodation.")
        return cleaned


@admin.register(Employee)
class EmployeeAdmin(HRAdminMixin, AuditedAdminMixin, admin.ModelAdmin):
    audit_module = "employees"
    audit_prefix = "employee"
    no_bulk_delete = True  # deleting an employee cascades into leave, documents, attendance, biometrics...

    form = EmployeeAdminForm
    inlines = [BiometricIdentityInline]

    list_display = (
        "staff_number",
        "employee_name",
        "department",
        "position_name",
        "employment_type",
        "employment_category",
        "state",
        "employment_date",
        "phone",
        "terminals",
        "bank_ok",
    )
    list_display_links = ("staff_number", "employee_name")
    list_select_related = ("department", "position")
    list_filter = (
        "status",
        "department",
        "employment_type",
        "employment_category",
        "gender",
        TerminalIdentityFilter,
        DataCheckFilter,
        "lives_in_company_hostel",
        "lives_in_external_accommodation",
        ("exit_date", admin.DateFieldListFilter),
    )
    search_fields = (
        "employee_id",
        "first_name",
        "middle_name",
        "last_name",
        "phone",
        "email",
        "biometric_user_id",
        "=biometric_identities__external_user_id",
    )
    autocomplete_fields = ("department", "position")
    date_hierarchy = "employment_date"
    ordering = (F("employment_date").desc(nulls_last=True), "-id")
    readonly_fields = ("created_at", "updated_at")
    actions = ["mark_inactive", "mark_active"]

    def audit_employee(self, obj):
        return obj

    # Salary and bank details are shown and editable only with the same permissions the API uses
    # (employees.serializers: view_salary / view_bank_details).
    base_fieldsets = (
        ("Identity", {"fields": ("employee_id", ("first_name", "middle_name", "last_name"), "gender", "date_of_birth"),
                      "description": "The staff number is also the terminal user id (without leading zeros). Changing it here does not renumber anyone on a terminal."}),
        ("Contact", {"fields": ("phone", "email")}),
        ("Employment", {"fields": ("status", "department", "position", "employment_type", "employment_category", "employment_date", "exit_date"),
                        "description": "Setting the status away from Active puts the person's terminal identities on hold; setting it back to Active restores them."}),
        ("Salary", {"fields": ("basic_salary",)}),
        ("Bank details", {"fields": ("bank_name", "account_number", "bank_code")}),
        ("Accommodation", {"fields": ("lives_in_company_hostel", "hostel_room_number", "lives_in_external_accommodation", "external_accommodation_address"),
                           "description": "Rooms are managed on the Accommodation page, which keeps these fields in step.", "classes": ("collapse",)}),
        ("Biometric (legacy)", {"fields": ("biometric_user_id",),
                                "description": "Older single-id field. The terminal identities below are what scans are matched against.", "classes": ("collapse",)}),
        ("Record", {"fields": ("created_at", "updated_at"), "classes": ("collapse",)}),
    )

    def has_salary_permission(self, request):
        return request.user.has_perm("employees.view_salary")

    def has_bank_permission(self, request):
        return request.user.has_perm("employees.view_bank_details")

    def get_fieldsets(self, request, obj=None):
        fieldsets = []
        for name, options in self.base_fieldsets:
            if name == "Salary" and not self.has_salary_permission(request):
                continue
            if name == "Bank details" and not self.has_bank_permission(request):
                continue
            fieldsets.append((name, options))
        return fieldsets

    def get_list_display(self, request):
        columns = list(super().get_list_display(request))
        if not self.has_bank_permission(request):
            columns.remove("bank_ok")
        return columns

    def get_queryset(self, request):
        identities = BiometricIdentity.objects.filter(employee=OuterRef("pk")).order_by().values("employee")
        active = Subquery(identities.filter(is_active=True).annotate(c=Count("pk")).values("c"))
        held = Subquery(identities.filter(is_active=False).annotate(c=Count("pk")).values("c"))
        return super().get_queryset(request).annotate(
            active_ids=Coalesce(active, Value(0), output_field=IntegerField()),
            held_ids=Coalesce(held, Value(0), output_field=IntegerField()),
        )

    # ---- columns

    @admin.display(description="Staff No.", ordering="employee_id")
    def staff_number(self, obj):
        return obj.employee_id

    @admin.display(description="Name", ordering="first_name")
    def employee_name(self, obj):
        return obj.full_name

    @admin.display(description="Position", ordering="position__name")
    def position_name(self, obj):
        return obj.position.name if obj.position else "-"

    @admin.display(description="Status", ordering="status")
    def state(self, obj):
        return status_badge(obj, EMPLOYEE_STATUS_TONES)

    @admin.display(description="Terminal ids", ordering="active_ids")
    def terminals(self, obj):
        active, held = obj.active_ids, obj.held_ids
        if not active and not held:
            return badge("none", "red" if obj.status == "active" else "grey")
        text = f"{active} active" + (f" + {held} on hold" if held else "")
        return badge(text, "green" if active else "amber")

    @admin.display(description="Bank details", boolean=True)
    def bank_ok(self, obj):
        # Presence only, never the values - mirrors "Missing bank details" in the API's attention reasons.
        return bool(obj.bank_name and obj.account_number and obj.bank_code)

    # ---- actions

    def _change_status(self, request, queryset, *, target, action, title, verb, notes, danger):
        candidates = [employee for employee in queryset if employee.status != target]
        already = [employee for employee in queryset if employee.status == target]
        by_status = ", ".join(f"{count} {name}" for name, count in _count_by(candidates, "status").items()) or "nobody"
        _, response = confirm_step(
            self, request, queryset, action=action, title=title,
            question=f"Mark {len(candidates)} employee(s) as {target}? (currently: {by_status})", notes=notes,
            confirm_label=f"Yes, mark {target}", danger=danger,
        )
        if response is not None:
            return response
        done = []
        with transaction.atomic():
            for employee in candidates:
                previous = employee.status
                identities_before = employee.biometric_identities.filter(is_active=True).count()
                held_before = employee.biometric_identities.filter(is_active=False).count()
                employee.status = target
                # Employee.save() revokes / restores the terminal identities on this change and does it the same way
                # the API and the import do; only the changed columns are written so a stale copy cannot overwrite
                # anything else (e.g. a salary import running at the same time).
                employee.save(update_fields=["status", "updated_at"])
                identities_after = employee.biometric_identities.filter(is_active=True).count()
                log_action(
                    request, event_type="employee.admin_status_changed", module="employees", employee=employee, obj=employee,
                    severity=AuditSeverity.WARNING if target != "active" else AuditSeverity.SUCCESS,
                    title=f"Employee marked {target} in the admin",
                    description=f"{employee.full_name} ({employee.employee_id}) went from {previous} to {target}.",
                    metadata={
                        "from": previous, "to": target,
                        "identities_active_before": identities_before, "identities_held_before": held_before,
                        "identities_active_after": identities_after,
                    },
                )
                done.append(str(employee))
        report(self, request, done, skipped=[f"{employee} (already {target})" for employee in already], verb=f"marked {target}")

    @admin.action(description="Mark selected employees inactive", permissions=["change"])
    def mark_inactive(self, request, queryset):
        return self._change_status(
            request, queryset, target="inactive", action="mark_inactive", title="Mark employees inactive", verb="inactive", danger=True,
            notes=[
                "Their active terminal identities in HRM are put on hold, so scans stop being accepted (attendance and meal tickets).",
                "Nothing is deleted from any terminal, and nothing is deleted from HRM. Marking them active again restores the identities.",
                "The exit date is not changed - set it on the employee if it is a leaver.",
            ],
        )

    @admin.action(description="Mark selected employees active", permissions=["change"])
    def mark_active(self, request, queryset):
        return self._change_status(
            request, queryset, target="active", action="mark_active", title="Mark employees active", verb="active", danger=False,
            notes=["Their terminal identities that were put on hold are restored, so scans are accepted again."],
        )


def _count_by(rows, attribute):
    counts = {}
    for row in rows:
        counts[getattr(row, attribute)] = counts.get(getattr(row, attribute), 0) + 1
    return counts

from rest_framework import serializers
from datetime import date

from employees.models import BiometricIdentity
from .models import (
    Department,
    Position,
    Employee,
)


def calculate_years_of_service(employee):
    if not employee.employment_date:
        return None

    today = date.today()
    years = today.year - employee.employment_date.year

    if (today.month, today.day) < (
        employee.employment_date.month,
        employee.employment_date.day,
    ):
        years -= 1

    return years


def service_award_for_years(years):
    if years is None or years < 5:
        return None

    if years < 10:
        return "5 Years"

    if years < 15:
        return "10 Years"

    if years < 20:
        return "15 Years"

    if years < 25:
        return "20 Years"

    return "25 Years"


# Personal details (contact, date of birth, housing, terminal ids) are only returned to users who hold
# employees.view_employee (2026-09-25 review, S-03: any signed-in account could enumerate them for every employee).
# Name, staff number, department, position, status and the like stay visible: pickers and rosters need them.
PERSONAL_DETAIL_FIELDS = (
    "phone", "email", "date_of_birth", "biometric_user_id", "biometric", "biometric_identities",
    "lives_in_company_hostel", "hostel_room_number", "lives_in_external_accommodation",
    "external_accommodation_address", "hostel", "accommodation",
)


def hide_personal_details(data, request):
    if not (request and request.user.has_perm("employees.view_employee")):
        for field in PERSONAL_DETAIL_FIELDS:
            data.pop(field, None)
    return data


class DepartmentSerializer(serializers.ModelSerializer):

    class Meta:
        model = Department
        fields = [
            "id",
            "name",
            "description",
            "required_staff",
        ]


class PositionSerializer(serializers.ModelSerializer):

    department_name = serializers.CharField(
        source="department.name",
        read_only=True,
    )

    class Meta:
        model = Position
        fields = [
            "id",
            "name",
            "department",
            "department_name",
        ]


class EmployeeSerializer(serializers.ModelSerializer):

    department_name = serializers.CharField(
        source="department.name",
        read_only=True,
    )

    position_name = serializers.CharField(
        source="position.name",
        read_only=True,
    )

    full_name = serializers.CharField(
        read_only=True,
    )

    current_shift = serializers.SerializerMethodField()

    shift_plan = serializers.SerializerMethodField()

    attention_reasons = serializers.SerializerMethodField()

    hostel = serializers.SerializerMethodField()

    accommodation = serializers.SerializerMethodField()

    biometric = serializers.SerializerMethodField()

    years_of_service = serializers.SerializerMethodField()

    service_award_level = serializers.SerializerMethodField()

    biometric_identities = serializers.SerializerMethodField()


    def get_biometric(self, employee):
        # employee.biometric_identities.all() (not a fresh BiometricIdentity.objects.filter(...) query) so a
        # list view's .prefetch_related("biometric_identities") is actually used - one query total, not one
        # per employee.
        identities = sorted(employee.biometric_identities.all(), key=lambda identity: identity.pk)
        biometric = identities[0] if identities else None

        if biometric is None:
            return None

        return {
            "external_user_id": biometric.external_user_id,
            "system": biometric.system,
            "source_identifier": biometric.source_identifier,
        }

    def get_biometric_identities(self, employee):
        identities = sorted(
            (identity for identity in employee.biometric_identities.all() if identity.is_active),
            key=lambda identity: (identity.system, identity.source_identifier, identity.external_user_id),
        )
        return [
            {
                "system": identity.system,
                "source_identifier": identity.source_identifier,
                "external_user_id": identity.external_user_id,
                "is_active": identity.is_active,
            }
            for identity in identities
        ]


    def get_years_of_service(self, employee):
        return calculate_years_of_service(employee)

    def get_service_award_level(self, employee):
        return service_award_for_years(
            calculate_years_of_service(employee)
        )

    def get_hostel(self, employee):
        return {
            "resident": employee.lives_in_company_hostel,
            "room": employee.hostel_room_number,
        }

    def get_accommodation(self, employee):
        return {
            "external": employee.lives_in_external_accommodation,
            "address": employee.external_accommodation_address,
        }

    class Meta:
        model = Employee

        fields = [
            "id",
            "employee_id",
            "biometric_user_id",

            "first_name",
            "middle_name",
            "last_name",
            "full_name",

            "department",
            "department_name",

            "position",
            "position_name",

            "phone",
            "email",

            "gender",
            "date_of_birth",
            "employment_date",
            "exit_date",
            "employment_type",
            "employment_category",

            "basic_salary",

            "bank_name",
            "account_number",
            "bank_code",

            "lives_in_company_hostel",
            "hostel_room_number",
            "lives_in_external_accommodation",
            "external_accommodation_address",

            "status",

            "current_shift",
            "shift_plan",
            "attention_reasons",
            "hostel",
            "accommodation",
            "biometric",
            "biometric_identities",
            "years_of_service",
            "service_award_level",

            "created_at",
            "updated_at",

        ]

    def to_representation(self, instance):
        data = super().to_representation(instance)
        request = self.context.get("request")
        if not (request and request.user.has_perm("employees.view_salary")):
            data.pop("basic_salary", None)
        if not (request and request.user.has_perm("employees.view_bank_details")):
            data.pop("bank_name", None)
            data.pop("account_number", None)
            data.pop("bank_code", None)
        return hide_personal_details(data, request)

    def get_current_shift(self, employee):
        """Today's actual roster shift - the live source of truth, including a rotation plan's weekly
        Day/Night swap. The old static per-employee ShiftAssignment never reflects that swap, so it is not
        used here.

        A list view passes a prefetched {employee_id: EmployeeRosterDay} map via context (`today_rosters`)
        so this never runs one query per row; a single-object view (no context entry) queries directly."""
        rosters = self.context.get("today_rosters")
        if rosters is not None:
            today_row = rosters.get(employee.id)
        else:
            from django.utils import timezone

            from attendance.models import EmployeeRosterDay

            today_row = (
                EmployeeRosterDay.objects
                .select_related("shift")
                .filter(employee=employee, date=timezone.localdate(), status="work")
                .first()
            )

        if not today_row or not today_row.shift:
            return None

        return {
            "id": today_row.shift.id,
            "name": today_row.shift.name,
            "start_time": today_row.shift.start_time,
            "end_time": today_row.shift.end_time,
        }

    def get_shift_plan(self, employee):
        """The employee's current shift plan (and group, for a rotation plan) - not just today's shift, so
        the directory can show and filter by "who is on which plan" even on a rest day.

        Same prefetch-via-context pattern as get_current_shift, via `current_plan_assignments`."""
        assignments = self.context.get("current_plan_assignments")
        if assignments is not None:
            assignment = assignments.get(employee.id)
        else:
            from django.db.models import Q
            from django.utils import timezone

            from attendance.models import ShiftPlanAssignment

            today = timezone.localdate()
            assignment = (
                ShiftPlanAssignment.objects
                .select_related("plan")
                .filter(employee=employee, start_date__lte=today)
                .filter(Q(end_date__isnull=True) | Q(end_date__gte=today))
                .order_by("-start_date")
                .first()
            )

        if not assignment:
            return None

        return {
            "id": assignment.plan_id,
            "name": assignment.plan.name,
            "kind": assignment.plan.kind,
            "group": assignment.group,
        }

    def get_attention_reasons(self, employee):
        """Short, specific reasons this active employee needs attention - not just a flag. Presence/absence
        only (e.g. "Missing bank details"), never the actual values, so this is safe to show regardless of
        whether the viewer has permission to see the values themselves."""
        if employee.status != "active":
            return []
        reasons = []
        if self.get_shift_plan(employee) is None:
            reasons.append("No shift plan")
        if not (employee.bank_name and employee.account_number and employee.bank_code):
            reasons.append("Missing bank details")
        if not employee.biometric_user_id:
            reasons.append("No biometric link")
        return reasons



class EmployeeCreateUpdateSerializer(
    serializers.ModelSerializer
):

    class Meta:
        model = Employee

        fields = [
            "employee_id",
            "biometric_user_id",

            "first_name",
            "middle_name",
            "last_name",

            "department",
            "position",

            "phone",
            "email",

            "gender",
            "date_of_birth",
            "employment_date",
            "exit_date",
            "employment_type",
            "employment_category",

            "basic_salary",

            "bank_name",
            "account_number",
            "bank_code",

            "lives_in_company_hostel",
            "hostel_room_number",
            "lives_in_external_accommodation",
            "external_accommodation_address",

            "status",
        ]

    def validate_employee_id(self, value):
        # A staff number is written into exported spreadsheets; one that starts like a formula is never valid.
        if value and str(value).startswith(("=", "+", "-", "@", "\t", "\r")):
            raise serializers.ValidationError("A staff number cannot start with =, +, - or @.")
        return value

    def validate_basic_salary(self, value):
        request = self.context.get("request")
        if not (request and request.user.has_perm("employees.view_salary")):
            raise serializers.ValidationError(
                "You don't have permission to set the basic salary."
            )
        return value

    def _require_bank_details_permission(self, value):
        request = self.context.get("request")
        if not (request and request.user.has_perm("employees.view_bank_details")):
            raise serializers.ValidationError(
                "You don't have permission to set employee bank details."
            )
        return value

    def validate_bank_name(self, value):
        return self._require_bank_details_permission(value)

    def validate_account_number(self, value):
        return self._require_bank_details_permission(value)

    def validate_bank_code(self, value):
        return self._require_bank_details_permission(value)

    def validate(self, attrs):

        department = attrs.get(
            "department",
            getattr(
                self.instance,
                "department",
                None,
            ),
        )

        position = attrs.get(
            "position",
            getattr(
                self.instance,
                "position",
                None,
            ),
        )

        lives_in_hostel = attrs.get(
            "lives_in_company_hostel",
            getattr(
                self.instance,
                "lives_in_company_hostel",
                False,
            ),
        )

        hostel_room_number = attrs.get(
            "hostel_room_number",
            getattr(
                self.instance,
                "hostel_room_number",
                "",
            ),
        )

        lives_externally = attrs.get(
            "lives_in_external_accommodation",
            getattr(
                self.instance,
                "lives_in_external_accommodation",
                False,
            ),
        )

        external_accommodation_address = attrs.get(
            "external_accommodation_address",
            getattr(
                self.instance,
                "external_accommodation_address",
                "",
            ),
        )

        if (
            position
            and department
            and position.department_id
            != department.id
        ):
            raise serializers.ValidationError({
                "position":
                    "Selected position does not belong to the selected department."
            })

        if (
            hostel_room_number
            and not lives_in_hostel
        ):
            raise serializers.ValidationError({
                "hostel_room_number":
                    "Room number can only be entered for employees living in company accommodation."
            })

        if (
            external_accommodation_address
            and not lives_externally
        ):
            raise serializers.ValidationError({
                "external_accommodation_address":
                    "Address can only be entered for employees living in company-arranged external accommodation."
            })

        return attrs

from rest_framework import serializers

from .models import Employee, BiometricIdentity


class EmployeeProfileSerializer(serializers.ModelSerializer):
    department = serializers.CharField(
        source="department.name",
        read_only=True,
    )

    position = serializers.CharField(
        source="position.name",
        read_only=True,
    )

    biometric = serializers.SerializerMethodField()

    hostel = serializers.SerializerMethodField()

    accommodation = serializers.SerializerMethodField()

    full_name = serializers.SerializerMethodField()

    years_of_service = serializers.SerializerMethodField()

    service_award_level = serializers.SerializerMethodField()

    class Meta:
        model = Employee

        fields = [
            "id",
            "employee_id",
            "full_name",
            "first_name",
            "middle_name",
            "last_name",
            "department",
            "position",
            "phone",
            "email",
            "employment_date",
            "exit_date",
            "employment_type",
            "employment_category",
            "years_of_service",
            "service_award_level",
            "basic_salary",
            "bank_name",
            "account_number",
            "bank_code",
            "status",
            "hostel",
            "accommodation",
            "biometric",
        ]

    def to_representation(self, instance):
        data = super().to_representation(instance)
        request = self.context.get("request")
        if not (request and request.user.has_perm("employees.view_salary")):
            data.pop("basic_salary", None)
        if not (request and request.user.has_perm("employees.view_bank_details")):
            data.pop("bank_name", None)
            data.pop("account_number", None)
            data.pop("bank_code", None)
        return hide_personal_details(data, request)

    def get_full_name(self, obj):
        return " ".join(
            filter(
                None,
                [
                    obj.first_name,
                    obj.middle_name,
                    obj.last_name,
                ],
            )
        )

    def get_hostel(self, obj):
        return {
            "resident": obj.lives_in_company_hostel,
            "room": obj.hostel_room_number,
        }

    def get_accommodation(self, obj):
        return {
            "external": obj.lives_in_external_accommodation,
            "address": obj.external_accommodation_address,
        }

    def get_biometric(self, obj):
        biometric = (
            BiometricIdentity.objects.filter(
                employee=obj
            ).first()
        )

        if not biometric:
            return None

        return {
            "user_id": biometric.external_user_id,
            "system": biometric.system,
            "source": biometric.source_identifier,
        }

    def get_years_of_service(self, obj):
        return calculate_years_of_service(obj)

    def get_service_award_level(self, obj):
        return service_award_for_years(
            calculate_years_of_service(obj)
        )

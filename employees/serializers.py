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

    hostel = serializers.SerializerMethodField()

    biometric = serializers.SerializerMethodField()

    years_of_service = serializers.SerializerMethodField()

    service_award_level = serializers.SerializerMethodField()

    biometric_identities = serializers.SerializerMethodField()


    def get_biometric(self, employee):

        biometric = (
            BiometricIdentity.objects
            .filter(employee=employee)
            .first()
        )

        if biometric is None:
            return None

        return {
            "external_user_id": biometric.external_user_id,
            "system": biometric.system,
            "source_identifier": biometric.source_identifier,
        }

    def get_biometric_identities(self, employee):
        identities = employee.biometric_identities.filter(is_active=True).order_by(
            "system",
            "source_identifier",
            "external_user_id",
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

            "date_of_birth",
            "employment_date",
            "employment_type",
            "employment_category",

            "basic_salary",

            "lives_in_company_hostel",
            "hostel_room_number",

            "status",

            "current_shift",
            "hostel",
            "biometric",
            "biometric_identities",
            "years_of_service",
            "service_award_level",

            "created_at",
            "updated_at",

        ]

    def get_current_shift(self, employee):

        assignment = (
            employee.shift_assignments
            .select_related("shift")
            .filter(
                end_date__isnull=True
            )
            .order_by("-start_date")
            .first()
        )

        if not assignment:
            return None

        return {
            "id": assignment.shift.id,
            "name": assignment.shift.name,
            "start_time": assignment.shift.start_time,
            "end_time": assignment.shift.end_time,
        }



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

            "date_of_birth",
            "employment_date",
            "employment_type",
            "employment_category",

            "basic_salary",

            "lives_in_company_hostel",
            "hostel_room_number",

            "status",
        ]

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
            "employment_type",
            "employment_category",
            "years_of_service",
            "service_award_level",
            "basic_salary",
            "status",
            "hostel",
            "biometric",
        ]

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

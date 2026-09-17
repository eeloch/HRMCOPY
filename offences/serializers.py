from django.utils import timezone

from rest_framework import serializers

from .models import EmployeeOffence, OffenceType


class OffenceTypeSerializer(serializers.ModelSerializer):
    class Meta:
        model = OffenceType
        fields = ("id", "name", "default_amount", "description", "active")


class EmployeeOffenceSerializer(serializers.ModelSerializer):
    employee_name = serializers.CharField(source="employee.full_name", read_only=True)
    employee_number = serializers.CharField(source="employee.employee_id", read_only=True)
    offence_type_name = serializers.CharField(source="offence_type.name", read_only=True)
    recorded_by_name = serializers.CharField(source="recorded_by.get_full_name", read_only=True, default="")
    reviewer_name = serializers.CharField(source="reviewer.get_full_name", read_only=True, default="")

    class Meta:
        model = EmployeeOffence
        fields = (
            "id",
            "employee",
            "employee_name",
            "employee_number",
            "offence_type",
            "offence_type_name",
            "amount",
            "incident_date",
            "notes",
            "status",
            "recorded_by",
            "recorded_by_name",
            "reviewer",
            "reviewer_name",
            "reviewed_at",
            "comment",
            "created_at",
        )
        read_only_fields = ("status", "recorded_by", "reviewer", "reviewed_at", "comment", "created_at")

    def validate_incident_date(self, value):
        if value > timezone.localdate():
            raise serializers.ValidationError("The incident date cannot be in the future.")
        return value

from django.utils import timezone

from rest_framework import serializers

from .models import EmployeeOffence, OffenceType, RewardType


class OffenceTypeSerializer(serializers.ModelSerializer):
    class Meta:
        model = OffenceType
        fields = (
            "id", "category", "name", "default_amount", "description",
            "penalty_first", "penalty_second", "penalty_third", "amount_first", "amount_second", "amount_third",
            "sort_order", "active",
        )
        extra_kwargs = {"default_amount": {"required": False}}


class RewardTypeSerializer(serializers.ModelSerializer):
    auto_rule_label = serializers.CharField(source="get_auto_rule_display", read_only=True)

    class Meta:
        model = RewardType
        fields = ("id", "category", "name", "reward_first", "reward_second", "amount_first", "amount_second", "auto_rule", "auto_rule_label", "sort_order", "active")


class EmployeeOffenceSerializer(serializers.ModelSerializer):
    employee_name = serializers.CharField(source="employee.full_name", read_only=True)
    employee_number = serializers.CharField(source="employee.employee_id", read_only=True)
    offence_type_name = serializers.CharField(source="offence_type.name", read_only=True)
    offence_category = serializers.CharField(source="offence_type.category", read_only=True)
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
            "offence_category",
            "amount",
            "occurrence",
            "penalty_text",
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
        read_only_fields = ("status", "recorded_by", "reviewer", "reviewed_at", "comment", "created_at", "occurrence", "penalty_text")
        extra_kwargs = {"amount": {"required": False}}

    def validate_incident_date(self, value):
        if value > timezone.localdate():
            raise serializers.ValidationError("The incident date cannot be in the future.")
        return value

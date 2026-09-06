from datetime import date

from rest_framework import serializers

from .models import (
    EmployeeMealEntitlement,
    MealDevice,
    MealTicketRate,
)


def _ranges_overlap(start, end, other_start, other_end):
    upper_bound = date.max
    return start <= (other_end or upper_bound) and other_start <= (end or upper_bound)


class EmployeeMealEntitlementSerializer(serializers.ModelSerializer):
    employee_name = serializers.CharField(source="employee.full_name", read_only=True)

    class Meta:
        model = EmployeeMealEntitlement
        fields = (
            "id",
            "employee",
            "employee_name",
            "tickets_per_work_day",
            "effective_from",
            "effective_to",
            "reason",
            "is_exceptional_override",
            "set_by",
            "created_at",
        )
        read_only_fields = ("set_by", "created_at")

    def validate(self, attrs):
        is_exceptional_override = attrs.get(
            "is_exceptional_override",
            getattr(self.instance, "is_exceptional_override", False),
        )
        request = self.context.get("request")
        if is_exceptional_override and (
            request is None or not request.user.is_superuser
        ):
            raise serializers.ValidationError(
                {
                    "is_exceptional_override": (
                        "Exceptional meal entitlements require a Super User."
                    )
                }
            )

        employee = attrs.get("employee") or getattr(self.instance, "employee", None)
        effective_from = attrs.get(
            "effective_from",
            getattr(self.instance, "effective_from", None),
        )
        effective_to = attrs.get(
            "effective_to",
            getattr(self.instance, "effective_to", None),
        )
        if effective_to and effective_to < effective_from:
            raise serializers.ValidationError(
                {"effective_to": "The end date cannot be before the start date."}
            )
        if employee and effective_from:
            queryset = EmployeeMealEntitlement.objects.filter(employee=employee)
            if self.instance:
                queryset = queryset.exclude(pk=self.instance.pk)
            for existing in queryset:
                if _ranges_overlap(
                    effective_from,
                    effective_to,
                    existing.effective_from,
                    existing.effective_to,
                ):
                    raise serializers.ValidationError(
                        "Entitlement dates overlap an existing entitlement for this employee."
                    )
        return attrs


class MealTicketRateSerializer(serializers.ModelSerializer):
    class Meta:
        model = MealTicketRate
        fields = (
            "id",
            "amount",
            "effective_from",
            "effective_to",
            "active",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("created_at", "updated_at")

    def validate(self, attrs):
        amount = attrs.get("amount", getattr(self.instance, "amount", None))
        if amount is not None and amount <= 0:
            raise serializers.ValidationError(
                {"amount": "The meal ticket rate must be greater than zero."}
            )
        effective_from = attrs.get(
            "effective_from",
            getattr(self.instance, "effective_from", None),
        )
        effective_to = attrs.get(
            "effective_to",
            getattr(self.instance, "effective_to", None),
        )
        if effective_to and effective_to < effective_from:
            raise serializers.ValidationError(
                {"effective_to": "The end date cannot be before the start date."}
            )
        queryset = MealTicketRate.objects.all()
        if self.instance:
            queryset = queryset.exclude(pk=self.instance.pk)
        if effective_from:
            for existing in queryset:
                if _ranges_overlap(
                    effective_from,
                    effective_to,
                    existing.effective_from,
                    existing.effective_to,
                ):
                    raise serializers.ValidationError(
                        "Rate dates overlap an existing meal ticket rate."
                    )
        return attrs


class MealDeviceSerializer(serializers.ModelSerializer):
    class Meta:
        model = MealDevice
        fields = ("id", "name", "serial_number", "active", "created_at")
        read_only_fields = ("created_at",)

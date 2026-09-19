from decimal import Decimal

from django.db.models import Sum
from rest_framework import serializers

from .models import Bonus, BonusStatus, EmployeeOfTheMonth
from .services import month_label


def _name(user):
    return (user.get_full_name() or user.username) if user else None


class BonusSerializer(serializers.ModelSerializer):
    employee_name = serializers.CharField(source="employee.full_name", read_only=True)
    employee_number = serializers.CharField(source="employee.employee_id", read_only=True)
    department_name = serializers.CharField(source="employee.department.name", read_only=True, default=None)
    kind_label = serializers.CharField(source="get_kind_display", read_only=True)
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    performance_label = serializers.SerializerMethodField()
    pay_label = serializers.SerializerMethodField()
    recorded_by_name = serializers.SerializerMethodField()
    decided_by_name = serializers.SerializerMethodField()
    year_total = serializers.SerializerMethodField()

    class Meta:
        model = Bonus
        fields = ("id", "employee", "employee_name", "employee_number", "department_name", "kind", "kind_label", "amount", "reason", "performance_year", "performance_month", "performance_label", "pay_year", "pay_month", "pay_label", "status", "status_label", "recorded_by_name", "created_at", "decided_by_name", "decided_at", "decision_comment", "year_total")

    def get_performance_label(self, obj):
        return month_label(obj.performance_year, obj.performance_month)

    def get_pay_label(self, obj):
        return month_label(obj.pay_year, obj.pay_month)

    def get_recorded_by_name(self, obj):
        return _name(obj.recorded_by)

    def get_decided_by_name(self, obj):
        return _name(obj.decided_by)

    def get_year_total(self, obj):
        """What this person has already been given (approved or paid) in the same calendar year, other than this bonus,
        so the approver can see how often they are being rewarded."""
        total = Bonus.objects.filter(employee=obj.employee, performance_year=obj.performance_year, status__in=[BonusStatus.APPROVED, BonusStatus.PAID]).exclude(pk=obj.pk).aggregate(total=Sum("amount"))["total"]
        return str(total or Decimal("0.00"))


class EmployeeOfTheMonthSerializer(serializers.ModelSerializer):
    employee_name = serializers.CharField(source="employee.full_name", read_only=True)
    employee_number = serializers.CharField(source="employee.employee_id", read_only=True)
    department_name = serializers.CharField(source="department.name", read_only=True)
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    month_label = serializers.SerializerMethodField()
    recorded_by_name = serializers.SerializerMethodField()
    decided_by_name = serializers.SerializerMethodField()
    bonus_status = serializers.CharField(source="bonus.status", read_only=True, default=None)

    class Meta:
        model = EmployeeOfTheMonth
        fields = ("id", "department", "department_name", "year", "month", "month_label", "employee", "employee_name", "employee_number", "reason", "reward_amount", "status", "status_label", "bonus_status", "recorded_by_name", "created_at", "decided_by_name", "decided_at", "decision_comment")

    def get_month_label(self, obj):
        return month_label(obj.year, obj.month)

    def get_recorded_by_name(self, obj):
        return _name(obj.recorded_by)

    def get_decided_by_name(self, obj):
        return _name(obj.decided_by)


class BonusCreateSerializer(serializers.Serializer):
    employee = serializers.IntegerField()
    amount = serializers.DecimalField(max_digits=14, decimal_places=2, min_value=Decimal("0.01"))
    reason = serializers.CharField()
    kind = serializers.ChoiceField(choices=["performance", "other"], default="performance")
    performance_year = serializers.IntegerField(min_value=2000, max_value=2100)
    performance_month = serializers.IntegerField(min_value=1, max_value=12)
    pay_year = serializers.IntegerField(required=False, min_value=2000, max_value=2100)
    pay_month = serializers.IntegerField(required=False, min_value=1, max_value=12)


class DecisionSerializer(serializers.Serializer):
    comment = serializers.CharField(required=False, allow_blank=True)
    pay_year = serializers.IntegerField(required=False, min_value=2000, max_value=2100)
    pay_month = serializers.IntegerField(required=False, min_value=1, max_value=12)


class EotmCreateSerializer(serializers.Serializer):
    department = serializers.IntegerField()
    employee = serializers.IntegerField()
    year = serializers.IntegerField(min_value=2000, max_value=2100)
    month = serializers.IntegerField(min_value=1, max_value=12)
    reason = serializers.CharField()
    reward_amount = serializers.DecimalField(max_digits=14, decimal_places=2, min_value=Decimal("0"), required=False, allow_null=True)

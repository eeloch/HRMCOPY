from decimal import Decimal

from rest_framework import serializers

from employees.models import Employee

from .services import MIN_MONTHS_FOR_PARTIAL, MIN_NOTICE_MONTHS, PARTIAL_LIMIT_PERCENT, partial_eligible_from, partial_limit
from .models import DeferredFundAccount, DeferredFundEntry, DeferredFundWithdrawal


def _name(user):
    return (user.get_full_name() or user.username) if user else None


class AccountSerializer(serializers.ModelSerializer):
    employee_name = serializers.CharField(source="employee.full_name", read_only=True)
    employee_number = serializers.CharField(source="employee.employee_id", read_only=True)
    department_name = serializers.CharField(source="employee.department.name", read_only=True, default=None)
    position_name = serializers.CharField(source="employee.position.name", read_only=True, default=None)
    employee_active = serializers.SerializerMethodField()
    balance = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    available = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    monthly_amount = serializers.SerializerMethodField()
    saving_since = serializers.SerializerMethodField()
    partial_eligible_from = serializers.SerializerMethodField()
    partial_limit = serializers.SerializerMethodField()
    partial_withdrawn = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)

    class Meta:
        model = DeferredFundAccount
        fields = ("saving_since", "partial_eligible_from", "partial_limit", "partial_withdrawn", "id", "employee", "employee_name", "employee_number", "department_name", "position_name", "employee_active", "percent", "active", "enrolled_on", "balance", "available", "monthly_amount")

    def get_employee_active(self, obj):
        return obj.employee.status == "active"

    def get_saving_since(self, obj):
        return obj.saving_start.isoformat()

    def get_partial_eligible_from(self, obj):
        return partial_eligible_from(obj).isoformat()

    def get_partial_limit(self, obj):
        return str(min(partial_limit(obj), obj.available))

    def get_monthly_amount(self, obj):
        return str((obj.employee.basic_salary * obj.percent / 100).quantize(Decimal("0.01")))


class EntrySerializer(serializers.ModelSerializer):
    entry_type_label = serializers.CharField(source="get_entry_type_display", read_only=True)
    payroll_period_name = serializers.CharField(source="payroll_period.display_name", read_only=True, default=None)

    class Meta:
        model = DeferredFundEntry
        fields = ("id", "entry_type", "entry_type_label", "amount", "entry_date", "note", "payroll_period_name")


class WithdrawalSerializer(serializers.ModelSerializer):
    employee_name = serializers.CharField(source="account.employee.full_name", read_only=True)
    employee_number = serializers.CharField(source="account.employee.employee_id", read_only=True)
    balance = serializers.DecimalField(source="account.balance", max_digits=14, decimal_places=2, read_only=True)
    kind_label = serializers.CharField(source="get_kind_display", read_only=True)
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    recorded_by_name = serializers.SerializerMethodField()
    decided_by_name = serializers.SerializerMethodField()
    paid_by_name = serializers.SerializerMethodField()

    class Meta:
        model = DeferredFundWithdrawal
        fields = ("id", "account", "employee_name", "employee_number", "balance", "kind", "kind_label", "amount", "reason", "notice_given_on", "leaving_on", "rule_exceptions", "status", "status_label", "recorded_by_name", "created_at", "decided_by_name", "decision_comment", "paid_by_name", "paid_on", "payment_reference")

    def get_recorded_by_name(self, obj):
        return _name(obj.recorded_by)

    def get_decided_by_name(self, obj):
        return _name(obj.decided_by)

    def get_paid_by_name(self, obj):
        return _name(obj.paid_by)


class EnrolSerializer(serializers.Serializer):
    employee = serializers.PrimaryKeyRelatedField(queryset=Employee.objects.all())
    percent = serializers.DecimalField(max_digits=5, decimal_places=2, min_value=Decimal("0.01"), max_value=Decimal("100"))
    opening_balance = serializers.DecimalField(max_digits=14, decimal_places=2, min_value=Decimal("0"), required=False, default=Decimal("0"))
    saving_since = serializers.DateField(required=False, allow_null=True)


class PercentSerializer(serializers.Serializer):
    percent = serializers.DecimalField(max_digits=5, decimal_places=2, min_value=Decimal("0.01"), max_value=Decimal("100"))


class AdjustSerializer(serializers.Serializer):
    amount = serializers.DecimalField(max_digits=14, decimal_places=2)
    note = serializers.CharField()


class WithdrawalCreateSerializer(serializers.Serializer):
    account = serializers.PrimaryKeyRelatedField(queryset=DeferredFundAccount.objects.all())
    kind = serializers.ChoiceField(choices=["partial", "final"])
    amount = serializers.DecimalField(max_digits=14, decimal_places=2, min_value=Decimal("0.01"), required=False)
    reason = serializers.CharField(required=False, allow_blank=True)
    notice_given_on = serializers.DateField(required=False, allow_null=True)
    leaving_on = serializers.DateField(required=False, allow_null=True)


class ForfeitSerializer(serializers.Serializer):
    reason = serializers.CharField()


class WithdrawalActionSerializer(serializers.Serializer):
    comment = serializers.CharField(required=False, allow_blank=True)
    paid_on = serializers.DateField(required=False)
    reference = serializers.CharField(required=False, allow_blank=True)


POLICY = {"min_months_for_partial": MIN_MONTHS_FOR_PARTIAL, "partial_limit_percent": str(PARTIAL_LIMIT_PERCENT), "min_notice_months": MIN_NOTICE_MONTHS}

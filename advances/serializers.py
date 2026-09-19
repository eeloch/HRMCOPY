from decimal import Decimal

from rest_framework import serializers

from employees.models import Employee

from .models import SalaryAdvance
from .services import AdvanceService, MAX_MONTHS

MONTH_NAMES = ["", "January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]


class SalaryAdvanceSerializer(serializers.ModelSerializer):
    employee_name = serializers.CharField(source="employee.full_name", read_only=True)
    employee_number = serializers.CharField(source="employee.employee_id", read_only=True)
    department_name = serializers.CharField(source="employee.department.name", read_only=True, default=None)
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    recorded_by_name = serializers.SerializerMethodField()
    decided_by_name = serializers.SerializerMethodField()
    paid_by_name = serializers.SerializerMethodField()
    amount_repaid = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    balance = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    instalment = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    deduct_from_label = serializers.SerializerMethodField()
    employee_basic_salary = serializers.SerializerMethodField()
    other_outstanding = serializers.SerializerMethodField()

    class Meta:
        model = SalaryAdvance
        fields = ("id", "employee", "employee_name", "employee_number", "department_name", "amount", "reason", "repayment_months", "instalment", "deduct_from_year", "deduct_from_month", "deduct_from_label", "status", "status_label", "amount_repaid", "balance", "recorded_by_name", "created_at", "decided_by_name", "decided_at", "decision_comment", "paid_by_name", "paid_on", "payment_reference", "employee_basic_salary", "other_outstanding")

    @staticmethod
    def _name(user):
        return (user.get_full_name() or user.username) if user else None

    def get_recorded_by_name(self, obj):
        return self._name(obj.recorded_by)

    def get_decided_by_name(self, obj):
        return self._name(obj.decided_by)

    def get_paid_by_name(self, obj):
        return self._name(obj.paid_by)

    def get_deduct_from_label(self, obj):
        return f"{MONTH_NAMES[obj.deduct_from_month]} {obj.deduct_from_year}"

    def get_employee_basic_salary(self, obj):
        # Salary is sensitive: only shown to people who may already see it.
        request = self.context.get("request")
        if request is not None and request.user.has_perm("employees.view_salary"):
            return str(obj.employee.basic_salary)
        return None

    def get_other_outstanding(self, obj):
        return str(AdvanceService.outstanding_for(obj.employee, exclude=obj.pk))


class SalaryAdvanceCreateSerializer(serializers.Serializer):
    employee = serializers.PrimaryKeyRelatedField(queryset=Employee.objects.all())
    amount = serializers.DecimalField(max_digits=14, decimal_places=2, min_value=Decimal("0.01"))
    reason = serializers.CharField(required=False, allow_blank=True)
    repayment_months = serializers.IntegerField(min_value=1, max_value=MAX_MONTHS, default=1)
    deduct_from_year = serializers.IntegerField(required=False, min_value=2000, max_value=2100)
    deduct_from_month = serializers.IntegerField(required=False, min_value=1, max_value=12)

    def validate(self, attrs):
        year, month = attrs.pop("deduct_from_year", None), attrs.pop("deduct_from_month", None)
        if (year is None) != (month is None):
            raise serializers.ValidationError({"deduct_from_month": "Choose both the year and month of the first deduction."})
        attrs["deduct_from"] = (year, month) if year else None
        return attrs


class AdvanceDecisionSerializer(serializers.Serializer):
    comment = serializers.CharField(required=False, allow_blank=True)
    paid_on = serializers.DateField(required=False)
    reference = serializers.CharField(required=False, allow_blank=True)

from rest_framework import serializers

from payroll.models import EmployeePayroll, PayrollLineItem, PayrollPeriod, PayrollPeriodStatus


class PayrollPeriodSerializer(serializers.ModelSerializer):
    display_name = serializers.CharField(read_only=True)
    employee_count = serializers.IntegerField(read_only=True, default=0)
    total_basic_salary = serializers.DecimalField(max_digits=16, decimal_places=2, read_only=True, default=0)
    total_gross_earnings = serializers.DecimalField(max_digits=16, decimal_places=2, read_only=True, default=0)
    total_deductions = serializers.DecimalField(max_digits=16, decimal_places=2, read_only=True, default=0)
    total_net_pay = serializers.DecimalField(max_digits=16, decimal_places=2, read_only=True, default=0)

    class Meta:
        model = PayrollPeriod
        fields = ("id", "year", "month", "display_name", "status", "notes", "created_at", "approved_at", "employee_count", "total_basic_salary", "total_gross_earnings", "total_deductions", "total_net_pay")
        read_only_fields = ("status", "created_at", "approved_at")


class PayrollPeriodCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = PayrollPeriod
        fields = ("year", "month", "notes")


class PayrollLineItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = PayrollLineItem
        fields = ("id", "item_type", "code", "description", "amount", "source_type", "source_reference", "metadata", "is_system_generated", "created_at")
        read_only_fields = ("source_type", "source_reference", "is_system_generated", "created_at")


class PayrollLineItemCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = PayrollLineItem
        fields = ("item_type", "code", "description", "amount", "metadata")

    def validate_amount(self, value):
        if value < 0:
            raise serializers.ValidationError("Amount must be zero or greater.")
        return value


class EmployeePayrollListSerializer(serializers.ModelSerializer):
    employee_id = serializers.CharField(source="employee.employee_id", read_only=True)
    employee_name = serializers.CharField(source="employee.full_name", read_only=True)
    department_name = serializers.CharField(source="employee.department.name", read_only=True, default=None)
    payroll_period_name = serializers.CharField(source="payroll_period.display_name", read_only=True)

    class Meta:
        model = EmployeePayroll
        fields = ("id", "employee", "employee_id", "employee_name", "department_name", "payroll_period_name", "basic_salary", "gross_earnings", "total_deductions", "net_pay", "status", "generated_at", "updated_at")


class EmployeePayrollDetailSerializer(EmployeePayrollListSerializer):
    payroll_period = PayrollPeriodSerializer(read_only=True)
    line_items = PayrollLineItemSerializer(many=True, read_only=True)
    position_name = serializers.CharField(source="employee.position.name", read_only=True, default=None)
    employment_date = serializers.DateField(source="employee.employment_date", read_only=True)
    bank_name = serializers.CharField(source="employee.bank_name", read_only=True)
    account_number_masked = serializers.SerializerMethodField()

    class Meta(EmployeePayrollListSerializer.Meta):
        fields = EmployeePayrollListSerializer.Meta.fields + ("payroll_period", "line_items", "position_name", "employment_date", "bank_name", "account_number_masked")

    def get_account_number_masked(self, obj):
        digits = "".join(ch for ch in obj.employee.account_number if ch.isdigit())
        return f"******{digits[-4:]}" if len(digits) >= 4 else ""


class PayrollPeriodTransitionSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=(PayrollPeriodStatus.PROCESSING, PayrollPeriodStatus.REVIEW, PayrollPeriodStatus.APPROVED))

from rest_framework import serializers

from employees.models import Employee
from payroll.models import PayrollPeriod

from .models import EmployeePPEIssue, PPEType


class PPETypeSerializer(serializers.ModelSerializer):
    class Meta:
        model = PPEType
        fields = ("id", "name", "code", "description", "potentially_employee_deductible", "default_cost", "active")


class EmployeePPEIssueSerializer(serializers.ModelSerializer):
    employee_name = serializers.CharField(source="employee.full_name", read_only=True)
    employee_id = serializers.CharField(source="employee.employee_id", read_only=True)
    department_name = serializers.CharField(source="employee.department.name", read_only=True, default=None)
    ppe_type_name = serializers.CharField(source="ppe_type.name", read_only=True)
    ppe_type_code = serializers.CharField(source="ppe_type.code", read_only=True)
    target_payroll_period_name = serializers.CharField(source="target_payroll_period.display_name", read_only=True, default=None)
    deducted_payroll_id = serializers.IntegerField(read_only=True, allow_null=True)

    class Meta:
        model = EmployeePPEIssue
        fields = ("id", "employee", "employee_id", "employee_name", "department_name", "ppe_type", "ppe_type_name", "ppe_type_code", "quantity", "issue_date", "unit_cost", "total_cost", "notes", "employee_deductible", "deduction_status", "target_payroll_period", "target_payroll_period_name", "deducted_payroll_id", "payroll_line_item", "reviewed_by", "reviewed_at", "review_comment", "created_at")
        read_only_fields = ("total_cost", "employee_deductible", "deduction_status", "target_payroll_period", "deducted_payroll_id", "payroll_line_item", "reviewed_by", "reviewed_at", "review_comment", "created_at")


class PPEIssueCreateSerializer(serializers.Serializer):
    employee = serializers.PrimaryKeyRelatedField(queryset=Employee.objects.all())
    ppe_type = serializers.PrimaryKeyRelatedField(queryset=PPEType.objects.filter(active=True))
    quantity = serializers.IntegerField(min_value=1)
    issue_date = serializers.DateField()
    unit_cost = serializers.DecimalField(max_digits=14, decimal_places=2, min_value=0)
    notes = serializers.CharField(required=False, allow_blank=True)


class PPEDecisionSerializer(serializers.Serializer):
    payroll_period = serializers.PrimaryKeyRelatedField(queryset=PayrollPeriod.objects.all(), required=False)
    comment = serializers.CharField(required=False, allow_blank=True)

    def validate(self, attrs):
        action = self.context["action"]
        if action in {"approve", "defer"} and "payroll_period" not in attrs:
            raise serializers.ValidationError({"payroll_period": "Select a target payroll period."})
        if action == "hold" and not attrs.get("comment", "").strip():
            raise serializers.ValidationError({"comment": "A reason is required to hold a PPE deduction."})
        return attrs

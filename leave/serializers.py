from rest_framework import serializers

from employees.models import Employee
from leave.models import LeaveDuration, LeaveRequest, LeaveType


class LeaveTypeSerializer(serializers.ModelSerializer):
    class Meta:
        model = LeaveType
        fields = [
            "id",
            "name",
            "code",
            "description",
            "default_days",
            "requires_approval",
            "is_paid",
            "color",
        ]


class LeaveRequestCreateSerializer(serializers.Serializer):
    employee_id = serializers.PrimaryKeyRelatedField(
        source="employee",
        queryset=Employee.objects.all(),
    )
    leave_type_id = serializers.PrimaryKeyRelatedField(
        source="leave_type",
        queryset=LeaveType.objects.filter(is_active=True),
    )
    start_date = serializers.DateField()
    end_date = serializers.DateField()
    return_date = serializers.DateField(required=False, allow_null=True)
    duration_type = serializers.ChoiceField(
        choices=LeaveDuration.choices,
        required=False,
        default=LeaveDuration.FULL_DAY,
    )
    reason = serializers.CharField()


class LeaveRequestSerializer(serializers.ModelSerializer):
    employee_id = serializers.CharField(source="employee.employee_id", read_only=True)
    employee_name = serializers.CharField(source="employee.full_name", read_only=True)
    department_name = serializers.CharField(
        source="employee.department.name",
        read_only=True,
        default=None,
    )
    leave_type_name = serializers.CharField(source="leave_type.name", read_only=True)
    duration_type_display = serializers.CharField(
        source="get_duration_type_display",
        read_only=True,
    )
    status_display = serializers.CharField(source="get_status_display", read_only=True)
    requested_by_name = serializers.SerializerMethodField()
    approved_by_name = serializers.SerializerMethodField()

    class Meta:
        model = LeaveRequest
        fields = [
            "id",
            "request_number",
            "employee",
            "employee_id",
            "employee_name",
            "department_name",
            "leave_type",
            "leave_type_name",
            "start_date",
            "end_date",
            "return_date",
            "duration_type",
            "duration_type_display",
            "total_days",
            "reason",
            "status",
            "status_display",
            "requested_by",
            "requested_by_name",
            "approved_by",
            "approved_by_name",
            "approved_at",
            "approved_start_date",
            "approved_end_date",
            "approved_days",
            "rejection_reason",
            "created_at",
            "updated_at",
        ]

    @staticmethod
    def _display_user_name(user):
        if not user:
            return None

        return user.get_full_name() or user.get_username()

    def get_requested_by_name(self, leave_request):
        return self._display_user_name(leave_request.requested_by)

    def get_approved_by_name(self, leave_request):
        return self._display_user_name(leave_request.approved_by)


class PartialLeaveApprovalSerializer(serializers.Serializer):
    approved_start_date = serializers.DateField()
    approved_end_date = serializers.DateField()


class LeaveRejectionSerializer(serializers.Serializer):
    rejection_reason = serializers.CharField()

from django.utils import timezone
from django.db.models import Q
from rest_framework import serializers

from .models import AttendanceEvent, AttendanceException, DailyAttendance, EmployeeRosterDay, OvertimeRecord, RosterDayStatus, Shift, ShiftAssignment


class AttendanceEventSerializer(serializers.ModelSerializer):
    employee = serializers.IntegerField(source="employee_id", read_only=True)
    employee_id = serializers.CharField(source="employee.employee_id", read_only=True)
    employee_name = serializers.CharField(source="employee.full_name", read_only=True)
    department = serializers.CharField(source="employee.department.name", read_only=True, default=None)
    device_name = serializers.CharField(source="device.name", read_only=True, default=None)
    device_serial_number = serializers.CharField(source="device.serial_number", read_only=True, default=None)

    class Meta:
        model = AttendanceEvent
        fields = (
            "id", "employee", "employee_id", "employee_name", "department",
            "device_name", "device_serial_number", "timestamp",
            "verification_type", "external_event_id",
        )
        read_only_fields = fields


class OvertimeRecordSerializer(serializers.ModelSerializer):
    employee_name = serializers.CharField(source="employee.full_name", read_only=True)
    employee_id = serializers.CharField(source="employee.employee_id", read_only=True)
    department = serializers.CharField(source="employee.department.name", read_only=True, default=None)
    shift_name = serializers.CharField(source="shift.name", read_only=True)
    reviewed_by_name = serializers.CharField(source="reviewed_by.username", read_only=True, default=None)

    class Meta:
        model = OvertimeRecord
        fields = "__all__"
        read_only_fields = "__all__"


class OvertimeApprovalSerializer(serializers.Serializer):
    approved_overtime_minutes = serializers.IntegerField(required=False, min_value=1)
    comment = serializers.CharField(required=False, allow_blank=True)


class OvertimeRejectionSerializer(serializers.Serializer):
    comment = serializers.CharField()

    def validate_comment(self, value):
        if not value.strip():
            raise serializers.ValidationError("A rejection reason is required.")
        return value


class OvertimePaymentSerializer(serializers.Serializer):
    payment_note = serializers.CharField(required=False, allow_blank=True)


class DailyAttendanceSerializer(serializers.ModelSerializer):
    employee_id = serializers.CharField(
        source="employee.employee_id",
        read_only=True,
    )

    employee_name = serializers.CharField(
        source="employee.full_name",
        read_only=True,
    )

    shift_name = serializers.CharField(
        source="shift.name",
        read_only=True,
    )

    class Meta:
        model = DailyAttendance

        fields = [
            "id",
            "employee_id",
            "employee_name",
            "date",
            "shift_name",
            "scheduled_start",
            "scheduled_end",
            "actual_clock_in",
            "actual_clock_out",
            "late_minutes",
            "early_departure_minutes",
            "worked_minutes",
            "overtime_minutes",
            "status",
        ]


class EmployeeAttendanceHistorySerializer(DailyAttendanceSerializer):
    """Adds the operational conflict state without changing stored facts."""

    operational_status = serializers.SerializerMethodField()

    class Meta(DailyAttendanceSerializer.Meta):
        fields = DailyAttendanceSerializer.Meta.fields + ["operational_status"]

    def get_operational_status(self, attendance):
        if getattr(attendance, "leave_punch_conflicts", []):
            return "leave_punch_conflict"
        return attendance.status


class AttendanceExceptionSerializer(serializers.ModelSerializer):
    employee_id = serializers.CharField(
        source="attendance.employee.employee_id",
        read_only=True,
    )

    employee_name = serializers.CharField(
        source="attendance.employee.full_name",
        read_only=True,
    )

    department = serializers.CharField(
        source="attendance.employee.department.name",
        read_only=True,
        allow_null=True,
    )

    attendance_date = serializers.DateField(
        source="attendance.date",
        read_only=True,
    )

    shift_name = serializers.CharField(
        source="attendance.shift.name",
        read_only=True,
        allow_null=True,
    )

    scheduled_start = serializers.DateTimeField(
        source="attendance.scheduled_start",
        read_only=True,
    )

    actual_clock_in = serializers.DateTimeField(
        source="attendance.actual_clock_in",
        read_only=True,
    )

    scheduled_end = serializers.DateTimeField(
        source="attendance.scheduled_end",
        read_only=True,
    )

    actual_clock_out = serializers.DateTimeField(
        source="attendance.actual_clock_out",
        read_only=True,
    )

    class Meta:
        model = AttendanceException

        fields = [
            "id",

            "employee_id",
            "employee_name",
            "department",

            "attendance_date",
            "shift_name",

            "scheduled_start",
            "actual_clock_in",
            "scheduled_end",
            "actual_clock_out",

            "exception_type",
            "minutes_affected",
            "proposed_deduction",

            "status",

            "employee_reason",
            "supervisor_comment",
            "admin_comment",

            "reviewed_by",
            "reviewed_at",
            "created_at",
        ]

        read_only_fields = [
            "status",
            "reviewed_by",
            "reviewed_at",
        ]


class ExceptionDecisionSerializer(serializers.Serializer):

    DECISIONS = [
        ("approved", "Approve Deduction"),
        ("waived", "Waive Deduction"),
        ("held", "Hold / Investigate"),
    ]

    decision = serializers.ChoiceField(
        choices=DECISIONS,
    )

    comment = serializers.CharField(
        required=False,
        allow_blank=True,
    )

    def validate(self, attrs):
        if self.instance and self.instance.status != "pending":
            raise serializers.ValidationError(
                "This attendance exception has already been reviewed."
            )

        decision = attrs["decision"]
        comment = attrs.get("comment", "").strip()

        if decision in ["waived", "held"] and not comment:
            raise serializers.ValidationError({
                "comment":
                    "A reason is required when waiving or holding an exception."
            })

        return attrs

    def update(self, instance, validated_data):

        decision = validated_data["decision"]
        comment = validated_data.get(
            "comment",
            "",
        )

        request = self.context["request"]

        instance.status = decision
        instance.admin_comment = comment
        instance.reviewed_at = timezone.now()

        if request.user.get_full_name():
            instance.reviewed_by = request.user.get_full_name()
        else:
            instance.reviewed_by = request.user.username

        instance.save()

        return instance

    def create(self, validated_data):
        raise NotImplementedError


class ShiftSerializer(serializers.ModelSerializer):
    class Meta:
        model = Shift
        fields = [
            "id",
            "name",
            "start_time",
            "end_time",
            "is_overnight",
            "active",
        ]


class ShiftAssignmentSerializer(serializers.ModelSerializer):
    employee_name = serializers.CharField(source="employee.full_name", read_only=True)
    employee_number = serializers.CharField(source="employee.employee_id", read_only=True)
    department_name = serializers.CharField(
        source="employee.department.name",
        read_only=True,
        default=None,
    )
    shift_name = serializers.CharField(source="shift.name", read_only=True)
    shift_start_time = serializers.TimeField(source="shift.start_time", read_only=True)
    shift_end_time = serializers.TimeField(source="shift.end_time", read_only=True)
    is_overnight = serializers.BooleanField(source="shift.is_overnight", read_only=True)
    status = serializers.SerializerMethodField()

    class Meta:
        model = ShiftAssignment
        fields = [
            "id",
            "employee",
            "employee_name",
            "employee_number",
            "department_name",
            "shift",
            "shift_name",
            "shift_start_time",
            "shift_end_time",
            "is_overnight",
            "start_date",
            "end_date",
            "assigned_by",
            "created_at",
            "status",
        ]
        read_only_fields = ["assigned_by", "created_at"]

    def validate_employee(self, employee):
        if self.instance and employee != self.instance.employee:
            raise serializers.ValidationError("An assignment cannot be moved to another employee.")
        return employee

    def validate(self, attrs):
        employee = attrs.get("employee", self.instance.employee if self.instance else None)
        start_date = attrs.get("start_date", self.instance.start_date if self.instance else None)
        end_date = attrs.get("end_date", self.instance.end_date if self.instance else None)

        if end_date and end_date < start_date:
            raise serializers.ValidationError(
                {"end_date": "The assignment end date cannot be before its start date."}
            )

        assignments = ShiftAssignment.objects.filter(employee=employee)
        if self.instance:
            assignments = assignments.exclude(pk=self.instance.pk)
        if end_date:
            assignments = assignments.filter(start_date__lte=end_date)
        assignments = assignments.filter(
            Q(end_date__isnull=True) | Q(end_date__gte=start_date)
        )
        if assignments.exists():
            raise serializers.ValidationError(
                {"non_field_errors": "This assignment overlaps an existing shift assignment."}
            )

        return attrs

    @staticmethod
    def get_status(assignment):
        today = timezone.localdate()
        if assignment.start_date > today:
            return "upcoming"
        if assignment.end_date and assignment.end_date < today:
            return "ended"
        return "active"


class ShiftChangeSerializer(serializers.Serializer):
    employee = serializers.IntegerField()
    shift = serializers.PrimaryKeyRelatedField(queryset=Shift.objects.filter(active=True))
    start_date = serializers.DateField()

    def validate_employee(self, employee_id):
        from employees.models import Employee

        if not Employee.objects.filter(pk=employee_id).exists():
            raise serializers.ValidationError("Employee not found.")
        return employee_id


class EmployeeRosterDaySerializer(serializers.ModelSerializer):
    employee_name = serializers.CharField(source="employee.full_name", read_only=True)
    employee_number = serializers.CharField(source="employee.employee_id", read_only=True)
    shift_name = serializers.CharField(source="shift.name", read_only=True, default=None)
    updated_by_name = serializers.SerializerMethodField()

    class Meta:
        model = EmployeeRosterDay
        fields = ["id", "employee", "employee_name", "employee_number", "date", "status", "shift", "shift_name", "source", "notes", "created_at", "updated_at", "updated_by_name"]
        read_only_fields = ["source", "created_at", "updated_at", "updated_by_name"]

    def get_updated_by_name(self, instance):
        if not instance.updated_by:
            return None
        return instance.updated_by.get_full_name() or instance.updated_by.username

    def validate(self, attrs):
        status = attrs.get("status", self.instance.status if self.instance else None)
        shift = attrs.get("shift", self.instance.shift if self.instance else None)
        if status == RosterDayStatus.WORK and shift is None:
            raise serializers.ValidationError({"shift": "A shift is required for a scheduled work day."})
        if status == RosterDayStatus.REST and shift is not None:
            raise serializers.ValidationError({"shift": "A rest day cannot have a working shift."})
        return attrs


class RosterGenerationSerializer(serializers.Serializer):
    employee = serializers.IntegerField()
    start_date = serializers.DateField()
    end_date = serializers.DateField()
    shift = serializers.PrimaryKeyRelatedField(queryset=Shift.objects.filter(active=True))
    work_days = serializers.IntegerField(min_value=1, max_value=31)
    rest_days = serializers.IntegerField(min_value=1, max_value=31)
    working_weekdays = serializers.ListField(
        child=serializers.IntegerField(min_value=0, max_value=6),
        required=False,
        allow_empty=False,
    )
    notes = serializers.CharField(required=False, allow_blank=True)

    def validate_employee(self, employee_id):
        from employees.models import Employee
        if not Employee.objects.filter(pk=employee_id).exists():
            raise serializers.ValidationError("Employee not found.")
        return employee_id

    def validate(self, attrs):
        if attrs["end_date"] < attrs["start_date"]:
            raise serializers.ValidationError({"end_date": "The roster end date cannot be before its start date."})
        if len(set(attrs.get("working_weekdays", []))) != len(attrs.get("working_weekdays", [])):
            raise serializers.ValidationError({"working_weekdays": "Weekdays must not be repeated."})
        return attrs


class RotationGenerationSerializer(serializers.Serializer):
    employee = serializers.IntegerField()
    start_date = serializers.DateField()
    end_date = serializers.DateField()
    starting_shift = serializers.ChoiceField(choices=("day", "night"))
    day_shift = serializers.PrimaryKeyRelatedField(queryset=Shift.objects.filter(active=True))
    night_shift = serializers.PrimaryKeyRelatedField(queryset=Shift.objects.filter(active=True))
    notes = serializers.CharField(required=False, allow_blank=True)

    def validate_employee(self, employee_id):
        from employees.models import Employee
        if not Employee.objects.filter(pk=employee_id).exists():
            raise serializers.ValidationError("Employee not found.")
        return employee_id

    def validate(self, attrs):
        if attrs["end_date"] < attrs["start_date"]:
            raise serializers.ValidationError({"end_date": "The roster end date cannot be before its start date."})
        if attrs["day_shift"] == attrs["night_shift"]:
            raise serializers.ValidationError("Day and Night shifts must be different.")
        return attrs


class RosterOverrideSerializer(serializers.Serializer):
    employee = serializers.IntegerField()
    date = serializers.DateField()
    status = serializers.ChoiceField(choices=RosterDayStatus.choices)
    shift = serializers.PrimaryKeyRelatedField(queryset=Shift.objects.filter(active=True), required=False, allow_null=True)
    notes = serializers.CharField(required=False, allow_blank=True)

    def validate_employee(self, employee_id):
        from employees.models import Employee
        if not Employee.objects.filter(pk=employee_id).exists():
            raise serializers.ValidationError("Employee not found.")
        return employee_id

    def validate(self, attrs):
        if attrs["status"] == RosterDayStatus.WORK and attrs.get("shift") is None:
            raise serializers.ValidationError({"shift": "A shift is required for a scheduled work day."})
        if attrs["status"] == RosterDayStatus.REST and attrs.get("shift") is not None:
            raise serializers.ValidationError({"shift": "A rest day cannot have a working shift."})
        return attrs

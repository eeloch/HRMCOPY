

# Create your models here.
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from employees.models import Employee


class Shift(models.Model):

    name = models.CharField(
        max_length=100,
        unique=True,
    )

    start_time = models.TimeField()
    end_time = models.TimeField()

    is_overnight = models.BooleanField(
        default=False,
    )

    active = models.BooleanField(
        default=True,
    )

    def __str__(self):
        return self.name


class ShiftAssignment(models.Model):

    employee = models.ForeignKey(
        Employee,
        on_delete=models.CASCADE,
        related_name="shift_assignments",
    )

    shift = models.ForeignKey(
        Shift,
        on_delete=models.PROTECT,
        related_name="assignments",
    )

    start_date = models.DateField()

    end_date = models.DateField(
        null=True,
        blank=True,
    )

    assigned_by = models.CharField(
        max_length=150,
        blank=True,
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    class Meta:
        permissions = [("manage_shifts", "Can assign and change employee shifts")]

    def __str__(self):
        return f"{self.employee.employee_id} - {self.shift.name}"


class RosterDayStatus(models.TextChoices):
    WORK = "work", "Work"
    REST = "rest", "Rest"


class RosterDaySource(models.TextChoices):
    GENERATED = "generated", "Generated"
    MANUAL = "manual", "Manual"
    OVERRIDE = "override", "Manual override"


class EmployeeRosterDay(models.Model):
    """The authoritative date-level work/rest expectation for one employee."""

    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name="roster_days")
    date = models.DateField()
    status = models.CharField(max_length=12, choices=RosterDayStatus.choices)
    shift = models.ForeignKey(Shift, on_delete=models.PROTECT, null=True, blank=True, related_name="roster_days")
    source = models.CharField(max_length=12, choices=RosterDaySource.choices, default=RosterDaySource.MANUAL)
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="created_roster_days")
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="updated_roster_days")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["date", "employee__last_name", "employee__first_name"]
        constraints = [
            models.UniqueConstraint(fields=["employee", "date"], name="unique_employee_roster_day"),
            models.CheckConstraint(
                condition=(Q(status=RosterDayStatus.WORK, shift__isnull=False) | Q(status=RosterDayStatus.REST, shift__isnull=True)),
                name="roster_day_status_matches_shift",
            ),
        ]
        permissions = [("manage_roster", "Can manage employee work rosters")]

    def clean(self):
        if self.status == RosterDayStatus.WORK and self.shift_id is None:
            raise ValidationError({"shift": "A shift is required for a scheduled work day."})
        if self.status == RosterDayStatus.REST and self.shift_id is not None:
            raise ValidationError({"shift": "A rest day cannot have a working shift."})

    def __str__(self):
        return f"{self.employee.employee_id} - {self.date} ({self.status})"

class BiometricDevice(models.Model):

    DEVICE_TYPES = [
        ("factory", "Factory"),
        ("hostel", "Hostel"),
        ("office", "Office"),
    ]

    name = models.CharField(
        max_length=100,
    )

    serial_number = models.CharField(
        max_length=100,
        unique=True,
    )

    model = models.CharField(
        max_length=100,
        blank=True,
    )

    location = models.CharField(
        max_length=200,
    )

    device_type = models.CharField(
        max_length=20,
        choices=DEVICE_TYPES,
    )

    ip_address = models.GenericIPAddressField(
        null=True,
        blank=True,
    )

    is_online = models.BooleanField(
        default=False,
    )

    last_sync_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    def __str__(self):
        return f"{self.name} - {self.serial_number}"


class AttendanceEvent(models.Model):

    VERIFICATION_TYPES = [
        ("face", "Face"),
        ("fingerprint", "Fingerprint"),
        ("card", "Card"),
        ("manual", "Manual"),
        ("unknown", "Unknown"),
    ]

    employee = models.ForeignKey(
        Employee,
        on_delete=models.CASCADE,
        related_name="attendance_events",
    )

    device = models.ForeignKey(
        BiometricDevice,
        on_delete=models.SET_NULL,
        null=True,
        related_name="attendance_events",
    )

    timestamp = models.DateTimeField()

    verification_type = models.CharField(
        max_length=20,
        choices=VERIFICATION_TYPES,
        default="unknown",
    )

    external_event_id = models.CharField(
        max_length=150,
        blank=True,
        null=True,
    )

    raw_payload = models.JSONField(
        default=dict,
        blank=True,
    )

    imported_at = models.DateTimeField(
        auto_now_add=True,
    )

    class Meta:
        ordering = ["timestamp"]

        constraints = [
            models.UniqueConstraint(
                fields=[
                    "device",
                    "external_event_id",
                ],
                name="unique_device_external_event",
            )
        ]

    def __str__(self):
        return f"{self.employee.employee_id} - {self.timestamp}"  


class DailyAttendance(models.Model):

    STATUS_CHOICES = [
        ("present", "Present"),
        ("late", "Late"),
        ("absent", "Absent"),
        ("leave", "Leave"),
        ("incomplete", "Incomplete"),
    ]

    employee = models.ForeignKey(
        Employee,
        on_delete=models.CASCADE,
        related_name="daily_attendance",
    )

    date = models.DateField()

    shift = models.ForeignKey(
        Shift,
        on_delete=models.SET_NULL,
        null=True,
    )

    scheduled_start = models.DateTimeField(
        null=True,
        blank=True,
    )

    scheduled_end = models.DateTimeField(
        null=True,
        blank=True,
    )

    actual_clock_in = models.DateTimeField(
        null=True,
        blank=True,
    )

    actual_clock_out = models.DateTimeField(
        null=True,
        blank=True,
    )

    late_minutes = models.PositiveIntegerField(
        default=0,
    )

    early_departure_minutes = models.PositiveIntegerField(
        default=0,
    )

    worked_minutes = models.PositiveIntegerField(
        default=0,
    )

    overtime_minutes = models.PositiveIntegerField(
        default=0,
    )

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="present",
    )

    processed_at = models.DateTimeField(
        auto_now=True,
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=[
                    "employee",
                    "date",
                ],
                name="unique_daily_employee_attendance",
            )
        ]

    def __str__(self):
        return f"{self.employee.employee_id} - {self.date}"

class AttendanceException(models.Model):

    EXCEPTION_TYPES = [
        ("late", "Late Arrival"),
        ("early_departure", "Early Departure"),
        ("absence", "Absence"),
        ("missing_clock_in", "Missing Clock In"),
        ("missing_clock_out", "Missing Clock Out"),
        ("hostel_violation", "Hostel During Shift"),
        ("leave_punch_conflict", "Punch while on approved leave"),
    ]

    STATUS_CHOICES = [
        ("pending", "Pending"),
        ("approved", "Approved"),
        ("waived", "Waived"),
        ("held", "Held"),
    ]

    attendance = models.ForeignKey(
        DailyAttendance,
        on_delete=models.CASCADE,
        related_name="exceptions",
    )

    exception_type = models.CharField(
        max_length=30,
        choices=EXCEPTION_TYPES,
    )

    minutes_affected = models.PositiveIntegerField(
        default=0,
    )

    proposed_deduction = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0,
    )

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="pending",
    )

    employee_reason = models.TextField(
        blank=True,
    )

    supervisor_comment = models.TextField(
        blank=True,
    )

    admin_comment = models.TextField(
        blank=True,
    )

    reviewed_by = models.CharField(
        max_length=150,
        blank=True,
    )

    reviewed_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    class Meta:
        permissions = [
            ("review_attendanceexception", "Can review attendance exceptions"),
        ]

    def __str__(self):
        return (
            f"{self.attendance.employee.employee_id} "
            f"- {self.exception_type}"
        )


class OvertimeStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    APPROVED = "approved", "Approved"
    REJECTED = "rejected", "Rejected"


class OvertimePaymentStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    PAID = "paid", "Paid"


class OvertimeRecord(models.Model):
    """A reviewed payment obligation derived from, but separate from, attendance facts."""

    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="overtime_records")
    attendance = models.OneToOneField(DailyAttendance, on_delete=models.PROTECT, related_name="overtime_record")
    shift = models.ForeignKey(Shift, on_delete=models.PROTECT, related_name="overtime_records")
    work_date = models.DateField()
    scheduled_end = models.DateTimeField()
    actual_clock_out = models.DateTimeField()
    threshold_minutes_snapshot = models.PositiveIntegerField()
    potential_overtime_minutes = models.PositiveIntegerField()
    approved_overtime_minutes = models.PositiveIntegerField(null=True, blank=True)
    status = models.CharField(max_length=12, choices=OvertimeStatus.choices, default=OvertimeStatus.PENDING)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="reviewed_overtime_records")
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_comment = models.TextField(blank=True)
    basic_salary_snapshot = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    roster_work_days_snapshot = models.PositiveIntegerField(null=True, blank=True)
    daily_rate_snapshot = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    scheduled_shift_minutes_snapshot = models.PositiveIntegerField(null=True, blank=True)
    normal_hourly_rate_snapshot = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    overtime_multiplier_snapshot = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    overtime_hourly_rate_snapshot = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    payable_amount = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    payment_due_date = models.DateField()
    payment_status = models.CharField(max_length=12, choices=OvertimePaymentStatus.choices, default=OvertimePaymentStatus.PENDING)
    paid_at = models.DateTimeField(null=True, blank=True)
    paid_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="paid_overtime_records")
    payment_note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-work_date", "employee__last_name", "employee__first_name"]
        permissions = [("review_overtime", "Can review overtime records")]

    def __str__(self):
        return f"{self.employee.employee_id} - overtime {self.work_date}"

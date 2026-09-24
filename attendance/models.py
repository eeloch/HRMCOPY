

# Create your models here.
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone
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


class ShiftPlan(models.Model):
    """How a group of people work across the weeks, so nobody has to be moved by hand each week.

    fixed    - always the same shift on the chosen weekdays (Permanent Day, Permanent Night, Admin...).
    rotation - the Rotic weekly Day/Night rotation. Group A is on Day in the anchor week and on Night the week
               after, and so on; Group B is the opposite. In a Day week people work Monday to Saturday on Day and
               start the Night shift on Sunday 19:00; in a Night week they work Monday to Saturday nights and rest
               on Sunday (Sunday 07:00 to Monday 07:00), then they are back on Day.
    """

    KIND_CHOICES = [("fixed", "Fixed shift"), ("rotation", "Weekly Day / Night rotation")]

    name = models.CharField(max_length=120, unique=True)
    kind = models.CharField(max_length=20, choices=KIND_CHOICES)
    description = models.CharField(max_length=255, blank=True)
    shift = models.ForeignKey(Shift, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    working_weekdays = models.JSONField(default=list, blank=True)  # 0 = Monday ... 6 = Sunday (fixed plans)
    day_shift = models.ForeignKey(Shift, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    night_shift = models.ForeignKey(Shift, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    anchor_monday = models.DateField(null=True, blank=True)
    active = models.BooleanField(default=True)

    def __str__(self):
        return self.name


class ShiftPlanAssignment(models.Model):
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name="shift_plan_assignments")
    plan = models.ForeignKey(ShiftPlan, on_delete=models.PROTECT, related_name="assignments")
    group = models.CharField(max_length=1, blank=True)  # "A" or "B" for a rotation plan
    start_date = models.DateField()
    end_date = models.DateField(null=True, blank=True)
    assigned_by = models.CharField(max_length=150, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-start_date", "-id"]


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

    PURPOSES = [
        ("attendance", "Attendance"),
        ("meal_ticket", "Meal Ticket"),
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

    purpose = models.CharField(
        max_length=20,
        choices=PURPOSES,
        default="attendance",
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

    # The terminals close and reopen their connection about every 30s on their own, so the connected flag
    # drops for a few seconds at a time; treating that as "offline" made every device look like it kept
    # going down. A device is only reported offline once it has been unseen for longer than this.
    ONLINE_GRACE = timedelta(seconds=120)

    class Meta:
        permissions = [("manage_devices", "Can register and manage biometric devices")]

    def __str__(self):
        return f"{self.name} - {self.serial_number}"

    @property
    def is_reachable(self):
        if self.is_online:
            return True
        return self.last_sync_at is not None and timezone.now() - self.last_sync_at <= self.ONLINE_GRACE

    def free_enrollid(self, preferred=None):
        """The user id to enroll someone under on this terminal.

        Ids here are staff numbers (terminals were enrolled that way and the
        reconcile step relies on it), so `preferred` - normally the employee's
        numeric staff number, or their id on the device they were cloned from -
        is used whenever it's free. Free means neither linked to anyone in HRM
        nor present in the id list the terminal itself last reported, since
        enrolling onto a taken id would overwrite whoever owns it. Otherwise
        falls back to one past the highest known id.
        """
        from attendance.integrations.aiface_protocol import IDENTITY_SYSTEM
        from employees.models import BiometricIdentity

        taken = {
            str(value) for value in BiometricIdentity.objects.filter(
                system=IDENTITY_SYSTEM, source_identifier=self.serial_number,
            ).values_list("external_user_id", flat=True)
        }
        latest = self.commands.filter(command_type="refresh_enrolled_ids", status="acked").order_by("-id").first()
        if latest:
            taken |= {str(value) for value in (latest.result.get("record") or [])}
        if preferred is not None and preferred > 0 and str(preferred) not in taken:
            return preferred
        return max((int(value) for value in taken if value.isdigit()), default=0) + 1


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


class DeviceCommand(models.Model):
    """An outbox row the AiFace gateway polls and delivers to a connected terminal.

    Only one command may be in flight per device at a time - the AiFace
    protocol itself requires commands be sent one-by-one, waiting for the
    device's response before the next is sent (see the vendor's own
    Notes.txt). The gateway enforces that by only picking up a device's next
    pending command once its previous one reaches "acked" or "failed".
    """

    COMMAND_TYPES = [
        ("enroll_user", "Enroll User"),
        ("delete_user", "Delete User"),
        ("refresh_enrolled_ids", "Refresh Enrolled IDs"),
        ("clone_enrollment", "Clone Enrollment To Other Devices"),
        ("purge_user", "Remove Inactive User From Device"),
        ("set_user_enabled", "Enable Or Disable User On Terminal"),
        ("list_user_slots", "List Every Enrolled Slot On Terminal"),
    ]

    # Bulk background jobs: they must neither lock a device's admin panel while a
    # big backlog drains nor be served ahead of an admin's own command.
    BACKGROUND_TYPES = ("clone_enrollment", "purge_user", "list_user_slots")

    STATUSES = [
        ("pending", "Pending"),
        ("sent", "Sent"),
        ("acked", "Acknowledged"),
        ("failed", "Failed"),
    ]

    device = models.ForeignKey(BiometricDevice, on_delete=models.CASCADE, related_name="commands")
    command_type = models.CharField(max_length=30, choices=COMMAND_TYPES)
    payload = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=20, choices=STATUSES, default="pending")
    result = models.JSONField(default=dict, blank=True)
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    # The device-commands UI itself gives up polling after 45s (see
    # DeviceCommandPanel.tsx's POLL_TIMEOUT_MS) - keep this comfortably above
    # that so a genuinely slow-but-working round trip isn't killed early, but
    # close enough that a retry right after the UI times out isn't still
    # blocked by the very row that just failed.
    STALE_AFTER = timedelta(seconds=60)
    SWITCH_MAX_ATTEMPTS = 6
    SWITCH_STALE_AFTER = timedelta(seconds=10)  # a one-line switch that isn't answered quickly was lost in a reconnect
    CLONE_STALE_AFTER = timedelta(minutes=5)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return f"{self.command_type} -> {self.device.serial_number} ({self.status})"

    @classmethod
    def expire_stale(cls, device=None):
        """Fail any "sent" command whose device round-trip never came back.

        A command reaches "sent" once the gateway has pushed it to the
        device's live connection - if the device then disconnects (e.g. the
        reconnect cycle some terminals do) before replying, nothing ever
        marks it "acked"/"failed", and it blocks this device's one-command-
        at-a-time queue forever. "pending" commands are left alone since
        they're legitimately waiting for the device to come online.

        "clone_enrollment" gets the longer CLONE_STALE_AFTER instead: a relay
        legitimately runs past STALE_AFTER (a getuserinfo round trip plus one
        setuserinfo per target), but must still be bounded, or a relay cut off
        by a dropped connection would block that device's queue forever.
        """
        now = timezone.now()
        # A switch on/off that was lost (the terminal reconnected before answering) is retried at once, a few times,
        # instead of being left for the next 5-minute check.
        lost = cls.objects.filter(status="sent", command_type="set_user_enabled", sent_at__lt=now - cls.SWITCH_STALE_AFTER)
        if device is not None:
            lost = lost.filter(device=device)
        for command in lost:
            attempts = int(command.payload.get("attempts", 0)) + 1
            if attempts >= cls.SWITCH_MAX_ATTEMPTS:
                cls.objects.filter(pk=command.pk).update(status="failed", result={"detail": f"No answer from the device after {attempts} tries."}, completed_at=now)
            else:
                cls.objects.filter(pk=command.pk).update(status="pending", sent_at=None, payload={**command.payload, "attempts": attempts})
        stale = cls.objects.filter(status="sent").filter(
            (~Q(command_type__in=["clone_enrollment", "set_user_enabled", "list_user_slots"]) & Q(sent_at__lt=now - cls.STALE_AFTER))
            | Q(command_type__in=["clone_enrollment", "list_user_slots"], sent_at__lt=now - cls.CLONE_STALE_AFTER)
        )
        if device is not None:
            stale = stale.filter(device=device)
        stale.update(status="failed", result={"detail": "Timed out waiting for the device to respond."}, completed_at=timezone.now())

from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models

from employees.models import Employee, EmploymentCategory, EmploymentType, Position
from attendance.models import Shift
from payroll.models import EmployeePayroll, PayrollLineItem, PayrollPeriod


class MealDevice(models.Model):
    name = models.CharField(max_length=100)
    serial_number = models.CharField(max_length=100, unique=True)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta: ordering = ["name"]
    def __str__(self): return f"{self.name} - {self.serial_number}"


class MealTicketRate(models.Model):
    amount = models.DecimalField(max_digits=12, decimal_places=2, validators=[MinValueValidator(Decimal("0.00"))])
    effective_from = models.DateField()
    effective_to = models.DateField(null=True, blank=True)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    class Meta: ordering = ["-effective_from", "-id"]
    def __str__(self): return f"N {self.amount:,.2f} from {self.effective_from}"


class MealEntitlementRule(models.Model):
    employment_type = models.CharField(max_length=20, choices=EmploymentType.choices, blank=True)
    employment_category = models.CharField(max_length=20, choices=EmploymentCategory.choices, blank=True)
    position = models.ForeignKey(Position, on_delete=models.SET_NULL, null=True, blank=True, related_name="meal_entitlement_rules")
    minimum_years_of_service = models.PositiveIntegerField(null=True, blank=True)
    minimum_months_of_service = models.PositiveIntegerField(null=True, blank=True)
    tickets_per_work_day = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    priority = models.PositiveIntegerField(default=100)
    description = models.CharField(max_length=255, blank=True)
    active = models.BooleanField(default=True)
    class Meta: ordering = ["priority", "id"]
    def __str__(self): return self.description or f"{self.tickets_per_work_day} ticket(s)"


class EmployeeMealEntitlement(models.Model):
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="meal_entitlements")
    tickets_per_work_day = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    effective_from = models.DateField()
    effective_to = models.DateField(null=True, blank=True)
    reason = models.TextField()
    is_exceptional_override = models.BooleanField(default=False)
    set_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="meal_entitlements_set")
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta: ordering = ["-effective_from", "-id"]
    def __str__(self): return f"{self.employee.employee_id} {self.employee.full_name} - {self.tickets_per_work_day} ticket(s)/day from {self.effective_from}"


class MealEvent(models.Model):
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="meal_events")
    device = models.ForeignKey(MealDevice, on_delete=models.PROTECT, related_name="events")
    timestamp = models.DateTimeField()
    external_event_id = models.CharField(max_length=150)
    verification_type = models.CharField(max_length=20, default="unknown")
    source_system = models.CharField(max_length=50)
    raw_payload = models.JSONField(default=dict, blank=True)
    imported_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        ordering = ["-timestamp", "-id"]
        constraints = [models.UniqueConstraint(fields=["device", "external_event_id"], name="unique_meal_device_event")]
    def __str__(self): return f"{self.employee.employee_id} {self.employee.full_name} - {self.timestamp:%Y-%m-%d %H:%M} - {self.device.name}"


class MealCollectionStatus(models.TextChoices):
    WITHIN = "within_entitlement", "Within entitlement"
    EXCESS = "excess", "Excess"
    REST_DAY = "rest_day", "Not entitled - rest day"


class MealCollection(models.Model):
    event = models.OneToOneField(MealEvent, on_delete=models.PROTECT, related_name="collection")
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="meal_collections")
    work_date = models.DateField()
    shift = models.ForeignKey(Shift, on_delete=models.SET_NULL, null=True, blank=True)
    sequence_number = models.PositiveIntegerField()
    entitlement_snapshot = models.PositiveIntegerField(default=0)
    rate_snapshot = models.DecimalField(max_digits=12, decimal_places=2)
    status = models.CharField(max_length=30, choices=MealCollectionStatus.choices)
    created_at = models.DateTimeField(auto_now_add=True)
    # A voided ticket (test scan, accidental scan) stays on record for audit but no
    # longer counts: not toward the vendor's amount owed, the employee's daily
    # sequence, or their excess. See MealService.void_collection.
    # The excess decision this ticket belongs to (null for an entitled ticket). Each
    # ticket beyond the entitlement needs a decision of its own: it joins the day's
    # still-open one, or opens a new one - it never inherits an earlier decision.
    excess_exception = models.ForeignKey("MealExcessException", on_delete=models.SET_NULL, null=True, blank=True, related_name="tickets")
    voided_at = models.DateTimeField(null=True, blank=True)
    voided_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="meal_collections_voided")
    void_reason = models.TextField(blank=True)
    class Meta: ordering = ["-event__timestamp"]
    def __str__(self): return f"{self.employee.employee_id} {self.employee.full_name} - {self.work_date} - ticket {self.sequence_number} of {self.entitlement_snapshot}" + (" (voided)" if self.voided_at else "")


class MealExcessStatus(models.TextChoices):
    PENDING = "pending", "Pending review"
    APPROVED = "approved", "Approved"
    CANCELLED = "cancelled", "Cancelled"
    DEDUCTED = "deducted", "Deducted"
    DECLINED = "declined", "Declined"


class MealExcessException(models.Model):
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="meal_excess_exceptions")
    work_date = models.DateField()
    entitlement_snapshot = models.PositiveIntegerField()
    collected_quantity = models.PositiveIntegerField()
    excess_quantity = models.PositiveIntegerField()
    rate_snapshot = models.DecimalField(max_digits=12, decimal_places=2)
    proposed_deduction = models.DecimalField(max_digits=12, decimal_places=2)
    status = models.CharField(max_length=20, choices=MealExcessStatus.choices, default=MealExcessStatus.PENDING)
    reviewer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="meal_excess_reviewed")
    reviewed_at = models.DateTimeField(null=True, blank=True)
    comment = models.TextField(blank=True)
    payroll_period = models.ForeignKey(PayrollPeriod, on_delete=models.SET_NULL, null=True, blank=True)
    payroll = models.ForeignKey(EmployeePayroll, on_delete=models.SET_NULL, null=True, blank=True)
    payroll_line_item = models.OneToOneField(PayrollLineItem, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    class Meta:
        permissions = [("record_meal_operations", "Can record meal operations"), ("review_meal_excess", "Can review meal excess deductions"), ("manage_meal_configuration", "Can manage meal configuration")]
    def __str__(self): return f"{self.employee.employee_id} {self.employee.full_name} - {self.work_date} - {self.excess_quantity} extra - N {self.proposed_deduction:,.2f} ({self.get_status_display()})"


class MealAbsencePenaltyType(models.TextChoices):
    SINGLE_ABSENCE = "single_absence", "1-day absence penalty"
    TWO_CONSECUTIVE = "two_consecutive", "2 consecutive-day absence penalty"
    THREE_MONTHLY = "three_monthly", "3 absences in month - roster week penalty"


class MealAbsencePenaltyStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    ACTIVE = "active", "Active"
    COMPLETED = "completed", "Completed"
    CANCELLED = "cancelled", "Cancelled"


class MealAbsencePenalty(models.Model):
    employee = models.ForeignKey(
        Employee,
        on_delete=models.PROTECT,
        related_name="meal_absence_penalties",
    )

    penalty_type = models.CharField(
        max_length=30,
        choices=MealAbsencePenaltyType.choices,
    )

    source_absence_dates = models.JSONField(
        default=list,
        help_text="Attendance work dates that caused this meal penalty.",
    )

    tickets_to_reduce_per_work_day = models.PositiveIntegerField(
        default=1,
        validators=[MinValueValidator(1)],
    )

    work_days_to_apply = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text=(
            "Number of scheduled WORK days affected. "
            "May remain unset for a roster-week penalty until the roster is resolved."
        ),
    )


    target_work_dates = models.JSONField(
        default=list,
        blank=True,
        help_text=(
            "Exact scheduled WORK dates selected for this penalty. "
            "Once resolved, these dates form the fixed penalty window."
        ),
    )

    work_days_applied = models.PositiveIntegerField(default=0)

    status = models.CharField(
        max_length=20,
        choices=MealAbsencePenaltyStatus.choices,
        default=MealAbsencePenaltyStatus.PENDING,
    )

    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="meal_absence_penalties_approved",
    )

    approved_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["created_at", "id"]

    def __str__(self):
        return (
            f"{self.employee.employee_id} - "
            f"{self.get_penalty_type_display()}"
        )


class MealVendorPayment(models.Model):
    payroll_period = models.ForeignKey(PayrollPeriod, on_delete=models.PROTECT, related_name="meal_vendor_payments")
    amount = models.DecimalField(max_digits=14, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))])
    payment_date = models.DateField()
    reference = models.CharField(max_length=150, blank=True)
    notes = models.TextField(blank=True)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="meal_vendor_payments_recorded")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-payment_date", "-id"]

    def __str__(self):
        return f"{self.payroll_period} - N {self.amount:,.2f}"

class MealTerminalUserState(models.Model):
    """Whether a person is currently switched on at a meal terminal, as last confirmed by the terminal.
    Only people covered by MEAL_GATING_EMPLOYEE_IDS ever appear here."""

    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name="meal_terminal_states")
    device_serial = models.CharField(max_length=100)
    enabled = models.BooleanField()
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.employee.employee_id} {self.employee.full_name} - {self.device_serial} {'on' if self.enabled else 'off'}"

    class Meta:
        constraints = [models.UniqueConstraint(fields=["employee", "device_serial"], name="one_terminal_state_per_person_per_device")]


class MealExtraAuthorization(models.Model):
    """A supervisor lets someone collect extra ticket(s) today, beyond their entitlement, deciding in advance who pays.

    The person is switched on at the meal terminal for them (see meals.gating) and the extra ticket is decided
    the moment it is scanned: the employee pays (accepted - deducted from that month's pay) or the company
    pays (waived - no deduction, the vendor is still paid). Unused authorisations simply lapse at the end of the day.
    """

    PAYS_CHOICES = [("employee", "Employee pays (deducted from pay)"), ("company", "Company pays (no deduction)")]

    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="meal_extra_authorizations")
    work_date = models.DateField()
    quantity = models.PositiveSmallIntegerField()
    used = models.PositiveSmallIntegerField(default=0)
    pays = models.CharField(max_length=10, choices=PAYS_CHOICES)
    reason = models.CharField(max_length=255, blank=True)
    authorised_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"{self.employee.employee_id} {self.employee.full_name} - {self.quantity} extra on {self.work_date} ({self.pays} pays)"

    class Meta:
        ordering = ["-work_date", "-created_at"]

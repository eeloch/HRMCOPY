

# Create your models here.
from django.db import models


class Department(models.Model):
    name = models.CharField(max_length=120, unique=True)
    description = models.TextField(blank=True)
    required_staff = models.PositiveIntegerField(
        default=0,
        help_text="Normal number of employees required for daily operations."
    )


    def __str__(self):
        return self.name


class Position(models.Model):
    name = models.CharField(max_length=120)
    department = models.ForeignKey(
        Department,
        on_delete=models.CASCADE,
        related_name="positions",
    )

    def __str__(self):
        return f"{self.name} - {self.department.name}"


class EmploymentType(models.TextChoices):
    PERMANENT = "permanent", "Permanent"
    CONTRACT = "contract", "Contract"
    CASUAL = "casual", "Casual"
    INTERN = "intern", "Intern"
    NYSC = "nysc", "NYSC"
    EXPATRIATE = "expatriate", "Expatriate"


class EmploymentCategory(models.TextChoices):
    STAFF = "staff", "Staff"
    MANAGEMENT = "management", "Management"
    EXECUTIVE = "executive", "Executive"


class Employee(models.Model):

    EMPLOYMENT_STATUS = [
        ("active", "Active"),
        ("inactive", "Inactive"),
        ("suspended", "Suspended"),
        ("terminated", "Terminated"),
    ]

    employee_id = models.CharField(
        max_length=50,
        unique=True,
    )

    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)

    middle_name = models.CharField(
        max_length=100,
        blank=True,
    )

    department = models.ForeignKey(
        Department,
        on_delete=models.SET_NULL,
        null=True,
        related_name="employees",
    )

    position = models.ForeignKey(
        Position,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="employees",
    )

    phone = models.CharField(
        max_length=30,
        blank=True,
    )

    email = models.EmailField(
        blank=True,
    )

    date_of_birth = models.DateField(
        null=True,
        blank=True,
    )

    employment_date = models.DateField(
        null=True,
        blank=True,
    )

    employment_type = models.CharField(
        max_length=20,
        choices=EmploymentType.choices,
        default=EmploymentType.PERMANENT,
    )

    employment_category = models.CharField(
        max_length=20,
        choices=EmploymentCategory.choices,
        default=EmploymentCategory.STAFF,
    )

    basic_salary = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=0,
    )

    biometric_user_id = models.CharField(
        max_length=100,
        blank=True,
        null=True,
    )

    status = models.CharField(
        max_length=20,
        choices=EMPLOYMENT_STATUS,
        default="active",
    )

    lives_in_company_hostel = models.BooleanField(
        default=False,
    )

    hostel_room_number = models.CharField(
        max_length=50,
        blank=True,
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    updated_at = models.DateTimeField(
        auto_now=True,
    )

    @property
    def full_name(self):
        names = [
            self.first_name,
            self.middle_name,
            self.last_name,
        ]

        return " ".join(
            name for name in names if name
        )

    def __str__(self):
        return f"{self.employee_id} - {self.full_name}"

class BiometricIdentity(models.Model):

    SYSTEM_CHOICES = [
        ("yunatt", "Yunatt / TIMY"),
        ("device", "Direct Device"),
        ("vendor_flask_gateway", "Vendor Flask Gateway"),
        ("other", "Other"),
    ]

    employee = models.ForeignKey(
        Employee,
        on_delete=models.CASCADE,
        related_name="biometric_identities",
    )

    system = models.CharField(
        max_length=30,
        choices=SYSTEM_CHOICES,
        default="yunatt",
    )

    source_identifier = models.CharField(
        max_length=120,
        default="cloud",
        help_text=(
            "Use 'cloud' for Yunatt cloud IDs, "
            "or a device serial number for device-specific IDs."
        ),
    )

    external_user_id = models.CharField(
        max_length=120,
    )

    is_active = models.BooleanField(
        default=True,
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    updated_at = models.DateTimeField(
        auto_now=True,
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=[
                    "system",
                    "source_identifier",
                    "external_user_id",
                ],
                name="unique_biometric_identity",
            )
        ]

    def __str__(self):
        return (
            f"{self.employee.employee_id} - "
            f"{self.system} - "
            f"{self.external_user_id}"
        )

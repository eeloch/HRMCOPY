from django.conf import settings
from django.db import models

from employees.models import Employee


class DocumentType(models.TextChoices):
    APPOINTMENT = "appointment", "Appointment Letter"
    CONTRACT = "contract", "Employment Contract"
    NATIONAL_ID = "national_id", "National ID"
    PASSPORT = "passport", "International Passport"
    DRIVERS_LICENSE = "drivers_license", "Driver's Licence"
    PASSPORT_PHOTO = "passport_photo", "Passport Photograph"
    MEDICAL = "medical", "Medical Certificate"
    GUARANTOR = "guarantor", "Guarantor Form"
    CERTIFICATE = "certificate", "Certificate"
    PROMOTION = "promotion", "Promotion Letter"
    WARNING = "warning", "Warning Letter"
    TERMINATION = "termination", "Termination Letter"
    OTHER = "other", "Other"


def employee_document_path(instance, filename):
    return (
        f"employees/"
        f"{instance.employee.employee_id}/"
        f"{filename}"
    )


class EmployeeDocument(models.Model):

    employee = models.ForeignKey(
        Employee,
        on_delete=models.CASCADE,
        related_name="documents",
    )

    document_type = models.CharField(
        max_length=40,
        choices=DocumentType.choices,
    )

    title = models.CharField(
        max_length=255,
    )

    file = models.FileField(
        upload_to=employee_document_path,
    )

    description = models.TextField(
        blank=True,
    )

    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )

    uploaded_at = models.DateTimeField(
        auto_now_add=True,
    )

    expiry_date = models.DateField(
        null=True,
        blank=True,
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
        ordering = [
            "-uploaded_at",
        ]
        permissions = [
            ("view_documents", "Can view employee documents"),
            ("manage_documents", "Can upload and delete employee documents"),
        ]

    def __str__(self):
        return (
            f"{self.employee.full_name} - "
            f"{self.title}"
        )
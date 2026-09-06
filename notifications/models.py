from django.conf import settings
from django.db import models

from employees.models import Employee


class NotificationSeverity(models.TextChoices):
    INFO = "info", "Info"
    SUCCESS = "success", "Success"
    WARNING = "warning", "Warning"
    ERROR = "error", "Error"


class Notification(models.Model):
    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notifications",
    )
    event_type = models.CharField(max_length=100)
    title = models.CharField(max_length=255)
    message = models.TextField()
    severity = models.CharField(
        max_length=20,
        choices=NotificationSeverity.choices,
        default=NotificationSeverity.INFO,
    )
    employee = models.ForeignKey(
        Employee,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="notifications",
    )
    related_url = models.CharField(max_length=500, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    is_read = models.BooleanField(default=False)
    read_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["recipient", "is_read"]),
            models.Index(fields=["recipient", "created_at"]),
        ]

    def __str__(self):
        return f"{self.recipient}: {self.title}"

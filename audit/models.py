from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models

from employees.models import Employee


class AuditSeverity(models.TextChoices):
    INFO = "info", "Info"
    SUCCESS = "success", "Success"
    WARNING = "warning", "Warning"
    ERROR = "error", "Error"


class AuditEvent(models.Model):
    event_type = models.CharField(max_length=100)
    module = models.CharField(max_length=50)
    employee = models.ForeignKey(
        Employee,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_events",
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_events",
    )
    content_type = models.ForeignKey(
        ContentType,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    object_id = models.PositiveBigIntegerField(null=True, blank=True)
    content_object = GenericForeignKey("content_type", "object_id")
    severity = models.CharField(
        max_length=20,
        choices=AuditSeverity.choices,
        default=AuditSeverity.INFO,
    )
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["module", "event_type"]),
            models.Index(fields=["created_at"]),
        ]

    def __str__(self):
        return f"{self.event_type}: {self.title}"

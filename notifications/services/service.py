from django.utils import timezone

from notifications.models import Notification, NotificationSeverity


class NotificationService:
    """Create and manage recipient-owned in-app notifications."""

    @staticmethod
    def create(
        *,
        recipient,
        event_type,
        title,
        message,
        severity=NotificationSeverity.INFO,
        employee=None,
        related_url="",
        metadata=None,
    ):
        return Notification.objects.create(
            recipient=recipient,
            event_type=event_type,
            title=title,
            message=message,
            severity=severity,
            employee=employee,
            related_url=related_url,
            metadata=metadata or {},
        )

    @staticmethod
    def mark_read(notification, user):
        if notification.recipient_id != user.id:
            return False

        if not notification.is_read:
            notification.is_read = True
            notification.read_at = timezone.now()
            notification.save(update_fields=["is_read", "read_at"])

        return True

    @staticmethod
    def mark_all_read(user):
        return Notification.objects.filter(
            recipient=user,
            is_read=False,
        ).update(
            is_read=True,
            read_at=timezone.now(),
        )

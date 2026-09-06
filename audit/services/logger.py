from django.contrib.contenttypes.models import ContentType

from audit.models import AuditEvent, AuditSeverity


class AuditService:
    """Create generic, module-agnostic audit records."""

    @staticmethod
    def log(
        *,
        event_type,
        module,
        employee=None,
        actor=None,
        object=None,
        severity=AuditSeverity.INFO,
        title="",
        description="",
        metadata=None,
    ):
        content_type = None
        object_id = None

        if object is not None:
            content_type = ContentType.objects.get_for_model(
                object,
                for_concrete_model=False,
            )
            object_id = object.pk

        return AuditEvent.objects.create(
            event_type=event_type,
            module=module,
            employee=employee,
            actor=actor,
            content_type=content_type,
            object_id=object_id,
            severity=severity,
            title=title,
            description=description,
            metadata=metadata or {},
        )

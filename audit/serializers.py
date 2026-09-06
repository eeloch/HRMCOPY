from rest_framework import serializers

from audit.models import AuditEvent


class AuditEventSerializer(serializers.ModelSerializer):
    employee_id = serializers.IntegerField(source="employee.pk", read_only=True)
    employee_number = serializers.CharField(source="employee.employee_id", read_only=True)
    employee_name = serializers.CharField(source="employee.full_name", read_only=True)
    actor_name = serializers.SerializerMethodField()
    object_type = serializers.SerializerMethodField()

    class Meta:
        model = AuditEvent
        fields = [
            "id",
            "event_type",
            "module",
            "employee_id",
            "employee_number",
            "employee_name",
            "actor_name",
            "object_id",
            "object_type",
            "severity",
            "title",
            "description",
            "metadata",
            "created_at",
        ]

    @staticmethod
    def get_actor_name(audit_event):
        if not audit_event.actor:
            return None

        return audit_event.actor.get_full_name() or audit_event.actor.get_username()

    @staticmethod
    def get_object_type(audit_event):
        if not audit_event.content_type:
            return None

        return f"{audit_event.content_type.app_label}.{audit_event.content_type.model}"

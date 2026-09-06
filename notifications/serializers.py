from rest_framework import serializers

from notifications.models import Notification


class NotificationSerializer(serializers.ModelSerializer):
    employee_id = serializers.IntegerField(source="employee.pk", read_only=True)
    employee_name = serializers.CharField(source="employee.full_name", read_only=True)

    class Meta:
        model = Notification
        fields = [
            "id",
            "event_type",
            "title",
            "message",
            "severity",
            "employee_id",
            "employee_name",
            "related_url",
            "metadata",
            "is_read",
            "read_at",
            "created_at",
        ]

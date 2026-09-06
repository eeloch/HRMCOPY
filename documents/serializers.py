import os

from rest_framework import serializers

from .models import EmployeeDocument


class EmployeeDocumentSerializer(serializers.ModelSerializer):

    employee_name = serializers.CharField(
        source="employee.full_name",
        read_only=True,
    )

    uploaded_by_name = serializers.SerializerMethodField()

    # The stored file path is never handed back to a client - only its
    # basename (for a file-type icon) and a link to the authenticated
    # download endpoint, which is the one place the bytes are reachable
    # from. See documents.views.EmployeeDocumentDownloadAPIView.
    file = serializers.FileField(write_only=True)
    file_name = serializers.SerializerMethodField()
    download_url = serializers.SerializerMethodField()

    class Meta:
        model = EmployeeDocument

        fields = [
            "id",
            "employee",
            "employee_name",
            "document_type",
            "title",
            "file",
            "file_name",
            "download_url",
            "description",
            "uploaded_by",
            "uploaded_by_name",
            "uploaded_at",
            "expiry_date",
            "is_active",
        ]

        read_only_fields = [
            "uploaded_by",
            "uploaded_at",
        ]

    def get_uploaded_by_name(self, obj):

        if obj.uploaded_by:
            return obj.uploaded_by.get_username()

        return None

    def get_file_name(self, obj):

        if obj.file:
            return os.path.basename(obj.file.name)

        return None

    def get_download_url(self, obj):

        if obj.file:
            return f"/documents/{obj.pk}/download/"

        return None
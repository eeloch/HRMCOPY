import os

from rest_framework import generics, permissions
from rest_framework.permissions import BasePermission
from rest_framework.views import APIView
from django.conf import settings
from django.db import transaction
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404

from .models import EmployeeDocument
from .serializers import EmployeeDocumentSerializer
from audit.models import AuditSeverity
from audit.services import AuditService


class CanViewDocuments(BasePermission):
    def has_permission(self, request, view):
        return request.user.has_perm("documents.view_documents") or request.user.has_perm(
            "documents.manage_documents"
        )


class CanManageDocuments(BasePermission):
    def has_permission(self, request, view):
        return request.user.has_perm("documents.manage_documents")


class EmployeeDocumentListCreateAPIView(
    generics.ListCreateAPIView
):
    """
    GET -> List documents (requires documents.view_documents or manage_documents)

    POST -> Upload document (requires documents.manage_documents)
    """

    serializer_class = EmployeeDocumentSerializer

    def get_permissions(self):
        permission = CanManageDocuments if self.request.method == "POST" else CanViewDocuments
        return [permissions.IsAuthenticated(), permission()]

    def get_queryset(self):
        queryset = (
            EmployeeDocument.objects
            .select_related(
                "employee",
                "uploaded_by",
            )
        )

        employee = self.request.query_params.get(
            "employee"
        )

        if employee:
            queryset = queryset.filter(
                employee_id=employee
            )

        return queryset

    def perform_create(
        self,
        serializer,
    ):
        user = (
            self.request.user
            if self.request.user.is_authenticated
            else None
        )

        document = serializer.save(
            uploaded_by=user
        )

        AuditService.log(
            event_type="document.uploaded",
            module="documents",
            employee=document.employee,
            actor=user,
            object=document,
            severity=AuditSeverity.SUCCESS,
            title="Document uploaded",
            description=f"{document.title} was uploaded for {document.employee.full_name}.",
            metadata={
                "document_type": document.document_type,
                "document_id": document.pk,
            },
        )


class EmployeeDocumentDetailAPIView(
    generics.RetrieveDestroyAPIView
):
    """
    GET -> Retrieve document (requires documents.view_documents or manage_documents)

    DELETE -> Delete document and file (requires documents.manage_documents)
    """

    queryset = (
        EmployeeDocument.objects
        .select_related(
            "employee",
            "uploaded_by",
        )
    )

    serializer_class = (
        EmployeeDocumentSerializer
    )

    def get_permissions(self):
        permission = CanManageDocuments if self.request.method == "DELETE" else CanViewDocuments
        return [permissions.IsAuthenticated(), permission()]

    def perform_destroy(
        self,
        instance,
    ):
        if instance.file:
            instance.file.delete(
                save=False
            )

        with transaction.atomic():
            AuditService.log(
                event_type="document.deleted",
                module="documents",
                employee=instance.employee,
                actor=self.request.user,
                object=instance,
                severity=AuditSeverity.SUCCESS,
                title="Document deleted",
                description=f"{instance.title} was deleted for {instance.employee.full_name}.",
                metadata={
                    "document_type": instance.document_type,
                    "document_id": instance.pk,
                },
            )
            instance.delete()


class EmployeeDocumentDownloadAPIView(APIView):
    """
    GET -> stream the document's file (requires documents.view_documents or manage_documents)

    This is the only place a document's bytes should ever be reachable from.
    A reverse proxy must never serve MEDIA_ROOT directly for this app - that
    would let anyone with a guessed/leaked path bypass the permission check
    below entirely.
    """

    permission_classes = [permissions.IsAuthenticated, CanViewDocuments]

    def get(self, request, pk):
        document = get_object_or_404(EmployeeDocument, pk=pk)
        if not document.file:
            raise Http404("This document has no file attached.")

        filename = os.path.basename(document.file.name)

        if getattr(settings, "DOCUMENTS_USE_X_ACCEL_REDIRECT", False):
            # Hand the response off to nginx's internal-only location block
            # (see deploy/nginx.conf) so it serves the bytes directly - this
            # view has already done the one thing nginx can't: check the
            # requester's permission on this specific document.
            response = HttpResponse()
            response["Content-Disposition"] = f'attachment; filename="{filename}"'
            response["X-Accel-Redirect"] = f"/protected-media/{document.file.name}"
            return response

        return FileResponse(document.file.open("rb"), as_attachment=True, filename=filename)

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from rest_framework.test import APIClient

from documents.models import EmployeeDocument
from employees.models import Employee


class EmployeeDocumentAccessTests(TestCase):
    def setUp(self):
        self.employee = Employee.objects.create(employee_id="DOC001", first_name="Doc", last_name="Employee")
        self.document = EmployeeDocument.objects.create(
            employee=self.employee,
            document_type="national_id",
            title="National ID",
            file=SimpleUploadedFile("id.pdf", b"file-bytes", content_type="application/pdf"),
        )
        self.client = APIClient()

    def _user_with(self, username, codename):
        user = get_user_model().objects.create_user(username, password="password")
        if codename:
            user.user_permissions.add(Permission.objects.get(codename=codename))
        return user

    def test_list_requires_view_or_manage_permission(self):
        unauthorized = self._user_with("no-doc-perm", None)
        self.client.force_authenticate(unauthorized)

        response = self.client.get(f"/api/documents/?employee={self.employee.pk}")
        self.assertEqual(response.status_code, 403)

    def test_list_is_allowed_with_view_permission(self):
        viewer = self._user_with("doc-viewer", "view_documents")
        self.client.force_authenticate(viewer)

        response = self.client.get(f"/api/documents/?employee={self.employee.pk}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        # The raw storage path is never returned - only a link to the
        # authenticated download endpoint.
        self.assertNotIn("file", response.data[0])
        self.assertEqual(response.data[0]["download_url"], f"/documents/{self.document.pk}/download/")

    def test_upload_requires_manage_permission_not_just_view(self):
        viewer = self._user_with("doc-viewer-2", "view_documents")
        self.client.force_authenticate(viewer)

        response = self.client.post(
            "/api/documents/",
            {
                "employee": self.employee.pk,
                "document_type": "medical",
                "title": "Medical certificate",
                "file": SimpleUploadedFile("cert.pdf", b"more-bytes"),
            },
            format="multipart",
        )
        self.assertEqual(response.status_code, 403)

    def test_upload_is_allowed_with_manage_permission(self):
        manager = self._user_with("doc-manager", "manage_documents")
        self.client.force_authenticate(manager)

        response = self.client.post(
            "/api/documents/",
            {
                "employee": self.employee.pk,
                "document_type": "medical",
                "title": "Medical certificate",
                "file": SimpleUploadedFile("cert.pdf", b"more-bytes"),
            },
            format="multipart",
        )
        self.assertEqual(response.status_code, 201)

    def test_delete_requires_manage_permission_not_just_view(self):
        viewer = self._user_with("doc-viewer-3", "view_documents")
        self.client.force_authenticate(viewer)

        response = self.client.delete(f"/api/documents/{self.document.pk}/")
        self.assertEqual(response.status_code, 403)
        self.assertTrue(EmployeeDocument.objects.filter(pk=self.document.pk).exists())

    def test_download_requires_view_permission(self):
        unauthorized = self._user_with("no-doc-perm-2", None)
        self.client.force_authenticate(unauthorized)

        response = self.client.get(f"/api/documents/{self.document.pk}/download/")
        self.assertEqual(response.status_code, 403)

    def test_download_streams_the_file_with_view_permission(self):
        viewer = self._user_with("doc-viewer-4", "view_documents")
        self.client.force_authenticate(viewer)

        response = self.client.get(f"/api/documents/{self.document.pk}/download/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(b"".join(response.streaming_content), b"file-bytes")
        self.assertIn("attachment", response["Content-Disposition"])

    def test_download_is_anonymous_when_not_authenticated(self):
        response = self.client.get(f"/api/documents/{self.document.pk}/download/")
        self.assertEqual(response.status_code, 401)

    def tearDown(self):
        for document in EmployeeDocument.objects.all():
            if document.file:
                document.file.delete(save=False)

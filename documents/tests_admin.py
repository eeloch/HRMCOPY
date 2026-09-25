import shutil
import tempfile
from datetime import timedelta

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.utils import timezone

from employees.admin_testing import AdminTestCase

from .models import EmployeeDocument

MEDIA = tempfile.mkdtemp(prefix="hrm-admin-test-media-")


@override_settings(MEDIA_ROOT=MEDIA)
class DocumentAdminTests(AdminTestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA, ignore_errors=True)

    def setUp(self):
        super().setUp()
        self.ada = self.make_employee(middle="Grace")
        self.bola = self.make_employee("000685", "Bola", "Adeyemi")
        today = timezone.localdate()
        self.expired = self.document(self.ada, "Old passport", today - timedelta(days=5), "passport")
        self.soon = self.document(self.ada, "Medical certificate", today + timedelta(days=20), "medical")
        self.later = self.document(self.bola, "Contract", today + timedelta(days=200), "contract")
        self.never = self.document(self.bola, "Appointment letter", None, "appointment")

    def document(self, employee, title, expiry, kind):
        return EmployeeDocument.objects.create(
            employee=employee, title=title, document_type=kind, expiry_date=expiry, uploaded_by=self.admin_user,
            file=SimpleUploadedFile(f"{title}.pdf", b"%PDF-1.4 test"),
        )

    def found(self, **params):
        return {d.pk for d in self.get_list(EmployeeDocument, **params).context["cl"].result_list}

    def test_list_loads_and_shows_name_department_and_file(self):
        page = self.get_list(EmployeeDocument).content.decode()
        for text in ("000684", "Ada Grace Okafor", "Operations", "Old_passport", "(expired)"):
            self.assertIn(text, page)

    def test_search(self):
        self.assert_search(EmployeeDocument, "000684", [self.expired.pk, self.soon.pk])
        self.assert_search(EmployeeDocument, "Adeyemi", [self.later.pk, self.never.pk])
        self.assert_search(EmployeeDocument, "Contract", [self.later.pk])

    def test_expiry_filter(self):
        self.assertEqual(self.found(expiry="expired"), {self.expired.pk})
        self.assertEqual(self.found(expiry="30"), {self.soon.pk})
        self.assertEqual(self.found(expiry="90"), {self.soon.pk})
        self.assertEqual(self.found(expiry="later"), {self.later.pk})
        self.assertEqual(self.found(expiry="none"), {self.never.pk})

    def test_other_filters_load(self):
        for params in ({"document_type__exact": "passport"}, {"is_active__exact": "1"}, {"employee__department__id__exact": self.department.pk}):
            self.get_list(EmployeeDocument, **params)

    def test_no_n_plus_one(self):
        counter = iter(range(10, 200))

        def more():
            for _ in range(4):
                n = next(counter)
                self.document(self.make_employee(f"0008{n}", "Extra", f"Person{n}"), f"Doc {n}", None, "other")

        self.assert_same_query_count(EmployeeDocument, more)

    def test_str(self):
        self.assertEqual(str(self.soon), "000684 Ada Grace Okafor - Medical Certificate - Medical certificate")

    def test_no_bulk_delete_action(self):
        response = self.get_list(EmployeeDocument)
        choices = [name for name, _ in response.context["action_form"].fields["action"].choices]
        self.assertNotIn("delete_selected", choices)
        self.assertIn("archive_documents", choices)

    def test_change_form_shows_file_name_but_no_media_link(self):
        response = self.client.get(self.url(EmployeeDocument, "change", self.expired.pk))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Old_passport")
        self.assertNotContains(response, "/media/")

    def test_add_sets_uploader_and_audits(self):
        data = {"employee": self.bola.pk, "document_type": "other", "title": "Warning", "description": "", "is_active": "on",
                "file": SimpleUploadedFile("warn.pdf", b"%PDF-1.4 x")}
        response = self.client.post(self.url(EmployeeDocument, "add"), data)
        self.assertEqual(response.status_code, 302, getattr(response, "context", None) and response.context["adminform"].form.errors)
        created = EmployeeDocument.objects.get(title="Warning")
        self.assertEqual(created.uploaded_by, self.admin_user)
        self.assertEqual(self.audit("document.uploaded", employee=self.bola).count(), 1)

    def test_archive_and_restore_are_confirmed_and_audited(self):
        self.run_action(EmployeeDocument, "archive_documents", [self.expired.pk], confirm=False)
        self.expired.refresh_from_db()
        self.assertTrue(self.expired.is_active)
        self.run_action(EmployeeDocument, "archive_documents", [self.expired.pk, self.soon.pk])
        self.expired.refresh_from_db()
        self.assertFalse(self.expired.is_active)
        self.assertEqual(self.audit("document.admin_archived", employee=self.ada).count(), 2)
        self.run_action(EmployeeDocument, "restore_documents", [self.expired.pk])
        self.expired.refresh_from_db()
        self.assertTrue(self.expired.is_active)
        self.assertEqual(self.audit("document.admin_restored", employee=self.ada).count(), 1)

    def test_single_delete_is_audited(self):
        response = self.client.post(self.url(EmployeeDocument, "delete", self.never.pk), {"post": "yes"})
        self.assertEqual(response.status_code, 302)
        self.assertFalse(EmployeeDocument.objects.filter(pk=self.never.pk).exists())
        self.assertEqual(self.audit("document.admin_deleted", employee=self.bola).count(), 1)

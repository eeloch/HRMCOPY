from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.forms.models import model_to_dict

from attendance.models import BiometricDevice
from audit.models import AuditEvent

from .admin_testing import AdminTestCase
from .models import BiometricIdentity, Department, Employee, Position

GATEWAY = "vendor_flask_gateway"


class EmployeeAdminTests(AdminTestCase):
    def setUp(self):
        super().setUp()
        self.device = BiometricDevice.objects.create(name="RAE Dev 1", serial_number="SER1", location="Gate", device_type="factory")
        self.ada = self.make_employee(middle="Grace", phone="08030001111", bank_name="GTB", account_number="0123456789", bank_code="058", position=self.position)
        self.bola = self.make_employee("000685", "Bola", "Adeyemi", status="inactive")
        self.identity = BiometricIdentity.objects.create(employee=self.ada, system=GATEWAY, source_identifier="SER1", external_user_id="684")

    # ---- lists

    def test_changelists_load(self):
        for model in (Department, Position, Employee, BiometricIdentity):
            self.get_list(model)

    def test_employee_filters_load(self):
        for params in (
            {"status__exact": "active"}, {"department__id__exact": self.department.pk}, {"identity": "none_active"},
            {"identity": "held"}, {"identity": "some"}, {"check": "no_bank"}, {"check": "no_phone"}, {"check": "no_department"},
            {"employment_type__exact": "permanent"}, {"exit_date__isnull": "True"},
        ):
            self.get_list(Employee, **params)

    def test_employee_filters_select_the_right_people(self):
        found = lambda **p: {e.pk for e in self.get_list(Employee, **p).context["cl"].result_list}  # noqa: E731
        self.assertEqual(found(identity="some"), {self.ada.pk})
        self.assertEqual(found(identity="none_active"), {self.bola.pk})
        self.assertEqual(found(check="no_bank"), {self.bola.pk})

    def test_identity_filters(self):
        found = lambda **p: {i.pk for i in self.get_list(BiometricIdentity, **p).context["cl"].result_list}  # noqa: E731
        other = BiometricIdentity.objects.create(employee=self.ada, system=GATEWAY, source_identifier="SER1", external_user_id="9999")
        self.assertEqual(found(terminal="SER1"), {self.identity.pk, other.pk})
        self.assertEqual(found(terminal="cloud"), set())
        self.assertEqual(found(id_check="differs"), {other.pk})
        self.assertEqual(found(id_check="matches"), {self.identity.pk})
        self.assertEqual(found(is_active__exact="1"), {self.identity.pk, other.pk})

    def test_search_by_staff_number_and_names(self):
        self.assert_search(Employee, "000684", [self.ada.pk])
        self.assert_search(Employee, "684", [self.ada.pk])
        self.assert_search(Employee, "Ada", [self.ada.pk])
        self.assert_search(Employee, "Grace", [self.ada.pk])
        self.assert_search(Employee, "Okafor", [self.ada.pk])
        self.assert_search(Employee, "Ada Okafor", [self.ada.pk])
        self.assert_search(Employee, "08030001111", [self.ada.pk])
        self.assert_search(Employee, "nobody", [])

    def test_employee_search_by_terminal_id(self):
        self.assert_search(Employee, "684", [self.ada.pk])  # exact terminal id or staff number containing it

    def test_identity_search(self):
        self.assert_search(BiometricIdentity, "000684", [self.identity.pk])
        self.assert_search(BiometricIdentity, "Okafor", [self.identity.pk])
        self.assert_search(BiometricIdentity, "SER1", [self.identity.pk])
        self.assert_search(BiometricIdentity, "684", [self.identity.pk])

    def test_no_n_plus_one(self):
        counter = iter(range(100, 1000))

        def more():
            for _ in range(5):
                n = next(counter)
                e = self.make_employee(f"000{n}", "Extra", f"Person{n}", position=self.position)
                BiometricIdentity.objects.create(employee=e, system=GATEWAY, source_identifier="SER1", external_user_id=str(n))

        self.assert_same_query_count(Employee, more)
        self.assert_same_query_count(BiometricIdentity, more)
        self.assert_same_query_count(Position, lambda: Position.objects.create(name="Fitter", department=self.department))

    def test_identity_list_shows_terminal_name_and_flags_mismatch(self):
        BiometricIdentity.objects.create(employee=self.ada, system=GATEWAY, source_identifier="SER1", external_user_id="9999")
        page = self.get_list(BiometricIdentity).content.decode()
        self.assertIn("RAE Dev 1", page)
        self.assertIn("= staff no.", page)
        self.assertIn("differs", page)

    def test_str_reads_with_name(self):
        self.assertEqual(str(self.identity), "000684 Ada Grace Okafor - Vendor Flask Gateway SER1 - id 684")
        self.assertIn("Okafor", str(self.ada))

    # ---- forms

    def test_change_form_loads_with_inline_showing_device_name(self):
        response = self.client.get(self.url(Employee, "change", self.ada.pk))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "RAE Dev 1")
        self.assertContains(response, "basic_salary")
        self.assertContains(response, "account_number")

    def test_add_form_loads(self):
        self.assertEqual(self.client.get(self.url(Employee, "add")).status_code, 200)

    def test_salary_and_bank_fields_hidden_without_permission(self):
        staff = get_user_model().objects.create_user(username="hr-clerk", password="x", is_staff=True)
        staff.user_permissions.add(*Permission.objects.filter(content_type__app_label="employees", codename__in=["view_employee", "change_employee", "view_department", "view_position"]))
        self.client.force_login(staff)
        page = self.client.get(self.url(Employee, "change", self.ada.pk)).content.decode()
        self.assertNotIn("basic_salary", page)
        self.assertNotIn("account_number", page)
        self.assertNotIn("0123456789", page)
        self.assertNotIn("bank_ok", self.client.get(self.url(Employee)).content.decode())
        # with the permissions the same page shows them
        staff.user_permissions.add(*Permission.objects.filter(codename__in=["view_salary", "view_bank_details"]))
        staff = get_user_model().objects.get(pk=staff.pk)
        self.client.force_login(staff)
        page = self.client.get(self.url(Employee, "change", self.ada.pk)).content.decode()
        self.assertIn("basic_salary", page)
        self.assertIn("account_number", page)

    def _form_data(self, employee, **overrides):
        data = {k: v for k, v in model_to_dict(employee).items() if v is not None and v is not False}
        data.pop("biometric_user_id", None)
        data.update({"biometric_identities-TOTAL_FORMS": "0", "biometric_identities-INITIAL_FORMS": "0",
                     "biometric_identities-MIN_NUM_FORMS": "0", "biometric_identities-MAX_NUM_FORMS": "1000"})
        data.update(overrides)
        return data

    def test_edit_through_the_form_is_audited_without_values(self):
        loner = self.make_employee("000700", "Chidi", "Eze", basic_salary=50000)
        response = self.client.post(self.url(Employee, "change", loner.pk), self._form_data(loner, phone="0801", basic_salary="65000"))
        self.assertEqual(response.status_code, 302, getattr(response, "context", None) and response.context["adminform"].form.errors)
        event = self.audit("employee.admin_updated", employee=loner).get()
        self.assertEqual(event.metadata["changed_fields"], ["basic_salary", "phone"])
        self.assertNotIn("65000", str(event.metadata) + event.description)
        self.assertEqual(event.actor, self.admin_user)

    def test_form_rejects_position_from_another_department(self):
        other = Department.objects.create(name="Kitchen")
        stray = Position.objects.create(name="Cook", department=other)
        loner = self.make_employee("000701", "Dayo", "Bello")
        response = self.client.post(self.url(Employee, "change", loner.pk), self._form_data(loner, position=stray.pk))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "does not belong to the selected department")

    # ---- actions

    def test_mark_inactive_asks_first_then_revokes_identities_and_audits(self):
        first, second = self.run_action(Employee, "mark_inactive", [self.ada.pk])
        self.assertContains(first, "Mark employees inactive")
        self.ada.refresh_from_db()
        self.assertEqual(self.ada.status, "inactive")
        self.identity.refresh_from_db()
        self.assertFalse(self.identity.is_active)
        event = self.audit("employee.admin_status_changed", employee=self.ada).get()
        self.assertEqual((event.metadata["from"], event.metadata["to"], event.metadata["identities_active_before"]), ("active", "inactive", 1))
        self.assertEqual(event.actor, self.admin_user)

    def test_confirmation_page_changes_nothing(self):
        self.run_action(Employee, "mark_inactive", [self.ada.pk], confirm=False)
        self.ada.refresh_from_db()
        self.assertEqual(self.ada.status, "active")
        self.assertFalse(AuditEvent.objects.filter(event_type="employee.admin_status_changed").exists())

    def test_mark_active_restores_held_identities(self):
        self.run_action(Employee, "mark_inactive", [self.ada.pk])
        self.run_action(Employee, "mark_active", [self.ada.pk, self.bola.pk])
        self.ada.refresh_from_db()
        self.bola.refresh_from_db()
        self.assertEqual((self.ada.status, self.bola.status), ("active", "active"))
        self.identity.refresh_from_db()
        self.assertTrue(self.identity.is_active)
        self.assertEqual(self.audit("employee.admin_status_changed", employee=self.ada).count(), 2)
        self.assertEqual(self.audit("employee.admin_status_changed", employee=self.bola).count(), 1)

    def test_no_bulk_delete_action(self):
        response = self.get_list(Employee)
        self.assertNotIn("delete_selected", [name for name, _ in response.context["action_form"].fields["action"].choices])

    def test_deactivate_identity_only_changes_the_mapping(self):
        first, _ = self.run_action(BiometricIdentity, "deactivate_identities", [self.identity.pk], confirm=False)
        self.assertContains(first, "still ACTIVE")  # warns that sync may push an active person back
        self.run_action(BiometricIdentity, "deactivate_identities", [self.identity.pk])
        self.identity.refresh_from_db()
        self.assertFalse(self.identity.is_active)
        self.ada.refresh_from_db()
        self.assertEqual(self.ada.status, "active")
        self.assertTrue(BiometricIdentity.objects.filter(pk=self.identity.pk).exists())  # never deleted
        self.assertEqual(self.audit("biometric_identity.admin_deactivated", employee=self.ada).count(), 1)

    def test_reactivate_identity_skips_people_who_are_not_active(self):
        held = BiometricIdentity.objects.create(employee=self.bola, system=GATEWAY, source_identifier="SER1", external_user_id="685")
        self.assertFalse(held.is_active)  # created on hold because Bola is inactive
        self.identity.is_active = False
        self.identity.save()
        self.run_action(BiometricIdentity, "reactivate_identities", [self.identity.pk, held.pk])
        self.identity.refresh_from_db()
        held.refresh_from_db()
        self.assertTrue(self.identity.is_active)
        self.assertFalse(held.is_active)
        self.assertEqual(self.audit("biometric_identity.admin_reactivated").count(), 1)

    def test_department_list_shows_headcount_gap(self):
        page = self.get_list(Department).content.decode()
        self.assertIn("short by 2", page)  # 3 required, 1 active

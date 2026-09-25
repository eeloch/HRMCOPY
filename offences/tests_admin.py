from datetime import date
from decimal import Decimal

from employees.admin_testing import AdminTestCase
from payroll.models import EmployeePayroll, PayrollLineItem, PayrollPeriod

from .models import EmployeeOffence, EmployeeOffenceStatus, OffenceType


class OffenceAdminTests(AdminTestCase):
    def setUp(self):
        super().setUp()
        self.ada = self.make_employee(middle="Grace", basic_salary=Decimal("100000.00"))
        self.bola = self.make_employee("000685", "Bola", "Adeyemi", basic_salary=Decimal("100000.00"))
        self.kind = OffenceType.objects.create(name="Late to work", default_amount=Decimal("2000.00"))
        self.one = self.offence(self.ada, date(2026, 9, 7))
        self.two = self.offence(self.bola, date(2026, 9, 8))

    def offence(self, employee, when):
        return EmployeeOffence.objects.create(
            employee=employee, offence_type=self.kind, amount=Decimal("2000.00"), incident_date=when, recorded_by=self.admin_user,
        )

    def test_changelists_load(self):
        self.get_list(OffenceType)
        self.get_list(EmployeeOffence)
        for params in ({"status__exact": "pending"}, {"offence_type__id__exact": self.kind.pk}, {"employee__department__id__exact": self.department.pk},
                       {"incident_date__gte": "2026-09-01"}):
            self.get_list(EmployeeOffence, **params)

    def test_search(self):
        self.assert_search(EmployeeOffence, "000684", [self.one.pk])
        self.assert_search(EmployeeOffence, "Grace", [self.one.pk])
        self.assert_search(EmployeeOffence, "Adeyemi", [self.two.pk])
        self.assert_search(EmployeeOffence, "Late", [self.one.pk, self.two.pk])

    def test_list_has_staff_number_name_department_and_badge(self):
        page = self.get_list(EmployeeOffence).content.decode()
        for text in ("000684", "Ada Grace Okafor", "Operations", "Pending"):
            self.assertIn(text, page)

    def test_no_n_plus_one(self):
        counter = iter(range(10, 200))

        def more():
            for _ in range(4):
                n = next(counter)
                self.offence(self.make_employee(f"0008{n}", "Extra", f"Person{n}"), date(2026, 9, 9))

        self.assert_same_query_count(EmployeeOffence, more)
        self.assert_same_query_count(OffenceType, lambda: OffenceType.objects.create(name=f"Other {next(counter)}", default_amount=Decimal("5")))

    def test_str(self):
        self.assertEqual(str(self.one), "000684 Ada Grace Okafor - Late to work - 07 Sep 2026")

    def test_no_bulk_delete_and_deducted_rows_are_read_only(self):
        response = self.get_list(EmployeeOffence)
        self.assertNotIn("delete_selected", [name for name, _ in response.context["action_form"].fields["action"].choices])
        EmployeeOffence.objects.filter(pk=self.one.pk).update(status=EmployeeOffenceStatus.DEDUCTED)
        self.assertEqual(self.client.get(self.url(EmployeeOffence, "delete", self.one.pk)).status_code, 403)

    def test_adding_records_who_added_it_and_notifies_reviewers(self):
        data = {"employee": self.ada.pk, "offence_type": self.kind.pk, "amount": "1500.00", "incident_date": "2026-09-10", "notes": "", "status": "pending", "comment": ""}
        response = self.client.post(self.url(EmployeeOffence, "add"), data)
        self.assertEqual(response.status_code, 302, getattr(response, "context", None) and response.context["adminform"].form.errors)
        created = EmployeeOffence.objects.get(incident_date=date(2026, 9, 10))
        self.assertEqual(created.recorded_by, self.admin_user)
        self.assertEqual(self.audit("offence.admin_created", employee=self.ada).count(), 1)

    def test_approve_goes_through_the_service_and_audits(self):
        period = PayrollPeriod.objects.create(year=2026, month=9)
        EmployeePayroll.objects.create(payroll_period=period, employee=self.ada, basic_salary=Decimal("100000.00"), gross_earnings=Decimal("100000.00"), net_pay=Decimal("100000.00"))
        first, _ = self.run_action(EmployeeOffence, "approve_offences", [self.one.pk, self.two.pk], comment="ok")
        self.assertContains(first, "Approve offences")
        self.one.refresh_from_db()
        self.two.refresh_from_db()
        self.assertEqual(self.one.status, EmployeeOffenceStatus.DEDUCTED)  # payroll record already existed -> deducted now
        self.assertEqual(self.two.status, EmployeeOffenceStatus.APPROVED)  # no payroll record yet -> waits
        self.assertEqual(self.one.reviewer, self.admin_user)
        self.assertEqual(PayrollLineItem.objects.filter(source_type="employee_offence").count(), 1)
        self.assertEqual(self.audit("offences.approved").count(), 2)

    def test_confirmation_changes_nothing(self):
        self.run_action(EmployeeOffence, "approve_offences", [self.one.pk], confirm=False)
        self.one.refresh_from_db()
        self.assertEqual(self.one.status, EmployeeOffenceStatus.PENDING)

    def test_reject_needs_a_reason_then_audits(self):
        _, second = self.run_action(EmployeeOffence, "reject_offences", [self.one.pk], follow=False)
        self.assertContains(second, "This is required.")
        self.one.refresh_from_db()
        self.assertEqual(self.one.status, EmployeeOffenceStatus.PENDING)
        self.run_action(EmployeeOffence, "reject_offences", [self.one.pk], reason="Not proven")
        self.one.refresh_from_db()
        self.assertEqual((self.one.status, self.one.comment), (EmployeeOffenceStatus.REJECTED, "Not proven"))
        self.assertEqual(self.audit("offences.rejected", employee=self.ada).count(), 1)

    def test_decided_offences_are_left_alone(self):
        self.run_action(EmployeeOffence, "reject_offences", [self.one.pk], reason="Not proven")
        _, second = self.run_action(EmployeeOffence, "approve_offences", [self.one.pk])
        self.one.refresh_from_db()
        self.assertEqual(self.one.status, EmployeeOffenceStatus.REJECTED)
        self.assertContains(second, "left as they were")

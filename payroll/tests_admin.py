from decimal import Decimal

from django.test import TestCase

from audit.admin_testing import admin_messages, run_admin_action, superuser
from audit.models import AuditEvent
from employees.models import Department, Employee
from payroll.models import EmployeePayroll, PayrollLineItem, PayrollPeriod
from payroll.services import generate_payroll_for_period, recalculate_employee_payroll

PERIODS = "/admin/payroll/payrollperiod/"
PAYROLLS = "/admin/payroll/employeepayroll/"
LINES = "/admin/payroll/payrolllineitem/"


class PayrollAdminTests(TestCase):
    def setUp(self):
        self.client.force_login(superuser())
        self.dept = Department.objects.create(name="Melting")
        self.ada = Employee.objects.create(employee_id="000684", first_name="Ada", middle_name="N", last_name="Okafor", basic_salary=Decimal("65000.00"), department=self.dept)
        self.bayo = Employee.objects.create(employee_id="000700", first_name="Bayo", last_name="Eze", basic_salary=Decimal("90000.00"), department=self.dept)
        self.period = PayrollPeriod.objects.create(year=2026, month=9)
        generate_payroll_for_period(self.period)
        self.payroll = EmployeePayroll.objects.get(payroll_period=self.period, employee=self.ada)

    def manual_line(self, amount="5000.00", payroll=None):
        return PayrollLineItem.objects.create(payroll=payroll or self.payroll, item_type="deduction", code="FINE", description="Fine", amount=Decimal(amount), source_type="manual")

    def lock(self, status="approved"):
        PayrollPeriod.objects.filter(pk=self.period.pk).update(status=status)
        EmployeePayroll.objects.filter(payroll_period=self.period).update(status="approved")

    # ---- lists
    def test_every_list_loads_with_data(self):
        self.manual_line()
        for url in (PERIODS, PAYROLLS, LINES, "/admin/payroll/payrollsetting/"):
            self.assertEqual(self.client.get(url).status_code, 200, url)

    def test_str_reads_like_a_payslip_line(self):
        self.assertEqual(str(self.payroll), "000684 Ada N Okafor - Sep 2026 - N 65,000.00")

    def test_employee_payroll_list_shows_who_and_naira_and_searches_by_number_and_name(self):
        response = self.client.get(PAYROLLS)
        self.assertContains(response, "000684")
        self.assertContains(response, "Ada N Okafor")
        self.assertContains(response, "N 65,000.00")
        self.assertNotContains(response, "column-id")
        for term, present, absent in (("000684", "Ada", "Bayo"), ("Okafor", "Ada", "Bayo"), ("Ada Okafor", "Ada", "Bayo"), ("Bayo", "Bayo", "Ada")):
            page = self.client.get(PAYROLLS, {"q": term})
            self.assertContains(page, present)
            self.assertNotContains(page, absent)

    def test_period_list_shows_totals(self):
        response = self.client.get(PERIODS)
        self.assertContains(response, "September 2026")
        self.assertContains(response, "N 155,000.00")  # basic and net of the two records

    def test_line_item_list_searchable_by_staff_number(self):
        self.manual_line()
        self.assertEqual(len(self.client.get(LINES, {"q": "000684"}).context["cl"].result_list), 1)
        self.assertEqual(len(self.client.get(LINES, {"q": "000700"}).context["cl"].result_list), 0)
        self.assertEqual(len(self.client.get(LINES, {"q": "Ada Okafor"}).context["cl"].result_list), 1)

    # ---- financial safety
    def test_no_delete_selected_on_payroll_models(self):
        for url in (PERIODS, PAYROLLS, LINES):
            self.assertNotContains(self.client.get(url), "delete_selected")

    def test_approved_period_is_read_only_and_cannot_be_deleted(self):
        self.lock()
        url = f"{PERIODS}{self.period.pk}/change/"
        page = self.client.get(url)
        self.assertEqual(page.status_code, 200)
        self.assertNotContains(page, 'name="_save"')
        self.assertEqual(set(page.context["adminform"].readonly_fields), {"year", "month", "status", "notes", "created_by", "created_at", "approved_by", "approved_at", "paid_at"})
        self.assertEqual(self.client.post(url, {"notes": "tampered", "_save": "Save"}).status_code, 403)
        self.assertEqual(self.client.get(f"{PERIODS}{self.period.pk}/delete/").status_code, 403)
        self.assertEqual(self.client.post(f"{PERIODS}{self.period.pk}/delete/", {"post": "yes"}).status_code, 403)
        self.period.refresh_from_db()
        self.assertEqual(self.period.notes, "")

    def test_paid_and_closed_periods_are_also_locked(self):
        for status in ("paid", "closed"):
            PayrollPeriod.objects.filter(pk=self.period.pk).update(status=status)
            self.assertEqual(self.client.post(f"{PERIODS}{self.period.pk}/change/", {"notes": "x", "_save": "Save"}).status_code, 403)

    def test_a_draft_period_allows_notes_only_and_the_edit_is_audited(self):
        url = f"{PERIODS}{self.period.pk}/change/"
        self.client.post(url, {"notes": "Check overtime", "status": "approved", "year": 2030, "_save": "Save"})
        self.period.refresh_from_db()
        self.assertEqual((self.period.notes, self.period.status, self.period.year), ("Check overtime", "draft", 2026))
        event = AuditEvent.objects.get(event_type="admin.payrollperiod.changed")
        self.assertEqual(event.metadata["after"], {"notes": "Check overtime"})
        self.assertEqual(event.metadata["via"], "django-admin")

    def test_a_period_with_records_cannot_be_deleted_but_an_empty_draft_can(self):
        self.assertEqual(self.client.get(f"{PERIODS}{self.period.pk}/delete/").status_code, 403)
        empty = PayrollPeriod.objects.create(year=2027, month=1)
        self.assertEqual(self.client.post(f"{PERIODS}{empty.pk}/delete/", {"post": "yes"}).status_code, 302)
        self.assertFalse(PayrollPeriod.objects.filter(pk=empty.pk).exists())
        self.assertTrue(AuditEvent.objects.filter(event_type="admin.payrollperiod.deleted").exists())

    def test_employee_payroll_is_view_only_and_shows_its_line_items(self):
        self.manual_line()
        url = f"{PAYROLLS}{self.payroll.pk}/change/"
        page = self.client.get(url)
        self.assertContains(page, "FINE")
        self.assertContains(page, "N 5,000.00")
        self.assertNotContains(page, "bank_details")
        self.assertEqual(self.client.post(url, {"status": "approved", "_save": "Save"}).status_code, 403)
        self.assertEqual(self.client.get(f"{PAYROLLS}{self.payroll.pk}/delete/").status_code, 403)

    def test_line_items_of_an_approved_payroll_and_system_lines_cannot_be_changed(self):
        manual, system = self.manual_line(), PayrollLineItem.objects.create(payroll=self.payroll, item_type="deduction", code="SYS", description="System", amount=Decimal("100.00"), source_type="x", source_reference="1", is_system_generated=True)
        self.assertEqual(self.client.post(f"{LINES}{system.pk}/change/", {"item_type": "deduction", "code": "SYS", "description": "System", "amount": "1.00", "_save": "Save"}).status_code, 403)
        self.assertEqual(self.client.get(f"{LINES}{system.pk}/delete/").status_code, 403)
        self.lock()
        self.assertEqual(self.client.post(f"{LINES}{manual.pk}/change/", {"item_type": "deduction", "code": "FINE", "description": "Fine", "amount": "1.00", "_save": "Save"}).status_code, 403)
        self.assertEqual(self.client.get(f"{LINES}{manual.pk}/delete/").status_code, 403)
        manual.refresh_from_db()
        self.assertEqual(manual.amount, Decimal("5000.00"))

    # ---- corrections
    def test_editing_a_manual_line_recalculates_the_payslip_and_audits_it(self):
        line = self.manual_line()
        recalculate_employee_payroll(self.payroll)
        response = self.client.post(f"{LINES}{line.pk}/change/", {"item_type": "deduction", "code": "FINE", "description": "Fine", "amount": "7500.00", "_save": "Save"})
        self.assertEqual(response.status_code, 302)
        self.payroll.refresh_from_db()
        self.assertEqual((self.payroll.total_deductions, self.payroll.net_pay), (Decimal("7500.00"), Decimal("57500.00")))
        event = AuditEvent.objects.get(event_type="payroll.line_item_admin_changed")
        self.assertEqual((event.employee, event.metadata["net_before"], event.metadata["net_after"]), (self.ada, "60000.00", "57500.00"))

    def test_deleting_a_manual_line_recalculates_and_audits_it(self):
        line = self.manual_line()
        recalculate_employee_payroll(self.payroll)
        self.assertEqual(self.client.post(f"{LINES}{line.pk}/delete/", {"post": "yes"}).status_code, 302)
        self.payroll.refresh_from_db()
        self.assertEqual(self.payroll.net_pay, Decimal("65000.00"))
        self.assertTrue(AuditEvent.objects.filter(event_type="payroll.line_item_admin_deleted", employee=self.ada).exists())

    def test_recalculate_action_fixes_a_stale_draft_and_skips_an_approved_one(self):
        self.manual_line()  # created without recalculating: the stored net pay is stale
        page, response = run_admin_action(self.client, PAYROLLS, "recalculate_draft_records", [self.payroll.pk])
        self.assertContains(page, "000684 Ada N Okafor")
        self.payroll.refresh_from_db()
        self.assertEqual(self.payroll.net_pay, Decimal("60000.00"))
        self.assertTrue(any("N 65,000.00 -> N 60,000.00" in text for text in admin_messages(response)))
        self.assertTrue(AuditEvent.objects.filter(event_type="admin.payroll.recalculate_records", actor__username="root-admin").exists())

        self.manual_line("1000.00")
        self.lock()
        _, response = run_admin_action(self.client, PAYROLLS, "recalculate_draft_records", [self.payroll.pk])
        self.payroll.refresh_from_db()
        self.assertEqual(self.payroll.net_pay, Decimal("60000.00"))
        self.assertTrue(any("only draft payroll" in text for text in admin_messages(response)))

    def test_the_confirmation_page_changes_nothing(self):
        self.manual_line()
        page, _ = run_admin_action(self.client, PAYROLLS, "recalculate_draft_records", [self.payroll.pk], confirm=False)
        self.assertContains(page, "Cancel - change nothing")
        self.payroll.refresh_from_db()
        self.assertEqual(self.payroll.net_pay, Decimal("65000.00"))
        self.assertFalse(AuditEvent.objects.filter(event_type__startswith="admin.payroll").exists())

    def test_regenerate_adds_missing_records_for_a_draft_period_only(self):
        Employee.objects.create(employee_id="000800", first_name="New", last_name="Starter", basic_salary=Decimal("50000.00"))
        _, response = run_admin_action(self.client, PERIODS, "regenerate_draft_periods", [self.period.pk])
        self.assertTrue(EmployeePayroll.objects.filter(payroll_period=self.period, employee__employee_id="000800").exists())
        self.assertTrue(any("1 new record(s)" in text for text in admin_messages(response)))
        self.assertTrue(AuditEvent.objects.filter(event_type="admin.payroll.regenerate_periods").exists())

        Employee.objects.create(employee_id="000801", first_name="Later", last_name="Starter", basic_salary=Decimal("50000.00"))
        self.lock()
        _, response = run_admin_action(self.client, PERIODS, "regenerate_draft_periods", [self.period.pk])
        self.assertFalse(EmployeePayroll.objects.filter(employee__employee_id="000801").exists())
        self.assertTrue(any("only draft payroll" in text for text in admin_messages(response)))

    def test_resync_action_refuses_a_locked_period(self):
        self.lock()
        _, response = run_admin_action(self.client, PERIODS, "resync_draft_periods", [self.period.pk])
        self.assertTrue(any("only draft payroll" in text for text in admin_messages(response)))
        self.assertTrue(AuditEvent.objects.filter(event_type="admin.payroll.resync_attendance").exists())

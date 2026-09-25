from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase

from audit.admin_testing import admin_messages, run_admin_action, superuser
from audit.models import AuditEvent
from employees.models import Department, Employee
from payroll.models import EmployeePayroll, PayrollPeriod
from payroll.services import generate_payroll_for_period

from .models import Bonus, BonusStatus, EmployeeOfTheMonth, EotmStatus
from .services import BonusService, EmployeeOfTheMonthService

BONUSES = "/admin/bonuses/bonus/"
AWARDS = "/admin/bonuses/employeeofthemonth/"


class BonusAdminTests(TestCase):
    def setUp(self):
        self.root = superuser()
        self.client.force_login(self.root)
        self.hr = get_user_model().objects.create_user("hr", password="pw")
        self.dept = Department.objects.create(name="Melting")
        self.ada = Employee.objects.create(employee_id="000684", first_name="Ada", last_name="Okafor", basic_salary=Decimal("100000.00"), department=self.dept)
        self.period = PayrollPeriod.objects.create(year=2026, month=9)
        self.bonus = BonusService.record(employee=self.ada, amount=Decimal("20000"), reason="Covered extra shifts", performance_year=2026, performance_month=9, actor=self.hr)

    def test_lists_load_show_who_and_search(self):
        EmployeeOfTheMonthService.propose(department=self.dept, year=2026, month=9, employee=self.ada, reason="Best", reward_amount=Decimal("5000"), actor=self.hr)
        for url in (BONUSES, AWARDS):
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)
            for text in ("000684", "Ada Okafor"):
                self.assertContains(response, text)
            for term in ("000684", "Okafor", "Ada Okafor"):
                self.assertEqual(len(self.client.get(url, {"q": term}).context["cl"].result_list), 1, (url, term))
        self.assertContains(self.client.get(BONUSES), "N 20,000.00")
        self.assertContains(self.client.get(AWARDS), "N 5,000.00")
        self.assertEqual(str(self.bonus), "000684 Ada Okafor - Performance bonus N 20,000.00 (09/2026)")

    def test_read_only_and_no_delete(self):
        detail = f"{BONUSES}{self.bonus.pk}/change/"
        self.assertEqual(self.client.get(detail).status_code, 200)
        self.assertEqual(self.client.post(detail, {"amount": "1", "_save": "Save"}).status_code, 403)
        self.assertEqual(self.client.get(f"{BONUSES}{self.bonus.pk}/delete/").status_code, 403)
        self.assertNotContains(self.client.get(BONUSES), "delete_selected")
        self.assertNotContains(self.client.get(AWARDS), "delete_selected")

    def test_approve_adds_the_bonus_to_an_open_payroll_through_the_service(self):
        generate_payroll_for_period(self.period)
        _, response = run_admin_action(self.client, BONUSES, "approve_bonuses", [self.bonus.pk], reason="Well done")
        self.bonus.refresh_from_db()
        self.assertEqual(self.bonus.status, BonusStatus.PAID)
        self.assertEqual(EmployeePayroll.objects.get(employee=self.ada).net_pay, Decimal("120000.00"))
        self.assertTrue(AuditEvent.objects.filter(event_type="bonus.approved", actor=self.root).exists())
        self.assertTrue(AuditEvent.objects.filter(event_type="admin.bonuses.approve_bonuses").exists())
        self.assertIn("Bonuses approved: 1 of 1 selected.", admin_messages(response))

    def test_approve_is_refused_for_an_approved_payroll_month(self):
        PayrollPeriod.objects.filter(pk=self.period.pk).update(status="approved")
        _, response = run_admin_action(self.client, BONUSES, "approve_bonuses", [self.bonus.pk])
        self.bonus.refresh_from_db()
        self.assertEqual(self.bonus.status, BonusStatus.PROPOSED)
        self.assertTrue(any("already approved" in text for text in admin_messages(response)))

    def test_decline_and_cancel_bonus(self):
        run_admin_action(self.client, BONUSES, "decline_bonuses", [self.bonus.pk], reason="Not justified")
        self.bonus.refresh_from_db()
        self.assertEqual(self.bonus.status, BonusStatus.DECLINED)
        second = BonusService.record(employee=self.ada, amount=Decimal("1000"), reason="x", performance_year=2026, performance_month=9, actor=self.hr)
        run_admin_action(self.client, BONUSES, "cancel_bonuses", [second.pk])
        second.refresh_from_db()
        self.assertEqual(second.status, BonusStatus.CANCELLED)

    def test_employee_of_the_month_approve_and_cancel(self):
        award = EmployeeOfTheMonthService.propose(department=self.dept, year=2026, month=9, employee=self.ada, reason="Best", actor=self.hr)
        run_admin_action(self.client, AWARDS, "approve_awards", [award.pk])
        award.refresh_from_db()
        self.assertEqual(award.status, EotmStatus.APPROVED)
        self.assertTrue(AuditEvent.objects.filter(event_type="eotm.approved", actor=self.root).exists())
        run_admin_action(self.client, AWARDS, "cancel_awards", [award.pk])
        award.refresh_from_db()
        self.assertEqual(award.status, EotmStatus.CANCELLED)
        self.assertEqual(str(award), "Melting 09/2026 - 000684 Ada Okafor")

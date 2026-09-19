from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from rest_framework.test import APIClient

from employees.models import Department, Employee
from payroll.models import EmployeePayroll, PayrollPeriod
from payroll.services import generate_payroll_for_period

from .models import Bonus, BonusStatus, EmployeeOfTheMonth
from .services import BonusService, EmployeeOfTheMonthService


def user_with(name, *codenames):
    user = get_user_model().objects.create_user(name, password="pw")
    user.user_permissions.add(*Permission.objects.filter(codename__in=codenames))
    return user


class BonusTests(TestCase):
    def setUp(self):
        self.hr = user_with("hr", "record_bonus", "view_bonuses")
        self.boss = user_with("boss", "approve_bonus")
        self.melting = Department.objects.create(name="Melting")
        self.powder = Department.objects.create(name="Powder Coating")
        self.ada = Employee.objects.create(employee_id="A1", first_name="Ada", last_name="Obi", basic_salary=Decimal("100000.00"), department=self.melting)
        self.bayo = Employee.objects.create(employee_id="A2", first_name="Bayo", last_name="Eze", basic_salary=Decimal("100000.00"), department=self.melting)
        self.sept = PayrollPeriod.objects.create(year=2026, month=9)
        self.oct = PayrollPeriod.objects.create(year=2026, month=10)

    def record(self, amount="20000", **kw):
        values = dict(employee=self.ada, amount=Decimal(amount), reason="Covered two extra shifts and trained a new starter", performance_year=2026, performance_month=9, actor=self.hr)
        values.update(kw)
        return BonusService.record(**values)

    def generate(self, period, employee=None):
        generate_payroll_for_period(period, actor=self.hr)
        return EmployeePayroll.objects.get(payroll_period=period, employee=employee or self.ada)

    def test_an_approved_bonus_becomes_an_earning_on_that_months_payroll(self):
        bonus = self.record()
        BonusService.approve(bonus, actor=self.boss)
        payroll = self.generate(self.sept)
        self.assertEqual(payroll.gross_earnings, Decimal("120000.00"))
        self.assertEqual(payroll.net_pay, Decimal("120000.00"))
        line = payroll.line_items.get(code="BONUS")
        self.assertEqual((line.item_type, line.description), ("earning", "Performance bonus (September 2026)"))
        bonus.refresh_from_db()
        self.assertEqual(bonus.status, BonusStatus.PAID)

    def test_a_bonus_that_is_only_recorded_or_declined_never_reaches_payroll(self):
        self.record()
        declined = self.record(amount="5000", employee=self.bayo)
        BonusService.decline(declined, actor=self.boss, comment="Already rewarded")
        self.assertEqual(self.generate(self.sept).gross_earnings, Decimal("100000.00"))

    def test_approving_after_payroll_exists_adds_it_straight_away_and_only_once(self):
        payroll = self.generate(self.sept)
        BonusService.approve(self.record(), actor=self.boss)
        payroll.refresh_from_db()
        self.assertEqual(payroll.gross_earnings, Decimal("120000.00"))
        self.assertEqual(self.generate(self.sept).gross_earnings, Decimal("120000.00"))

    def test_the_bonus_can_be_paid_with_a_later_month(self):
        bonus = self.record()
        BonusService.approve(bonus, actor=self.boss, pay_year=2026, pay_month=10)
        self.assertEqual(self.generate(self.sept).gross_earnings, Decimal("100000.00"))
        self.assertEqual(self.generate(self.oct).gross_earnings, Decimal("120000.00"))

    def test_a_locked_payroll_month_cannot_take_a_new_bonus(self):
        PayrollPeriod.objects.filter(pk=self.sept.pk).update(status="approved")
        with self.assertRaises(ValueError):
            BonusService.approve(self.record(), actor=self.boss)

    def test_a_reason_is_required_and_only_active_staff_qualify(self):
        with self.assertRaises(ValueError):
            self.record(reason="  ")
        Employee.objects.filter(pk=self.bayo.pk).update(status="inactive")
        with self.assertRaises(ValueError):
            self.record(employee=Employee.objects.get(pk=self.bayo.pk))

    def test_steps_in_order_and_cancel_before_payroll(self):
        bonus = self.record()
        with self.assertRaises(ValueError):
            BonusService.decline(bonus, actor=self.boss, comment=" ")
        BonusService.approve(bonus, actor=self.boss)
        with self.assertRaises(ValueError):
            BonusService.approve(bonus, actor=self.boss)
        second = self.record(employee=self.bayo)
        BonusService.cancel(second, actor=self.hr)
        self.assertEqual(self.generate(self.sept, self.bayo).gross_earnings, Decimal("100000.00"))


class EmployeeOfTheMonthTests(TestCase):
    def setUp(self):
        self.hr = user_with("hr", "record_bonus")
        self.boss = user_with("boss", "approve_bonus")
        self.melting = Department.objects.create(name="Melting")
        self.extrusion = Department.objects.create(name="Extrusion")
        self.ada = Employee.objects.create(employee_id="A1", first_name="Ada", last_name="Obi", basic_salary=Decimal("100000.00"), department=self.melting)
        self.chi = Employee.objects.create(employee_id="A3", first_name="Chi", last_name="Ike", basic_salary=Decimal("100000.00"), department=self.extrusion)
        self.sept = PayrollPeriod.objects.create(year=2026, month=9)

    def propose(self, department=None, employee=None, reward="15000"):
        return EmployeeOfTheMonthService.propose(department=department or self.melting, employee=employee or self.ada, year=2026, month=9, reason="Zero rejects all month", reward_amount=Decimal(reward) if reward else None, actor=self.hr)

    def test_the_winner_must_belong_to_the_department_and_there_is_one_per_department_per_month(self):
        with self.assertRaises(ValueError):
            self.propose(department=self.extrusion, employee=self.ada)
        self.propose()
        with self.assertRaises(ValueError):
            self.propose()
        self.propose(department=self.extrusion, employee=self.chi)  # another department is fine

    def test_approving_with_a_reward_pays_it_through_payroll(self):
        entry = self.propose()
        EmployeeOfTheMonthService.approve(entry, actor=self.boss)
        entry.refresh_from_db()
        self.assertEqual((entry.status, entry.bonus.status, entry.bonus.kind), ("approved", "approved", "employee_of_month"))
        generate_payroll_for_period(self.sept, actor=self.hr)
        payroll = EmployeePayroll.objects.get(payroll_period=self.sept, employee=self.ada)
        self.assertEqual(payroll.gross_earnings, Decimal("115000.00"))
        self.assertEqual(payroll.line_items.get(code="EOTM").description, "Employee of the Month - Melting (September 2026)")

    def test_without_a_reward_it_is_recognition_only(self):
        entry = self.propose(reward=None)
        EmployeeOfTheMonthService.approve(entry, actor=self.boss)
        entry.refresh_from_db()
        self.assertIsNone(entry.bonus)
        self.assertFalse(Bonus.objects.exists())

    def test_declined_or_cancelled_entries_free_the_slot_and_a_paid_reward_blocks_cancelling(self):
        entry = self.propose()
        EmployeeOfTheMonthService.decline(entry, actor=self.boss, comment="Not this month")
        second = self.propose()
        EmployeeOfTheMonthService.approve(second, actor=self.boss)
        generate_payroll_for_period(self.sept, actor=self.hr)
        with self.assertRaises(ValueError):
            EmployeeOfTheMonthService.cancel(second, actor=self.hr)
        third = self.propose(department=self.extrusion, employee=self.chi)
        EmployeeOfTheMonthService.cancel(third, actor=self.hr)
        self.assertEqual(EmployeeOfTheMonth.objects.get(pk=third.pk).status, "cancelled")

    def test_api_roles_and_the_month_overview(self):
        client = APIClient()
        client.force_authenticate(self.hr)
        created = client.post("/api/bonuses/employee-of-the-month/propose/", {"department": self.melting.pk, "employee": self.ada.pk, "year": 2026, "month": 9, "reason": "Best output", "reward_amount": "10000"}, format="json")
        self.assertEqual(created.status_code, 201)
        entry_id = created.json()["id"]
        self.assertEqual(client.post(f"/api/bonuses/employee-of-the-month/{entry_id}/approve/").status_code, 403)
        client.force_authenticate(self.boss)
        self.assertEqual(client.post(f"/api/bonuses/employee-of-the-month/{entry_id}/approve/").status_code, 200)
        overview = client.get("/api/bonuses/employee-of-the-month/?year=2026&month=9").json()
        self.assertEqual((overview["chosen"], overview["awaiting"]), (1, 0))
        melting = next(d for d in overview["departments"] if d["name"] == "Melting")
        self.assertEqual(melting["entry"]["employee_name"], "Ada Obi")
        client.force_authenticate(get_user_model().objects.create_user("nobody", password="pw"))
        self.assertEqual(client.get("/api/bonuses/employee-of-the-month/?year=2026&month=9").status_code, 403)

    def test_bonus_api_summary_and_permissions(self):
        client = APIClient()
        client.force_authenticate(self.hr)
        created = client.post("/api/bonuses/", {"employee": self.ada.pk, "amount": "20000", "reason": "Above and beyond", "performance_year": 2026, "performance_month": 9}, format="json")
        self.assertEqual(created.status_code, 201)
        listing = client.get("/api/bonuses/?year=2026&month=9").json()
        self.assertEqual((listing["summary"]["pending_count"], listing["summary"]["pending_total"]), (1, "20000.00"))
        self.assertEqual(client.post(f"/api/bonuses/{created.json()['id']}/approve/").status_code, 403)
        client.force_authenticate(self.boss)
        self.assertEqual(client.post("/api/bonuses/", {"employee": self.ada.pk, "amount": "1", "reason": "x", "performance_year": 2026, "performance_month": 9}, format="json").status_code, 403)
        self.assertEqual(client.post(f"/api/bonuses/{created.json()['id']}/approve/").json()["status"], "approved")

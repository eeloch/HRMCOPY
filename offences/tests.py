from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from employees.models import Employee
from offences.models import EmployeeOffence, EmployeeOffenceStatus, OffenceType
from offences.services import OffenceService
from payroll.models import EmployeePayroll, EmployeePayrollStatus, PayrollLineItem, PayrollPeriod
from payroll.services import generate_payroll_for_period


class OffenceDeductionTimingTests(TestCase):
    """An approved punishment comes out of that month's salary, whether or not the
    payroll record existed at the moment it was approved."""

    def setUp(self):
        self.actor = get_user_model().objects.create_user(username="offence-approver", password="pw")
        self.employee = Employee.objects.create(employee_id="000010", first_name="Ada", last_name="Okafor", basic_salary=Decimal("100000.00"))
        self.kind = OffenceType.objects.create(name="Late to work", default_amount=Decimal("2000.00"))
        self.offence = EmployeeOffence.objects.create(
            employee=self.employee, offence_type=self.kind, amount=Decimal("2000.00"), incident_date=date(2026, 9, 7), recorded_by=self.actor,
        )
        self.period = PayrollPeriod.objects.create(year=2026, month=9)

    def test_approving_before_payroll_is_generated_waits_then_deducts_when_it_is(self):
        approved = OffenceService.approve(self.offence, self.actor)

        self.assertEqual(approved.status, EmployeeOffenceStatus.APPROVED)
        self.assertFalse(PayrollLineItem.objects.exists())

        summary = generate_payroll_for_period(self.period)

        self.assertEqual(summary.deductions_applied, 1)
        self.offence.refresh_from_db()
        payroll = EmployeePayroll.objects.get(payroll_period=self.period, employee=self.employee)
        self.assertEqual((self.offence.status, self.offence.payroll), (EmployeeOffenceStatus.DEDUCTED, payroll))
        line = PayrollLineItem.objects.get(payroll=payroll)
        self.assertEqual((line.code, line.amount), ("OFFENCE", Decimal("2000.00")))
        payroll.refresh_from_db()
        self.assertEqual((payroll.total_deductions, payroll.net_pay), (Decimal("2000.00"), Decimal("98000.00")))

    def test_approving_when_the_payroll_record_already_exists_deducts_immediately(self):
        generate_payroll_for_period(self.period)

        approved = OffenceService.approve(self.offence, self.actor)

        self.assertEqual(approved.status, EmployeeOffenceStatus.DEDUCTED)
        self.assertEqual(PayrollLineItem.objects.filter(source_type="employee_offence").count(), 1)

    def test_approving_with_no_payroll_period_at_all_still_works_and_lands_in_the_incident_month(self):
        self.period.delete()
        approved = OffenceService.approve(self.offence, self.actor)
        self.assertEqual((approved.status, approved.payroll_period), (EmployeeOffenceStatus.APPROVED, None))

        period = PayrollPeriod.objects.create(year=2026, month=9)
        generate_payroll_for_period(period)

        self.offence.refresh_from_db()
        self.assertEqual(self.offence.status, EmployeeOffenceStatus.DEDUCTED)
        self.assertEqual(self.offence.payroll_period, period)

    def test_generating_again_never_deducts_the_same_offence_twice(self):
        OffenceService.approve(self.offence, self.actor)
        generate_payroll_for_period(self.period)

        again = generate_payroll_for_period(self.period)

        self.assertEqual(again.deductions_applied, 0)
        self.assertEqual(PayrollLineItem.objects.filter(source_type="employee_offence").count(), 1)

    def test_an_approved_or_paid_payroll_record_is_still_never_changed(self):
        EmployeePayroll.objects.create(
            payroll_period=self.period, employee=self.employee, basic_salary=Decimal("100000.00"),
            gross_earnings=Decimal("100000.00"), net_pay=Decimal("100000.00"), status=EmployeePayrollStatus.APPROVED,
        )
        with self.assertRaisesMessage(ValueError, "cannot accept offence deductions"):
            OffenceService.approve(self.offence, self.actor)
        self.offence.refresh_from_db()
        self.assertEqual(self.offence.status, EmployeeOffenceStatus.PENDING)


class DisciplinaryPolicyTests(TestCase):
    """The Disciplinary Action Policy: 1st/2nd/3rd penalties, non-money penalties, and the loader."""

    def setUp(self):
        from django.contrib.auth import get_user_model
        from django.contrib.auth.models import Permission

        self.user = get_user_model().objects.create_user(username="policy-clerk", password="pw")
        self.user.user_permissions.add(*Permission.objects.filter(codename__in=["record_employee_offences", "review_employee_offences"]))
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.employee = Employee.objects.create(employee_id="POL-001", first_name="Pol", last_name="Icy")

    def log(self, offence_type, **extra):
        return self.client.post("/api/offences/", {"employee": self.employee.pk, "offence_type": offence_type.pk, "incident_date": "2026-09-01", **extra}, format="json")

    def test_the_penalty_follows_how_many_times_the_person_has_done_it(self):
        late = OffenceType.objects.create(category="Attendance", name="Late arrival after 6:45", penalty_first="₦300", penalty_second="₦300", penalty_third="₦500", amount_first=300, amount_second=300, amount_third=500)
        amounts = [self.log(late).data["amount"] for _ in range(4)]
        self.assertEqual(amounts, ["300.00", "300.00", "500.00", "500.00"])
        self.assertEqual([o.occurrence for o in EmployeeOffence.objects.order_by("id")], [1, 2, 3, 4])

    def test_a_rejected_offence_does_not_count_towards_the_next_one(self):
        kind = OffenceType.objects.create(name="Throwing dirt", penalty_first="₦500", penalty_second="₦1,000", amount_first=500, amount_second=1000)
        first = self.log(kind)
        self.client.post(f"/api/offences/{first.data['id']}/reject/", {"reason": "Mistaken identity"}, format="json")
        self.assertEqual(self.log(kind).data["amount"], "500.00")

    def test_a_penalty_that_is_not_money_is_recorded_with_no_deduction_and_can_be_approved(self):
        warning = OffenceType.objects.create(category="Behaviour", name="Sleeping at work", penalty_first="Verbal Warning", penalty_second="₦5,000", amount_second=5000)
        response = self.log(warning)
        self.assertEqual(response.data["amount"], "0.00")
        self.assertEqual(response.data["penalty_text"], "Verbal Warning")
        approved = self.client.post(f"/api/offences/{response.data['id']}/approve/", {}, format="json")
        self.assertEqual(approved.status_code, 200)
        self.assertEqual(approved.data["status"], "approved")
        self.assertFalse(PayrollLineItem.objects.filter(source_type="employee_offence").exists())

    def test_a_blank_tier_falls_back_to_the_last_one_that_says_something(self):
        fighting = OffenceType.objects.create(name="Fighting", penalty_first="Dismissal")
        self.log(fighting)
        self.assertEqual(self.log(fighting).data["penalty_text"], "Dismissal")

    def test_the_amount_can_still_be_set_by_hand_for_cost_based_penalties(self):
        damage = OffenceType.objects.create(name="Damaging property", penalty_first="20% of the repair costs")
        self.assertEqual(self.log(damage, amount="12500.00").data["amount"], "12500.00")

    def test_the_preview_says_what_will_happen_before_logging(self):
        kind = OffenceType.objects.create(name="Using wrong entry", penalty_first="Verbal Warning", penalty_second="₦500", amount_second=500)
        self.log(kind)
        preview = self.client.get(f"/api/offences/penalty-preview/?employee={self.employee.pk}&offence_type={kind.pk}")
        self.assertEqual(preview.json(), {"occurrence": 2, "penalty_text": "₦500", "amount": "500.00"})

    def test_the_policy_loads_and_loading_it_again_changes_nothing(self):
        from django.core.management import call_command

        from .models import RewardType
        from .policy_data import OFFENCES, REWARDS

        call_command("load_disciplinary_policy")
        call_command("load_disciplinary_policy")
        self.assertEqual(OffenceType.objects.count(), len(OFFENCES))
        self.assertEqual(RewardType.objects.count(), len(REWARDS))
        self.assertEqual(OffenceType.objects.filter(name="Stealing").count(), 2)  # one under Behaviour At Work, one in the hostel
        absence = RewardType.objects.get(name="No absence")
        self.assertEqual((absence.amount_first, absence.amount_second, absence.auto_rule), (1000, 2000, "no_absence"))
        self.assertEqual(RewardType.objects.get(name="No lateness").amount_first, 2000)

    def test_a_hand_made_unused_type_is_adopted_not_duplicated(self):
        from django.core.management import call_command

        OffenceType.objects.create(name="STEALING", default_amount=10000)
        call_command("load_disciplinary_policy")
        self.assertEqual(OffenceType.objects.filter(name__iexact="stealing").count(), 2)
        self.assertFalse(OffenceType.objects.filter(category="", name__iexact="stealing").exists())

    def test_the_reward_list_is_available_to_people_who_can_see_offences(self):
        from django.core.management import call_command

        call_command("load_disciplinary_policy")
        response = self.client.get("/api/offences/reward-types/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["count"], 19)

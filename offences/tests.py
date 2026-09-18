from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase

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

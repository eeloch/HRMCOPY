from datetime import date
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.contrib.auth.models import Permission, User
from django.db import IntegrityError, transaction
from django.test import TestCase
from rest_framework.test import APIClient

from audit.models import AuditEvent
from employees.models import Employee
from payroll.models import EmployeePayroll, PayrollLineItem, PayrollPeriod
from payroll.services import daily_rate, generate_payroll_for_period, recalculate_employee_payroll, sync_attendance_deductions_for_period
from attendance.models import AttendanceException, DailyAttendance, EmployeeRosterDay, Shift
from leave.models import LeavePolicy, LeaveRequest, LeaveStatus, LeaveType


class PayrollFoundationTests(TestCase):
    def setUp(self):
        self.active_employee = Employee.objects.create(
            employee_id="PAY-001",
            first_name="Active",
            last_name="Employee",
            basic_salary=Decimal("150000.00"),
        )
        self.inactive_employee = Employee.objects.create(
            employee_id="PAY-002",
            first_name="Inactive",
            last_name="Employee",
            basic_salary=Decimal("100000.00"),
            status="inactive",
        )
        self.period = PayrollPeriod.objects.create(year=2028, month=2)

    def test_period_is_unique_and_calculates_calendar_dates(self):
        self.assertEqual(self.period.display_name, "February 2028")
        self.assertEqual(self.period.start_date.isoformat(), "2028-02-01")
        self.assertEqual(self.period.end_date.isoformat(), "2028-02-29")

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                PayrollPeriod.objects.create(year=2028, month=2)

    def test_generation_creates_one_record_per_active_employee_and_snapshots_salary(self):
        summary = generate_payroll_for_period(self.period)

        self.assertEqual(summary.created, 1)
        self.assertEqual(summary.existing, 0)
        self.assertFalse(EmployeePayroll.objects.filter(employee=self.inactive_employee).exists())
        payroll = EmployeePayroll.objects.get(employee=self.active_employee, payroll_period=self.period)
        self.assertEqual(payroll.basic_salary, Decimal("150000.00"))
        self.assertEqual(payroll.gross_earnings, Decimal("150000.00"))
        self.assertEqual(payroll.total_deductions, Decimal("0.00"))
        self.assertEqual(payroll.net_pay, Decimal("150000.00"))
        self.assertEqual(PayrollLineItem.objects.filter(payroll=payroll).count(), 0)
        self.assertTrue(AuditEvent.objects.filter(event_type="payroll.generated").exists())

    def test_generation_is_idempotent_and_never_overwrites_salary_snapshot(self):
        generate_payroll_for_period(self.period)
        self.active_employee.basic_salary = Decimal("200000.00")
        self.active_employee.save(update_fields=["basic_salary"])

        summary = generate_payroll_for_period(self.period)
        payroll = EmployeePayroll.objects.get(employee=self.active_employee, payroll_period=self.period)

        self.assertEqual(summary.created, 0)
        self.assertEqual(summary.existing, 1)
        self.assertEqual(EmployeePayroll.objects.count(), 1)
        self.assertEqual(payroll.basic_salary, Decimal("150000.00"))
        self.assertEqual(payroll.net_pay, Decimal("150000.00"))
        self.assertEqual(AuditEvent.objects.filter(event_type="payroll.generated").count(), 1)

    def test_line_items_recalculate_earnings_deductions_and_net_pay_using_decimals(self):
        generate_payroll_for_period(self.period)
        payroll = EmployeePayroll.objects.get(employee=self.active_employee, payroll_period=self.period)
        PayrollLineItem.objects.create(
            payroll=payroll,
            item_type="earning",
            code="ALLOWANCE",
            description="Transport allowance",
            amount=Decimal("12500.25"),
        )
        PayrollLineItem.objects.create(
            payroll=payroll,
            item_type="deduction",
            code="ADJUSTMENT",
            description="Manual adjustment",
            amount=Decimal("750.10"),
        )

        recalculated = recalculate_employee_payroll(payroll)

        self.assertEqual(recalculated.gross_earnings, Decimal("162500.25"))
        self.assertEqual(recalculated.total_deductions, Decimal("750.10"))
        self.assertEqual(recalculated.net_pay, Decimal("161750.15"))

    def test_negative_line_items_are_rejected_and_period_employee_is_unique(self):
        generate_payroll_for_period(self.period)
        payroll = EmployeePayroll.objects.get(employee=self.active_employee, payroll_period=self.period)
        negative_item = PayrollLineItem(
            payroll=payroll,
            item_type="deduction",
            code="INVALID",
            description="Invalid negative amount",
            amount=Decimal("-1.00"),
        )

        with self.assertRaises(ValidationError):
            negative_item.full_clean()
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                PayrollLineItem.objects.create(
                    payroll=payroll,
                    item_type="deduction",
                    code="INVALID-DB",
                    description="Invalid negative amount",
                    amount=Decimal("-1.00"),
                )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                EmployeePayroll.objects.create(
                    payroll_period=self.period,
                    employee=self.active_employee,
                    basic_salary=Decimal("1.00"),
                    gross_earnings=Decimal("1.00"),
                    total_deductions=Decimal("0.00"),
                    net_pay=Decimal("1.00"),
                )


class PayrollAPITests(TestCase):
    def setUp(self):
        self.employee = Employee.objects.create(employee_id="PAY-API-001", first_name="Api", last_name="Employee", basic_salary=Decimal("100000.00"))
        self.viewer = User.objects.create_user("payroll-viewer", password="password")
        self.manager = User.objects.create_user("payroll-manager", password="password")
        self.viewer.user_permissions.add(Permission.objects.get(codename="view_payroll"))
        self.manager.user_permissions.add(Permission.objects.get(codename="manage_payroll"))
        self.client = APIClient()

    def authenticate(self, user):
        self.client.force_authenticate(user)

    def test_viewer_can_view_but_cannot_manage_periods(self):
        self.authenticate(self.viewer)
        self.assertEqual(self.client.get("/api/payroll/periods/").status_code, 200)
        self.assertEqual(self.client.post("/api/payroll/periods/", {"year": 2030, "month": 1}, format="json").status_code, 403)

    def test_manager_creates_period_and_duplicate_is_rejected(self):
        self.authenticate(self.manager)
        response = self.client.post("/api/payroll/periods/", {"year": 2030, "month": 1, "notes": "January"}, format="json")
        self.assertEqual(response.status_code, 201)
        duplicate = self.client.post("/api/payroll/periods/", {"year": 2030, "month": 1}, format="json")
        self.assertEqual(duplicate.status_code, 400)

    def test_generation_summary_and_manual_line_items_recalculate(self):
        period = PayrollPeriod.objects.create(year=2030, month=2)
        self.authenticate(self.manager)
        generated = self.client.post(f"/api/payroll/periods/{period.id}/generate/")
        self.assertEqual(generated.status_code, 200)
        self.assertEqual(generated.json()["summary"]["created"], 1)
        payroll = EmployeePayroll.objects.get(payroll_period=period, employee=self.employee)
        add = self.client.post(f"/api/payroll/employee-payrolls/{payroll.id}/line-items/", {"item_type": "earning", "code": "TRANSPORT", "description": "Transport", "amount": "1200.50"}, format="json")
        self.assertEqual(add.status_code, 201)
        payroll.refresh_from_db()
        self.assertEqual(payroll.net_pay, Decimal("101200.50"))
        line_item_id = add.json()["id"]
        self.assertEqual(self.client.delete(f"/api/payroll/line-items/{line_item_id}/").status_code, 204)
        payroll.refresh_from_db()
        self.assertEqual(payroll.net_pay, Decimal("100000.00"))
        self.assertTrue(AuditEvent.objects.filter(event_type="payroll.line_item_added").exists())
        self.assertTrue(AuditEvent.objects.filter(event_type="payroll.line_item_deleted").exists())

    def test_invalid_transitions_and_approved_period_changes_are_rejected(self):
        period = PayrollPeriod.objects.create(year=2030, month=3)
        self.authenticate(self.manager)
        invalid = self.client.post(f"/api/payroll/periods/{period.id}/transition/", {"status": "review"}, format="json")
        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(self.client.post(f"/api/payroll/periods/{period.id}/transition/", {"status": "processing"}, format="json").status_code, 200)
        self.assertEqual(self.client.post(f"/api/payroll/periods/{period.id}/transition/", {"status": "review"}, format="json").status_code, 200)
        self.assertEqual(self.client.post(f"/api/payroll/periods/{period.id}/transition/", {"status": "approved"}, format="json").status_code, 200)
        self.assertEqual(self.client.post(f"/api/payroll/periods/{period.id}/generate/").status_code, 400)

    def test_period_summary_uses_employee_payroll_decimal_totals(self):
        period = PayrollPeriod.objects.create(year=2030, month=4)
        self.authenticate(self.manager)
        self.client.post(f"/api/payroll/periods/{period.id}/generate/")
        payroll = EmployeePayroll.objects.get(payroll_period=period)
        self.client.post(f"/api/payroll/employee-payrolls/{payroll.id}/line-items/", {"item_type": "deduction", "code": "ADJ", "description": "Adjustment", "amount": "250.25"}, format="json")
        response = self.client.get(f"/api/payroll/periods/{period.id}/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["total_gross_earnings"], "100000.00")
        self.assertEqual(response.json()["total_deductions"], "250.25")
        self.assertEqual(response.json()["total_net_pay"], "99749.75")

    def test_approved_period_rejects_manual_line_item_changes(self):
        period = PayrollPeriod.objects.create(year=2030, month=5)
        self.authenticate(self.manager)
        self.client.post(f"/api/payroll/periods/{period.id}/generate/")
        payroll = EmployeePayroll.objects.get(payroll_period=period)
        self.client.post(f"/api/payroll/periods/{period.id}/transition/", {"status": "processing"}, format="json")
        self.client.post(f"/api/payroll/periods/{period.id}/transition/", {"status": "review"}, format="json")
        self.client.post(f"/api/payroll/periods/{period.id}/transition/", {"status": "approved"}, format="json")
        response = self.client.post(f"/api/payroll/employee-payrolls/{payroll.id}/line-items/", {"item_type": "earning", "code": "LOCKED", "description": "Not allowed", "amount": "1.00"}, format="json")
        self.assertEqual(response.status_code, 400)


class PayrollAttendanceIntegrationTests(TestCase):
    def setUp(self):
        self.employee = Employee.objects.create(employee_id="PAY-ATT-001", first_name="Payroll", last_name="Attendance", basic_salary=Decimal("150000.00"))
        self.period = PayrollPeriod.objects.create(year=2030, month=6)
        self.shift = Shift.objects.create(name="Payroll Test Shift", start_time="07:00", end_time="19:00")
        self.attendance = DailyAttendance.objects.create(employee=self.employee, date=date(2030, 6, 5), shift=self.shift, status="absent")
        self.manager = User.objects.create_user("payroll-sync-manager", password="password")
        self.manager.user_permissions.add(Permission.objects.get(codename="manage_payroll"))
        self.client = APIClient()
        self.client.force_authenticate(self.manager)

    def exception(self, exception_type="absence", status="pending"):
        return AttendanceException.objects.create(attendance=self.attendance, exception_type=exception_type, status=status)

    def move_to_approved(self):
        for value in ("processing", "review", "approved"):
            response = self.client.post(f"/api/payroll/periods/{self.period.id}/transition/", {"status": value}, format="json")
            self.assertEqual(response.status_code, 200)

    def test_pending_exceptions_block_approval_but_held_exceptions_do_not(self):
        self.exception(status="pending")
        self.assertEqual(self.client.post(f"/api/payroll/periods/{self.period.id}/transition/", {"status": "processing"}, format="json").status_code, 200)
        self.assertEqual(self.client.post(f"/api/payroll/periods/{self.period.id}/transition/", {"status": "review"}, format="json").status_code, 200)
        blocked = self.client.post(f"/api/payroll/periods/{self.period.id}/transition/", {"status": "approved"}, format="json")
        self.assertEqual(blocked.status_code, 400)
        self.assertIn("1 pending", blocked.json()["detail"])
        AttendanceException.objects.update(status="held")
        self.assertEqual(self.client.post(f"/api/payroll/periods/{self.period.id}/transition/", {"status": "approved"}, format="json").status_code, 200)

    def test_sync_reports_reviewed_items_without_inventing_deductions_or_roster_days(self):
        self.exception(status="approved")
        self.exception(exception_type="late", status="approved")
        self.exception(exception_type="early_departure", status="held")
        PayrollLineItem.objects.create(payroll=EmployeePayroll.objects.create(payroll_period=self.period, employee=self.employee, basic_salary=Decimal("150000"), gross_earnings=Decimal("150000"), net_pay=Decimal("150000")), item_type="deduction", code="MANUAL", description="Manual", amount=Decimal("50"), source_type="manual")
        response = self.client.post(f"/api/payroll/periods/{self.period.id}/sync-attendance/")
        self.assertEqual(response.status_code, 200)
        summary = response.json()["summary"]
        self.assertEqual(summary["approved_absence"], 1)
        self.assertEqual(summary["approved_lateness"], 1)
        self.assertEqual(summary["held"], 1)
        self.assertEqual(summary["created"], 0)
        self.assertFalse(summary["roster_required"])
        self.assertEqual(PayrollLineItem.objects.count(), 1)
        self.assertEqual(self.client.post(f"/api/payroll/periods/{self.period.id}/sync-attendance/").json()["summary"], summary)

    def test_waived_pending_held_and_unsupported_exception_types_create_no_deductions(self):
        self.exception(status="waived")
        self.exception(exception_type="missing_clock_out", status="pending")
        self.exception(exception_type="early_departure", status="approved")
        summary = sync_attendance_deductions_for_period(self.period)
        self.assertEqual(summary.created, 0)
        self.assertEqual(summary.pending, 1)
        self.assertEqual(summary.approved_early_departure, 1)
        self.assertEqual(PayrollLineItem.objects.count(), 0)

    def test_paid_and_unpaid_leave_are_reported_without_monetary_line_items(self):
        paid = LeaveType.objects.create(name="Payroll Test Paid", code="PAY-TEST-PAID", is_paid=True)
        unpaid = LeaveType.objects.create(name="Payroll Test Unpaid", code="PAY-TEST-UNPAID", is_paid=False)
        LeavePolicy.objects.create(leave_type=paid, employment_type=self.employee.employment_type, allocated_days=10, is_paid=True)
        LeavePolicy.objects.create(leave_type=unpaid, employment_type=self.employee.employment_type, allocated_days=10, is_paid=False)
        for index, leave_type in enumerate((paid, unpaid), start=1):
            LeaveRequest.objects.create(request_number=f"PAY-TEST-{index}", employee=self.employee, leave_type=leave_type, start_date=date(2030, 6, 10), end_date=date(2030, 6, 10), approved_start_date=date(2030, 6, 10), approved_end_date=date(2030, 6, 10), approved_days=Decimal("1.00"), total_days=Decimal("1.00"), reason="Test", status=LeaveStatus.APPROVED)
        summary = sync_attendance_deductions_for_period(self.period)
        self.assertEqual(summary.paid_leave, 1)
        self.assertEqual(summary.unpaid_leave, 1)
        self.assertEqual(PayrollLineItem.objects.count(), 0)

    def test_approved_payroll_cannot_be_synchronized(self):
        self.move_to_approved()
        response = self.client.post(f"/api/payroll/periods/{self.period.id}/sync-attendance/")
        self.assertEqual(response.status_code, 400)


class PayrollMonetaryAttendanceTests(TestCase):
    def setUp(self):
        self.employee = Employee.objects.create(employee_id="PAY-V4-001", first_name="Payroll", last_name="V4", basic_salary=Decimal("150000.00"))
        self.period = PayrollPeriod.objects.create(year=2032, month=6)
        self.shift = Shift.objects.create(name="Payroll V4 Shift", start_time="07:00", end_time="19:00")
        self.payroll = EmployeePayroll.objects.create(payroll_period=self.period, employee=self.employee, basic_salary=Decimal("150000.00"), gross_earnings=Decimal("150000.00"), net_pay=Decimal("150000.00"))

    def complete_roster(self):
        for day in range(1, 31):
            EmployeeRosterDay.objects.get_or_create(employee=self.employee, date=date(2032, 6, day), defaults={"status": "work" if day <= 27 else "rest", "shift": self.shift if day <= 27 else None, "source": "manual"})

    def absence(self, day, status="approved", exception_type="absence"):
        attendance = DailyAttendance.objects.create(employee=self.employee, date=date(2032, 6, day), shift=self.shift, status="absent")
        return AttendanceException.objects.create(attendance=attendance, exception_type=exception_type, status=status)

    def unpaid_leave(self, start_day, end_day, *, paid=False, partial=False):
        leave_type = LeaveType.objects.create(name=f"V4 {'Paid' if paid else 'Unpaid'} {start_day}", code=f"V4-{start_day}-{'P' if paid else 'U'}", is_paid=paid)
        LeavePolicy.objects.create(leave_type=leave_type, employment_type=self.employee.employment_type, allocated_days=30, is_paid=paid)
        return LeaveRequest.objects.create(request_number=f"V4-LV-{start_day}-{end_day}-{paid}", employee=self.employee, leave_type=leave_type, start_date=date(2032, 6, start_day), end_date=date(2032, 6, end_day), approved_start_date=date(2032, 6, start_day), approved_end_date=date(2032, 6, end_day), total_days=Decimal("0.50") if partial else Decimal(end_day - start_day + 1), approved_days=Decimal("0.50") if partial else Decimal(end_day - start_day + 1), duration_type="first_half" if partial else "full_day", reason="Test", status=LeaveStatus.PARTIALLY_APPROVED if partial else LeaveStatus.APPROVED)

    def test_daily_rate_and_approved_absences_use_roster_decimal_divisor(self):
        self.complete_roster()
        self.absence(3); self.absence(4)
        self.assertEqual(daily_rate(Decimal("150000.00"), 27), Decimal("5555.56"))
        summary = sync_attendance_deductions_for_period(self.period)
        lines = PayrollLineItem.objects.filter(payroll=self.payroll, is_system_generated=True)
        self.assertEqual(summary.absence_deductions_created, 2)
        self.assertEqual(lines.count(), 2)
        self.assertEqual(sum((line.amount for line in lines), Decimal("0.00")), Decimal("11111.12"))
        self.payroll.refresh_from_db(); self.assertEqual(self.payroll.net_pay, Decimal("138888.88"))

    def test_unpaid_leave_uses_work_dates_only_and_prevents_same_date_absence_double_charge(self):
        self.complete_roster()
        self.unpaid_leave(27, 29)
        self.absence(27)
        summary = sync_attendance_deductions_for_period(self.period)
        lines = PayrollLineItem.objects.filter(payroll=self.payroll, is_system_generated=True)
        self.assertEqual(summary.unpaid_leave_deductions_created, 1)
        self.assertEqual(summary.rest_days_ignored, 2)
        self.assertEqual(lines.count(), 1)
        self.assertEqual(lines.first().code, "UNPAID_LEAVE")
        self.assertEqual(lines.first().amount, Decimal("5555.56"))

    def test_paid_leave_and_unreviewed_or_unsupported_exceptions_create_no_money_lines(self):
        self.complete_roster()
        self.unpaid_leave(5, 5, paid=True)
        self.absence(6, status="waived"); self.absence(7, status="held"); self.absence(8, status="pending")
        self.absence(9, exception_type="late"); self.absence(10, exception_type="early_departure"); self.absence(11, exception_type="missing_clock_out")
        summary = sync_attendance_deductions_for_period(self.period)
        self.assertEqual(PayrollLineItem.objects.filter(payroll=self.payroll, is_system_generated=True).count(), 0)
        self.assertEqual(summary.paid_leave_days_ignored, Decimal("1.00"))
        self.assertEqual(summary.unsupported, 1)

    def test_incomplete_roster_skips_financial_sync_and_unchanged_rerun_is_idempotent(self):
        EmployeeRosterDay.objects.create(employee=self.employee, date=date(2032, 6, 1), status="work", shift=self.shift)
        self.absence(1)
        skipped = sync_attendance_deductions_for_period(self.period)
        self.assertEqual(skipped.employees_skipped_incomplete_roster, 1)
        self.assertFalse(PayrollLineItem.objects.filter(payroll=self.payroll).exists())
        self.complete_roster()
        first = sync_attendance_deductions_for_period(self.period)
        audit_count = AuditEvent.objects.filter(event_type="payroll.attendance_deductions_synced").count()
        second = sync_attendance_deductions_for_period(self.period)
        self.assertEqual(first.created, 1)
        self.assertEqual(second.created, 0)
        self.assertEqual(second.existing, 1)
        self.assertEqual(AuditEvent.objects.filter(event_type="payroll.attendance_deductions_synced").count(), audit_count)

    def test_manual_items_survive_and_locked_period_rejects_sync(self):
        self.complete_roster(); self.absence(2)
        manual = PayrollLineItem.objects.create(payroll=self.payroll, item_type="deduction", code="MANUAL", description="Manual", amount=Decimal("10.00"), source_type="manual")
        sync_attendance_deductions_for_period(self.period)
        self.assertTrue(PayrollLineItem.objects.filter(pk=manual.pk).exists())
        self.period.status = "approved"; self.period.save(update_fields=["status"])
        with self.assertRaises(ValueError): sync_attendance_deductions_for_period(self.period)

    def attendance_exception(self, day, exception_type, minutes, status="approved"):
        attendance, _ = DailyAttendance.objects.get_or_create(
            employee=self.employee,
            date=date(2032, 6, day),
            defaults={"shift": self.shift, "status": "late"},
        )
        return AttendanceException.objects.create(
            attendance=attendance,
            exception_type=exception_type,
            minutes_affected=minutes,
            status=status,
        )

    def test_lateness_fixed_bands_and_half_day_rate_use_roster_denominator(self):
        self.complete_roster()
        cases = [(1, Decimal("300.00"), "1_15"), (15, Decimal("300.00"), "1_15"), (16, Decimal("500.00"), "16_60"), (60, Decimal("500.00"), "16_60"), (61, Decimal("2777.78"), "over_60_half_day"), (120, Decimal("2777.78"), "over_60_half_day")]
        for day, (minutes, expected_amount, policy_band) in enumerate(cases, start=1):
            exception = self.attendance_exception(day, "late", minutes)
            summary = sync_attendance_deductions_for_period(self.period)
            line = PayrollLineItem.objects.get(payroll=self.payroll, source_reference=str(exception.pk))
            self.assertEqual(line.code, "ATTENDANCE_LATE")
            self.assertEqual(line.amount, expected_amount)
            self.assertEqual(line.metadata["minutes_affected"], minutes)
            self.assertEqual(line.metadata["policy_band"], policy_band)
            if minutes > 60:
                self.assertEqual(line.metadata["daily_rate"], "5555.56")
                self.assertEqual(line.metadata["half_day_rate"], "2777.78")
            self.assertEqual(summary.lateness_deductions_created, 1)

    def test_early_departure_fixed_bands_and_half_day_rate(self):
        self.complete_roster()
        cases = [(1, Decimal("300.00")), (15, Decimal("300.00")), (16, Decimal("500.00")), (60, Decimal("500.00")), (61, Decimal("2777.78"))]
        for day, (minutes, expected_amount) in enumerate(cases, start=7):
            exception = self.attendance_exception(day, "early_departure", minutes)
            sync_attendance_deductions_for_period(self.period)
            line = PayrollLineItem.objects.get(payroll=self.payroll, source_reference=str(exception.pk))
            self.assertEqual(line.code, "ATTENDANCE_EARLY_DEPARTURE")
            self.assertEqual(line.amount, expected_amount)

    def test_unapproved_and_zero_minute_exceptions_create_no_deduction(self):
        self.complete_roster()
        for day, status in enumerate(("pending", "waived", "held"), start=1):
            self.attendance_exception(day, "late", 30, status=status)
        self.attendance_exception(4, "late", 0)
        sync_attendance_deductions_for_period(self.period)
        self.assertFalse(PayrollLineItem.objects.filter(payroll=self.payroll, is_system_generated=True).exists())

    def test_full_day_absence_and_approved_leave_suppress_same_date_exception_penalties(self):
        self.complete_roster()
        self.absence(3)
        late_with_absence = self.attendance_exception(3, "late", 30)
        self.unpaid_leave(4, 4)
        late_with_leave = self.attendance_exception(4, "early_departure", 30)
        sync_attendance_deductions_for_period(self.period)
        self.assertTrue(PayrollLineItem.objects.filter(payroll=self.payroll, code="ATTENDANCE_ABSENCE").exists())
        self.assertTrue(PayrollLineItem.objects.filter(payroll=self.payroll, code="UNPAID_LEAVE").exists())
        self.assertFalse(PayrollLineItem.objects.filter(payroll=self.payroll, source_reference=str(late_with_absence.pk)).exists())
        self.assertFalse(PayrollLineItem.objects.filter(payroll=self.payroll, source_reference=str(late_with_leave.pk)).exists())

    def test_lateness_resync_is_idempotent_and_preserves_attendance_and_salary_snapshot(self):
        self.complete_roster()
        exception = self.attendance_exception(5, "late", 61)
        original_minutes, original_status, original_salary = exception.minutes_affected, exception.status, self.payroll.basic_salary
        first = sync_attendance_deductions_for_period(self.period)
        audit_count = AuditEvent.objects.filter(event_type="payroll.attendance_deductions_synced").count()
        second = sync_attendance_deductions_for_period(self.period)
        self.assertEqual(first.lateness_deductions_created, 1)
        self.assertEqual(second.lateness_deductions_existing, 1)
        self.assertEqual(PayrollLineItem.objects.filter(payroll=self.payroll, source_reference=str(exception.pk)).count(), 1)
        self.assertEqual(AuditEvent.objects.filter(event_type="payroll.attendance_deductions_synced").count(), audit_count)
        exception.refresh_from_db(); self.payroll.refresh_from_db()
        self.assertEqual((exception.minutes_affected, exception.status), (original_minutes, original_status))
        self.assertEqual(self.payroll.basic_salary, original_salary)

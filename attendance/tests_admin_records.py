from datetime import date, datetime, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from attendance.models import (
    AttendanceEvent,
    AttendanceException,
    BiometricDevice,
    DailyAttendance,
    EmployeeRosterDay,
    OvertimeRecord,
    Shift,
)
from audit.models import AuditEvent
from employees.models import Department, Employee
from payroll.models import PayrollPeriod


def aware(year, month, day, hour=0, minute=0):
    return timezone.make_aware(datetime(year, month, day, hour, minute))


class AdminRecordsBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin_user = get_user_model().objects.create_superuser("root", "root@example.com", "pw")
        cls.department = Department.objects.create(name="Packing")
        cls.employee = Employee.objects.create(
            employee_id="000684", first_name="Ada", middle_name="Ngozi", last_name="Okafor",
            department=cls.department, basic_salary=Decimal("150000.00"),
        )
        cls.other = Employee.objects.create(employee_id="000701", first_name="Bola", last_name="Adeyemi", department=cls.department)
        cls.day_shift = Shift.objects.create(name="Day", start_time="07:00", end_time="19:00")
        cls.night_shift = Shift.objects.create(name="Night", start_time="19:00", end_time="07:00", is_overnight=True)
        cls.device = BiometricDevice.objects.create(name="Gate 1", serial_number="SN-ADMIN-1", location="Factory", device_type="factory")
        cls.work_date = date(2025, 6, 5)

        for offset in range(-2, 3):
            EmployeeRosterDay.objects.create(employee=cls.employee, date=cls.work_date + timedelta(days=offset), status="work", shift=cls.day_shift)

        cls.attendance = DailyAttendance.objects.create(
            employee=cls.employee, date=cls.work_date, shift=cls.day_shift,
            scheduled_start=aware(2025, 6, 5, 7), scheduled_end=aware(2025, 6, 5, 19),
            actual_clock_in=aware(2025, 6, 5, 7, 20), actual_clock_out=aware(2025, 6, 5, 20, 30),
            late_minutes=20, worked_minutes=730, overtime_minutes=90, status="late",
        )
        cls.exception = AttendanceException.objects.create(attendance=cls.attendance, exception_type="late", minutes_affected=20, proposed_deduction=Decimal("300.00"))
        cls.absence = AttendanceException.objects.create(attendance=cls.attendance, exception_type="absence", minutes_affected=720)
        cls.event = AttendanceEvent.objects.create(employee=cls.employee, device=cls.device, timestamp=aware(2025, 6, 5, 7, 20), verification_type="face", external_event_id="e1", raw_payload={"a": 1})
        cls.roster_row = EmployeeRosterDay.objects.get(employee=cls.employee, date=cls.work_date)
        cls.overtime = OvertimeRecord.objects.create(
            employee=cls.employee, attendance=cls.attendance, shift=cls.day_shift, work_date=cls.work_date,
            scheduled_end=aware(2025, 6, 5, 19), actual_clock_out=aware(2025, 6, 5, 20, 30),
            threshold_minutes_snapshot=60, potential_overtime_minutes=30, payment_due_date=cls.work_date + timedelta(days=1),
        )

    def setUp(self):
        self.client.force_login(self.admin_user)

    def act(self, name, model, ids, action, confirm=True, **extra):
        url = reverse(f"admin:attendance_{model}_changelist")
        data = {"action": action, "_selected_action": [str(i) for i in ids], "select_across": "0", **extra}
        if confirm:
            data["confirm"] = "yes"
        return self.client.post(url, data)

    def events(self, event_type):
        return AuditEvent.objects.filter(event_type=event_type)


class ChangelistTests(AdminRecordsBase):
    def test_every_changelist_and_change_form_loads(self):
        rows = {
            "attendanceevent": self.event, "dailyattendance": self.attendance, "attendanceexception": self.exception,
            "overtimerecord": self.overtime, "employeerosterday": self.roster_row,
        }
        for model, obj in rows.items():
            response = self.client.get(reverse(f"admin:attendance_{model}_changelist"))
            self.assertEqual(response.status_code, 200, model)
            self.assertContains(response, "000684", msg_prefix=model)
            self.assertContains(response, "Ada Ngozi Okafor", msg_prefix=model)
            self.assertContains(response, "Packing", msg_prefix=model)
            response = self.client.get(reverse(f"admin:attendance_{model}_change", args=[obj.pk]))
            self.assertEqual(response.status_code, 200, model)

    def test_search_by_staff_number_and_name(self):
        for model in ("attendanceevent", "dailyattendance", "attendanceexception", "overtimerecord", "employeerosterday"):
            url = reverse(f"admin:attendance_{model}_changelist")
            for term in ("000684", "Okafor", "ada", "Ngozi", "Ada Okafor"):
                response = self.client.get(url, {"q": term})
                self.assertEqual(response.context["cl"].result_count, {"attendanceexception": 2, "employeerosterday": 5}.get(model, 1), f"{model} {term}")
            self.assertEqual(self.client.get(url, {"q": "Adeyemi"}).context["cl"].result_count, 0, model)

    def test_filters_work(self):
        url = reverse("admin:attendance_dailyattendance_changelist")
        self.assertEqual(self.client.get(url, {"status__exact": "late"}).context["cl"].result_count, 1)
        self.assertEqual(self.client.get(url, {"status__exact": "absent"}).context["cl"].result_count, 0)
        self.assertEqual(self.client.get(url, {"needs_review": "yes"}).context["cl"].result_count, 1)
        self.assertEqual(self.client.get(url, {"needs_review": "no"}).context["cl"].result_count, 0)
        self.assertEqual(self.client.get(url, {"date__gte": "2025-06-05", "date__lt": "2025-06-06"}).context["cl"].result_count, 1)
        self.assertEqual(self.client.get(reverse("admin:attendance_attendanceexception_changelist"), {"status__exact": "pending", "exception_type__exact": "late"}).context["cl"].result_count, 1)

    def test_changelists_do_not_run_a_query_per_row(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        def count(model):
            with CaptureQueriesContext(connection) as context:
                self.assertEqual(self.client.get(reverse(f"admin:attendance_{model}_changelist")).status_code, 200)
            return len(context)

        models = ("attendanceevent", "dailyattendance", "attendanceexception", "overtimerecord", "employeerosterday")
        before = {model: count(model) for model in models}
        for offset in range(1, 12):
            employee = Employee.objects.create(employee_id=f"9{offset:05d}", first_name="Extra", last_name=f"Person{offset}", department=self.department)
            day = DailyAttendance.objects.create(employee=employee, date=self.work_date, shift=self.day_shift, scheduled_start=aware(2025, 6, 5, 7), scheduled_end=aware(2025, 6, 5, 19), status="absent")
            AttendanceException.objects.create(attendance=day, exception_type="absence")
            AttendanceEvent.objects.create(employee=employee, device=self.device, timestamp=aware(2025, 6, 5, 8), external_event_id=f"x{offset}")
            EmployeeRosterDay.objects.create(employee=employee, date=self.work_date, status="work", shift=self.day_shift)
        for model in models:
            self.assertEqual(count(model), before[model], model)

    def test_str_contains_the_name(self):
        self.assertEqual(str(self.attendance), "000684 Ada Ngozi Okafor - 2025-06-05")
        self.assertEqual(str(self.exception), "000684 Ada Ngozi Okafor - late - 05 Jun 2025")
        self.assertIn("000684 Ada Ngozi Okafor - 2025-06-05 07:20", str(self.event))
        self.assertIn("Ada Ngozi Okafor", str(self.overtime))
        self.assertIn("Ada Ngozi Okafor", str(self.roster_row))


class ReadOnlyTests(AdminRecordsBase):
    def test_events_are_view_only_and_have_no_delete(self):
        add = self.client.get(reverse("admin:attendance_attendanceevent_add"))
        self.assertEqual(add.status_code, 403)
        change = reverse("admin:attendance_attendanceevent_change", args=[self.event.pk])
        self.assertEqual(self.client.post(change, {"verification_type": "card"}).status_code, 403)
        self.assertEqual(self.client.get(reverse("admin:attendance_attendanceevent_delete", args=[self.event.pk])).status_code, 403)
        listing = self.client.get(reverse("admin:attendance_attendanceevent_changelist"))
        self.assertNotIn("delete_selected", listing.context["action_form"].fields["action"].choices.__str__())
        self.event.refresh_from_db()
        self.assertEqual(self.event.verification_type, "face")

    def test_overtime_cannot_be_added_edited_or_deleted(self):
        self.assertEqual(self.client.get(reverse("admin:attendance_overtimerecord_add")).status_code, 403)
        self.assertEqual(self.client.get(reverse("admin:attendance_overtimerecord_delete", args=[self.overtime.pk])).status_code, 403)


class DailyAttendanceActionTests(AdminRecordsBase):
    def test_reprocess_needs_confirmation_then_recalculates_and_audits(self):
        AttendanceEvent.objects.create(employee=self.employee, device=self.device, timestamp=aware(2025, 6, 5, 7, 5), external_event_id="e0")
        AttendanceEvent.objects.create(employee=self.employee, device=self.device, timestamp=aware(2025, 6, 5, 19, 10), external_event_id="e2")
        page = self.act("x", "dailyattendance", [self.attendance.pk], "reprocess_selected", confirm=False)
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Re-process selected days")
        self.attendance.refresh_from_db()
        self.assertEqual(self.attendance.late_minutes, 20)  # nothing happened yet

        response = self.act("x", "dailyattendance", [self.attendance.pk], "reprocess_selected")
        self.assertEqual(response.status_code, 302)
        self.attendance.refresh_from_db()
        self.assertEqual((self.attendance.late_minutes, self.attendance.actual_clock_in, self.attendance.actual_clock_out), (5, aware(2025, 6, 5, 7, 5), aware(2025, 6, 5, 19, 10)))
        event = self.events("attendance.admin_reprocessed").get()
        self.assertEqual(event.actor, self.admin_user)
        self.assertEqual(event.metadata["outcomes"], {"changed": 1})

    def test_rest_day_is_left_alone(self):
        EmployeeRosterDay.objects.filter(pk=self.roster_row.pk).update(status="rest", shift=None)
        self.act("x", "dailyattendance", [self.attendance.pk], "reprocess_selected")
        self.attendance.refresh_from_db()
        self.assertEqual(self.attendance.late_minutes, 20)
        self.assertEqual(self.events("attendance.admin_reprocessed").get().metadata["outcomes"], {"rest_day": 1})

    def test_punch_action_maps_night_punch_to_the_shift_start_day(self):
        EmployeeRosterDay.objects.filter(employee=self.employee, date=date(2025, 6, 6)).update(shift=self.night_shift)
        punch = AttendanceEvent.objects.create(employee=self.employee, device=self.device, timestamp=aware(2025, 6, 7, 1, 0), external_event_id="night")
        response = self.act("x", "attendanceevent", [punch.pk], "reprocess_days")
        self.assertEqual(response.status_code, 302)
        self.assertTrue(DailyAttendance.objects.filter(employee=self.employee, date=date(2025, 6, 6)).exists())
        event = self.events("attendance.admin_reprocessed").get()
        self.assertIn("1 selected punch", event.metadata["source"])

    def test_manual_edit_is_audited(self):
        url = reverse("admin:attendance_dailyattendance_change", args=[self.attendance.pk])
        page = self.client.get(url)
        form = page.context["adminform"].form
        data = {name: (form[name].value() if form[name].value() is not None else "") for name in form.fields if name not in {"actual_clock_in", "actual_clock_out", "scheduled_start", "scheduled_end"}}
        data.update({
            "scheduled_start_0": "2025-06-05", "scheduled_start_1": "07:00:00", "scheduled_end_0": "2025-06-05", "scheduled_end_1": "19:00:00",
            "actual_clock_in_0": "2025-06-05", "actual_clock_in_1": "07:20:00", "actual_clock_out_0": "2025-06-05", "actual_clock_out_1": "20:30:00",
            "late_minutes": "25",
        })
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 302, getattr(response, "context", None) and response.context["adminform"].form.errors)
        self.assertEqual(self.events("attendance.daily_edited").get().metadata["changes"]["late_minutes"], {"from": 20, "to": 25})


class ExceptionActionTests(AdminRecordsBase):
    def test_approve_mirrors_the_api_and_audits(self):
        page = self.act("x", "attendanceexception", [self.exception.pk], "approve_selected", confirm=False)
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Approve selected exceptions")
        self.act("x", "attendanceexception", [self.exception.pk], "approve_selected", reason="ok")
        self.exception.refresh_from_db()
        self.assertEqual((self.exception.status, self.exception.reviewed_by), ("approved", "root"))
        self.assertIsNotNone(self.exception.reviewed_at)
        event = self.events("attendance.exception_approved").get()
        self.assertEqual((event.actor, event.employee), (self.admin_user, self.employee))

    def test_approved_absence_applies_meal_penalty_like_the_api(self):
        from meals.models import MealAbsencePenalty

        self.act("x", "attendanceexception", [self.absence.pk], "approve_selected")
        self.assertEqual(MealAbsencePenalty.objects.filter(employee=self.employee).count(), 1)

    def test_waive_requires_a_reason(self):
        response = self.act("x", "attendanceexception", [self.exception.pk], "waive_selected")
        self.assertEqual(response.status_code, 200)  # form re-shown with an error
        self.exception.refresh_from_db()
        self.assertEqual(self.exception.status, "pending")
        self.act("x", "attendanceexception", [self.exception.pk], "waive_selected", reason="Gate was down")
        self.exception.refresh_from_db()
        self.assertEqual((self.exception.status, self.exception.admin_comment), ("waived", "Gate was down"))
        self.assertEqual(self.events("attendance.exception_waived").count(), 1)

    def test_hold_and_already_reviewed_rows_are_skipped(self):
        self.act("x", "attendanceexception", [self.exception.pk], "hold_selected", reason="check cctv")
        self.exception.refresh_from_db()
        self.assertEqual(self.exception.status, "held")
        response = self.act("x", "attendanceexception", [self.exception.pk], "waive_selected", reason="again")
        self.assertEqual(response.status_code, 302)
        self.exception.refresh_from_db()
        self.assertEqual(self.exception.status, "held")

    def test_reopen_sends_back_to_pending_and_audits(self):
        self.act("x", "attendanceexception", [self.exception.pk], "waive_selected", reason="mistake")
        self.act("x", "attendanceexception", [self.exception.pk], "reopen_selected", reason="Waived wrongly")
        self.exception.refresh_from_db()
        self.assertEqual((self.exception.status, self.exception.reviewed_by, self.exception.reviewed_at), ("pending", "", None))
        self.assertIn("Waived wrongly", self.exception.admin_comment)
        event = self.events("attendance.exception_reopened").get()
        self.assertEqual(event.metadata["previous"]["status"], "waived")

    def test_reopen_is_blocked_when_payroll_is_locked(self):
        self.act("x", "attendanceexception", [self.exception.pk], "approve_selected")
        PayrollPeriod.objects.create(year=2025, month=6, status="approved")
        self.act("x", "attendanceexception", [self.exception.pk], "reopen_selected", reason="oops")
        self.exception.refresh_from_db()
        self.assertEqual(self.exception.status, "approved")
        self.assertFalse(self.events("attendance.exception_reopened").exists())

    def test_reopen_is_blocked_for_absence_with_meal_penalty(self):
        self.act("x", "attendanceexception", [self.absence.pk], "approve_selected")
        self.act("x", "attendanceexception", [self.absence.pk], "reopen_selected", reason="oops")
        self.absence.refresh_from_db()
        self.assertEqual(self.absence.status, "approved")

    def test_status_cannot_be_changed_on_the_form(self):
        url = reverse("admin:attendance_attendanceexception_change", args=[self.exception.pk])
        page = self.client.get(url)
        self.assertNotIn("status", page.context["adminform"].form.fields)


class OvertimeActionTests(AdminRecordsBase):
    def test_approve_then_mark_paid(self):
        for offset in range(1, 31):
            EmployeeRosterDay.objects.get_or_create(employee=self.employee, date=date(2025, 6, offset), defaults={"status": "work", "shift": self.day_shift})
        self.act("x", "overtimerecord", [self.overtime.pk], "approve_selected", reason="fine")
        self.overtime.refresh_from_db()
        self.assertEqual((self.overtime.status, self.overtime.approved_overtime_minutes), ("approved", 30))
        self.assertIsNotNone(self.overtime.payable_amount)
        self.assertEqual(self.events("attendance.overtime_approved").get().actor, self.admin_user)
        self.act("x", "overtimerecord", [self.overtime.pk], "mark_paid_selected", reason="bank ref 1")
        self.overtime.refresh_from_db()
        self.assertEqual(self.overtime.payment_status, "paid")
        self.assertEqual(self.events("attendance.overtime_paid").count(), 1)

    def test_reject_needs_a_reason(self):
        response = self.act("x", "overtimerecord", [self.overtime.pk], "reject_selected")
        self.assertEqual(response.status_code, 200)
        self.overtime.refresh_from_db()
        self.assertEqual(self.overtime.status, "pending")
        self.act("x", "overtimerecord", [self.overtime.pk], "reject_selected", reason="not authorised")
        self.overtime.refresh_from_db()
        self.assertEqual((self.overtime.status, self.overtime.review_comment), ("rejected", "not authorised"))
        self.assertEqual(self.events("attendance.overtime_rejected").count(), 1)


class RosterActionTests(AdminRecordsBase):
    def test_mark_rest_then_work(self):
        self.act("x", "employeerosterday", [self.roster_row.pk], "mark_rest", reason="Public holiday")
        self.roster_row.refresh_from_db()
        self.assertEqual((self.roster_row.status, self.roster_row.shift, self.roster_row.source, self.roster_row.updated_by), ("rest", None, "override", self.admin_user))
        self.assertEqual(self.roster_row.notes, "Public holiday")
        self.assertEqual(self.events("attendance.roster_day_overridden").count(), 1)

        page = self.act("x", "employeerosterday", [self.roster_row.pk], "mark_work", confirm=False)
        self.assertContains(page, "Shift")
        response = self.act("x", "employeerosterday", [self.roster_row.pk], "mark_work", shift=self.night_shift.pk)
        self.assertEqual(response.status_code, 302)
        self.roster_row.refresh_from_db()
        self.assertEqual((self.roster_row.status, self.roster_row.shift), ("work", self.night_shift))
        self.assertEqual(self.events("attendance.roster_day_overridden").count(), 2)

    def test_editing_a_generated_day_makes_it_an_override(self):
        EmployeeRosterDay.objects.filter(pk=self.roster_row.pk).update(source="generated")
        url = reverse("admin:attendance_employeerosterday_change", args=[self.roster_row.pk])
        response = self.client.post(url, {"status": "work", "shift": self.night_shift.pk, "source": "generated", "notes": ""})
        self.assertEqual(response.status_code, 302)
        self.roster_row.refresh_from_db()
        self.assertEqual((self.roster_row.shift, self.roster_row.source), (self.night_shift, "override"))
        self.assertEqual(self.events("attendance.roster_day_edited").count(), 1)

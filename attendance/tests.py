from datetime import date, datetime
from decimal import Decimal

from django.test import TestCase, override_settings
from django.utils import timezone
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from rest_framework.test import APIClient

from attendance.models import AttendanceEvent, AttendanceException, BiometricDevice, DailyAttendance, EmployeeRosterDay, OvertimeRecord, Shift, ShiftAssignment
from attendance.integrations import BiometricIngestionService, NormalizedBiometricPunch
from attendance.services.processing import process_employee_attendance
from attendance.services.roster import IncompleteRosterError, expected_attendance_days, generate_roster, roster_completeness
from attendance.services.overtime import approve_overtime, reject_overtime, sync_overtime_for_date
from audit.models import AuditEvent
from employees.models import BiometricIdentity, Employee
from leave.models import LeaveRequest, LeaveStatus, LeaveType
from meals.models import (
    MealAbsencePenalty,
    MealAbsencePenaltyStatus,
    MealAbsencePenaltyType,
)
from payroll.models import PayrollSetting


class OvertimeServiceTests(TestCase):
    def setUp(self):
        self.work_date = date(2033, 6, 5)
        self.employee = Employee.objects.create(employee_id="OT-001", first_name="Overtime", last_name="Employee", basic_salary="150000.00")
        self.shift = Shift.objects.create(name="Overtime Day Shift", start_time="07:00", end_time="19:00")
        for day in range(1, 31):
            EmployeeRosterDay.objects.create(employee=self.employee, date=date(2033, 6, day), status="work" if day <= 27 else "rest", shift=self.shift if day <= 27 else None)
        self.attendance = DailyAttendance.objects.create(employee=self.employee, date=self.work_date, shift=self.shift, scheduled_start=timezone.make_aware(datetime(2033, 6, 5, 7)), scheduled_end=timezone.make_aware(datetime(2033, 6, 5, 19)), actual_clock_out=timezone.make_aware(datetime(2033, 6, 5, 20, 30)))
        self.reviewer = get_user_model().objects.create_user("overtime-reviewer")

    def test_detection_threshold_boundaries_are_configurable_and_idempotent(self):
        self.attendance.actual_clock_out = timezone.make_aware(datetime(2033, 6, 5, 20, 0)); self.attendance.save()
        self.assertEqual(sync_overtime_for_date(self.work_date).created, 0)
        self.attendance.actual_clock_out = timezone.make_aware(datetime(2033, 6, 5, 20, 1)); self.attendance.save()
        self.assertEqual(sync_overtime_for_date(self.work_date).created, 1)
        record = OvertimeRecord.objects.get(); self.assertEqual(record.potential_overtime_minutes, 1)
        self.assertEqual(sync_overtime_for_date(self.work_date).created, 0)
        PayrollSetting.objects.create(key="overtime_threshold_minutes", value={"value": 30})
        another = DailyAttendance.objects.create(employee=self.employee, date=date(2033, 6, 6), shift=self.shift, scheduled_start=timezone.make_aware(datetime(2033, 6, 6, 7)), scheduled_end=timezone.make_aware(datetime(2033, 6, 6, 19)), actual_clock_out=timezone.make_aware(datetime(2033, 6, 6, 20)))
        sync_overtime_for_date(another.date)
        self.assertEqual(OvertimeRecord.objects.get(attendance=another).potential_overtime_minutes, 30)

    def test_approval_snapshots_amount_and_payment_workflow(self):
        sync_overtime_for_date(self.work_date)
        record = OvertimeRecord.objects.get()
        approved = approve_overtime(record.id, actor=self.reviewer, approved_minutes=30)
        self.assertEqual(approved.payment_due_date, date(2033, 6, 6))
        self.assertEqual(approved.payable_amount, Decimal("347.22"))
        self.employee.basic_salary = "1.00"; self.employee.save()
        approved.refresh_from_db(); self.assertEqual(approved.basic_salary_snapshot, Decimal("150000.00"))
        with self.assertRaises(ValueError): approve_overtime(record.id, actor=self.reviewer, approved_minutes=1)
        self.assertEqual(AuditEvent.objects.filter(event_type="attendance.overtime_approved").count(), 1)

    def test_rejection_requires_reason(self):
        sync_overtime_for_date(self.work_date); record = OvertimeRecord.objects.get()
        with self.assertRaises(ValueError): reject_overtime(record.id, actor=self.reviewer, comment="")


@override_settings(BIOMETRIC_BRIDGE_SECRET="test-bridge-secret")
class VendorGatewayBridgeTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.employee = Employee.objects.create(employee_id="BRIDGE-001", first_name="Bridge", last_name="Employee")
        self.device = BiometricDevice.objects.create(name="Bridge device", serial_number="TEST123", location="Factory", device_type="factory")
        BiometricIdentity.objects.create(employee=self.employee, system="vendor_flask_gateway", source_identifier="gateway", external_user_id="1001")
        self.payload = {"source": "vendor_flask_gateway", "records": [{"gateway_record_id": 123, "enroll_id": "1001", "device_serial_number": "TEST123", "timestamp": "2026-09-03 19:30:00", "mode": 0, "inout": 0, "event": 0}]}

    def post(self, payload=None, secret="test-bridge-secret"):
        return self.client.post("/api/attendance/integrations/vendor-gateway/punches/", payload or self.payload, format="json", HTTP_X_BIOMETRIC_BRIDGE_KEY=secret)

    def test_authenticated_bridge_creates_immutable_timezone_aware_event_and_is_idempotent(self):
        self.assertEqual(self.post().json()["created"], 1)
        event = AttendanceEvent.objects.get(); self.assertTrue(timezone.is_aware(event.timestamp)); self.assertEqual(event.verification_type, "unknown"); self.assertEqual(event.raw_payload["mode"], 0)
        original_timestamp = event.timestamp
        self.assertEqual(self.post().json()["results"][0]["gateway_record_id"], 123)
        self.assertEqual(self.post().json()["duplicate"], 1)
        event.refresh_from_db(); self.assertEqual(event.timestamp, original_timestamp)

    def test_bridge_rejects_bad_secret_biometric_payload_and_mixed_failure(self):
        self.assertEqual(self.post(secret="wrong").status_code, 401)
        image_payload = {"records": [{**self.payload["records"][0], "image": "not-allowed"}]}
        self.assertEqual(self.post(image_payload).json()["invalid"], 1)
        mixed = {"records": [self.payload["records"][0], {**self.payload["records"][0], "gateway_record_id": 124, "enroll_id": "unknown"}]}
        result = self.post(mixed).json(); self.assertEqual(result["created"], 1); self.assertEqual(result["unmapped_employee"], 1)


class BiometricEventFeedTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = get_user_model().objects.create_user("biometric-feed-user")
        self.employee = Employee.objects.create(employee_id="FEED-001", first_name="Feed", last_name="Employee")
        self.device = BiometricDevice.objects.create(name="Feed Device", serial_number="FEED-DEVICE", location="Factory", device_type="factory")
        self.old = AttendanceEvent.objects.create(employee=self.employee, device=self.device, timestamp=timezone.make_aware(datetime(2026, 9, 3, 9)), external_event_id="feed-old", raw_payload={"mode": 0, "image": "must-not-be-exposed"})
        self.new = AttendanceEvent.objects.create(employee=self.employee, device=self.device, timestamp=timezone.make_aware(datetime(2026, 9, 3, 10)), external_event_id="feed-new")

    def test_feed_requires_authentication_and_returns_safe_newest_first_fields(self):
        self.assertEqual(self.client.get("/api/attendance/biometric-events/").status_code, 401)
        self.client.force_authenticate(self.user)
        response = self.client.get("/api/attendance/biometric-events/")
        self.assertEqual(response.status_code, 200)
        results = response.json()["results"]
        self.assertEqual([item["id"] for item in results], [self.new.id, self.old.id])
        self.assertEqual(results[0]["employee_id"], "FEED-001")
        self.assertEqual(results[0]["device_serial_number"], "FEED-DEVICE")
        self.assertNotIn("raw_payload", results[1])
        self.assertNotIn("image", results[1])

    def test_feed_filters_by_date_employee_and_device_and_is_read_only(self):
        self.client.force_authenticate(self.user)
        self.assertEqual(self.client.get(f"/api/attendance/biometric-events/?date=2026-09-03&employee={self.employee.id}&device=FEED-DEVICE").json()["count"], 2)
        self.assertEqual(self.client.post("/api/attendance/biometric-events/", {}, format="json").status_code, 405)


class AttendanceProcessingTests(TestCase):
    work_date = date(2026, 10, 15)

    def setUp(self):
        self.day_shift, _ = Shift.objects.get_or_create(
            name="Test Day Shift",
            defaults={"start_time": "07:00", "end_time": "19:00"},
        )
        self.night_shift, _ = Shift.objects.get_or_create(
            name="Test Night Shift",
            defaults={"start_time": "19:00", "end_time": "07:00", "is_overnight": True},
        )

    def employee_with_shift(self, suffix, shift=None):
        employee = Employee.objects.create(
            employee_id=f"ATT-{suffix}",
            first_name="Attendance",
            last_name=suffix,
        )
        ShiftAssignment.objects.create(
            employee=employee,
            shift=shift or self.day_shift,
            start_date=self.work_date,
        )
        return employee

    def add_event(self, employee, event_date, hour, minute=0):
        AttendanceEvent.objects.create(
            employee=employee,
            timestamp=timezone.make_aware(
                datetime(event_date.year, event_date.month, event_date.day, hour, minute)
            ),
        )

    def test_day_shift_on_time(self):
        employee = self.employee_with_shift("ONTIME")
        self.add_event(employee, self.work_date, 7)
        self.add_event(employee, self.work_date, 19)

        attendance = process_employee_attendance(employee, self.work_date)

        self.assertEqual(attendance.status, "present")
        self.assertEqual(attendance.worked_minutes, 720)
        self.assertEqual(attendance.late_minutes, 0)

    def test_late_early_departure_and_overtime(self):
        late_employee = self.employee_with_shift("LATE")
        self.add_event(late_employee, self.work_date, 7, 17)
        self.add_event(late_employee, self.work_date, 18, 45)
        late_attendance = process_employee_attendance(late_employee, self.work_date)
        self.assertEqual(late_attendance.status, "late")
        self.assertEqual(late_attendance.late_minutes, 17)
        self.assertEqual(late_attendance.early_departure_minutes, 15)
        self.assertEqual(late_attendance.worked_minutes, 688)
        self.assertTrue(
            AttendanceException.objects.filter(
                attendance=late_attendance,
                exception_type="late",
                status="pending",
            ).exists()
        )
        self.assertTrue(
            AttendanceException.objects.filter(
                attendance=late_attendance,
                exception_type="early_departure",
                status="pending",
            ).exists()
        )

        overtime_employee = self.employee_with_shift("OVERTIME")
        self.add_event(overtime_employee, self.work_date, 7)
        self.add_event(overtime_employee, self.work_date, 19, 30)
        overtime_attendance = process_employee_attendance(overtime_employee, self.work_date)
        self.assertEqual(overtime_attendance.overtime_minutes, 30)
        self.assertEqual(overtime_attendance.worked_minutes, 750)

    def test_single_punch_is_incomplete(self):
        employee = self.employee_with_shift("SINGLE")
        self.add_event(employee, self.work_date, 7, 5)

        attendance = process_employee_attendance(employee, self.work_date)

        self.assertEqual(attendance.status, "incomplete")
        self.assertIsNotNone(attendance.actual_clock_in)
        self.assertIsNone(attendance.actual_clock_out)
        self.assertTrue(
            AttendanceException.objects.filter(
                attendance=attendance,
                exception_type="missing_clock_out",
                status="pending",
            ).exists()
        )

    def test_late_single_punch_is_missing_clock_in(self):
        employee = self.employee_with_shift("MISSING-IN")
        self.add_event(employee, self.work_date, 19, 30)

        attendance = process_employee_attendance(employee, self.work_date)

        self.assertEqual(attendance.status, "incomplete")
        self.assertIsNone(attendance.actual_clock_in)
        self.assertIsNotNone(attendance.actual_clock_out)
        self.assertTrue(
            AttendanceException.objects.filter(
                attendance=attendance,
                exception_type="missing_clock_in",
                status="pending",
            ).exists()
        )

    def test_no_punch_is_absent(self):
        employee = self.employee_with_shift("ABSENT")

        attendance = process_employee_attendance(employee, self.work_date)

        self.assertEqual(attendance.status, "absent")
        self.assertTrue(
            AttendanceException.objects.filter(
                attendance=attendance,
                exception_type="absence",
                status="pending",
            ).exists()
        )

    def test_approved_leave_does_not_create_an_absence(self):
        employee = self.employee_with_shift("LEAVE")
        leave_type = LeaveType.objects.create(name="Processing Leave", code="PROCESSING")
        LeaveRequest.objects.create(
            request_number="LV-2026-PROCESSING",
            employee=employee,
            leave_type=leave_type,
            start_date=self.work_date,
            end_date=self.work_date,
            total_days=1,
            reason="Approved leave test",
            status=LeaveStatus.APPROVED,
            approved_start_date=self.work_date,
            approved_end_date=self.work_date,
            approved_days=1,
        )

        attendance = process_employee_attendance(employee, self.work_date)

        self.assertIsNone(attendance)
        self.assertFalse(DailyAttendance.objects.filter(employee=employee, date=self.work_date).exists())

    def test_night_shift_pairs_events_across_midnight(self):
        employee = self.employee_with_shift("NIGHT", self.night_shift)
        self.add_event(employee, self.work_date, 19, 10)
        self.add_event(employee, date(2026, 10, 16), 6, 50)

        attendance = process_employee_attendance(employee, self.work_date)

        self.assertEqual(attendance.status, "late")
        self.assertEqual(attendance.late_minutes, 10)
        self.assertEqual(attendance.early_departure_minutes, 10)
        self.assertEqual(attendance.worked_minutes, 700)

    def test_reprocessing_is_idempotent_and_preserves_reviewed_exceptions(self):
        employee = self.employee_with_shift("IDEMPOTENT")
        self.add_event(employee, self.work_date, 7, 15)
        self.add_event(employee, self.work_date, 19)

        attendance = process_employee_attendance(employee, self.work_date)
        late_exception = AttendanceException.objects.get(attendance=attendance, exception_type="late")
        late_exception.status = "approved"
        late_exception.minutes_affected = 999
        late_exception.save()
        audit_count = AuditEvent.objects.filter(employee=employee).count()

        processed_again = process_employee_attendance(employee, self.work_date)
        late_exception.refresh_from_db()

        self.assertEqual(processed_again.pk, attendance.pk)
        self.assertEqual(DailyAttendance.objects.filter(employee=employee, date=self.work_date).count(), 1)
        self.assertEqual(AttendanceException.objects.filter(attendance=attendance, exception_type="late").count(), 1)
        self.assertEqual(late_exception.status, "approved")
        self.assertEqual(late_exception.minutes_affected, 999)
        self.assertEqual(AuditEvent.objects.filter(employee=employee).count(), audit_count)


class AttendanceExceptionReviewTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.reviewer = get_user_model().objects.create_user(
            username="attendance-reviewer",
            password="test-password",
            first_name="HR",
            last_name="Reviewer",
        )
        self.reviewer.user_permissions.add(
            Permission.objects.get(codename="review_attendanceexception")
        )
        self.unauthorized_user = get_user_model().objects.create_user(
            username="attendance-user",
            password="test-password",
        )
        self.employee = Employee.objects.create(
            employee_id="ATT-REVIEW",
            first_name="Review",
            last_name="Employee",
        )
        self.shift = Shift.objects.create(
            name="Review Shift",
            start_time="07:00",
            end_time="19:00",
        )
        self.attendance = DailyAttendance.objects.create(
            employee=self.employee,
            date=date(2026, 10, 15),
            shift=self.shift,
            scheduled_start=timezone.make_aware(datetime(2026, 10, 15, 7)),
            scheduled_end=timezone.make_aware(datetime(2026, 10, 15, 19)),
            actual_clock_in=timezone.make_aware(datetime(2026, 10, 15, 7, 12)),
            actual_clock_out=timezone.make_aware(datetime(2026, 10, 15, 18, 50)),
            late_minutes=12,
            early_departure_minutes=10,
            worked_minutes=698,
            status="late",
        )

    def create_exception(self):
        return AttendanceException.objects.create(
            attendance=self.attendance,
            exception_type="late",
            minutes_affected=12,
            proposed_deduction="250.00",
        )

    def review(self, exception, decision, comment=""):
        return self.client.post(
            f"/api/attendance/exceptions/{exception.pk}/decision/",
            {"decision": decision, "comment": comment},
            format="json",
        )

    def test_authorized_reviewer_can_approve_without_comment(self):
        exception = self.create_exception()
        self.client.force_authenticate(self.reviewer)

        response = self.review(exception, "approved")

        self.assertEqual(response.status_code, 200)
        exception.refresh_from_db()
        self.assertEqual(exception.status, "approved")
        self.assertEqual(exception.reviewed_by, "HR Reviewer")
        self.assertIsNotNone(exception.reviewed_at)
        self.assertEqual(exception.admin_comment, "")

    def test_unauthorized_authenticated_user_receives_403(self):
        exception = self.create_exception()
        self.client.force_authenticate(self.unauthorized_user)

        response = self.review(exception, "approved")

        self.assertEqual(response.status_code, 403)
        exception.refresh_from_db()
        self.assertEqual(exception.status, "pending")

    def test_waive_and_hold_require_reasons(self):
        self.client.force_authenticate(self.reviewer)
        waived = self.create_exception()
        held = AttendanceException.objects.create(
            attendance=self.attendance,
            exception_type="early_departure",
            minutes_affected=10,
            proposed_deduction="100.00",
        )

        waive_response = self.review(waived, "waived")
        hold_response = self.review(held, "held")

        self.assertEqual(waive_response.status_code, 400)
        self.assertEqual(hold_response.status_code, 400)
        self.assertFalse(AuditEvent.objects.filter(object_id__in=[waived.pk, held.pk]).exists())

    def test_review_cannot_be_repeated_and_failed_review_is_not_audited(self):
        exception = self.create_exception()
        self.client.force_authenticate(self.reviewer)

        first_response = self.review(exception, "waived", "Device clock was faulty.")
        audit_count = AuditEvent.objects.filter(object_id=exception.pk).count()
        second_response = self.review(exception, "held", "Trying again.")

        self.assertEqual(first_response.status_code, 200)
        self.assertEqual(second_response.status_code, 400)
        exception.refresh_from_db()
        self.assertEqual(exception.status, "waived")
        self.assertEqual(AuditEvent.objects.filter(object_id=exception.pk).count(), audit_count)

    def test_successful_review_records_audit_and_preserves_attendance_facts(self):
        exception = self.create_exception()
        self.client.force_authenticate(self.reviewer)
        before = {
            "actual_clock_in": self.attendance.actual_clock_in,
            "actual_clock_out": self.attendance.actual_clock_out,
            "late_minutes": self.attendance.late_minutes,
            "early_departure_minutes": self.attendance.early_departure_minutes,
            "worked_minutes": self.attendance.worked_minutes,
            "status": self.attendance.status,
        }

        response = self.review(exception, "held", "Supervisor confirmation is required.")

        self.assertEqual(response.status_code, 200)
        exception.refresh_from_db()
        self.attendance.refresh_from_db()
        audit = AuditEvent.objects.get(object_id=exception.pk, event_type="attendance.exception_held")
        self.assertEqual(exception.admin_comment, "Supervisor confirmation is required.")
        self.assertEqual(audit.actor, self.reviewer)
        self.assertEqual(audit.metadata["decision"], "held")
        self.assertEqual(audit.metadata["comment"], "Supervisor confirmation is required.")
        self.assertEqual(self.attendance.actual_clock_in, before["actual_clock_in"])
        self.assertEqual(self.attendance.actual_clock_out, before["actual_clock_out"])
        self.assertEqual(self.attendance.late_minutes, before["late_minutes"])
        self.assertEqual(self.attendance.early_departure_minutes, before["early_departure_minutes"])
        self.assertEqual(self.attendance.worked_minutes, before["worked_minutes"])
        self.assertEqual(self.attendance.status, before["status"])

    def test_approving_an_absence_syncs_meal_penalties(self):
        EmployeeRosterDay.objects.create(
            employee=self.employee,
            date=self.attendance.date,
            status="work",
            shift=self.shift,
        )
        EmployeeRosterDay.objects.create(
            employee=self.employee,
            date=date(2026, 10, 16),
            status="work",
            shift=self.shift,
        )
        exception = AttendanceException.objects.create(
            attendance=self.attendance,
            exception_type="absence",
            minutes_affected=720,
            proposed_deduction="0.00",
        )
        self.client.force_authenticate(self.reviewer)

        response = self.review(exception, "approved")

        self.assertEqual(response.status_code, 200)
        penalty = MealAbsencePenalty.objects.get(
            employee=self.employee,
            penalty_type=MealAbsencePenaltyType.SINGLE_ABSENCE,
            status=MealAbsencePenaltyStatus.ACTIVE,
        )
        self.assertEqual(penalty.source_absence_dates, ["2026-10-15"])
        self.assertEqual(penalty.target_work_dates, ["2026-10-16"])

    def test_approving_a_non_absence_does_not_sync_meal_penalties(self):
        exception = self.create_exception()
        self.client.force_authenticate(self.reviewer)

        response = self.review(exception, "approved")

        self.assertEqual(response.status_code, 200)
        self.assertFalse(MealAbsencePenalty.objects.exists())


class BiometricIngestionTests(TestCase):
    def setUp(self):
        from attendance.models import BiometricDevice

        self.employee = Employee.objects.create(
            employee_id="ATT-BIO-001",
            first_name="Biometric",
            last_name="Employee",
        )
        self.device = BiometricDevice.objects.create(
            name="Factory Clock",
            serial_number="DEVICE-001",
            location="Factory Gate",
            device_type="factory",
        )
        BiometricIdentity.objects.create(
            employee=self.employee,
            system="yunatt",
            source_identifier="cloud",
            external_user_id="cloud-user-001",
        )

    def record(self, **overrides):
        values = {
            "system": "yunatt",
            "source_identifier": "cloud",
            "external_event_id": "event-001",
            "external_user_id": "cloud-user-001",
            "device_serial_number": "DEVICE-001",
            "timestamp": timezone.make_aware(datetime(2026, 10, 15, 7, 5)),
            "verification_type": "face",
            "raw_payload": {"event": "clock_in", "face_template": "must-not-persist"},
        }
        values.update(overrides)
        return NormalizedBiometricPunch(**values)

    def test_mapped_biometric_identity_creates_event(self):
        result = BiometricIngestionService.ingest(self.record())

        self.assertEqual(result.status, "created")
        event = AttendanceEvent.objects.get(pk=result.attendance_event_id)
        self.assertEqual(event.employee, self.employee)
        self.assertEqual(event.device, self.device)
        self.assertEqual(event.external_event_id, "event-001")
        self.assertEqual(event.raw_payload, {"event": "clock_in"})
        self.assertFalse(DailyAttendance.objects.exists())

    def test_duplicate_import_never_changes_existing_event(self):
        first = BiometricIngestionService.ingest(self.record())
        event = AttendanceEvent.objects.get(pk=first.attendance_event_id)
        original = {
            "timestamp": event.timestamp,
            "verification_type": event.verification_type,
            "raw_payload": event.raw_payload,
        }

        duplicate = BiometricIngestionService.ingest(
            self.record(
                timestamp=timezone.make_aware(datetime(2026, 10, 15, 8)),
                verification_type="card",
                raw_payload={"event": "changed"},
            )
        )

        event.refresh_from_db()
        self.assertEqual(duplicate.status, "duplicate")
        self.assertEqual(AttendanceEvent.objects.count(), 1)
        self.assertEqual(event.timestamp, original["timestamp"])
        self.assertEqual(event.verification_type, original["verification_type"])
        self.assertEqual(event.raw_payload, original["raw_payload"])

    def test_unmapped_employee_is_reported(self):
        summary = BiometricIngestionService.ingest_many(
            [self.record(external_event_id="event-unmapped", external_user_id="unknown-user")]
        )

        self.assertEqual(summary.unmapped_employee, 1)
        self.assertEqual(summary.created, 0)
        self.assertFalse(AttendanceEvent.objects.exists())

    def test_unknown_device_is_reported(self):
        result = BiometricIngestionService.ingest(
            self.record(external_event_id="event-device", device_serial_number="UNKNOWN-DEVICE")
        )

        self.assertEqual(result.status, "unknown_device")
        self.assertFalse(AttendanceEvent.objects.exists())

    def test_invalid_timestamp_or_missing_external_event_id_is_reported(self):
        naive_timestamp = BiometricIngestionService.ingest(
            self.record(external_event_id="event-naive", timestamp=datetime(2026, 10, 15, 7))
        )
        no_external_id = BiometricIngestionService.ingest(
            self.record(external_event_id=None)
        )

        self.assertEqual(naive_timestamp.status, "invalid")
        self.assertIn("timezone-aware", naive_timestamp.reason)
        self.assertEqual(no_external_id.status, "invalid")
        self.assertIn("external_event_id", no_external_id.reason)
        self.assertFalse(AttendanceEvent.objects.exists())

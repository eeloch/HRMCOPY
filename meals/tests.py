from datetime import date, datetime
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from attendance.models import (
    AttendanceException,
    DailyAttendance,
    EmployeeRosterDay,
    RosterDayStatus,
    Shift,
)
from audit.models import AuditEvent
from employees.models import BiometricIdentity, Employee
from meals.models import (
    MealAbsencePenalty,
    MealAbsencePenaltyStatus,
    MealAbsencePenaltyType,
    EmployeeMealEntitlement,
    MealCollection,
    MealCollectionStatus,
    MealDevice,
    MealEvent,
    MealExcessException,
    MealExcessStatus,
    MealTicketRate,
)
from notifications.models import Notification
from payroll.models import (
    EmployeePayroll,
    EmployeePayrollStatus,
    PayrollLineItem,
    PayrollPeriod,
)
from meals.services import MealService


class MealAbsencePenaltyTests(TestCase):
    def setUp(self):
        self.employee = Employee.objects.create(
            employee_id="MEALTEST001",
            first_name="Meal",
            last_name="Test",
        )

        self.shift = Shift.objects.create(
            name="Meal Test Day Shift",
            start_time="07:00",
            end_time="19:00",
            is_overnight=False,
        )

    def create_approved_absence(self, work_date):
        EmployeeRosterDay.objects.create(
            employee=self.employee,
            date=work_date,
            status=RosterDayStatus.WORK,
            shift=self.shift,
        )

        attendance = DailyAttendance.objects.create(
            employee=self.employee,
            date=work_date,
            shift=self.shift,
            status="absent",
        )

        return AttendanceException.objects.create(
            attendance=attendance,
            exception_type="absence",
            status="approved",
        )

    def create_approved_rest_absence(self, work_date):
        EmployeeRosterDay.objects.create(
            employee=self.employee,
            date=work_date,
            status=RosterDayStatus.REST,
        )
        attendance = DailyAttendance.objects.create(
            employee=self.employee,
            date=work_date,
            status="absent",
        )
        return AttendanceException.objects.create(
            attendance=attendance,
            exception_type="absence",
            status="approved",
        )

    def test_single_approved_absence_creates_single_penalty(self):
        absence = self.create_approved_absence(date(2026, 9, 1))

        MealService.sync_absence_penalties(
            self.employee,
            date(2026, 9, 1),
        )

        penalties = MealAbsencePenalty.objects.filter(
            employee=self.employee,
            status=MealAbsencePenaltyStatus.ACTIVE,
        )

        self.assertEqual(penalties.count(), 1)

        penalty = penalties.get()

        self.assertEqual(
            penalty.penalty_type,
            MealAbsencePenaltyType.SINGLE_ABSENCE,
        )
        self.assertEqual(
            penalty.source_absence_dates,
            [absence.attendance.date.isoformat()],
        )
        self.assertEqual(penalty.tickets_to_reduce_per_work_day, 1)
        self.assertEqual(penalty.work_days_to_apply, 1)

    def test_approved_rest_day_absence_is_ignored(self):
        self.create_approved_rest_absence(date(2026, 9, 1))

        result = MealService.sync_absence_penalties(
            self.employee,
            date(2026, 9, 1),
        )

        self.assertEqual(result, [])
        self.assertFalse(MealAbsencePenalty.objects.exists())

    def test_rest_day_absence_does_not_form_a_consecutive_penalty(self):
        self.create_approved_absence(date(2026, 9, 1))
        self.create_approved_rest_absence(date(2026, 9, 2))

        MealService.sync_absence_penalties(
            self.employee,
            date(2026, 9, 2),
        )

        self.assertFalse(
            MealAbsencePenalty.objects.filter(
                penalty_type=MealAbsencePenaltyType.TWO_CONSECUTIVE,
            ).exists()
        )
        self.assertTrue(
            MealAbsencePenalty.objects.filter(
                penalty_type=MealAbsencePenaltyType.SINGLE_ABSENCE,
                status=MealAbsencePenaltyStatus.ACTIVE,
            ).exists()
        )

    def test_rest_day_absence_does_not_count_toward_monthly_threshold(self):
        self.create_approved_absence(date(2026, 9, 1))
        self.create_approved_absence(date(2026, 9, 3))
        self.create_approved_rest_absence(date(2026, 9, 5))

        MealService.sync_absence_penalties(
            self.employee,
            date(2026, 9, 5),
        )

        self.assertFalse(
            MealAbsencePenalty.objects.filter(
                penalty_type=MealAbsencePenaltyType.THREE_MONTHLY,
            ).exists()
        )

    def test_two_consecutive_work_day_absences_create_two_day_penalty_only(self):
        first_absence = self.create_approved_absence(date(2026, 9, 1))

        # Sept 2 is intentionally not rostered.
        # Sept 3 is therefore the employee's next scheduled WORK day.
        second_absence = self.create_approved_absence(date(2026, 9, 3))

        MealService.sync_absence_penalties(
            self.employee,
            date(2026, 9, 3),
        )

        active_penalties = MealAbsencePenalty.objects.filter(
            employee=self.employee,
            status=MealAbsencePenaltyStatus.ACTIVE,
        )

        self.assertEqual(active_penalties.count(), 1)

        penalty = active_penalties.get()

        self.assertEqual(
            penalty.penalty_type,
            MealAbsencePenaltyType.TWO_CONSECUTIVE,
        )
        self.assertEqual(
            penalty.source_absence_dates,
            [
                first_absence.attendance.date.isoformat(),
                second_absence.attendance.date.isoformat(),
            ],
        )
        self.assertEqual(penalty.tickets_to_reduce_per_work_day, 1)
        self.assertEqual(penalty.work_days_to_apply, 2)

        active_single_penalties = MealAbsencePenalty.objects.filter(
            employee=self.employee,
            penalty_type=MealAbsencePenaltyType.SINGLE_ABSENCE,
            status=MealAbsencePenaltyStatus.ACTIVE,
        )

        self.assertEqual(active_single_penalties.count(), 0)

    def test_three_approved_absences_in_month_create_roster_week_penalty(self):
        self.create_approved_absence(date(2026, 9, 1))
        self.create_approved_absence(date(2026, 9, 3))
        self.create_approved_absence(date(2026, 9, 5))

        MealService.sync_absence_penalties(
            self.employee,
            date(2026, 9, 5),
        )

        monthly_penalties = MealAbsencePenalty.objects.filter(
            employee=self.employee,
            penalty_type=MealAbsencePenaltyType.THREE_MONTHLY,
            status=MealAbsencePenaltyStatus.ACTIVE,
        )

        self.assertEqual(monthly_penalties.count(), 1)

        penalty = monthly_penalties.get()

        self.assertEqual(
            penalty.source_absence_dates,
            [
                "2026-09-01",
                "2026-09-03",
                "2026-09-05",
            ],
        )
        self.assertEqual(penalty.tickets_to_reduce_per_work_day, 1)
        self.assertIsNone(penalty.work_days_to_apply)

    def test_three_monthly_penalty_starts_next_scheduled_work_day(self):
        self.create_approved_absence(date(2026, 9, 1))
        self.create_approved_absence(date(2026, 9, 3))
        self.create_approved_absence(date(2026, 9, 5))

        # Sept 6 is REST / not rostered.
        # The next scheduled WORK day is Sept 7.
        for work_date in [
            date(2026, 9, 7),
            date(2026, 9, 8),
            date(2026, 9, 9),
            date(2026, 9, 10),
            date(2026, 9, 11),
            date(2026, 9, 12),
        ]:
            EmployeeRosterDay.objects.create(
                employee=self.employee,
                date=work_date,
                status=RosterDayStatus.WORK,
                shift=self.shift,
            )

        MealService.sync_absence_penalties(
            self.employee,
            date(2026, 9, 5),
        )

        penalty = MealAbsencePenalty.objects.get(
            employee=self.employee,
            penalty_type=MealAbsencePenaltyType.THREE_MONTHLY,
            status=MealAbsencePenaltyStatus.ACTIVE,
        )

        MealService.resolve_three_monthly_penalty_roster(penalty)

        penalty.refresh_from_db()

        self.assertEqual(
            penalty.target_work_dates,
            [
                "2026-09-07",
                "2026-09-08",
                "2026-09-09",
                "2026-09-10",
                "2026-09-11",
                "2026-09-12",
            ],
        )

        self.assertEqual(penalty.work_days_to_apply, 6)

    def test_unresolved_three_monthly_penalty_resolves_when_roster_is_added_later(self):
        self.create_approved_absence(date(2026, 9, 1))
        self.create_approved_absence(date(2026, 9, 3))
        self.create_approved_absence(date(2026, 9, 5))

        # First sync: no future roster exists yet.
        MealService.sync_absence_penalties(
            self.employee,
            date(2026, 9, 5),
        )

        penalty = MealAbsencePenalty.objects.get(
            employee=self.employee,
            penalty_type=MealAbsencePenaltyType.THREE_MONTHLY,
        )

        self.assertEqual(penalty.target_work_dates, [])
        self.assertIsNone(penalty.work_days_to_apply)

        # Future roster is entered later.
        for work_date in [
            date(2026, 9, 7),
            date(2026, 9, 8),
            date(2026, 9, 9),
            date(2026, 9, 10),
            date(2026, 9, 11),
            date(2026, 9, 12),
        ]:
            EmployeeRosterDay.objects.create(
                employee=self.employee,
                date=work_date,
                status=RosterDayStatus.WORK,
                shift=self.shift,
            )

        # Sync again after the roster becomes available.
        MealService.sync_absence_penalties(
            self.employee,
            date(2026, 9, 5),
        )

        penalty.refresh_from_db()

        self.assertEqual(
            penalty.target_work_dates,
            [
                "2026-09-07",
                "2026-09-08",
                "2026-09-09",
                "2026-09-10",
                "2026-09-11",
                "2026-09-12",
            ],
        )
        self.assertEqual(penalty.work_days_to_apply, 6)

    def test_single_absence_penalty_targets_next_work_day(self):
        self.create_approved_absence(date(2026, 9, 1))

        EmployeeRosterDay.objects.create(
            employee=self.employee,
            date=date(2026, 9, 3),
            status=RosterDayStatus.WORK,
            shift=self.shift,
        )

        MealService.sync_absence_penalties(
            self.employee,
            date(2026, 9, 1),
        )

        penalty = MealAbsencePenalty.objects.get(
            employee=self.employee,
            penalty_type=MealAbsencePenaltyType.SINGLE_ABSENCE,
            status=MealAbsencePenaltyStatus.ACTIVE,
        )

        MealService.resolve_short_absence_penalty_roster(penalty)
        penalty.refresh_from_db()

        self.assertEqual(
            penalty.target_work_dates,
            ["2026-09-03"],
        )


    def test_two_consecutive_penalty_targets_next_two_work_days(self):
        self.create_approved_absence(date(2026, 9, 1))
        self.create_approved_absence(date(2026, 9, 3))

        for work_date in [
            date(2026, 9, 5),
            date(2026, 9, 7),
        ]:
            EmployeeRosterDay.objects.create(
                employee=self.employee,
                date=work_date,
                status=RosterDayStatus.WORK,
                shift=self.shift,
            )

        MealService.sync_absence_penalties(
            self.employee,
            date(2026, 9, 3),
        )

        penalty = MealAbsencePenalty.objects.get(
            employee=self.employee,
            penalty_type=MealAbsencePenaltyType.TWO_CONSECUTIVE,
            status=MealAbsencePenaltyStatus.ACTIVE,
        )

        MealService.resolve_short_absence_penalty_roster(penalty)
        penalty.refresh_from_db()

        self.assertEqual(
            penalty.target_work_dates,
            [
                "2026-09-05",
                "2026-09-07",
            ],
        )

    def test_absence_penalty_reduction_applies_single_penalty(self):
        MealAbsencePenalty.objects.create(
            employee=self.employee,
            penalty_type=MealAbsencePenaltyType.SINGLE_ABSENCE,
            source_absence_dates=["2026-09-01"],
            tickets_to_reduce_per_work_day=1,
            work_days_to_apply=1,
            target_work_dates=["2026-09-03"],
            status=MealAbsencePenaltyStatus.ACTIVE,
        )

        reduction = MealService.absence_penalty_reduction(
            self.employee,
            date(2026, 9, 3),
        )

        self.assertEqual(reduction, 1)

    def test_absence_penalty_reduction_is_cumulative(self):
        MealAbsencePenalty.objects.create(
            employee=self.employee,
            penalty_type=MealAbsencePenaltyType.SINGLE_ABSENCE,
            source_absence_dates=["2026-09-01"],
            tickets_to_reduce_per_work_day=1,
            work_days_to_apply=1,
            target_work_dates=["2026-09-07"],
            status=MealAbsencePenaltyStatus.ACTIVE,
        )

        MealAbsencePenalty.objects.create(
            employee=self.employee,
            penalty_type=MealAbsencePenaltyType.THREE_MONTHLY,
            source_absence_dates=[
                "2026-09-01",
                "2026-09-03",
                "2026-09-05",
            ],
            tickets_to_reduce_per_work_day=1,
            work_days_to_apply=6,
            target_work_dates=[
                "2026-09-07",
                "2026-09-08",
                "2026-09-09",
                "2026-09-10",
                "2026-09-11",
                "2026-09-12",
            ],
            status=MealAbsencePenaltyStatus.ACTIVE,
        )

        reduction = MealService.absence_penalty_reduction(
            self.employee,
            date(2026, 9, 7),
        )

        self.assertEqual(reduction, 2)

    def test_ingest_applies_absence_penalty_to_entitlement_snapshot(self):
        work_date = date(2026, 9, 7)
        device_serial_number = "MEALDEVICE001"

        EmployeeRosterDay.objects.create(
            employee=self.employee,
            date=work_date,
            status=RosterDayStatus.WORK,
            shift=self.shift,
        )

        EmployeeMealEntitlement.objects.create(
            employee=self.employee,
            tickets_per_work_day=2,
            effective_from=work_date,
            reason="Meal test entitlement",
        )

        MealTicketRate.objects.create(
            amount=Decimal("700.00"),
            effective_from=work_date,
        )

        MealDevice.objects.create(
            name="Meal Test Device",
            serial_number=device_serial_number,
            active=True,
        )

        BiometricIdentity.objects.create(
            employee=self.employee,
            system="device",
            source_identifier=device_serial_number,
            external_user_id="BIOUSER001",
            is_active=True,
        )

        MealAbsencePenalty.objects.create(
            employee=self.employee,
            penalty_type=MealAbsencePenaltyType.SINGLE_ABSENCE,
            source_absence_dates=["2026-09-01"],
            tickets_to_reduce_per_work_day=1,
            work_days_to_apply=1,
            target_work_dates=[work_date.isoformat()],
            status=MealAbsencePenaltyStatus.ACTIVE,
        )

        collection, created = MealService.ingest(
            system="device",
            source_identifier=device_serial_number,
            device_serial_number=device_serial_number,
            external_user_id="BIOUSER001",
            external_event_id="EVENT001",
            timestamp=timezone.make_aware(datetime(2026, 9, 7, 10, 0)),
        )

        self.assertTrue(created)
        self.assertIsInstance(collection, MealCollection)
        self.assertEqual(collection.work_date, work_date)
        self.assertEqual(collection.entitlement_snapshot, 1)

    def test_reduced_entitlement_drives_excess_calculation(self):
        work_date = date(2026, 9, 7)
        device_serial_number = "MEALDEVICE002"

        EmployeeRosterDay.objects.create(
            employee=self.employee,
            date=work_date,
            status=RosterDayStatus.WORK,
            shift=self.shift,
        )
        EmployeeMealEntitlement.objects.create(
            employee=self.employee,
            tickets_per_work_day=2,
            effective_from=work_date,
            reason="Reduced entitlement excess test",
        )
        MealTicketRate.objects.create(
            amount=Decimal("700.00"),
            effective_from=work_date,
        )
        MealDevice.objects.create(
            name="Meal Excess Test Device",
            serial_number=device_serial_number,
            active=True,
        )
        BiometricIdentity.objects.create(
            employee=self.employee,
            system="device",
            source_identifier=device_serial_number,
            external_user_id="BIOUSER002",
            is_active=True,
        )
        MealAbsencePenalty.objects.create(
            employee=self.employee,
            penalty_type=MealAbsencePenaltyType.SINGLE_ABSENCE,
            source_absence_dates=["2026-09-01"],
            tickets_to_reduce_per_work_day=1,
            work_days_to_apply=1,
            target_work_dates=[work_date.isoformat()],
            status=MealAbsencePenaltyStatus.ACTIVE,
        )

        first_collection, first_created = MealService.ingest(
            system="device",
            source_identifier=device_serial_number,
            device_serial_number=device_serial_number,
            external_user_id="BIOUSER002",
            external_event_id="EVENT002A",
            timestamp=timezone.make_aware(datetime(2026, 9, 7, 10, 0)),
        )
        self.assertFalse(
            MealExcessException.objects.filter(
                employee=self.employee,
                work_date=work_date,
            ).exists()
        )
        second_collection, second_created = MealService.ingest(
            system="device",
            source_identifier=device_serial_number,
            device_serial_number=device_serial_number,
            external_user_id="BIOUSER002",
            external_event_id="EVENT002B",
            timestamp=timezone.make_aware(datetime(2026, 9, 7, 11, 0)),
        )

        self.assertTrue(first_created)
        self.assertTrue(second_created)
        self.assertEqual(first_collection.sequence_number, 1)
        self.assertEqual(first_collection.entitlement_snapshot, 1)
        self.assertEqual(first_collection.status, MealCollectionStatus.WITHIN)
        self.assertEqual(second_collection.sequence_number, 2)
        self.assertEqual(second_collection.entitlement_snapshot, 1)
        self.assertEqual(second_collection.status, MealCollectionStatus.EXCESS)

        excess = MealExcessException.objects.get(
            employee=self.employee,
            work_date=work_date,
        )
        self.assertEqual(excess.entitlement_snapshot, 1)
        self.assertEqual(excess.collected_quantity, 2)
        self.assertEqual(excess.excess_quantity, 1)
        self.assertEqual(excess.rate_snapshot, Decimal("700.00"))
        self.assertEqual(excess.proposed_deduction, Decimal("700.00"))

    def test_pending_excess_updates_after_third_scan(self):
        work_date = date(2026, 9, 7)
        device_serial_number = "MEALDEVICE003"

        EmployeeRosterDay.objects.create(
            employee=self.employee,
            date=work_date,
            status=RosterDayStatus.WORK,
            shift=self.shift,
        )
        EmployeeMealEntitlement.objects.create(
            employee=self.employee,
            tickets_per_work_day=1,
            effective_from=work_date,
            reason="Pending excess update test",
        )
        MealTicketRate.objects.create(
            amount=Decimal("700.00"),
            effective_from=work_date,
        )
        MealDevice.objects.create(
            name="Meal Pending Excess Device",
            serial_number=device_serial_number,
            active=True,
        )
        BiometricIdentity.objects.create(
            employee=self.employee,
            system="device",
            source_identifier=device_serial_number,
            external_user_id="BIOUSER003",
            is_active=True,
        )

        for event_id, hour in [
            ("EVENT003A", 10),
            ("EVENT003B", 11),
            ("EVENT003C", 12),
        ]:
            collection, created = MealService.ingest(
                system="device",
                source_identifier=device_serial_number,
                device_serial_number=device_serial_number,
                external_user_id="BIOUSER003",
                external_event_id=event_id,
                timestamp=timezone.make_aware(datetime(2026, 9, 7, hour, 0)),
            )
            self.assertTrue(created)
            self.assertEqual(collection.entitlement_snapshot, 1)

        excess = MealExcessException.objects.get(
            employee=self.employee,
            work_date=work_date,
        )
        self.assertEqual(excess.status, MealExcessStatus.PENDING)
        self.assertEqual(excess.collected_quantity, 3)
        self.assertEqual(excess.excess_quantity, 2)
        self.assertEqual(excess.proposed_deduction, Decimal("1400.00"))
        self.assertEqual(
            MealExcessException.objects.filter(
                employee=self.employee,
                work_date=work_date,
            ).count(),
            1,
        )

    def test_cumulative_absence_penalties_floor_ingested_entitlement_at_zero(self):
        work_date = date(2026, 9, 7)
        device_serial_number = "MEALDEVICE004"

        EmployeeRosterDay.objects.create(
            employee=self.employee,
            date=work_date,
            status=RosterDayStatus.WORK,
            shift=self.shift,
        )
        EmployeeMealEntitlement.objects.create(
            employee=self.employee,
            tickets_per_work_day=1,
            effective_from=work_date,
            reason="Zero entitlement floor test",
        )
        MealTicketRate.objects.create(
            amount=Decimal("700.00"),
            effective_from=work_date,
        )
        MealDevice.objects.create(
            name="Meal Zero Floor Device",
            serial_number=device_serial_number,
            active=True,
        )
        BiometricIdentity.objects.create(
            employee=self.employee,
            system="device",
            source_identifier=device_serial_number,
            external_user_id="BIOUSER004",
            is_active=True,
        )
        for penalty_type, source_dates in [
            (MealAbsencePenaltyType.SINGLE_ABSENCE, ["2026-09-01"]),
            (MealAbsencePenaltyType.TWO_CONSECUTIVE, ["2026-09-02", "2026-09-03"]),
        ]:
            MealAbsencePenalty.objects.create(
                employee=self.employee,
                penalty_type=penalty_type,
                source_absence_dates=source_dates,
                tickets_to_reduce_per_work_day=1,
                work_days_to_apply=1,
                target_work_dates=[work_date.isoformat()],
                status=MealAbsencePenaltyStatus.ACTIVE,
            )

        collection, created = MealService.ingest(
            system="device",
            source_identifier=device_serial_number,
            device_serial_number=device_serial_number,
            external_user_id="BIOUSER004",
            external_event_id="EVENT004",
            timestamp=timezone.make_aware(datetime(2026, 9, 7, 10, 0)),
        )

        self.assertTrue(created)
        self.assertEqual(collection.entitlement_snapshot, 0)
        self.assertEqual(collection.status, MealCollectionStatus.REST_DAY)

        excess = MealExcessException.objects.get(
            employee=self.employee,
            work_date=work_date,
        )
        self.assertEqual(excess.entitlement_snapshot, 0)
        self.assertEqual(excess.collected_quantity, 1)
        self.assertEqual(excess.excess_quantity, 1)
        self.assertEqual(excess.proposed_deduction, Decimal("700.00"))

    def test_duplicate_meal_event_is_idempotent(self):
        work_date = date(2026, 9, 7)
        device_serial_number = "MEALDEVICE005"

        EmployeeRosterDay.objects.create(
            employee=self.employee,
            date=work_date,
            status=RosterDayStatus.WORK,
            shift=self.shift,
        )
        EmployeeMealEntitlement.objects.create(
            employee=self.employee,
            tickets_per_work_day=1,
            effective_from=work_date,
            reason="Duplicate event test",
        )
        MealTicketRate.objects.create(
            amount=Decimal("700.00"),
            effective_from=work_date,
        )
        device = MealDevice.objects.create(
            name="Meal Duplicate Event Device",
            serial_number=device_serial_number,
            active=True,
        )
        BiometricIdentity.objects.create(
            employee=self.employee,
            system="device",
            source_identifier=device_serial_number,
            external_user_id="BIOUSER005",
            is_active=True,
        )

        timestamp = timezone.make_aware(datetime(2026, 9, 7, 10, 0))
        first_collection, first_created = MealService.ingest(
            system="device",
            source_identifier=device_serial_number,
            device_serial_number=device_serial_number,
            external_user_id="BIOUSER005",
            external_event_id="EVENT005",
            timestamp=timestamp,
        )
        second_collection, second_created = MealService.ingest(
            system="device",
            source_identifier=device_serial_number,
            device_serial_number=device_serial_number,
            external_user_id="BIOUSER005",
            external_event_id="EVENT005",
            timestamp=timestamp,
        )

        self.assertTrue(first_created)
        self.assertFalse(second_created)
        self.assertEqual(first_collection.pk, second_collection.pk)
        self.assertEqual(
            MealEvent.objects.filter(
                device=device,
                external_event_id="EVENT005",
            ).count(),
            1,
        )
        self.assertEqual(
            MealCollection.objects.filter(
                event__device=device,
                event__external_event_id="EVENT005",
            ).count(),
            1,
        )
        self.assertEqual(first_collection.sequence_number, 1)
        self.assertEqual(second_collection.sequence_number, 1)
        self.assertEqual(
            MealExcessException.objects.filter(
                employee=self.employee,
                work_date=work_date,
            ).count(),
            0,
        )

    def test_meal_devices_share_employee_work_date_counter(self):
        work_date = date(2026, 9, 7)
        first_serial_number = "MEALDEVICE006A"
        second_serial_number = "MEALDEVICE006B"

        EmployeeRosterDay.objects.create(
            employee=self.employee,
            date=work_date,
            status=RosterDayStatus.WORK,
            shift=self.shift,
        )
        EmployeeMealEntitlement.objects.create(
            employee=self.employee,
            tickets_per_work_day=1,
            effective_from=work_date,
            reason="Shared device counter test",
        )
        MealTicketRate.objects.create(
            amount=Decimal("700.00"),
            effective_from=work_date,
        )
        first_device = MealDevice.objects.create(
            name="Meal Shared Counter Device A",
            serial_number=first_serial_number,
            active=True,
        )
        MealDevice.objects.create(
            name="Meal Shared Counter Device B",
            serial_number=second_serial_number,
            active=True,
        )
        for serial_number in [first_serial_number, second_serial_number]:
            BiometricIdentity.objects.create(
                employee=self.employee,
                system="device",
                source_identifier=serial_number,
                external_user_id="BIOUSER006",
                is_active=True,
            )

        first_collection, first_created = MealService.ingest(
            system="device",
            source_identifier=first_serial_number,
            device_serial_number=first_serial_number,
            external_user_id="BIOUSER006",
            external_event_id="EVENT006A",
            timestamp=timezone.make_aware(datetime(2026, 9, 7, 10, 0)),
        )
        second_collection, second_created = MealService.ingest(
            system="device",
            source_identifier=second_serial_number,
            device_serial_number=second_serial_number,
            external_user_id="BIOUSER006",
            external_event_id="EVENT006B",
            timestamp=timezone.make_aware(datetime(2026, 9, 7, 11, 0)),
        )

        self.assertTrue(first_created)
        self.assertTrue(second_created)
        self.assertEqual(first_collection.sequence_number, 1)
        self.assertEqual(second_collection.sequence_number, 2)
        self.assertEqual(second_collection.status, MealCollectionStatus.EXCESS)
        excess = MealExcessException.objects.get(
            employee=self.employee,
            work_date=work_date,
        )
        self.assertEqual(excess.entitlement_snapshot, 1)
        self.assertEqual(excess.collected_quantity, 2)
        self.assertEqual(excess.excess_quantity, 1)

    def test_overnight_meal_scan_uses_previous_work_date(self):
        work_date = date(2026, 9, 3)
        device_serial_number = "MEALDEVICE007"
        night_shift = Shift.objects.create(
            name="Meal Test Night Shift",
            start_time="19:00",
            end_time="07:00",
            is_overnight=True,
        )

        EmployeeRosterDay.objects.create(
            employee=self.employee,
            date=work_date,
            status=RosterDayStatus.WORK,
            shift=night_shift,
        )
        EmployeeMealEntitlement.objects.create(
            employee=self.employee,
            tickets_per_work_day=1,
            effective_from=work_date,
            reason="Overnight work date test",
        )
        MealTicketRate.objects.create(
            amount=Decimal("700.00"),
            effective_from=work_date,
        )
        MealDevice.objects.create(
            name="Meal Overnight Device",
            serial_number=device_serial_number,
            active=True,
        )
        BiometricIdentity.objects.create(
            employee=self.employee,
            system="device",
            source_identifier=device_serial_number,
            external_user_id="BIOUSER007",
            is_active=True,
        )

        collection, created = MealService.ingest(
            system="device",
            source_identifier=device_serial_number,
            device_serial_number=device_serial_number,
            external_user_id="BIOUSER007",
            external_event_id="EVENT007",
            timestamp=timezone.make_aware(datetime(2026, 9, 4, 2, 0)),
        )

        self.assertTrue(created)
        self.assertEqual(collection.work_date, work_date)
        self.assertEqual(collection.shift, night_shift)

    def test_overnight_resolution_uses_configured_shift_end(self):
        night_shift = Shift.objects.create(
            name="Meal Resolution Night Shift",
            start_time="19:00",
            end_time="07:00",
            is_overnight=True,
        )
        EmployeeRosterDay.objects.create(
            employee=self.employee,
            date=date(2026, 9, 3),
            status=RosterDayStatus.WORK,
            shift=night_shift,
        )
        current_roster = EmployeeRosterDay.objects.create(
            employee=self.employee,
            date=date(2026, 9, 4),
            status=RosterDayStatus.WORK,
            shift=self.shift,
        )

        before_end_date, before_end_roster = MealService.resolve_work_day(
            self.employee,
            timezone.make_aware(datetime(2026, 9, 4, 6, 59)),
        )
        after_end_date, after_end_roster = MealService.resolve_work_day(
            self.employee,
            timezone.make_aware(datetime(2026, 9, 4, 7, 1)),
        )

        self.assertEqual(before_end_date, date(2026, 9, 3))
        self.assertEqual(before_end_roster.shift, night_shift)
        self.assertEqual(after_end_date, date(2026, 9, 4))
        self.assertEqual(after_end_roster, current_roster)

    def test_custom_overnight_resolution_uses_its_configured_end(self):
        custom_night_shift = Shift.objects.create(
            name="Meal Custom Night Shift",
            start_time="21:30",
            end_time="09:30",
            is_overnight=True,
        )
        EmployeeRosterDay.objects.create(
            employee=self.employee,
            date=date(2026, 9, 3),
            status=RosterDayStatus.WORK,
            shift=custom_night_shift,
        )
        current_roster = EmployeeRosterDay.objects.create(
            employee=self.employee,
            date=date(2026, 9, 4),
            status=RosterDayStatus.WORK,
            shift=self.shift,
        )

        before_end_date, before_end_roster = MealService.resolve_work_day(
            self.employee,
            timezone.make_aware(datetime(2026, 9, 4, 9, 29)),
        )
        after_end_date, after_end_roster = MealService.resolve_work_day(
            self.employee,
            timezone.make_aware(datetime(2026, 9, 4, 9, 31)),
        )

        self.assertEqual(before_end_date, date(2026, 9, 3))
        self.assertEqual(before_end_roster.shift, custom_night_shift)
        self.assertEqual(after_end_date, date(2026, 9, 4))
        self.assertEqual(after_end_roster, current_roster)

    def test_day_shift_resolution_remains_on_the_same_date(self):
        roster = EmployeeRosterDay.objects.create(
            employee=self.employee,
            date=date(2026, 9, 7),
            status=RosterDayStatus.WORK,
            shift=self.shift,
        )

        work_date, resolved_roster = MealService.resolve_work_day(
            self.employee,
            timezone.make_aware(datetime(2026, 9, 7, 10, 0)),
        )

        self.assertEqual(work_date, date(2026, 9, 7))
        self.assertEqual(resolved_roster, roster)


class MealWorkflowTests(TestCase):
    def setUp(self):
        self.employee = Employee.objects.create(
            employee_id="MEALWORK001",
            first_name="Workflow",
            last_name="Test",
            basic_salary=Decimal("50000.00"),
        )
        self.actor = get_user_model().objects.create_superuser(
            username="meal-workflow-admin",
            email="meal-workflow@example.com",
            password="password",
        )
        self.client = APIClient()

    def create_excess(self, **overrides):
        values = {
            "employee": self.employee,
            "work_date": date(2026, 9, 7),
            "entitlement_snapshot": 1,
            "collected_quantity": 3,
            "excess_quantity": 2,
            "rate_snapshot": Decimal("700.00"),
            "proposed_deduction": Decimal("1400.00"),
            "status": MealExcessStatus.PENDING,
        }
        values.update(overrides)
        return MealExcessException.objects.create(**values)

    def test_approve_creates_full_idempotent_meal_deduction(self):
        period = PayrollPeriod.objects.create(year=2026, month=9)
        payroll = EmployeePayroll.objects.create(
            payroll_period=period,
            employee=self.employee,
            basic_salary=Decimal("50000.00"),
            gross_earnings=Decimal("50000.00"),
            net_pay=Decimal("50000.00"),
        )
        exception = self.create_excess()

        result = MealService.approve(exception, period, self.actor)

        self.assertEqual(result.status, MealExcessStatus.DEDUCTED)
        self.assertEqual(result.reviewer, self.actor)
        self.assertIsNotNone(result.reviewed_at)
        line = PayrollLineItem.objects.get(
            payroll=payroll,
            source_type="meal_excess",
            source_reference=str(exception.pk),
        )
        self.assertEqual(line.item_type, "deduction")
        self.assertEqual(line.amount, Decimal("1400.00"))
        self.assertTrue(line.is_system_generated)
        self.assertEqual(PayrollLineItem.objects.filter(payroll=payroll).count(), 1)
        self.assertEqual(AuditEvent.objects.filter(event_type="meals.excess_approved").count(), 1)

        with self.assertRaisesMessage(ValueError, "already been decided"):
            MealService.approve(result, period, self.actor)
        self.assertEqual(PayrollLineItem.objects.filter(payroll=payroll).count(), 1)

    def test_approve_posts_full_meal_deduction_when_net_pay_is_lower(self):
        period = PayrollPeriod.objects.create(year=2026, month=9)
        payroll = EmployeePayroll.objects.create(
            payroll_period=period,
            employee=self.employee,
            basic_salary=Decimal("500.00"),
            gross_earnings=Decimal("500.00"),
            net_pay=Decimal("500.00"),
        )
        exception = self.create_excess(
            entitlement_snapshot=0,
            collected_quantity=1,
            excess_quantity=1,
            rate_snapshot=Decimal("700.00"),
            proposed_deduction=Decimal("700.00"),
        )

        result = MealService.approve(exception, period, self.actor)

        payroll.refresh_from_db()
        line = PayrollLineItem.objects.get(
            payroll=payroll,
            source_type="meal_excess",
            source_reference=str(exception.pk),
        )
        self.assertEqual(result.status, MealExcessStatus.DEDUCTED)
        self.assertEqual(line.item_type, "deduction")
        self.assertEqual(line.code, "MEAL_EXCESS")
        self.assertEqual(line.amount, Decimal("700.00"))
        self.assertTrue(line.is_system_generated)
        self.assertEqual(payroll.total_deductions, Decimal("700.00"))
        self.assertEqual(payroll.net_pay, Decimal("-200.00"))
        self.assertEqual(PayrollLineItem.objects.filter(payroll=payroll).count(), 1)

        with self.assertRaisesMessage(ValueError, "already been decided"):
            MealService.approve(result, period, self.actor)

        payroll.refresh_from_db()
        self.assertEqual(payroll.total_deductions, Decimal("700.00"))
        self.assertEqual(payroll.net_pay, Decimal("-200.00"))
        self.assertEqual(PayrollLineItem.objects.filter(payroll=payroll).count(), 1)

    def test_approve_requires_employee_payroll_and_leaves_exception_pending(self):
        period = PayrollPeriod.objects.create(year=2026, month=9)
        exception = self.create_excess()

        with self.assertRaisesMessage(ValueError, "Generate this employee's payroll record"):
            MealService.approve(exception, period, self.actor)

        exception.refresh_from_db()
        self.assertEqual(exception.status, MealExcessStatus.PENDING)
        self.assertIsNone(exception.reviewer)
        self.assertFalse(PayrollLineItem.objects.exists())

    def test_approve_rejects_immutable_payroll_period(self):
        period = PayrollPeriod.objects.create(year=2026, month=9, status="paid")
        exception = self.create_excess()

        with self.assertRaisesMessage(ValueError, "cannot accept meal deductions"):
            MealService.approve(exception, period, self.actor)

        exception.refresh_from_db()
        self.assertEqual(exception.status, MealExcessStatus.PENDING)

    def test_approve_rejects_immutable_employee_payroll(self):
        for month, payroll_status in [
            (9, EmployeePayrollStatus.APPROVED),
            (10, EmployeePayrollStatus.PAID),
        ]:
            with self.subTest(payroll_status=payroll_status):
                period = PayrollPeriod.objects.create(year=2026, month=month)
                payroll = EmployeePayroll.objects.create(
                    payroll_period=period,
                    employee=self.employee,
                    basic_salary=Decimal("50000.00"),
                    gross_earnings=Decimal("50000.00"),
                    net_pay=Decimal("50000.00"),
                    status=payroll_status,
                )
                exception = self.create_excess(
                    work_date=date(2026, month, 7),
                )

                with self.assertRaisesMessage(
                    ValueError,
                    "employee payroll record cannot accept meal deductions",
                ):
                    MealService.approve(exception, period, self.actor)

                exception.refresh_from_db()
                self.assertEqual(exception.status, MealExcessStatus.PENDING)
                self.assertIsNone(exception.reviewer)
                self.assertFalse(
                    PayrollLineItem.objects.filter(payroll=payroll).exists()
                )

    def test_cancel_requires_reason_and_records_audit_and_notification(self):
        exception = self.create_excess()

        with self.assertRaisesMessage(ValueError, "cancellation reason is required"):
            MealService.cancel(exception, self.actor, "  ")

        result = MealService.cancel(exception, self.actor, "Duplicate meal scan reviewed.")

        self.assertEqual(result.status, MealExcessStatus.CANCELLED)
        self.assertEqual(result.reviewer, self.actor)
        self.assertIsNotNone(result.reviewed_at)
        self.assertEqual(result.comment, "Duplicate meal scan reviewed.")
        self.assertTrue(AuditEvent.objects.filter(event_type="meals.excess_cancelled").exists())
        self.assertTrue(
            Notification.objects.filter(
                recipient=self.actor,
                event_type="meals.excess_cancelled",
            ).exists()
        )

    def test_reviewed_excess_cannot_be_reviewed_again(self):
        period = PayrollPeriod.objects.create(year=2026, month=9)
        EmployeePayroll.objects.create(
            payroll_period=period,
            employee=self.employee,
            basic_salary=Decimal("50000.00"),
            gross_earnings=Decimal("50000.00"),
            net_pay=Decimal("50000.00"),
        )
        approved = self.create_excess()
        MealService.approve(approved, period, self.actor)

        with self.assertRaisesMessage(ValueError, "already been decided"):
            MealService.cancel(approved, self.actor, "Cannot cancel approved excess.")

        cancelled = self.create_excess(work_date=date(2026, 9, 8))
        MealService.cancel(cancelled, self.actor, "Cancelled after review.")

        with self.assertRaisesMessage(ValueError, "already been decided"):
            MealService.approve(cancelled, period, self.actor)

    def test_meal_configuration_apis_preserve_history_and_reject_overlap(self):
        self.actor.user_permissions.add(
            Permission.objects.get(codename="manage_meal_configuration")
        )
        self.client.force_authenticate(self.actor)

        entitlement = self.client.post(
            "/api/meals/entitlements/",
            {
                "employee": self.employee.pk,
                "tickets_per_work_day": 2,
                "effective_from": "2026-01-01",
                "effective_to": "2026-03-31",
                "reason": "Approved history",
            },
            format="json",
        )
        self.assertEqual(entitlement.status_code, 201)
        overlap = self.client.post(
            "/api/meals/entitlements/",
            {
                "employee": self.employee.pk,
                "tickets_per_work_day": 1,
                "effective_from": "2026-03-01",
                "reason": "Overlapping record",
            },
            format="json",
        )
        self.assertEqual(overlap.status_code, 400)

        first_rate = self.client.post(
            "/api/meals/rates/",
            {
                "amount": "700.00",
                "effective_from": "2026-01-01",
                "effective_to": "2026-03-31",
            },
            format="json",
        )
        second_rate = self.client.post(
            "/api/meals/rates/",
            {
                "amount": "800.00",
                "effective_from": "2026-04-01",
                "effective_to": "2026-08-31",
            },
            format="json",
        )
        self.assertEqual(first_rate.status_code, 201, first_rate.data)
        self.assertEqual(second_rate.status_code, 201)
        self.assertEqual(self.client.get("/api/meals/rates/").json()["count"], 3)

        device = self.client.post(
            "/api/meals/devices/",
            {"name": "Configured Meal Device", "serial_number": "CONFIG001"},
            format="json",
        )
        self.assertEqual(device.status_code, 201)
        deactivated = self.client.patch(
            f"/api/meals/devices/{device.json()['id']}/",
            {"active": False},
            format="json",
        )
        self.assertEqual(deactivated.status_code, 200)
        self.assertFalse(deactivated.json()["active"])

    def test_configuration_user_can_create_a_normal_entitlement(self):
        configuration_user = get_user_model().objects.create_user(
            username="meal-configuration-user",
            password="password",
        )
        configuration_user.user_permissions.add(
            Permission.objects.get(codename="manage_meal_configuration")
        )
        self.client.force_authenticate(configuration_user)

        response = self.client.post(
            "/api/meals/entitlements/",
            {
                "employee": self.employee.pk,
                "tickets_per_work_day": 2,
                "effective_from": "2026-01-01",
                "reason": "Normal approved entitlement",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201, response.data)
        self.assertFalse(response.data["is_exceptional_override"])

    def test_configuration_user_cannot_create_an_exceptional_entitlement(self):
        configuration_user = get_user_model().objects.create_user(
            username="meal-limited-configuration-user",
            password="password",
        )
        configuration_user.user_permissions.add(
            Permission.objects.get(codename="manage_meal_configuration")
        )
        self.client.force_authenticate(configuration_user)

        response = self.client.post(
            "/api/meals/entitlements/",
            {
                "employee": self.employee.pk,
                "tickets_per_work_day": 4,
                "effective_from": "2026-01-01",
                "reason": "Exceptional entitlement request",
                "is_exceptional_override": True,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("is_exceptional_override", response.data)
        self.assertFalse(EmployeeMealEntitlement.objects.exists())

    def test_superuser_can_create_an_uncapped_exceptional_entitlement(self):
        self.client.force_authenticate(self.actor)

        response = self.client.post(
            "/api/meals/entitlements/",
            {
                "employee": self.employee.pk,
                "tickets_per_work_day": 7,
                "effective_from": "2026-01-01",
                "reason": "Super User exceptional entitlement",
                "is_exceptional_override": True,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201, response.data)
        self.assertTrue(response.data["is_exceptional_override"])
        self.assertEqual(response.data["tickets_per_work_day"], 7)

    def test_historical_rate_snapshot_is_not_retroactively_changed(self):
        work_date = date(2026, 9, 5)
        later_date = date(2026, 9, 7)
        shift = Shift.objects.create(
            name="Meal Rate History Shift",
            start_time="07:00",
            end_time="19:00",
        )
        for roster_date in (work_date, later_date):
            EmployeeRosterDay.objects.create(
                employee=self.employee,
                date=roster_date,
                status=RosterDayStatus.WORK,
                shift=shift,
            )
        EmployeeMealEntitlement.objects.create(
            employee=self.employee,
            tickets_per_work_day=1,
            effective_from=work_date,
            reason="Historical rate test",
        )
        MealTicketRate.objects.create(
            amount=Decimal("700.00"),
            effective_from=work_date,
            effective_to=date(2026, 9, 6),
        )
        device = MealDevice.objects.create(
            name="Historical Rate Device",
            serial_number="RATEHISTORY001",
        )
        for scan_date, external_id in ((work_date, "RATE001"), (later_date, "RATE002")):
            BiometricIdentity.objects.create(
                employee=self.employee,
                system="device",
                source_identifier=device.serial_number,
                external_user_id=f"RATEUSER{external_id}",
            )
            if scan_date == later_date:
                MealTicketRate.objects.create(
                    amount=Decimal("800.00"),
                    effective_from=later_date,
                )
            collection, created = MealService.ingest(
                system="device",
                source_identifier=device.serial_number,
                device_serial_number=device.serial_number,
                external_user_id=f"RATEUSER{external_id}",
                external_event_id=external_id,
                timestamp=timezone.make_aware(
                    datetime.combine(scan_date, datetime.min.time())
                ),
            )
            self.assertTrue(created)
            if scan_date == work_date:
                self.assertEqual(collection.rate_snapshot, Decimal("700.00"))
            else:
                self.assertEqual(collection.rate_snapshot, Decimal("800.00"))
        historical = MealCollection.objects.get(event__external_event_id="RATE001")
        self.assertEqual(historical.rate_snapshot, Decimal("700.00"))

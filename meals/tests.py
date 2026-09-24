from datetime import date, datetime
from decimal import Decimal

from attendance.integrations.aiface_protocol import IDENTITY_SYSTEM

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from attendance.models import (
    BiometricDevice,
    DeviceCommand,
    AttendanceException,
    DailyAttendance,
    EmployeeRosterDay,
    RosterDayStatus,
    Shift,
)
from audit.models import AuditEvent
from employees.models import BiometricIdentity, Employee
from meals.models import (
    MealExtraAuthorization,
    MealTerminalUserState,
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
from payroll.services import generate_payroll_for_period


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

    def test_each_extra_scan_gets_its_own_pending_excess(self):
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

        # The 1 entitled ticket goes straight through; each of the 2 extra scans is
        # its own pending decision (so they can be accepted/declined individually).
        decisions = MealExcessException.objects.filter(employee=self.employee, work_date=work_date).order_by("id")
        self.assertEqual(decisions.count(), 2)
        for decision in decisions:
            self.assertEqual(decision.status, MealExcessStatus.PENDING)
            self.assertEqual(decision.excess_quantity, 1)
            self.assertEqual(decision.proposed_deduction, Decimal("700.00"))
        self.assertEqual([d.collected_quantity for d in decisions], [2, 3])

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

    def test_accepting_before_the_payroll_record_exists_waits_and_deducts_when_payroll_is_generated(self):
        """Accept works any time; the deduction lands in that month's payroll once it exists."""
        period = PayrollPeriod.objects.create(year=2026, month=9)
        exception = self.create_excess()

        accepted = MealService.approve(exception, period, self.actor)

        self.assertEqual(accepted.status, MealExcessStatus.APPROVED)
        self.assertFalse(PayrollLineItem.objects.exists())

        summary = generate_payroll_for_period(period)

        self.assertEqual(summary.deductions_applied, 1)
        exception.refresh_from_db()
        payroll = EmployeePayroll.objects.get(payroll_period=period, employee=self.employee)
        line = PayrollLineItem.objects.get(payroll=payroll)
        self.assertEqual((exception.status, exception.payroll, exception.payroll_line_item), (MealExcessStatus.DEDUCTED, payroll, line))
        self.assertEqual((line.code, line.amount, line.source_reference), ("MEAL_EXCESS", exception.proposed_deduction, str(exception.pk)))
        payroll.refresh_from_db()
        self.assertEqual(payroll.total_deductions, exception.proposed_deduction)

    def test_accepting_before_the_months_payroll_period_even_exists(self):
        exception = self.create_excess()

        accepted = MealService.approve(exception, None, self.actor)

        self.assertEqual((accepted.status, accepted.payroll_period), (MealExcessStatus.APPROVED, None))
        period = PayrollPeriod.objects.create(year=2026, month=9)
        generate_payroll_for_period(period)
        exception.refresh_from_db()
        self.assertEqual(exception.status, MealExcessStatus.DEDUCTED)
        self.assertEqual(exception.payroll_period, period)

    def test_generating_again_does_not_deduct_the_same_excess_twice(self):
        period = PayrollPeriod.objects.create(year=2026, month=9)
        exception = self.create_excess()
        MealService.approve(exception, period, self.actor)

        generate_payroll_for_period(period)
        again = generate_payroll_for_period(period)

        self.assertEqual(again.deductions_applied, 0)
        self.assertEqual(PayrollLineItem.objects.filter(source_type="meal_excess").count(), 1)

    def test_an_excess_is_only_deducted_from_its_own_months_payroll(self):
        october = PayrollPeriod.objects.create(year=2026, month=10)
        exception = self.create_excess()  # a September work date
        MealService.approve(exception, None, self.actor)

        generate_payroll_for_period(october)

        exception.refresh_from_db()
        self.assertEqual(exception.status, MealExcessStatus.APPROVED)
        self.assertFalse(PayrollLineItem.objects.exists())

    def test_generation_leaves_an_already_approved_employee_payroll_untouched(self):
        period = PayrollPeriod.objects.create(year=2026, month=9)
        exception = self.create_excess()
        MealService.approve(exception, period, self.actor)
        EmployeePayroll.objects.create(payroll_period=period, employee=self.employee, basic_salary=Decimal("50000.00"), gross_earnings=Decimal("50000.00"), net_pay=Decimal("50000.00"), status=EmployeePayrollStatus.APPROVED)

        generate_payroll_for_period(period)

        exception.refresh_from_db()
        self.assertEqual(exception.status, MealExcessStatus.APPROVED)
        self.assertFalse(PayrollLineItem.objects.exists())

    def test_the_accept_endpoint_needs_no_payroll_period(self):
        exception = self.create_excess()
        self.actor.user_permissions.add(Permission.objects.get(codename="review_meal_excess"))
        client = APIClient()
        client.force_authenticate(self.actor)

        response = client.post(f"/api/meals/excess/{exception.pk}/approve/", {}, format="json")

        self.assertEqual((response.status_code, response.json()["status"]), (200, "approved"))

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

    def test_cancel_records_audit_and_notification(self):
        exception = self.create_excess()

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

    def test_a_reason_is_optional_when_waiving(self):
        exception = self.create_excess()

        result = MealService.cancel(exception, self.actor, "  ")

        self.assertEqual((result.status, result.comment), (MealExcessStatus.CANCELLED, ""))
        self.assertTrue(AuditEvent.objects.filter(event_type="meals.excess_cancelled").exists())

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

    def test_meal_devices_endpoint_is_read_only(self):
        """Devices are only ever created via the Biometric Devices page (see
        attendance.views.devices.sync_meal_device) - creating or editing one
        directly here would make a MealDevice with no matching
        BiometricDevice for the gateway to route scans to."""
        self.actor.user_permissions.add(
            Permission.objects.get(codename="manage_meal_configuration")
        )
        self.client.force_authenticate(self.actor)
        device = MealDevice.objects.create(name="Canteen Scanner", serial_number="MEALDEV001")

        listed = self.client.get("/api/meals/devices/")
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.json()["count"], 1)
        self.assertEqual(listed.json()["results"][0]["serial_number"], "MEALDEV001")

        create_attempt = self.client.post(
            "/api/meals/devices/",
            {"name": "Orphaned Device", "serial_number": "NOPE001"},
            format="json",
        )
        self.assertEqual(create_attempt.status_code, 405)

        edit_attempt = self.client.patch(
            f"/api/meals/devices/{device.pk}/",
            {"active": False},
            format="json",
        )
        self.assertEqual(edit_attempt.status_code, 404)

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


class MealDeviceMirrorSelfHealingTests(TestCase):
    """The Biometric Devices page is the source of truth for meal terminals; a
    missing or deactivated MealDevice mirror must not make the terminal's scans
    vanish as "Unknown meal device" (seen in production)."""

    SERIAL = "AYTF25068946"

    def setUp(self):
        self.work_date = date(2026, 9, 7)
        self.employee = Employee.objects.create(employee_id="000010", first_name="Test", last_name="Worker")
        EmployeeMealEntitlement.objects.create(employee=self.employee, tickets_per_work_day=1, effective_from=self.work_date, reason="test")
        MealTicketRate.objects.create(amount=Decimal("700.00"), effective_from=self.work_date)
        BiometricIdentity.objects.create(employee=self.employee, system="vendor_flask_gateway", source_identifier=self.SERIAL, external_user_id="10")

    def scan(self, serial=None):
        serial = serial or self.SERIAL
        return MealService.ingest(
            system="vendor_flask_gateway", source_identifier=serial, device_serial_number=serial,
            external_user_id="10", external_event_id="rec-1", timestamp=timezone.make_aware(datetime(2026, 9, 7, 12, 0)),
        )

    def test_a_registered_meal_terminal_whose_mirror_row_is_missing_still_records_the_scan(self):
        BiometricDevice.objects.create(name="AI Meal Ticket", serial_number=self.SERIAL, location="x", device_type="factory", purpose="meal_ticket")
        self.assertFalse(MealDevice.objects.exists())

        _, created = self.scan()

        self.assertTrue(created)
        mirror = MealDevice.objects.get(serial_number=self.SERIAL)
        self.assertTrue(mirror.active)
        self.assertEqual(mirror.name, "AI Meal Ticket")

    def test_a_deactivated_mirror_is_reactivated_for_a_registered_meal_terminal(self):
        BiometricDevice.objects.create(name="AI Meal Ticket", serial_number=self.SERIAL, location="x", device_type="factory", purpose="meal_ticket")
        MealDevice.objects.create(name="AI Meal Ticket", serial_number=self.SERIAL, active=False)

        _, created = self.scan()

        self.assertTrue(created)
        self.assertTrue(MealDevice.objects.get(serial_number=self.SERIAL).active)

    def test_a_serial_that_is_not_registered_at_all_is_still_rejected(self):
        with self.assertRaisesMessage(ValueError, "Unknown meal device."):
            self.scan()
        self.assertFalse(MealDevice.objects.exists())

    def test_an_attendance_terminal_cannot_post_meal_scans(self):
        BiometricDevice.objects.create(name="Gate", serial_number=self.SERIAL, location="x", device_type="factory", purpose="attendance")
        with self.assertRaisesMessage(ValueError, "Unknown meal device."):
            self.scan()


class MealTicketVoidTests(TestCase):
    """Test/accidental scans must be removable from what the vendor is owed
    without deleting the record, and without leaving a stale excess behind."""

    def setUp(self):
        self.day = date(2026, 9, 7)
        self.employee = Employee.objects.create(employee_id="000010", first_name="Test", last_name="Worker")
        EmployeeMealEntitlement.objects.create(employee=self.employee, tickets_per_work_day=1, effective_from=self.day, reason="test")
        MealTicketRate.objects.create(amount=Decimal("700.00"), effective_from=self.day)
        MealDevice.objects.create(name="Canteen", serial_number="MEAL001", active=True)
        BiometricIdentity.objects.create(employee=self.employee, system="device", source_identifier="MEAL001", external_user_id="10")
        self.reviewer = get_user_model().objects.create_user(username="void-reviewer", password="pw")
        self.reviewer.user_permissions.add(Permission.objects.get(codename="review_meal_excess"), Permission.objects.get(codename="record_meal_operations"))
        self.period = PayrollPeriod.objects.create(year=2026, month=9)
        shift = Shift.objects.create(name="Void Test Day", start_time="07:00", end_time="19:00", is_overnight=False)
        EmployeeRosterDay.objects.create(employee=self.employee, date=self.day, status=RosterDayStatus.WORK, shift=shift)
        self.client = APIClient()

    def scan(self, event_id, hour=12):
        collection, _ = MealService.ingest(
            system="device", source_identifier="MEAL001", device_serial_number="MEAL001", external_user_id="10",
            external_event_id=event_id, timestamp=timezone.make_aware(datetime(2026, 9, 7, hour, 0)),
        )
        return collection

    def owed(self):
        self.client.force_authenticate(self.reviewer)
        return self.client.get(f"/api/meals/vendor/{self.period.pk}/").json()

    def test_a_voided_ticket_drops_out_of_the_vendor_total_but_stays_on_record(self):
        collection = self.scan("e1")
        self.assertEqual((self.owed()["tickets_issued"], float(self.owed()["amount_owed"])), (1, 700.0))

        MealService.void_collection(collection, self.reviewer, "Test scan")

        self.assertEqual((self.owed()["tickets_issued"], float(self.owed()["amount_owed"])), (0, 0.0))
        collection.refresh_from_db()
        self.assertIsNotNone(collection.voided_at)
        self.assertEqual((collection.voided_by, collection.void_reason), (self.reviewer, "Test scan"))
        self.assertTrue(MealCollection.objects.filter(pk=collection.pk).exists())

    def test_voiding_the_only_over_entitlement_ticket_cancels_the_pending_excess(self):
        self.scan("e1")
        second = self.scan("e2", hour=13)
        exception = MealExcessException.objects.get(employee=self.employee, work_date=self.day)
        self.assertEqual((exception.status, exception.collected_quantity), (MealExcessStatus.PENDING, 2))

        MealService.void_collection(second, self.reviewer, "Accidental scan")

        exception.refresh_from_db()
        self.assertEqual(exception.status, MealExcessStatus.CANCELLED)
        self.assertIn("Accidental scan", exception.comment)

    def test_voiding_one_extra_ticket_cancels_only_its_own_decision(self):
        self.scan("e1")
        second = self.scan("e2", hour=13)
        third = self.scan("e3", hour=14)

        MealService.void_collection(third, self.reviewer, "Duplicate")

        second.refresh_from_db(); third.refresh_from_db()
        self.assertEqual(second.excess_exception.status, MealExcessStatus.PENDING)
        self.assertEqual(third.excess_exception.status, MealExcessStatus.CANCELLED)

    def test_a_voided_scan_does_not_use_up_the_employees_real_ticket_for_the_day(self):
        test_scan = self.scan("e1")
        MealService.void_collection(test_scan, self.reviewer, "Test scan")

        real = self.scan("e2", hour=13)

        self.assertEqual((real.sequence_number, real.status), (1, MealCollectionStatus.WITHIN))

    def test_cannot_void_once_the_excess_was_deducted_in_payroll(self):
        self.scan("e1")
        second = self.scan("e2", hour=13)
        MealExcessException.objects.filter(employee=self.employee).update(status=MealExcessStatus.DEDUCTED)

        with self.assertRaisesMessage(ValueError, "already deducted"):
            MealService.void_collection(second, self.reviewer, "Oops")
        second.refresh_from_db()
        self.assertIsNone(second.voided_at)

    def test_voiding_a_ticket_accepted_but_not_yet_in_payroll_withdraws_the_acceptance(self):
        self.scan("e1")
        second = self.scan("e2", hour=13)
        MealExcessException.objects.filter(employee=self.employee).update(status=MealExcessStatus.APPROVED)

        MealService.void_collection(second, self.reviewer, "Accepted by mistake")

        second.refresh_from_db()
        self.assertIsNotNone(second.voided_at)
        self.assertEqual(second.excess_exception.status, MealExcessStatus.CANCELLED)

    def test_a_ticket_can_be_voided_without_a_reason_but_not_twice(self):
        collection = self.scan("e1")
        MealService.void_collection(collection, self.reviewer, "   ")
        collection.refresh_from_db()
        self.assertIsNotNone(collection.voided_at)
        self.assertEqual(collection.void_reason, "")
        with self.assertRaisesMessage(ValueError, "already been voided"):
            MealService.void_collection(collection, self.reviewer, "Again")

    def test_the_void_endpoint_needs_the_review_permission_and_takes_an_optional_reason(self):
        collection = self.scan("e1")
        viewer = get_user_model().objects.create_user(username="void-viewer", password="pw")
        viewer.user_permissions.add(Permission.objects.get(codename="record_meal_operations"))
        self.client.force_authenticate(viewer)
        self.assertEqual(self.client.post(f"/api/meals/collections/{collection.pk}/void/", {"reason": "x"}, format="json").status_code, 403)

        self.client.force_authenticate(self.reviewer)
        ok = self.client.post(f"/api/meals/collections/{collection.pk}/void/", {}, format="json")
        self.assertEqual((ok.status_code, ok.json()["voided"]), (200, True))

    def test_the_operations_list_marks_voided_tickets(self):
        collection = self.scan("e1")
        MealService.void_collection(collection, self.reviewer, "Test scan")
        self.client.force_authenticate(self.reviewer)

        row = self.client.get("/api/meals/operations/").json()["collections"][0]

        self.assertEqual((row["voided"], row["void_reason"]), (True, "Test scan"))


class MealExcessDeclineTests(TestCase):
    """Entitled tickets go straight through; a non-entitled one is decided.
    Waive (cancel) keeps the ticket so the vendor is still paid; Decline voids it."""

    def setUp(self):
        self.day = date(2026, 9, 7)
        self.employee = Employee.objects.create(employee_id="000010", first_name="Test", last_name="Worker")
        EmployeeMealEntitlement.objects.create(employee=self.employee, tickets_per_work_day=1, effective_from=self.day, reason="test")
        MealTicketRate.objects.create(amount=Decimal("700.00"), effective_from=self.day)
        MealDevice.objects.create(name="Canteen", serial_number="MEAL001", active=True)
        BiometricIdentity.objects.create(employee=self.employee, system="device", source_identifier="MEAL001", external_user_id="10")
        shift = Shift.objects.create(name="Decline Test Day", start_time="07:00", end_time="19:00", is_overnight=False)
        EmployeeRosterDay.objects.create(employee=self.employee, date=self.day, status=RosterDayStatus.WORK, shift=shift)
        self.reviewer = get_user_model().objects.create_user(username="decline-reviewer", password="pw")
        self.reviewer.user_permissions.add(Permission.objects.get(codename="review_meal_excess"), Permission.objects.get(codename="record_meal_operations"))
        self.period = PayrollPeriod.objects.create(year=2026, month=9)
        self.client = APIClient()
        self.client.force_authenticate(self.reviewer)
        self.entitled = self.scan("e1", 12)
        self.extra = self.scan("e2", 13)
        self.exception = MealExcessException.objects.get(employee=self.employee, work_date=self.day)

    def scan(self, event_id, hour):
        collection, _ = MealService.ingest(
            system="device", source_identifier="MEAL001", device_serial_number="MEAL001", external_user_id="10",
            external_event_id=event_id, timestamp=timezone.make_aware(datetime(2026, 9, 7, hour, 0)),
        )
        return collection

    def vendor(self):
        data = self.client.get(f"/api/meals/vendor/{self.period.pk}/").json()
        return data["tickets_issued"], float(data["amount_owed"])

    def test_the_entitled_ticket_goes_straight_through_and_only_the_extra_needs_a_decision(self):
        self.assertEqual((self.entitled.status, self.extra.status), (MealCollectionStatus.WITHIN, MealCollectionStatus.EXCESS))
        self.assertEqual((self.exception.status, self.exception.excess_quantity), (MealExcessStatus.PENDING, 1))
        self.assertEqual(self.vendor(), (2, 1400.0))

    def test_declining_voids_only_the_extra_ticket_so_the_vendor_is_not_billed_for_it(self):
        MealService.decline(self.exception, self.reviewer, "Not authorised")

        self.exception.refresh_from_db(); self.entitled.refresh_from_db(); self.extra.refresh_from_db()
        self.assertEqual(self.exception.status, MealExcessStatus.DECLINED)
        self.assertEqual(self.exception.comment, "Not authorised")
        self.assertIsNone(self.entitled.voided_at)
        self.assertIsNotNone(self.extra.voided_at)
        self.assertIn("Not authorised", self.extra.void_reason)
        self.assertEqual(self.vendor(), (1, 700.0))

    def test_waiving_the_deduction_still_bills_the_vendor_for_the_extra_ticket(self):
        MealService.cancel(self.exception, self.reviewer, "Authorised overtime meal")

        self.assertEqual(self.vendor(), (2, 1400.0))

    def test_declining_does_not_create_a_payroll_deduction(self):
        MealService.decline(self.exception, self.reviewer, "Not authorised")
        self.exception.refresh_from_db()
        self.assertIsNone(self.exception.payroll_line_item)
        self.assertEqual(PayrollLineItem.objects.count(), 0)

    def test_a_reason_is_optional_and_only_a_pending_excess_can_be_declined(self):
        MealService.decline(self.exception, self.reviewer, " ")
        self.exception.refresh_from_db(); self.extra.refresh_from_db()
        self.assertEqual((self.exception.status, self.exception.comment), (MealExcessStatus.DECLINED, ""))
        self.assertEqual(self.extra.void_reason, "Excess declined")
        with self.assertRaisesMessage(ValueError, "already been decided"):
            MealService.decline(self.exception, self.reviewer, "Again")

    def test_a_declined_extra_frees_the_slot_so_a_later_real_scan_is_not_excess(self):
        MealService.decline(self.exception, self.reviewer, "Not authorised")
        MealService.void_collection(self.entitled, self.reviewer, "Wrong person scanned")

        again = self.scan("e3", 15)

        self.assertEqual((again.sequence_number, again.status), (1, MealCollectionStatus.WITHIN))

    def test_the_decline_endpoint_needs_the_review_permission(self):
        viewer = get_user_model().objects.create_user(username="decline-viewer", password="pw")
        viewer.user_permissions.add(Permission.objects.get(codename="record_meal_operations"))
        self.client.force_authenticate(viewer)
        self.assertEqual(self.client.post(f"/api/meals/excess/{self.exception.pk}/decline/", {"reason": "x"}, format="json").status_code, 403)

        self.client.force_authenticate(self.reviewer)
        ok = self.client.post(f"/api/meals/excess/{self.exception.pk}/decline/", {}, format="json")
        self.assertEqual((ok.status_code, ok.json()["status"]), (200, "declined"))

    def test_operations_rows_say_which_tickets_have_an_excess_decision(self):
        rows = {row["id"]: row for row in self.client.get("/api/meals/operations/").json()["collections"]}
        self.assertEqual(rows[self.extra.pk]["excess_id"], self.exception.pk)
        self.assertEqual(rows[self.extra.pk]["excess_status"], "pending")
        self.assertEqual(rows[self.extra.pk]["status"], "excess")
        self.assertEqual(rows[self.entitled.pk]["status"], "within_entitlement")


class MealSeparateExcessDecisionTests(TestCase):
    """A further extra ticket must never inherit a decision made for an earlier
    one (production: after one waive, every later scan that day was silently
    "waived", unreviewed, while the vendor was still billed)."""

    def setUp(self):
        self.day = date(2026, 9, 7)
        self.employee = Employee.objects.create(employee_id="000010", first_name="Test", last_name="Worker")
        EmployeeMealEntitlement.objects.create(employee=self.employee, tickets_per_work_day=1, effective_from=self.day, reason="test")
        MealTicketRate.objects.create(amount=Decimal("700.00"), effective_from=self.day)
        MealDevice.objects.create(name="Canteen", serial_number="MEAL001", active=True)
        BiometricIdentity.objects.create(employee=self.employee, system="device", source_identifier="MEAL001", external_user_id="10")
        shift = Shift.objects.create(name="Separate Test Day", start_time="07:00", end_time="19:00", is_overnight=False)
        EmployeeRosterDay.objects.create(employee=self.employee, date=self.day, status=RosterDayStatus.WORK, shift=shift)
        self.reviewer = get_user_model().objects.create_user(username="separate-reviewer", password="pw")
        self.reviewer.user_permissions.add(Permission.objects.get(codename="review_meal_excess"), Permission.objects.get(codename="record_meal_operations"))
        self.period = PayrollPeriod.objects.create(year=2026, month=9)
        self.client = APIClient()
        self.client.force_authenticate(self.reviewer)

    def scan(self, event_id, hour):
        collection, _ = MealService.ingest(
            system="device", source_identifier="MEAL001", device_serial_number="MEAL001", external_user_id="10",
            external_event_id=event_id, timestamp=timezone.make_aware(datetime(2026, 9, 7, hour, 0)),
        )
        collection.refresh_from_db()
        return collection

    def test_an_extra_ticket_after_an_earlier_one_was_waived_needs_its_own_decision(self):
        self.scan("e1", 12)
        second = self.scan("e2", 13)
        first_decision = second.excess_exception
        MealService.cancel(first_decision, self.reviewer, "Authorised overtime meal")

        third = self.scan("e3", 14)

        self.assertNotEqual(third.excess_exception_id, first_decision.pk)
        self.assertEqual(third.excess_exception.status, MealExcessStatus.PENDING)
        first_decision.refresh_from_db()
        self.assertEqual((first_decision.status, first_decision.excess_quantity), (MealExcessStatus.CANCELLED, 1))

    def test_declining_one_of_several_extra_tickets_declines_only_that_one(self):
        """Production: three extra scans by one person, one Decline click, all three declined."""
        self.scan("e1", 12)
        second, third, fourth = self.scan("e2", 13), self.scan("e3", 14), self.scan("e4", 15)
        self.assertEqual(len({second.excess_exception_id, third.excess_exception_id, fourth.excess_exception_id}), 3)

        MealService.decline(third.excess_exception, self.reviewer, "")

        second.refresh_from_db(); third.refresh_from_db(); fourth.refresh_from_db()
        self.assertEqual((second.voided_at is None, third.voided_at is None, fourth.voided_at is None), (True, False, True))
        self.assertEqual((second.excess_exception.status, fourth.excess_exception.status), (MealExcessStatus.PENDING, MealExcessStatus.PENDING))
        data = self.client.get(f"/api/meals/vendor/{self.period.pk}/").json()
        self.assertEqual((data["tickets_issued"], float(data["amount_owed"])), (3, 2100.0))

    def test_a_decision_already_accepted_is_left_alone_when_another_extra_arrives(self):
        self.scan("e1", 12)
        decision = self.scan("e2", 13).excess_exception
        MealExcessException.objects.filter(pk=decision.pk).update(status=MealExcessStatus.APPROVED)

        third = self.scan("e3", 14)

        decision.refresh_from_db()
        self.assertEqual((decision.status, decision.excess_quantity, decision.proposed_deduction), (MealExcessStatus.APPROVED, 1, Decimal("700.00")))
        self.assertEqual(third.excess_exception.status, MealExcessStatus.PENDING)

    def test_declining_the_new_decision_leaves_the_earlier_waived_ticket_standing(self):
        self.scan("e1", 12)
        second = self.scan("e2", 13)
        MealService.cancel(second.excess_exception, self.reviewer, "Authorised overtime meal")
        third = self.scan("e3", 14)

        MealService.decline(third.excess_exception, self.reviewer, "Not authorised")

        second.refresh_from_db(); third.refresh_from_db()
        self.assertIsNone(second.voided_at)
        self.assertIsNotNone(third.voided_at)
        data = self.client.get(f"/api/meals/vendor/{self.period.pk}/").json()
        self.assertEqual((data["tickets_issued"], float(data["amount_owed"])), (2, 1400.0))

    def test_operations_rows_point_each_ticket_at_its_own_decision(self):
        first = self.scan("e1", 12)
        second = self.scan("e2", 13)
        MealService.cancel(second.excess_exception, self.reviewer, "Authorised overtime meal")
        third = self.scan("e3", 14)

        rows = {row["id"]: row for row in self.client.get("/api/meals/operations/").json()["collections"]}

        self.assertIsNone(rows[first.pk]["excess_id"])
        self.assertEqual((rows[second.pk]["excess_status"], rows[third.pk]["excess_status"]), ("cancelled", "pending"))
        self.assertNotEqual(rows[second.pk]["excess_id"], rows[third.pk]["excess_id"])


class TicketDecisionBackfillTests(TestCase):
    """The data migration must repair tickets that were silently folded into an
    earlier decision, shaped like the production data that exposed the bug."""

    def test_undecided_live_tickets_get_a_pending_decision_and_decided_ones_keep_theirs(self):
        import importlib

        from django.apps import apps

        migration = importlib.import_module("meals.migrations.0010_link_tickets_to_excess_decisions")
        day = date(2026, 9, 7)
        employee = Employee.objects.create(employee_id="000010", first_name="Test", last_name="Worker")
        MealDevice.objects.create(name="Canteen", serial_number="MEAL001", active=True)
        BiometricIdentity.objects.create(employee=employee, system="device", source_identifier="MEAL001", external_user_id="10")
        MealTicketRate.objects.create(amount=Decimal("700.00"), effective_from=day)
        tickets = []
        for index, hour in enumerate((12, 13, 14)):
            collection, _ = MealService.ingest(
                system="device", source_identifier="MEAL001", device_serial_number="MEAL001", external_user_id="10",
                external_event_id=f"e{index}", timestamp=timezone.make_aware(datetime(2026, 9, 7, hour, 0)),
            )
            tickets.append(collection)
        # the old world: one decision for the day, covering only the first ticket, later ones folded in
        MealExcessException.objects.all().delete()
        old = MealExcessException.objects.create(employee=employee, work_date=day, entitlement_snapshot=0, collected_quantity=1, excess_quantity=1, rate_snapshot=Decimal("700.00"), proposed_deduction=Decimal("700.00"), status=MealExcessStatus.CANCELLED)
        MealCollection.objects.update(excess_exception=None)
        MealCollection.objects.filter(pk=tickets[2].pk).update(voided_at=None)

        migration.link_tickets_and_reopen_undecided(apps, None)

        first, second, third = (MealCollection.objects.get(pk=t.pk) for t in tickets)
        self.assertEqual(first.excess_exception_id, old.pk)
        self.assertNotEqual(second.excess_exception_id, old.pk)
        self.assertEqual(second.excess_exception_id, third.excess_exception_id)
        reopened = MealExcessException.objects.get(pk=second.excess_exception_id)
        self.assertEqual((reopened.status, reopened.excess_quantity, reopened.proposed_deduction), (MealExcessStatus.PENDING, 2, Decimal("1400.00")))
        old.refresh_from_db()
        self.assertEqual(old.status, MealExcessStatus.CANCELLED)



class TerminalReplyTests(TestCase):
    """The meal terminal is told to allow the scan (so its printer issues the ticket) and what to show."""

    def setUp(self):
        self.day = date(2026, 9, 7)
        self.employee = Employee.objects.create(employee_id="000010", first_name="Ada", last_name="Obi")
        EmployeeMealEntitlement.objects.create(employee=self.employee, tickets_per_work_day=1, effective_from=self.day, reason="test")
        MealTicketRate.objects.create(amount=Decimal("700.00"), effective_from=self.day)
        MealDevice.objects.create(name="Canteen", serial_number="MEAL001", active=True)
        BiometricIdentity.objects.create(employee=self.employee, system=IDENTITY_SYSTEM, source_identifier="MEAL001", external_user_id="10")
        shift = Shift.objects.create(name="Reply Test Day", start_time="07:00", end_time="19:00", is_overnight=False)
        EmployeeRosterDay.objects.create(employee=self.employee, date=self.day, status=RosterDayStatus.WORK, shift=shift)

    def post(self, enroll_id, event):
        from django.test import override_settings
        with override_settings(BIOMETRIC_BRIDGE_SECRET="s3cret", SECURE_SSL_REDIRECT=False):
            return APIClient().post(
                "/api/meals/integrations/vendor-gateway/punches/",
                {"records": [{"gateway_record_id": event, "device_serial_number": "MEAL001", "enroll_id": enroll_id, "timestamp": "2026-09-07 12:00:00"}]},
                format="json", HTTP_X_BIOMETRIC_BRIDGE_KEY="s3cret",
            ).json()["results"][0]

    def test_a_valid_scan_is_allowed_with_the_ticket_number(self):
        result = self.post("10", 1)
        self.assertEqual(result["access"], 1)
        self.assertTrue(result["entitled"])
        self.assertEqual(result["message"], "Ticket 1 of 1 - Ada")

    def test_an_extra_ticket_is_still_handed_over_because_hr_decides_later(self):
        self.post("10", 1)
        result = self.post("10", 2)
        self.assertEqual(result["access"], 1)
        self.assertFalse(result["entitled"])
        self.assertEqual(result["message"], "Not entitled - Ada")

    def test_someone_not_enrolled_is_denied_with_a_reason(self):
        result = self.post("999", 3)
        self.assertEqual((result["access"], result["message"]), (0, "Not enrolled for meals"))


class MealGatingTests(TestCase):
    """People listed in MEAL_GATING_EMPLOYEE_IDS are switched off at the terminal once they have had today's
    tickets (or have none today) and back on when a ticket is free again. Nobody else is ever touched."""

    def setUp(self):
        from django.test import override_settings
        from meals import gating

        self.gating = gating
        self.override = override_settings(MEAL_GATING_EMPLOYEE_IDS=["PILOT1"])
        self.override.enable()
        self.addCleanup(self.override.disable)
        self.today = timezone.localdate()
        self.pilot = Employee.objects.create(employee_id="PILOT1", first_name="Pilot", last_name="One")
        self.other = Employee.objects.create(employee_id="OTHER1", first_name="Other", last_name="One")
        self.device = BiometricDevice.objects.create(name="Canteen", serial_number="MEALGATE1", purpose="meal_ticket")
        MealDevice.objects.create(name="Canteen", serial_number="MEALGATE1", active=True)
        for number, employee in ((7, self.pilot), (8, self.other)):
            BiometricIdentity.objects.create(employee=employee, system=IDENTITY_SYSTEM, source_identifier="MEALGATE1", external_user_id=str(number))
            EmployeeMealEntitlement.objects.create(employee=employee, tickets_per_work_day=2, effective_from=self.today, reason="test")
        MealTicketRate.objects.create(amount=Decimal("700.00"), effective_from=self.today)
        self.shift = Shift.objects.create(name="Gate Day", start_time="07:00", end_time="19:00", is_overnight=False)
        for employee in (self.pilot, self.other):
            EmployeeRosterDay.objects.create(employee=employee, date=self.today, status=RosterDayStatus.WORK, shift=self.shift)

    def scan(self, employee, event):
        MealService.ingest(system=IDENTITY_SYSTEM, source_identifier="MEALGATE1", device_serial_number="MEALGATE1", external_user_id=str({self.pilot: 7, self.other: 8}[employee]), external_event_id=event, timestamp=timezone.now())

    def commands(self):
        return [(c.payload["enrollid"], c.payload["enabled"]) for c in DeviceCommand.objects.filter(command_type="set_user_enabled").order_by("id")]

    def confirm_all(self):
        from meals.gating import record_state

        for command in DeviceCommand.objects.filter(command_type="set_user_enabled", status="pending"):
            record_state(command)
            DeviceCommand.objects.filter(pk=command.pk).update(status="acked")

    def test_only_the_listed_person_is_ever_switched_and_the_first_pass_records_their_state(self):
        self.assertEqual(self.gating.reconcile(), 0)  # the terminal starts everyone enabled: nothing to send
        self.assertEqual(self.commands(), [])
        self.assertTrue(MealTerminalUserState.objects.get(employee=self.pilot).enabled)
        self.assertFalse(MealTerminalUserState.objects.filter(employee=self.other).exists())  # 8 (not listed) is untouched

    def test_switched_off_after_the_last_ticket_and_back_on_when_one_is_voided(self):
        self.gating.reconcile(); self.confirm_all()
        self.scan(self.pilot, "g1")
        self.assertEqual(self.gating.reconcile(), 0)  # one of two collected: still on
        self.scan(self.pilot, "g2")
        self.gating.reconcile()
        self.assertEqual(self.commands()[-1], (7, False))
        self.confirm_all()
        collection = MealCollection.objects.filter(employee=self.pilot).order_by("-id").first()
        MealService.void_collection(collection, get_user_model().objects.create_user("v", password="p"), "test")
        self.gating.reconcile()
        self.assertEqual(self.commands()[-1], (7, True))

    def test_off_on_a_rest_day_and_with_no_allocation(self):
        EmployeeRosterDay.objects.filter(employee=self.pilot).update(status=RosterDayStatus.REST, shift=None)
        self.assertEqual(self.gating.tickets_left_today(self.pilot), 0)
        self.gating.reconcile()
        self.assertEqual(self.commands(), [(7, False)])

    def test_it_does_not_queue_the_same_switch_twice(self):
        EmployeeRosterDay.objects.filter(employee=self.pilot).update(status=RosterDayStatus.REST, shift=None)
        self.gating.reconcile()
        self.gating.reconcile()
        self.assertEqual(self.commands(), [(7, False)])

    def test_release_switches_back_on_everyone_that_was_switched_off(self):
        self.gating.reconcile(); self.confirm_all()
        self.scan(self.pilot, "g1"); self.scan(self.pilot, "g2")
        self.gating.reconcile(); self.confirm_all()
        self.assertFalse(MealTerminalUserState.objects.get(employee=self.pilot).enabled)
        from django.test import override_settings
        with override_settings(MEAL_GATING_EMPLOYEE_IDS=[]):
            self.assertEqual(self.gating.reconcile(), 0)  # setting cleared: nothing new is decided
            self.assertEqual(self.gating.reconcile(release=True), 1)
        self.assertEqual(self.commands()[-1], (7, True))

    def test_a_used_authorisation_does_not_keep_adding_an_allowance(self):
        from meals.authorizations import authorise

        boss = get_user_model().objects.create_user("gate-boss", password="p")
        authorise(employee=self.pilot, quantity=1, pays="company", actor=boss)
        self.assertEqual(self.gating.tickets_left_today(self.pilot), 3)  # 2 entitled + 1 authorised
        self.scan(self.pilot, "x1"); self.scan(self.pilot, "x2"); self.scan(self.pilot, "x3")  # the third uses the authorisation
        self.assertEqual(self.gating.tickets_left_today(self.pilot), 0)
        for collection in MealCollection.objects.filter(employee=self.pilot):
            MealService.void_collection(collection, boss, "test")
        self.assertEqual(self.gating.tickets_left_today(self.pilot), 2)  # back to the plain entitlement, not 3

    def test_a_star_means_everyone_enrolled_on_the_meal_terminal_and_a_scan_only_rechecks_that_person(self):
        from django.test import override_settings

        third = Employee.objects.create(employee_id="NOTERM1", first_name="No", last_name="Terminal")  # not enrolled: never managed
        with override_settings(MEAL_GATING_EMPLOYEE_IDS=["*"]):
            self.assertEqual({e.employee_id for e in self.gating.gated_employees()}, {"PILOT1", "OTHER1"})
            self.assertFalse(self.gating.gated_employees().filter(pk=third.pk).exists())
            EmployeeRosterDay.objects.filter(employee__in=[self.pilot, self.other]).update(status=RosterDayStatus.REST, shift=None)
            self.assertEqual(self.gating.reconcile(employees=[self.pilot.pk]), 1)  # only the person asked about
            self.assertEqual(self.commands(), [(7, False)])
            self.assertEqual(self.gating.reconcile(), 1)  # a full pass then picks up the other one (the first is already queued)
            self.assertEqual(self.commands(), [(7, False), (8, False)])

    def test_voiding_a_ticket_switches_the_person_back_on_straight_away(self):
        self.gating.reconcile()
        self.scan(self.pilot, "v1"); self.scan(self.pilot, "v2")
        self.gating.reconcile(); self.confirm_all()
        self.assertFalse(MealTerminalUserState.objects.get(employee=self.pilot).enabled)
        MealService.void_collection(MealCollection.objects.filter(employee=self.pilot).order_by("-id").first(), get_user_model().objects.create_user("v2u", password="p"), "test")
        self.assertEqual(self.commands()[-1], (7, True))  # no waiting for the next full check

    def test_the_wire_message(self):
        """The legacy enableuser command (enflag) was replaced 2026-09-23: confirmed on production it
        only blocks face verification, not card or fingerprint scans for the same enrollid. The newer
        setuserinfo profile command's `enable` field is documented as governing the user as a whole."""
        from attendance.integrations.aiface_protocol import build_device_command
        self.assertEqual(build_device_command("SN1", "set_user_enabled", {"enrollid": 7, "enabled": False}), {"cmd": "setuserinfo", "sn": "SN1", "enrollid": 7, "enable": 0})
        self.assertEqual(build_device_command("SN1", "set_user_enabled", {"enrollid": 7, "enabled": True})["enable"], 1)


class ExtraTicketAuthorizationTests(TestCase):
    """A supervisor authorises an extra ticket and says who pays; it is decided the moment it is scanned, and the
    person is switched on at the terminal for it."""

    def setUp(self):
        from django.test import override_settings

        self.override = override_settings(MEAL_GATING_EMPLOYEE_IDS=["AUTH1"])
        self.override.enable()
        self.addCleanup(self.override.disable)
        self.today = timezone.localdate()
        self.boss = get_user_model().objects.create_user("meal-boss", password="pw")
        self.boss.user_permissions.add(Permission.objects.get(codename="review_meal_excess"), Permission.objects.get(codename="record_meal_operations"))
        self.person = Employee.objects.create(employee_id="AUTH1", first_name="Auth", last_name="One", status="active")
        BiometricDevice.objects.create(name="Canteen", serial_number="MEALAUTH1", purpose="meal_ticket")
        MealDevice.objects.create(name="Canteen", serial_number="MEALAUTH1", active=True)
        BiometricIdentity.objects.create(employee=self.person, system=IDENTITY_SYSTEM, source_identifier="MEALAUTH1", external_user_id="9")
        EmployeeMealEntitlement.objects.create(employee=self.person, tickets_per_work_day=1, effective_from=self.today, reason="test")
        MealTicketRate.objects.create(amount=Decimal("700.00"), effective_from=self.today)
        shift = Shift.objects.create(name="Auth Day", start_time="07:00", end_time="19:00", is_overnight=False)
        EmployeeRosterDay.objects.create(employee=self.person, date=self.today, status=RosterDayStatus.WORK, shift=shift)
        self.period = PayrollPeriod.objects.create(year=self.today.year, month=self.today.month)
        self.client = APIClient()
        self.client.force_authenticate(self.boss)

    def scan(self, event):
        collection, _ = MealService.ingest(system=IDENTITY_SYSTEM, source_identifier="MEALAUTH1", device_serial_number="MEALAUTH1", external_user_id="9", external_event_id=event, timestamp=timezone.now())
        collection.refresh_from_db()
        return collection

    def authorise(self, pays="employee", quantity=1, reason="Double shift"):
        return self.client.post("/api/meals/extra-authorizations/", {"employee": self.person.pk, "quantity": quantity, "pays": pays, "reason": reason}, format="json")

    def test_an_authorised_extra_ticket_is_decided_the_moment_it_is_scanned_and_the_employee_pays(self):
        self.scan("a1")  # the one they are entitled to
        self.assertEqual(self.authorise().status_code, 201)
        extra = self.scan("a2")
        self.assertEqual(extra.status, "excess")
        self.assertIn(extra.excess_exception.status, ("approved", "deducted"))
        self.assertEqual(MealExtraAuthorization.objects.get().used, 1)

    def test_when_the_company_pays_the_extra_is_waived_but_the_ticket_stands(self):
        self.scan("a1")
        self.authorise(pays="company")
        extra = self.scan("a2")
        self.assertEqual(extra.excess_exception.status, "cancelled")
        self.assertIsNone(extra.voided_at)  # still counts towards what the vendor is owed

    def test_only_the_authorised_number_is_covered_the_next_one_waits_for_a_person(self):
        self.scan("a1")
        self.authorise(quantity=1)
        self.scan("a2")
        third = self.scan("a3")
        self.assertEqual(third.excess_exception.status, "pending")

    def test_without_authorisation_an_extra_ticket_stays_pending(self):
        self.scan("a1")
        self.assertEqual(self.scan("a2").excess_exception.status, "pending")

    def test_authorising_switches_the_person_on_at_the_terminal_and_the_extra_is_counted(self):
        from meals import gating

        self.scan("a1")
        self.assertEqual(gating.tickets_left_today(self.person), 0)
        gating.reconcile()  # what the bridge does after a scan: switches them off...
        for command in DeviceCommand.objects.filter(command_type="set_user_enabled", status="pending"):
            gating.record_state(command)  # ...and the terminal confirms
            DeviceCommand.objects.filter(pk=command.pk).update(status="acked")
        self.authorise()
        self.assertEqual(gating.tickets_left_today(self.person), 1)
        self.assertEqual(DeviceCommand.objects.filter(command_type="set_user_enabled").order_by("-id").first().payload["enabled"], True)
        self.scan("a2")
        self.assertEqual(gating.tickets_left_today(self.person), 0)

    def test_it_works_on_a_rest_day_and_can_be_withdrawn_before_use(self):
        from meals import gating

        EmployeeRosterDay.objects.filter(employee=self.person).update(status=RosterDayStatus.REST, shift=None)
        self.assertEqual(gating.tickets_left_today(self.person), 0)
        authorization_id = self.authorise().json()["id"]
        self.assertEqual(gating.tickets_left_today(self.person), 1)
        self.assertEqual(self.client.post(f"/api/meals/extra-authorizations/{authorization_id}/cancel/").status_code, 200)
        self.assertEqual(gating.tickets_left_today(self.person), 0)
        self.assertEqual(self.client.post(f"/api/meals/extra-authorizations/{authorization_id}/cancel/").status_code, 400)

    def test_permissions_and_validation(self):
        self.assertEqual(self.authorise(pays="nobody").status_code, 400)
        self.assertEqual(self.authorise(quantity=99).status_code, 400)
        viewer = get_user_model().objects.create_user("meal-viewer", password="pw")
        viewer.user_permissions.add(Permission.objects.get(codename="record_meal_operations"))
        client = APIClient()
        client.force_authenticate(viewer)
        self.assertEqual(client.get("/api/meals/extra-authorizations/").status_code, 200)
        self.assertEqual(client.post("/api/meals/extra-authorizations/", {"employee": self.person.pk, "quantity": 1, "pays": "employee"}, format="json").status_code, 403)


class MealAdminListTests(TestCase):
    """The admin lists show who each row is about, not 'MealCollection object (771)'."""

    def test_collection_change_list_shows_the_employee(self):
        from datetime import date

        from django.contrib.auth import get_user_model
        from django.utils import timezone

        from employees.models import Employee
        from meals.models import MealCollection, MealDevice, MealEvent

        admin_user = get_user_model().objects.create_superuser("admin-x", "a@b.c", "pw")
        employee = Employee.objects.create(employee_id="009999", first_name="Ada", last_name="Okafor", status="active")
        device = MealDevice.objects.create(name="Canteen", serial_number="M-ADM", active=True)
        event = MealEvent.objects.create(employee=employee, device=device, timestamp=timezone.now(), external_event_id="adm-1", source_system="t")
        MealCollection.objects.create(event=event, employee=employee, work_date=date.today(), sequence_number=1, entitlement_snapshot=2, rate_snapshot="500", status="within_entitlement")
        self.client.force_login(admin_user)
        response = self.client.get("/admin/meals/mealcollection/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "009999")
        self.assertContains(response, "Ada Okafor")
        self.assertContains(response, "1 of 2")
        self.assertContains(response, "Canteen")

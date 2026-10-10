from datetime import date, datetime, timedelta
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

    def test_no_roster_at_all_is_unscheduled_but_still_reviewable(self):
        """No EmployeeRosterDay row is labelled unscheduled (not a declared rest day), but
        still opens a pending decision so it can never go unseen."""
        work_date = date(2026, 9, 7)
        device_serial_number = "MEALDEVICE004B"
        admin = get_user_model().objects.create_superuser(
            username="meal-unscheduled-admin",
            email="meal-unscheduled@example.com",
            password="password",
        )

        EmployeeMealEntitlement.objects.create(
            employee=self.employee,
            tickets_per_work_day=1,
            effective_from=work_date,
            reason="Unscheduled collection test",
        )
        MealTicketRate.objects.create(
            amount=Decimal("700.00"),
            effective_from=work_date,
        )
        MealDevice.objects.create(
            name="Meal Unscheduled Device",
            serial_number=device_serial_number,
            active=True,
        )
        BiometricIdentity.objects.create(
            employee=self.employee,
            system="device",
            source_identifier=device_serial_number,
            external_user_id="BIOUSER004B",
            is_active=True,
        )

        collection, created = MealService.ingest(
            system="device",
            source_identifier=device_serial_number,
            device_serial_number=device_serial_number,
            external_user_id="BIOUSER004B",
            external_event_id="EVENT004B",
            timestamp=timezone.make_aware(datetime(2026, 9, 7, 10, 0)),
        )

        self.assertTrue(created)
        self.assertEqual(collection.entitlement_snapshot, 0)
        self.assertEqual(collection.status, MealCollectionStatus.UNSCHEDULED)
        decision = MealExcessException.objects.get(employee=self.employee, work_date=work_date)
        self.assertEqual(collection.excess_exception, decision)
        self.assertEqual(decision.status, MealExcessStatus.PENDING)

        notification = Notification.objects.get(event_type="meals.unscheduled_collection", recipient=admin)
        self.assertEqual(notification.employee, self.employee)

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

    def send(self, records):
        from django.test import override_settings
        with override_settings(BIOMETRIC_BRIDGE_SECRET="s3cret", SECURE_SSL_REDIRECT=False):
            return APIClient().post("/api/meals/integrations/vendor-gateway/punches/", {"records": records}, format="json", HTTP_X_BIOMETRIC_BRIDGE_KEY="s3cret").json()

    def test_a_verification_the_terminal_refused_is_not_a_ticket(self):
        """2026-09-25: Meal Ticket 2 logs a switched-off person's refused scan as event 104; it was counted as a
        ticket (2 of 1) and queued a 700 deduction although nothing was issued."""
        from meals.models import MealCollection

        first = {"gateway_record_id": 1, "device_serial_number": "MEAL001", "enroll_id": "10", "timestamp": "2026-09-07 12:00:00", "event": 0}
        refused = {"gateway_record_id": 2, "device_serial_number": "MEAL001", "enroll_id": "10", "timestamp": "2026-09-07 13:00:00", "event": 104}
        data = self.send([first, refused])
        self.assertEqual(data["created"], 1)
        self.assertEqual(data["refused_by_terminal"], 1)
        self.assertEqual({r["status"] for r in data["results"]}, {"created", "refused_by_terminal"})
        self.assertEqual(MealCollection.objects.filter(employee=self.employee).count(), 1)
        self.assertFalse(MealExcessException.objects.filter(employee=self.employee).exists())

    def test_a_genuine_scan_without_an_event_field_or_with_event_zero_still_counts(self):
        data = self.send([{"gateway_record_id": 5, "device_serial_number": "MEAL001", "enroll_id": "10", "timestamp": "2026-09-07 12:00:00"}])
        self.assertEqual((data["created"], data["refused_by_terminal"]), (1, 0))


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
        """The profile switches; each is accompanied by a legacy `enableuser` twin (see test_every_switch_has_a_legacy_twin)."""
        return [(c.payload["enrollid"], c.payload["enabled"]) for c in DeviceCommand.objects.filter(command_type="set_user_enabled").exclude(payload__has_key="legacy").order_by("id")]

    def test_one_profile_switch_per_person_and_direction_and_no_legacy_twin(self):
        self.scan(self.pilot, "one-1"); self.scan(self.pilot, "one-2")
        self.gating.refresh(self.pilot)
        self.gating.reconcile()  # asking again never queues a second command
        commands = DeviceCommand.objects.filter(command_type="set_user_enabled")
        self.assertEqual([(c.payload["enrollid"], c.payload["enabled"], "legacy" in c.payload) for c in commands], [(7, False, False)])

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


class MealGatingExitTests(TestCase):
    """gated_employees() only ever manages active employees, so the moment someone exits they drop out of
    every future reconcile() and whatever enable state the terminal last had for them is never touched
    again. disable_everywhere() is the explicit fix: force every meal-terminal identity off regardless of
    gating scope or the identity's own active flag - wired to fire the moment an employee's status becomes
    inactive (found 2026-09-30: real production terminals had dozens of exited staff still cached/enrolled
    as enabled, weeks after they left)."""

    def setUp(self):
        from meals import gating

        self.gating = gating
        self.today = timezone.localdate()
        self.leaver = Employee.objects.create(employee_id="LEAVER1", first_name="Leaver", last_name="One", status="active")
        self.device_a = BiometricDevice.objects.create(name="Canteen A", serial_number="EXITGATE1", purpose="meal_ticket")
        self.device_b = BiometricDevice.objects.create(name="Canteen B", serial_number="EXITGATE2", purpose="meal_ticket")
        self.identity_a = BiometricIdentity.objects.create(employee=self.leaver, system=IDENTITY_SYSTEM, source_identifier="EXITGATE1", external_user_id="41")
        # Already revoked on this one device before disable_everywhere ever runs - it must still be switched off.
        self.identity_b = BiometricIdentity.objects.create(employee=self.leaver, system=IDENTITY_SYSTEM, source_identifier="EXITGATE2", external_user_id="42", is_active=False)

    def test_disable_everywhere_switches_off_every_identity_active_or_not(self):
        queued = self.gating.disable_everywhere(self.leaver)
        self.assertEqual(queued, 2)
        commands = {c.payload["enrollid"]: c.payload["enabled"] for c in DeviceCommand.objects.filter(command_type="set_user_enabled")}
        self.assertEqual(commands, {41: False, 42: False})

    def test_an_exited_employee_drops_out_of_gating_scope(self):
        self.leaver.status = "inactive"
        self.leaver.save()
        self.assertFalse(self.gating.gated_employees().filter(pk=self.leaver.pk).exists())

    def test_marking_someone_inactive_through_the_serializer_switches_them_off(self):
        from employees.serializers import EmployeeCreateUpdateSerializer

        serializer = EmployeeCreateUpdateSerializer(self.leaver, data={"status": "inactive"}, partial=True, context={"request": None})
        serializer.is_valid(raise_exception=True)
        serializer.save()
        commands = {c.payload["enrollid"]: c.payload["enabled"] for c in DeviceCommand.objects.filter(command_type="set_user_enabled")}
        self.assertEqual(commands, {41: False, 42: False})

    def test_saving_an_already_inactive_employee_again_does_not_resend(self):
        self.leaver.status = "inactive"
        self.leaver.save()
        from employees.serializers import EmployeeCreateUpdateSerializer

        serializer = EmployeeCreateUpdateSerializer(self.leaver, data={"first_name": "Leaver"}, partial=True, context={"request": None})
        serializer.is_valid(raise_exception=True)
        serializer.save()
        self.assertFalse(DeviceCommand.objects.filter(command_type="set_user_enabled").exists())

    def test_reactivating_someone_does_not_trigger_a_disable(self):
        self.leaver.status = "inactive"
        self.leaver.save(update_fields=["status"])
        from employees.serializers import EmployeeCreateUpdateSerializer

        serializer = EmployeeCreateUpdateSerializer(self.leaver, data={"status": "active"}, partial=True, context={"request": None})
        serializer.is_valid(raise_exception=True)
        serializer.save()
        self.assertFalse(DeviceCommand.objects.filter(command_type="set_user_enabled").exists())

    def test_management_command_remediates_the_existing_backlog(self):
        """The fix above only stops NEW exits from leaking - this is the one-off (and repeatable) cleanup
        for people who already went inactive before it existed."""
        from django.core.management import call_command

        self.leaver.status = "inactive"
        self.leaver.save(update_fields=["status"])  # bypasses the serializer, like the pre-fix backlog did
        self.assertFalse(DeviceCommand.objects.filter(command_type="set_user_enabled").exists())

        call_command("sync_meal_gating", "--exited")

        commands = {c.payload["enrollid"]: c.payload["enabled"] for c in DeviceCommand.objects.filter(command_type="set_user_enabled")}
        self.assertEqual(commands, {41: False, 42: False})


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
        self.assertNotContains(response, "column-id")  # the row's database id is not shown: the staff number identifies the person


class ResendSwitchesTests(TestCase):
    def test_everyone_recorded_as_off_gets_one_legacy_switch_off_and_repeating_adds_none(self):
        from io import StringIO

        from django.core.management import call_command

        from attendance.models import BiometricDevice, DeviceCommand
        from meals.models import MealTerminalUserState

        device = BiometricDevice.objects.create(name="Canteen 2", serial_number="MEALRS1", purpose="meal_ticket")
        off = Employee.objects.create(employee_id="RS-1", first_name="Off", last_name="Person")
        on = Employee.objects.create(employee_id="RS-2", first_name="On", last_name="Person")
        for number, person, enabled in ((5, off, False), (6, on, True)):
            BiometricIdentity.objects.create(employee=person, system=IDENTITY_SYSTEM, source_identifier="MEALRS1", external_user_id=str(number))
            MealTerminalUserState.objects.create(employee=person, device_serial="MEALRS1", enabled=enabled)
        call_command("resend_switches", "--device", "Canteen 2", stdout=StringIO())
        self.assertEqual(DeviceCommand.objects.count(), 0)  # dry run
        call_command("resend_switches", "--device", "Canteen 2", "--apply", stdout=StringIO())
        call_command("resend_switches", "--device", "Canteen 2", "--apply", stdout=StringIO())
        (command,) = DeviceCommand.objects.all()
        self.assertEqual((command.payload["enrollid"], command.payload["enabled"], command.payload["legacy"]), (5, False, True))


class OperationsFilterTests(TestCase):
    """The Meals page filters collections and decisions by date, person, terminal and status, and pages the list."""

    def setUp(self):
        from django.contrib.auth import get_user_model
        from django.contrib.auth.models import Permission
        from django.utils import timezone

        from meals.models import MealCollection, MealEvent, MealExcessException

        self.viewer = get_user_model().objects.create_user(username="ops-viewer", password="x")
        self.viewer.user_permissions.add(Permission.objects.get(codename="record_meal_operations"))
        self.client = APIClient()
        self.client.force_authenticate(self.viewer)
        MealTicketRate.objects.create(amount=Decimal("700.00"), effective_from=date(2026, 1, 1))
        self.d1, self.d2 = MealDevice.objects.create(name="Canteen 1", serial_number="OP1", active=True), MealDevice.objects.create(name="Canteen 2", serial_number="OP2", active=True)
        self.ada = Employee.objects.create(employee_id="000101", first_name="Ada", last_name="Okafor")
        self.bob = Employee.objects.create(employee_id="000102", first_name="Bob", middle_name="Chidi", last_name="Eze")
        self.rows = {}
        for key, employee, device, when, status in (
            ("a1", self.ada, self.d1, date(2026, 9, 24), "within_entitlement"),
            ("a2", self.ada, self.d1, date(2026, 9, 25), "excess"),
            ("b1", self.bob, self.d2, date(2026, 9, 25), "within_entitlement"),
        ):
            stamp = timezone.make_aware(datetime(when.year, when.month, when.day, 12, 0))
            event = MealEvent.objects.create(employee=employee, device=device, timestamp=stamp, external_event_id=key, source_system="t")
            self.rows[key] = MealCollection.objects.create(event=event, employee=employee, work_date=when, sequence_number=1, entitlement_snapshot=1, rate_snapshot="700.00", status=status)
        MealExcessException.objects.create(employee=self.ada, work_date=date(2026, 9, 25), entitlement_snapshot=1, collected_quantity=2, excess_quantity=1, rate_snapshot="700.00", proposed_deduction="700.00")
        MealExcessException.objects.create(employee=self.bob, work_date=date(2026, 9, 20), entitlement_snapshot=1, collected_quantity=2, excess_quantity=1, rate_snapshot="700.00", proposed_deduction="700.00", status="declined")

    def get(self, query=""):
        return self.client.get(f"/api/meals/operations/{query}").json()

    def test_the_default_response_is_unchanged_and_now_carries_staff_numbers(self):
        data = self.get()
        self.assertEqual(len(data["collections"]), 3)
        self.assertEqual({row["employee_number"] for row in data["collections"]}, {"000101", "000102"})
        self.assertEqual([e["status"] for e in data["exceptions"]], ["pending"])

    def test_date_range_search_terminal_and_status_filters(self):
        self.assertEqual({r["id"] for r in self.get("?date_from=2026-09-25&date_to=2026-09-25")["collections"]}, {self.rows["a2"].pk, self.rows["b1"].pk})
        self.assertEqual({r["id"] for r in self.get("?search=chidi")["collections"]}, {self.rows["b1"].pk})
        self.assertEqual({r["id"] for r in self.get("?search=000101")["collections"]}, {self.rows["a1"].pk, self.rows["a2"].pk})
        self.assertEqual({r["id"] for r in self.get("?device=Canteen 2")["collections"]}, {self.rows["b1"].pk})
        self.assertEqual({r["id"] for r in self.get("?status=excess")["collections"]}, {self.rows["a2"].pk})

    def test_decisions_can_be_pending_decided_or_all_and_filtered_by_date(self):
        self.assertEqual(len(self.get("?decisions=all")["exceptions"]), 2)
        self.assertEqual([e["status"] for e in self.get("?decisions=decided")["exceptions"]], ["declined"])
        self.assertEqual(len(self.get("?decisions=all&date_from=2026-09-24")["exceptions"]), 1)

    def test_pages_and_range_totals(self):
        data = self.get("?page=2&page_size=2")
        self.assertEqual((data["collections_total"], len(data["collections"]), data["page"]), (3, 1, 2))
        self.assertEqual(self.get("?date_from=2026-09-25&date_to=2026-09-25")["range_summary"], {"collections": 2, "within_entitlement": 1, "excess": 1, "voided": 0})


class MealExceptionsPaginationTests(TestCase):
    """The review tab's backlog can run into the thousands - every pending case must be reachable by
    paging, not just whichever ones happen to be newest (2026-09-30: a hard 500-row cap left ~1700 of a
    2222-case backlog unreachable through the page)."""

    def setUp(self):
        self.viewer = get_user_model().objects.create_user(username="pg-viewer", password="x")
        self.viewer.user_permissions.add(Permission.objects.get(codename="record_meal_operations"))
        self.client = APIClient()
        self.client.force_authenticate(self.viewer)
        self.people = [Employee.objects.create(employee_id=f"PG{i:03d}", first_name="Pg", last_name=str(i)) for i in range(7)]
        self.ids_newest_first = []
        for i, employee in enumerate(self.people):
            exc = MealExcessException.objects.create(
                employee=employee, work_date=date(2026, 9, 1 + i), entitlement_snapshot=1,
                collected_quantity=2, excess_quantity=1, rate_snapshot="700.00", proposed_deduction="700.00",
            )
            self.ids_newest_first.append(exc.pk)
        self.ids_newest_first.reverse()  # ordering is -work_date, -created_at: latest work_date first

    def get(self, query=""):
        return self.client.get(f"/api/meals/operations/{query}").json()

    def test_default_page_is_the_first_page_not_everything(self):
        data = self.get("?exceptions_page_size=3")
        self.assertEqual(data["exceptions_total"], 7)
        self.assertEqual([e["id"] for e in data["exceptions"]], self.ids_newest_first[:3])
        self.assertEqual((data["exceptions_page"], data["exceptions_page_size"]), (1, 3))

    def test_every_case_is_reachable_by_paging_through(self):
        seen = []
        for page in (1, 2, 3):
            seen += [e["id"] for e in self.get(f"?exceptions_page={page}&exceptions_page_size=3")["exceptions"]]
        self.assertEqual(seen, self.ids_newest_first)

    def test_page_size_is_capped(self):
        data = self.get("?exceptions_page_size=9999")
        self.assertEqual(data["exceptions_page_size"], 200)


class MealCardVerifiedBatchingTests(TestCase):
    """card_verified used to run one query per exception row - confirms the batched version still gets
    the right answer per person instead of mixing rows up."""

    def setUp(self):
        self.viewer = get_user_model().objects.create_user(username="cv-viewer", password="x")
        self.viewer.user_permissions.add(Permission.objects.get(codename="record_meal_operations"))
        self.client = APIClient()
        self.client.force_authenticate(self.viewer)
        MealTicketRate.objects.create(amount=Decimal("700.00"), effective_from=date(2026, 1, 1))
        device = MealDevice.objects.create(name="Canteen", serial_number="CV1", active=True)
        self.card_person = Employee.objects.create(employee_id="CV001", first_name="Card", last_name="Scanner")
        self.face_person = Employee.objects.create(employee_id="CV002", first_name="Face", last_name="Scanner")
        work_date = date(2026, 9, 10)
        for employee, key, mode in ((self.card_person, "cv-c", 3), (self.face_person, "cv-f", 1)):
            event = MealEvent.objects.create(
                employee=employee, device=device, timestamp=timezone.make_aware(datetime(2026, 9, 10, 12, 0)),
                external_event_id=key, source_system="device", raw_payload={"mode": mode},
            )
            MealCollection.objects.create(event=event, employee=employee, work_date=work_date, sequence_number=1, entitlement_snapshot=0, rate_snapshot="700.00", status="excess")
            MealExcessException.objects.create(employee=employee, work_date=work_date, entitlement_snapshot=0, collected_quantity=1, excess_quantity=1, rate_snapshot="700.00", proposed_deduction="700.00")

    def test_each_row_gets_its_own_answer_not_the_other_persons(self):
        rows = {row["employee_number"]: row["card_verified"] for row in self.client.get("/api/meals/operations/").json()["exceptions"]}
        self.assertEqual(rows, {"CV001": True, "CV002": False})


class MealExcessBulkDecisionTests(TestCase):
    """Accept/waive/decline several pending cases in one request - what the review tab's bulk toolbar
    now calls instead of one HTTP round trip per case."""

    def setUp(self):
        self.reviewer = get_user_model().objects.create_user(username="bulk-reviewer", password="pw")
        self.reviewer.user_permissions.add(Permission.objects.get(codename="review_meal_excess"), Permission.objects.get(codename="record_meal_operations"))
        self.client = APIClient()
        self.client.force_authenticate(self.reviewer)
        self.people = [Employee.objects.create(employee_id=f"BK{i:03d}", first_name="Bulk", last_name=str(i)) for i in range(4)]
        self.exceptions = [
            MealExcessException.objects.create(
                employee=employee, work_date=date(2026, 9, 10), entitlement_snapshot=1,
                collected_quantity=2, excess_quantity=1, rate_snapshot="700.00", proposed_deduction="700.00",
            )
            for employee in self.people
        ]
        self.ids = [e.pk for e in self.exceptions]

    def post(self, body):
        return self.client.post("/api/meals/excess/bulk-decision/", body, format="json")

    def test_bulk_waive_decides_every_id_and_sends_one_notification_not_one_per_ticket(self):
        superuser = get_user_model().objects.create_superuser(username="bulk-admin", password="pw", email="a@example.com")
        response = self.post({"action": "waive", "ids": self.ids, "reason": "Bulk cleanup"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["succeeded"], self.ids)
        self.assertEqual(response.json()["failed"], [])
        for exception in self.exceptions:
            exception.refresh_from_db()
            self.assertEqual(exception.status, "cancelled")
            self.assertEqual(exception.comment, "Bulk cleanup")
        self.assertEqual(Notification.objects.filter(recipient=superuser, event_type="meals.excess_cancelled").count(), 1)

    def test_bulk_accept_charges_every_ticket(self):
        response = self.post({"action": "accept", "ids": self.ids})
        self.assertEqual(response.json()["succeeded"], self.ids)
        for exception in self.exceptions:
            exception.refresh_from_db()
            self.assertEqual(exception.status, "approved")

    def test_bulk_decline_reports_already_decided_ids_as_failed_without_stopping_the_rest(self):
        MealService.decline(self.exceptions[0], self.reviewer, "already handled")
        response = self.post({"action": "decline", "ids": self.ids})
        data = response.json()
        self.assertEqual(data["succeeded"], self.ids[1:])
        self.assertEqual([f["id"] for f in data["failed"]], [self.ids[0]])
        self.assertIn("already been decided", data["failed"][0]["error"])

    def test_unknown_ids_are_reported_failed_not_a_500(self):
        response = self.post({"action": "waive", "ids": [999999, *self.ids]})
        self.assertEqual(response.json()["failed"], [{"id": 999999, "error": "Not found."}])
        self.assertEqual(response.json()["succeeded"], self.ids)

    def test_more_than_the_cap_is_rejected_with_no_partial_effect(self):
        response = self.post({"action": "waive", "ids": list(range(1, 302))})
        self.assertEqual(response.status_code, 400)
        for exception in self.exceptions:
            exception.refresh_from_db()
            self.assertEqual(exception.status, "pending")

    def test_bad_action_or_empty_ids_is_rejected(self):
        self.assertEqual(self.post({"action": "nonsense", "ids": self.ids}).status_code, 400)
        self.assertEqual(self.post({"action": "accept", "ids": []}).status_code, 400)

    def test_requires_the_review_permission(self):
        viewer = get_user_model().objects.create_user(username="bulk-viewer", password="pw")
        viewer.user_permissions.add(Permission.objects.get(codename="record_meal_operations"))
        self.client.force_authenticate(viewer)
        self.assertEqual(self.post({"action": "accept", "ids": self.ids}).status_code, 403)


class MealExcessPendingIdsTests(TestCase):
    """Backs the review tab's 'select all N matching this filter, not just this page' action."""

    def setUp(self):
        self.viewer = get_user_model().objects.create_user(username="ids-viewer", password="x")
        self.viewer.user_permissions.add(Permission.objects.get(codename="record_meal_operations"))
        self.client = APIClient()
        self.client.force_authenticate(self.viewer)
        self.ada = Employee.objects.create(employee_id="ID001", first_name="Ida", last_name="One")
        self.pending = MealExcessException.objects.create(employee=self.ada, work_date=date(2026, 9, 10), entitlement_snapshot=1, collected_quantity=2, excess_quantity=1, rate_snapshot="700.00", proposed_deduction="700.00")
        self.decided = MealExcessException.objects.create(employee=self.ada, work_date=date(2026, 9, 5), entitlement_snapshot=1, collected_quantity=2, excess_quantity=1, rate_snapshot="700.00", proposed_deduction="700.00", status="declined")

    def test_defaults_to_pending_only(self):
        data = self.client.get("/api/meals/excess/pending-ids/").json()
        self.assertEqual(data, {"ids": [self.pending.pk], "truncated": False})

    def test_decisions_all_includes_decided_too(self):
        data = self.client.get("/api/meals/excess/pending-ids/?decisions=all").json()
        self.assertEqual(set(data["ids"]), {self.pending.pk, self.decided.pk})

    def test_date_and_search_filters_apply(self):
        data = self.client.get("/api/meals/excess/pending-ids/?decisions=all&date_from=2026-09-06").json()
        self.assertEqual(data["ids"], [self.pending.pk])
        self.assertEqual(self.client.get("/api/meals/excess/pending-ids/?search=nobody").json()["ids"], [])


class MealTerminalOfflineAlertTests(TestCase):
    def setUp(self):
        from meals.management.commands.check_meal_terminals import check

        self.check = check
        self.admin = get_user_model().objects.create_superuser(username="terminal-alert-admin", email="terminal-alert@example.com", password="password")
        self.noon = timezone.make_aware(datetime(2026, 10, 1, 12, 0))
        self.device = BiometricDevice.objects.create(name="Alert Meal Terminal", serial_number="ALERT001", location="Canteen", device_type="face", purpose="meal_ticket")

    def alerts(self, event_type):
        return Notification.objects.filter(event_type=event_type, recipient=self.admin)

    def test_a_terminal_silent_for_over_three_minutes_alerts_once(self):
        BiometricDevice.objects.filter(pk=self.device.pk).update(is_online=False, last_sync_at=self.noon - timedelta(minutes=10))

        self.assertEqual(self.check(self.noon), ["offline: Alert Meal Terminal"])
        self.assertEqual(self.check(self.noon + timedelta(minutes=1)), [])
        self.assertEqual(self.alerts("meals.terminal_offline").count(), 1)

    def test_a_brief_reconnect_gap_does_not_alert(self):
        BiometricDevice.objects.filter(pk=self.device.pk).update(is_online=False, last_sync_at=self.noon - timedelta(minutes=1))

        self.assertEqual(self.check(self.noon), [])

    def test_a_connected_terminal_does_not_alert(self):
        BiometricDevice.objects.filter(pk=self.device.pk).update(is_online=True, last_sync_at=self.noon - timedelta(hours=2))

        self.assertEqual(self.check(self.noon), [])

    def test_no_alert_outside_working_hours(self):
        BiometricDevice.objects.filter(pk=self.device.pk).update(is_online=False, last_sync_at=self.noon - timedelta(hours=9))

        for hour in (2, 9, 21, 23):
            self.assertEqual(self.check(timezone.make_aware(datetime(2026, 10, 2, hour, 30))), [], hour)
        self.assertEqual(self.check(timezone.make_aware(datetime(2026, 10, 2, 10, 0))), ["offline: Alert Meal Terminal"])

    def test_coming_back_resolves_the_alert_and_says_so(self):
        BiometricDevice.objects.filter(pk=self.device.pk).update(is_online=False, last_sync_at=self.noon - timedelta(minutes=10))
        self.check(self.noon)
        BiometricDevice.objects.filter(pk=self.device.pk).update(is_online=True, last_sync_at=self.noon + timedelta(minutes=20))

        self.assertEqual(self.check(self.noon + timedelta(minutes=20)), ["back online: Alert Meal Terminal"])
        self.assertEqual(self.alerts("meals.terminal_back_online").count(), 1)

        BiometricDevice.objects.filter(pk=self.device.pk).update(is_online=False, last_sync_at=self.noon + timedelta(minutes=30))
        self.assertEqual(self.check(self.noon + timedelta(minutes=40)), ["offline: Alert Meal Terminal"])


class LateEntitlementBalancingTests(TestCase):
    """New starters scan before their entitlement and shift are entered: those tickets are recorded as excess
    against zero. Accept must balance them against the entitlement entered afterwards instead of charging."""

    def setUp(self):
        self.day = date(2026, 10, 2)
        self.employee = Employee.objects.create(employee_id="000777", first_name="New", last_name="Starter")
        MealTicketRate.objects.create(amount=Decimal("700.00"), effective_from=date(2026, 9, 1))
        MealDevice.objects.create(name="Canteen", serial_number="MEAL777", active=True)
        BiometricIdentity.objects.create(employee=self.employee, system="device", source_identifier="MEAL777", external_user_id="777")
        self.shift = Shift.objects.create(name="Late Entitlement Day", start_time="07:00", end_time="19:00", is_overnight=False)
        self.reviewer = get_user_model().objects.create_user(username="late-reviewer", password="pw")
        self.reviewer.user_permissions.add(Permission.objects.get(codename="review_meal_excess"), Permission.objects.get(codename="record_meal_operations"))
        self.client = APIClient()
        self.client.force_authenticate(self.reviewer)

    def scan(self, event_id, hour):
        collection, _ = MealService.ingest(
            system="device", source_identifier="MEAL777", device_serial_number="MEAL777", external_user_id="777",
            external_event_id=event_id, timestamp=timezone.make_aware(datetime(2026, 10, 2, hour, 0)),
        )
        collection.refresh_from_db()
        return collection

    def put_on_shift(self):
        EmployeeRosterDay.objects.update_or_create(employee=self.employee, date=self.day, defaults={"status": RosterDayStatus.WORK, "shift": self.shift})

    def entitle(self, tickets=1, effective_from=None):
        return EmployeeMealEntitlement.objects.create(employee=self.employee, tickets_per_work_day=tickets, effective_from=effective_from or self.day, reason="entered after induction")

    def test_accept_balances_a_ticket_the_late_entitlement_covers_and_charges_the_extra(self):
        self.put_on_shift()  # on the roster but nothing entitled yet
        first, second = self.scan("a", 12), self.scan("b", 13)
        self.assertEqual((first.entitlement_snapshot, second.entitlement_snapshot), (0, 0))

        self.entitle(1, effective_from=self.day)  # entered afterwards, backdated to the day
        MealService.approve(first.excess_exception, None, self.reviewer, "")
        MealService.approve(second.excess_exception, None, self.reviewer, "")

        first.refresh_from_db(); second.refresh_from_db()
        self.assertEqual((first.status, first.excess_exception_id), (MealCollectionStatus.WITHIN, None))
        self.assertEqual(second.excess_exception.status, MealExcessStatus.APPROVED)  # the second meal is a real extra
        self.assertEqual(second.excess_exception.proposed_deduction, Decimal("700.00"))
        self.assertEqual(MealExcessException.objects.filter(employee=self.employee, status=MealExcessStatus.CANCELLED).count(), 1)

    def test_a_first_entitlement_entered_late_and_starting_after_the_scan_also_balances(self):
        self.put_on_shift()
        ticket = self.scan("a", 12)
        self.entitle(1, effective_from=self.day + timedelta(days=3))  # "from Monday", entered after the scan

        MealService.approve(ticket.excess_exception, None, self.reviewer, "")

        ticket.refresh_from_db()
        self.assertEqual(ticket.status, MealCollectionStatus.WITHIN)
        self.assertFalse(MealExcessException.objects.filter(employee=self.employee, status__in=[MealExcessStatus.APPROVED, MealExcessStatus.DEDUCTED]).exists())

    def test_shift_allocated_after_the_scan_counts_too(self):
        self.entitle(1, effective_from=self.day - timedelta(days=1))  # entitlement was there, the shift was not
        ticket = self.scan("a", 12)
        self.assertEqual(ticket.status, MealCollectionStatus.UNSCHEDULED)
        self.put_on_shift()

        MealService.approve(ticket.excess_exception, None, self.reviewer, "")

        ticket.refresh_from_db()
        self.assertEqual(ticket.status, MealCollectionStatus.WITHIN)

    def test_an_entitlement_set_up_before_the_scan_for_a_later_start_still_charges(self):
        self.put_on_shift()
        self.entitle(1, effective_from=self.day + timedelta(days=5))  # deliberately not entitled yet - entered BEFORE the scan
        ticket = self.scan("a", 12)

        MealService.approve(ticket.excess_exception, None, self.reviewer, "")

        ticket.excess_exception.refresh_from_db()
        self.assertEqual(ticket.excess_exception.status, MealExcessStatus.APPROVED)

    def test_an_entitlement_starting_far_after_the_scan_still_charges(self):
        self.put_on_shift()
        ticket = self.scan("a", 12)
        self.entitle(1, effective_from=self.day + timedelta(days=60))

        MealService.approve(ticket.excess_exception, None, self.reviewer, "")

        ticket.excess_exception.refresh_from_db()
        self.assertEqual(ticket.excess_exception.status, MealExcessStatus.APPROVED)

    def test_still_no_shift_means_the_ticket_is_charged_as_before(self):
        ticket = self.scan("a", 12)
        self.entitle(1, effective_from=self.day)

        MealService.approve(ticket.excess_exception, None, self.reviewer, "")

        ticket.excess_exception.refresh_from_db()
        self.assertEqual(ticket.excess_exception.status, MealExcessStatus.APPROVED)

    def test_the_accept_endpoints_report_what_was_balanced(self):
        self.put_on_shift()
        ticket = self.scan("a", 12)
        self.entitle(1, effective_from=self.day)

        response = self.client.post("/api/meals/excess/bulk-decision/", {"action": "accept", "ids": [ticket.excess_exception_id]}, format="json")

        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual((response.json()["succeeded"], response.json()["balanced"]), ([ticket.excess_exception_id], [ticket.excess_exception_id]))

    def test_a_ticket_whose_earlier_scan_was_declined_is_the_persons_first_and_is_balanced(self):
        """Production, 5 Oct: ticket 1 declined and voided as a new-starter scan error; ticket 2 still carries
        number 2 although it is now the only valid ticket."""
        self.put_on_shift()
        first, second = self.scan("a", 12), self.scan("b", 13)
        MealService.decline(first.excess_exception, self.reviewer, "new employee scanning error")
        self.entitle(1, effective_from=self.day)

        MealService.approve(second.excess_exception, None, self.reviewer, "")

        second.refresh_from_db()
        self.assertEqual((second.status, second.sequence_number, second.excess_exception_id), (MealCollectionStatus.WITHIN, 1, None))
        self.assertFalse(MealExcessException.objects.filter(employee=self.employee, status__in=[MealExcessStatus.APPROVED, MealExcessStatus.DEDUCTED]).exists())

    def test_two_valid_tickets_against_one_late_entitlement_balance_only_the_first(self):
        self.put_on_shift()
        first, second = self.scan("a", 12), self.scan("b", 13)
        self.entitle(1, effective_from=self.day)

        MealService.approve(second.excess_exception, None, self.reviewer, "")  # the later meal is the extra one
        MealService.approve(first.excess_exception, None, self.reviewer, "")

        first.refresh_from_db(); second.refresh_from_db()
        self.assertEqual(second.excess_exception.status, MealExcessStatus.APPROVED)
        self.assertEqual((first.status, first.excess_exception_id), (MealCollectionStatus.WITHIN, None))


    def test_the_review_list_flags_cases_that_accept_will_clear(self):
        self.put_on_shift()
        ticket = self.scan("a", 12)
        rows = self.client.get("/api/meals/operations/").json()["exceptions"]
        self.assertEqual([row["balances_on_accept"] for row in rows], [False])  # nothing entered late yet

        self.entitle(1, effective_from=self.day)
        rows = self.client.get("/api/meals/operations/").json()["exceptions"]
        self.assertEqual([row["balances_on_accept"] for row in rows], [True])


class VendorSummaryTests(TestCase):
    """The vendor view by week, month or day over any date range, with its own permissions."""

    def setUp(self):
        self.employee = Employee.objects.create(employee_id="000888", first_name="Vendor", last_name="Test")
        MealTicketRate.objects.create(amount=Decimal("700.00"), effective_from=date(2026, 9, 1))
        MealDevice.objects.create(name="Canteen", serial_number="MEAL888", active=True)
        BiometricIdentity.objects.create(employee=self.employee, system="device", source_identifier="MEAL888", external_user_id="888")
        shift = Shift.objects.create(name="Vendor Test Day", start_time="07:00", end_time="19:00", is_overnight=False)
        EmployeeMealEntitlement.objects.create(employee=self.employee, tickets_per_work_day=1, effective_from=date(2026, 9, 1), reason="test")
        for number, day in enumerate((date(2026, 9, 28), date(2026, 10, 2), date(2026, 10, 5))):  # a Monday, the Friday, the next Monday
            EmployeeRosterDay.objects.create(employee=self.employee, date=day, status=RosterDayStatus.WORK, shift=shift)
            MealService.ingest(system="device", source_identifier="MEAL888", device_serial_number="MEAL888", external_user_id="888", external_event_id=f"v{number}", timestamp=timezone.make_aware(datetime(day.year, day.month, day.day, 12, 0)))
        self.viewer = self.user("vendor-viewer", "view_meal_vendor_payments")
        self.client = APIClient()
        self.client.force_authenticate(self.viewer)

    def user(self, name, *codenames):
        user = get_user_model().objects.create_user(username=name, password="pw")
        for codename in codenames:
            user.user_permissions.add(Permission.objects.get(codename=codename))
        return user

    def summary(self, **params):
        query = "&".join(f"{key}={value}" for key, value in params.items())
        return self.client.get(f"/api/meals/vendor/summary/?{query}")

    def test_weeks_run_monday_to_sunday_and_each_shows_tickets_and_amount(self):
        data = self.summary(date_from="2026-09-28", date_to="2026-10-11", group_by="week").json()
        weeks = {bucket["key"]: bucket for bucket in data["buckets"]}
        self.assertEqual(set(weeks), {"2026-09-28", "2026-10-05"})
        self.assertEqual((weeks["2026-09-28"]["tickets"], float(weeks["2026-09-28"]["owed"])), (2, 1400.0))
        self.assertEqual((weeks["2026-10-05"]["tickets"], float(weeks["2026-10-05"]["owed"])), (1, 700.0))
        self.assertEqual((data["totals"]["tickets"], float(data["totals"]["owed"])), (3, 2100.0))

    def test_months_and_days_group_the_same_tickets(self):
        months = {b["key"]: b["tickets"] for b in self.summary(date_from="2026-09-01", date_to="2026-10-31", group_by="month").json()["buckets"]}
        self.assertEqual(months, {"2026-09-01": 1, "2026-10-01": 2})
        days = self.summary(date_from="2026-09-28", date_to="2026-10-05", group_by="day").json()["buckets"]
        self.assertEqual((len(days), sum(day["tickets"] for day in days)), (8, 3))

    def test_a_range_cutting_a_week_only_counts_the_days_in_it(self):
        data = self.summary(date_from="2026-10-01", date_to="2026-10-02", group_by="week").json()
        self.assertEqual([(b["tickets"], b["start"], b["end"]) for b in data["buckets"]], [(1, "2026-10-01", "2026-10-02")])

    def test_a_voided_ticket_is_not_owed(self):
        ticket = MealCollection.objects.get(work_date=date(2026, 10, 2))
        MealService.void_collection(ticket, self.viewer, "test scan")
        self.assertEqual(self.summary(date_from="2026-09-28", date_to="2026-10-11").json()["totals"]["tickets"], 2)

    def test_a_payment_settles_the_week_it_covers_whenever_it_was_paid(self):
        recorder = self.user("vendor-recorder", "record_meal_vendor_payments")
        self.client.force_authenticate(recorder)
        response = self.client.post("/api/meals/vendor/payments/", {"amount": "1400.00", "payment_date": "2026-10-08", "covers_from": "2026-09-28", "covers_to": "2026-10-04", "reference": "TRF-1"}, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        self.assertIsNone(response.json()["payroll_period"])  # no payroll period exists for October: the date is enough

        weeks = {b["key"]: b for b in self.summary(date_from="2026-09-28", date_to="2026-10-11").json()["buckets"]}
        self.assertEqual((float(weeks["2026-09-28"]["paid"]), float(weeks["2026-09-28"]["balance"])), (1400.0, 0.0))
        self.assertEqual((float(weeks["2026-10-05"]["paid"]), float(weeks["2026-10-05"]["balance"])), (0.0, 700.0))

    def test_a_payment_without_cover_dates_counts_on_its_payment_date(self):
        self.client.force_authenticate(self.user("vendor-recorder2", "record_meal_vendor_payments"))
        self.client.post("/api/meals/vendor/payments/", {"amount": "700.00", "payment_date": "2026-10-06"}, format="json")
        weeks = {b["key"]: b for b in self.summary(date_from="2026-09-28", date_to="2026-10-11").json()["buckets"]}
        self.assertEqual(float(weeks["2026-10-05"]["paid"]), 700.0)

    def test_viewing_needs_only_the_vendor_permission_and_recording_needs_its_own(self):
        self.assertEqual(self.summary().status_code, 200)
        denied = self.client.post("/api/meals/vendor/payments/", {"amount": "100.00", "payment_date": "2026-10-06"}, format="json")
        self.assertEqual(denied.status_code, 403)
        self.client.force_authenticate(self.user("nobody"))
        self.assertEqual(self.summary().status_code, 403)
        self.client.force_authenticate(self.user("old-reviewer", "review_meal_excess"))  # anyone who could see it before still can
        self.assertEqual(self.summary().status_code, 200)

    def test_a_bad_range_is_refused(self):
        self.assertEqual(self.summary(date_from="2026-10-10", date_to="2026-10-01").status_code, 400)
        self.assertEqual(self.summary(group_by="decade").status_code, 400)


class VendorClaimTests(VendorSummaryTests):
    """Issued vs claimed: staff scan to be issued a ticket, but the vendor is paid for the tickets it claimed."""

    def claim(self, date_from, date_to, quantity, user=None):
        self.client.force_authenticate(user or self.user(f"claimer-{date_from}-{quantity}", "record_meal_vendor_payments"))
        return self.client.post("/api/meals/vendor/claims/", {"date_from": date_from, "date_to": date_to, "quantity": quantity, "reference": "INV-1"}, format="json")

    def week(self, key="2026-09-28", **params):
        params = {"date_from": "2026-09-28", "date_to": "2026-10-11", **params}
        return {b["key"]: b for b in self.summary(**params).json()["buckets"]}[key]

    def test_a_weekly_claim_below_the_issued_count_is_what_is_owed(self):
        self.assertEqual(self.claim("2026-09-28", "2026-10-04", 1).status_code, 201)  # 2 issued that week, 1 claimed
        bucket = self.week()
        self.assertEqual((bucket["tickets"], bucket["claimed"], bucket["unclaimed"], bucket["awaiting_claim"], float(bucket["owed"]), bucket["claim_status"]), (2, 1, 1, 0, 700.0, "claimed"))
        data = self.summary(date_from="2026-09-28", date_to="2026-10-11").json()
        self.assertEqual((data["totals"]["tickets"], data["totals"]["claimed"], data["totals"]["unclaimed"], float(data["totals"]["owed"])), (3, 1, 1, 1400.0))  # the next week is owed as issued

    def test_days_with_no_claim_yet_are_owed_as_issued_and_flagged(self):
        bucket = self.week("2026-10-05")
        self.assertEqual((bucket["tickets"], bucket["claimed"], bucket["awaiting_claim"], float(bucket["owed"]), bucket["claim_status"]), (1, 0, 1, 700.0, "none"))

    def test_a_claim_covering_part_of_a_week_makes_it_partial(self):
        self.claim("2026-09-28", "2026-09-30", 1)  # covers the Monday only (1 issued)
        bucket = self.week()
        self.assertEqual((bucket["claimed"], bucket["unclaimed"], bucket["awaiting_claim"], bucket["claim_status"]), (1, 0, 1, "partial"))

    def test_a_weekly_claim_shows_correctly_by_month_and_by_day(self):
        self.claim("2026-09-28", "2026-10-04", 1)
        months = {b["key"]: b for b in self.summary(date_from="2026-09-01", date_to="2026-10-31", group_by="month").json()["buckets"]}
        self.assertEqual(months["2026-09-01"]["claimed"] + months["2026-10-01"]["claimed"], 1)
        days = self.summary(date_from="2026-09-28", date_to="2026-10-05", group_by="day").json()["buckets"]
        self.assertEqual(sum(day["claimed"] for day in days), 1)

    def test_a_claim_cannot_make_the_vendor_paid_for_more_than_was_issued(self):
        response = self.claim("2026-09-28", "2026-10-04", 5)  # vendor claims 5 of the 2 issued
        self.assertEqual(response.status_code, 201)
        bucket = self.week()
        self.assertEqual((bucket["claimed"], float(bucket["owed"])), (2, 1400.0))
        claim = self.summary(date_from="2026-09-28", date_to="2026-10-11").json()["claims"][0]
        self.assertEqual((claim["quantity"], claim["issued"], claim["payable"], claim["over_claimed"]), (5, 2, 2, 3))

    def test_overlapping_future_and_unauthorised_claims_are_refused(self):
        self.claim("2026-09-28", "2026-10-04", 2)
        self.assertEqual(self.claim("2026-10-04", "2026-10-06", 1).status_code, 400)  # overlaps the first
        self.assertEqual(self.claim("2026-10-20", "2026-10-21", 1).status_code, 400)  # not happened yet
        self.assertEqual(self.claim("2026-10-05", "2026-10-04", 1).status_code, 400)  # backwards
        self.client.force_authenticate(self.viewer)  # may look, may not enter
        self.assertEqual(self.client.post("/api/meals/vendor/claims/", {"date_from": "2026-10-05", "date_to": "2026-10-05", "quantity": 1}, format="json").status_code, 403)

    def test_a_wrong_claim_can_be_deleted_and_the_days_are_owed_as_issued_again(self):
        claim_id = self.claim("2026-09-28", "2026-10-04", 1).json()["id"]
        self.assertEqual(self.client.delete(f"/api/meals/vendor/claims/{claim_id}/").status_code, 204)
        bucket = self.week()
        self.assertEqual((bucket["claimed"], bucket["awaiting_claim"], float(bucket["owed"])), (0, 2, 1400.0))

    def test_the_allocation_adds_up_exactly(self):
        from meals.models import MealVendorClaim
        from meals.services import allocate_vendor_claim

        claim = MealVendorClaim.objects.create(date_from=date(2026, 9, 28), date_to=date(2026, 10, 5), quantity=2)  # 3 issued over 3 days, 2 claimed
        allocation = allocate_vendor_claim(claim)
        self.assertEqual((allocation["issued"], allocation["claimed"]), (3, 2))
        self.assertEqual(sum(day["claimed"] for day in allocation["days"].values()), 2)


class MultipleTicketsReportTests(TestCase):
    """Who took EXTRA tickets (more than their entitlement) on a day, and what became of them."""

    def setUp(self):
        MealTicketRate.objects.create(amount=Decimal("700.00"), effective_from=date(2026, 9, 1))
        MealDevice.objects.create(name="Canteen", serial_number="MEAL900", active=True)
        self.shift = Shift.objects.create(name="Multi Day", start_time="07:00", end_time="19:00", is_overnight=False)
        self.day = date(2026, 10, 2)
        self.people = {}
        for number, entitlement, scans in (("000901", 2, 2), ("000902", 1, 2), ("000903", 1, 1), ("000904", 1, 3)):
            employee = Employee.objects.create(employee_id=number, first_name="P", last_name=number)
            BiometricIdentity.objects.create(employee=employee, system="device", source_identifier="MEAL900", external_user_id=number[-3:])
            EmployeeMealEntitlement.objects.create(employee=employee, tickets_per_work_day=entitlement, effective_from=date(2026, 9, 1), reason="t")
            EmployeeRosterDay.objects.create(employee=employee, date=self.day, status=RosterDayStatus.WORK, shift=self.shift)
            self.people[number] = employee
            for scan in range(scans):
                MealService.ingest(system="device", source_identifier="MEAL900", device_serial_number="MEAL900", external_user_id=number[-3:], external_event_id=f"{number}-{scan}", timestamp=timezone.make_aware(datetime(2026, 10, 2, 11 + scan, 0)))
        self.user = get_user_model().objects.create_user(username="multi-viewer", password="pw")
        self.user.user_permissions.add(Permission.objects.get(codename="view_meal_vendor_payments"))
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def report(self, **params):
        query = "&".join(f"{k}={v}" for k, v in {"date_from": "2026-10-01", "date_to": "2026-10-03", **params}.items())
        return self.client.get(f"/api/meals/vendor/multiple-tickets/?{query}").json()

    def test_counts_only_people_who_took_more_than_their_entitlement(self):
        data = self.report()
        # 000901 is entitled to two and took two; 000903 took one: neither took an extra. 000902 took 1 extra, 000904 took 2.
        self.assertEqual(data["totals"], {"people": 2, "person_days": 2, "extra_tickets": 3})
        self.assertEqual(data["days"], [{"date": "2026-10-02", "people": 2, "extra_tickets": 3}])
        self.assertEqual(sorted(row["employee_number"] for row in data["rows"]), ["000902", "000904"])

    def test_each_row_says_what_became_of_the_extra_tickets(self):
        rows = {row["employee_number"]: row for row in self.report()["rows"]}
        self.assertEqual((rows["000902"]["tickets"], rows["000902"]["entitled"], rows["000902"]["extra"], rows["000902"]["waiting"]), (2, 1, 1, 1))
        self.assertEqual((rows["000904"]["tickets"], rows["000904"]["entitled"], rows["000904"]["extra"], rows["000904"]["waiting"]), (3, 1, 2, 2))

    def test_two_or_more_extra_and_voided_tickets(self):
        self.assertEqual([row["employee_number"] for row in self.report(min=2)["rows"]], ["000904"])
        ticket = MealCollection.objects.filter(employee=self.people["000902"], work_date=self.day).order_by("-sequence_number").first()
        MealService.void_collection(ticket, self.user, "test")
        self.assertEqual(self.report()["totals"]["people"], 1)  # the extra was voided

    def test_a_ticket_with_no_entitlement_behind_it_counts_as_extra(self):
        newcomer = Employee.objects.create(employee_id="000905", first_name="New", last_name="Starter")
        BiometricIdentity.objects.create(employee=newcomer, system="device", source_identifier="MEAL900", external_user_id="905")
        EmployeeRosterDay.objects.create(employee=newcomer, date=self.day, status=RosterDayStatus.WORK, shift=self.shift)  # works, but no entitlement entered yet
        MealService.ingest(system="device", source_identifier="MEAL900", device_serial_number="MEAL900", external_user_id="905", external_event_id="905-0", timestamp=timezone.make_aware(datetime(2026, 10, 2, 12, 0)))
        rows = {row["employee_number"]: row for row in self.report()["rows"]}
        self.assertEqual((rows["000905"]["tickets"], rows["000905"]["entitled"], rows["000905"]["extra"]), (1, 0, 1))

    def test_needs_the_vendor_view_permission(self):
        self.client.force_authenticate(get_user_model().objects.create_user(username="nobody-multi", password="pw"))
        self.assertEqual(self.client.get("/api/meals/vendor/multiple-tickets/").status_code, 403)


class MealChargebackTests(TestCase):
    """Charging an employee for tickets already taken - the offence penalty for a meal they have had."""

    def setUp(self):
        self.day = date(2026, 9, 7)
        self.employee = Employee.objects.create(employee_id="000950", first_name="Charge", last_name="Back", basic_salary=Decimal("60000.00"))
        MealTicketRate.objects.create(amount=Decimal("700.00"), effective_from=date(2026, 9, 1))
        MealDevice.objects.create(name="Canteen", serial_number="MEAL950", active=True)
        BiometricIdentity.objects.create(employee=self.employee, system="device", source_identifier="MEAL950", external_user_id="950")
        shift = Shift.objects.create(name="Chargeback Day", start_time="07:00", end_time="19:00", is_overnight=False)
        EmployeeMealEntitlement.objects.create(employee=self.employee, tickets_per_work_day=1, effective_from=date(2026, 9, 1), reason="t")
        EmployeeRosterDay.objects.create(employee=self.employee, date=self.day, status=RosterDayStatus.WORK, shift=shift)
        MealService.ingest(system="device", source_identifier="MEAL950", device_serial_number="MEAL950", external_user_id="950", external_event_id="c1", timestamp=timezone.make_aware(datetime(2026, 9, 7, 12, 0)))
        self.admin = get_user_model().objects.create_user(username="charger", password="pw")
        self.admin.user_permissions.add(Permission.objects.get(codename="charge_back_meal_tickets"))
        self.client = APIClient()
        self.client.force_authenticate(self.admin)

    def post(self, **extra):
        body = {"employee_number": "000950", "work_date": "2026-09-07", "quantity": 1, "reason": "Entered the factory without safety boots", **extra}
        return self.client.post("/api/meals/chargebacks/", body, format="json")

    def test_a_ticket_already_taken_within_entitlement_can_be_charged_back(self):
        response = self.post()
        self.assertEqual(response.status_code, 201, response.content)
        data = response.json()
        self.assertEqual((float(data["amount"]), data["status"], data["quantity"]), (700.0, "pending", 1))  # no payroll yet: it waits

    def test_it_lands_in_that_months_payroll_when_it_is_generated_and_not_before(self):
        period = PayrollPeriod.objects.create(year=2026, month=9)
        self.post()
        self.assertFalse(PayrollLineItem.objects.exists())
        generate_payroll_for_period(period)
        payroll = EmployeePayroll.objects.get(payroll_period=period, employee=self.employee)
        line = PayrollLineItem.objects.get(payroll=payroll, code="MEAL_CHARGEBACK")
        self.assertEqual(line.amount, Decimal("700.00"))
        from meals.models import MealChargeback

        self.assertEqual(MealChargeback.objects.get().status, "deducted")

    def test_with_the_payroll_already_there_it_is_deducted_at_once(self):
        period = PayrollPeriod.objects.create(year=2026, month=9)
        generate_payroll_for_period(period)
        data = self.post().json()
        self.assertEqual(data["status"], "deducted")
        payroll = EmployeePayroll.objects.get(payroll_period=period, employee=self.employee)
        self.assertEqual(payroll.total_deductions, Decimal("700.00"))

    def test_the_same_ticket_cannot_be_charged_back_twice_or_beyond_what_was_taken(self):
        self.assertEqual(self.post().status_code, 201)
        second = self.post()
        self.assertEqual(second.status_code, 400)
        self.assertIn("can still be charged back", second.json()["detail"])
        self.assertEqual(self.post(quantity=5, work_date="2026-09-08").status_code, 400)  # took nothing that day

    def test_a_ticket_already_charged_as_an_excess_cannot_be_charged_back_again(self):
        MealService.ingest(system="device", source_identifier="MEAL950", device_serial_number="MEAL950", external_user_id="950", external_event_id="c2", timestamp=timezone.make_aware(datetime(2026, 9, 7, 13, 0)))
        extra = MealCollection.objects.get(employee=self.employee, sequence_number=2)
        MealService.approve(extra.excess_exception, None, self.admin)
        info = self.client.get("/api/meals/chargebacks/lookup/?employee=000950&date=2026-09-07").json()
        self.assertEqual((info["taken"], info["already_charged"], info["chargeable"]), (2, 1, 1))

    def test_a_reason_is_required(self):
        self.assertEqual(self.post(reason="  ").status_code, 400)

    def test_a_closed_month_refuses_and_a_later_month_can_be_chosen(self):
        period = PayrollPeriod.objects.create(year=2026, month=9)
        generate_payroll_for_period(period)
        PayrollPeriod.objects.filter(pk=period.pk).update(status="approved")
        self.assertEqual(self.post().status_code, 400)
        later = self.post(pay_year=2026, pay_month=10)
        self.assertEqual((later.status_code, later.json()["pay_month"]), (201, 10))

    def test_cancelling_takes_the_deduction_back_out_of_an_open_payroll(self):
        period = PayrollPeriod.objects.create(year=2026, month=9)
        generate_payroll_for_period(period)
        chargeback_id = self.post().json()["id"]
        response = self.client.post(f"/api/meals/chargebacks/{chargeback_id}/cancel/", {"reason": "Offence withdrawn"}, format="json")
        self.assertEqual((response.status_code, response.json()["status"]), (200, "cancelled"))
        payroll = EmployeePayroll.objects.get(payroll_period=period, employee=self.employee)
        self.assertEqual(payroll.total_deductions, Decimal("0.00"))
        self.assertFalse(PayrollLineItem.objects.filter(payroll=payroll, code="MEAL_CHARGEBACK").exists())
        self.assertEqual(self.post().status_code, 201)  # the ticket is free to be charged again

    def test_permissions(self):
        outsider = get_user_model().objects.create_user(username="no-charge", password="pw")
        self.client.force_authenticate(outsider)
        self.assertEqual(self.post().status_code, 403)
        self.assertEqual(self.client.get("/api/meals/chargebacks/").status_code, 403)
        reviewer = get_user_model().objects.create_user(username="reviewer-charge", password="pw")
        reviewer.user_permissions.add(Permission.objects.get(codename="review_meal_excess"))  # reviewers may charge too
        self.client.force_authenticate(reviewer)
        self.assertEqual(self.post().status_code, 201)

    def test_the_list_shows_what_was_charged(self):
        self.post()
        rows = self.client.get("/api/meals/chargebacks/?search=000950").json()["results"]
        self.assertEqual([(r["employee_number"], r["quantity"], r["status"]) for r in rows], [("000950", 1, "pending")])


class MealShiftReportTests(TestCase):
    """The morning-after report for a night's meals: who was rostered, who clocked in, what was issued, what needs a look."""

    DAY = date(2026, 10, 4)  # a Sunday

    def setUp(self):
        MealTicketRate.objects.create(amount=Decimal("700.00"), effective_from=date(2026, 9, 1))
        self.device = MealDevice.objects.create(name="Canteen", serial_number="MEALRPT", active=True)
        self.night = Shift.objects.create(name="Report Night", start_time="19:00", end_time="07:00", is_overnight=True)
        self.day_shift = Shift.objects.create(name="Report Day", start_time="07:00", end_time="19:00")
        self.people = {}
        for key, number in (("collected", "000601"), ("skipped", "000602"), ("absent", "000603"), ("no_clock", "000604"), ("extra", "000605"), ("none", "000606"), ("withheld", "000607"), ("waiting", "000608")):
            self.people[key] = self.add_person(number, key)
        self.entitle("collected", 2); self.entitle("skipped", 2); self.entitle("absent", 2); self.entitle("no_clock", 2); self.entitle("extra", 1); self.entitle("withheld", 2); self.entitle("waiting", 2)
        for key in ("collected", "skipped"):
            self.attend(key, "present")
        self.attend("extra", "present"); self.attend("absent", "absent"); self.attend("withheld", "present")
        MealAbsencePenalty.objects.create(employee=self.people["withheld"], penalty_type="three_monthly", source_absence_dates=["2026-10-01"], tickets_to_reduce_per_work_day=2,
                                          work_days_to_apply=1, target_work_dates=[self.DAY.isoformat()], status="active")
        self.ticket("collected", 19, 5, 1); self.ticket("collected", 19, 6, 2)
        self.ticket("no_clock", 19, 20, 1)  # ate, never clocked in
        self.ticket("extra", 19, 10, 1); self.ticket("extra", 19, 12, 2)
        self.user = get_user_model().objects.create_user(username="shift-report-viewer", password="pw")
        self.user.user_permissions.add(Permission.objects.get(codename="record_meal_operations"))
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def add_person(self, number, key, shift=None):
        employee = Employee.objects.create(employee_id=number, first_name=key.title(), last_name="Night", status="active")
        EmployeeRosterDay.objects.create(employee=employee, date=self.DAY, status=RosterDayStatus.WORK, shift=shift or self.night)
        return employee

    def entitle(self, key, tickets):
        EmployeeMealEntitlement.objects.create(employee=self.people[key], tickets_per_work_day=tickets, effective_from=date(2026, 9, 1), reason="t")

    def attend(self, key, status):
        from datetime import datetime as dt

        start = timezone.make_aware(dt(2026, 10, 4, 19, 0))
        DailyAttendance.objects.create(employee=self.people[key], date=self.DAY, shift=self.night, status=status, actual_clock_in=start if status != "absent" else None)

    def ticket(self, key, hour, minute, number, employee=None):
        from datetime import datetime as dt

        employee = employee or self.people[key]
        event = MealEvent.objects.create(employee=employee, device=self.device, timestamp=timezone.make_aware(dt(2026, 10, 4, hour, minute)), external_event_id=f"r-{employee.employee_id}-{number}", source_system="device")
        MealCollection.objects.create(event=event, employee=employee, work_date=self.DAY, sequence_number=number, entitlement_snapshot=1, rate_snapshot=Decimal("700.00"), status="within_entitlement")

    def report(self, **params):
        query = "&".join(f"{k}={v}" for k, v in {"date": self.DAY.isoformat(), "scope": "night", **params}.items())
        return self.client.get(f"/api/meals/shift-report/?{query}").json()

    def results(self, data):
        return {row["employee_number"]: row["result"] for row in data["rows"]}

    def test_each_person_gets_the_right_result(self):
        self.assertEqual(self.results(self.report()), {
            "000601": "collected", "000602": "did_not_collect", "000603": "absent", "000604": "no_attendance",
            "000605": "extra", "000606": "no_entitlement", "000607": "withheld", "000608": "no_clock_in_yet",
        })

    def test_the_totals_add_up(self):
        totals = self.report()["totals"]
        self.assertEqual((totals["rostered"], totals["clocked_in"], totals["collected"], totals["did_not_collect"]), (8, 4, 3, 1))
        self.assertEqual(totals["tickets_issued"], 5)  # 2 + 1 + 2
        self.assertEqual(totals["needs_a_look"], 3)  # the extra, the ticket without a clock-in, the missing entitlement
        self.assertEqual(float(totals["value_at_rate"]), 3500.0)

    def test_rows_show_when_the_tickets_were_issued(self):
        row = next(r for r in self.report()["rows"] if r["employee_number"] == "000601")
        self.assertEqual((row["tickets"], row["ticket_times"], row["entitled"], row["clock_in"]), (2, ["19:05", "19:06"], 2, "19:00"))

    def test_tickets_for_people_not_on_the_night_shift_are_listed_separately(self):
        stranger = Employee.objects.create(employee_id="000699", first_name="Not", last_name="Rostered", status="active")
        self.ticket("x", 19, 30, 1, employee=stranger)
        data = self.report()
        self.assertEqual([(o["employee_number"], o["tickets"]) for o in data["others"]], [("000699", 1)])
        self.assertEqual(data["totals"]["tickets_issued"], 6)
        self.assertEqual(data["totals"]["tickets_for_others"], 1)

    def test_a_day_workers_lunch_ticket_is_not_part_of_the_night_report(self):
        day_worker = self.add_person("000700", "daily", shift=self.day_shift)
        self.ticket("x", 12, 0, 1, employee=day_worker)
        data = self.report()
        self.assertNotIn("000700", self.results(data))
        self.assertEqual(data["others"], [])
        self.assertIn("000700", self.results(self.report(scope="all")))  # but it is there when asked for every shift

    def test_a_voided_ticket_is_not_counted(self):
        MealCollection.objects.filter(employee=self.people["collected"], sequence_number=2).update(voided_at=timezone.now())
        row = next(r for r in self.report()["rows"] if r["employee_number"] == "000601")
        self.assertEqual(row["tickets"], 1)

    def test_it_needs_a_meal_permission(self):
        self.client.force_authenticate(get_user_model().objects.create_user(username="no-meals-here", password="pw"))
        self.assertEqual(self.client.get("/api/meals/shift-report/").status_code, 403)


class SundayNightMealsOpenAfterClockInTests(TestCase):
    """Unsupervised night meals: on the configured weekdays an overnight shift's meals open only after a clock-in."""

    def setUp(self):
        from django.test import override_settings
        from meals import gating

        self.gating = gating
        self.today = timezone.localdate()
        self.override = override_settings(MEAL_GATING_EMPLOYEE_IDS=["*"], MEAL_REQUIRE_CLOCK_IN_NIGHT_WEEKDAYS=[self.today.weekday()])
        self.override.enable()
        self.addCleanup(self.override.disable)
        self.night = Shift.objects.create(name="Clock-in Night", start_time="19:00", end_time="07:00", is_overnight=True)
        self.day_shift = Shift.objects.create(name="Clock-in Day", start_time="07:00", end_time="19:00")
        self.worker = self.person("CI-1", self.night)
        self.evening = timezone.make_aware(datetime.combine(self.today, datetime.min.time().replace(hour=20)))  # 20:00 on the night

    def person(self, number, shift):
        employee = Employee.objects.create(employee_id=number, first_name="Night", last_name=number, status="active")
        EmployeeMealEntitlement.objects.create(employee=employee, tickets_per_work_day=2, effective_from=self.today - timedelta(days=30), reason="t")
        EmployeeRosterDay.objects.create(employee=employee, date=self.today, status=RosterDayStatus.WORK, shift=shift)
        return employee

    def punch(self, employee, hour, minute):
        from attendance.models import AttendanceEvent

        AttendanceEvent.objects.create(employee=employee, timestamp=timezone.make_aware(datetime.combine(self.today, datetime.min.time().replace(hour=hour, minute=minute))), external_event_id=f"ci-{employee.employee_id}-{hour}{minute}")

    def left(self, employee):
        return self.gating.tickets_left_today(employee, self.evening)

    def test_before_clocking_in_there_are_no_meals_and_after_there_are(self):
        self.assertEqual(self.left(self.worker), 0)
        self.punch(self.worker, 19, 30)
        self.assertEqual(self.left(self.worker), 2)

    def test_an_earlier_shifts_punch_does_not_count_as_clocking_in(self):
        self.punch(self.worker, 7, 5)  # clocking out of the previous shift this morning
        self.assertEqual(self.left(self.worker), 0)

    def test_arriving_a_little_early_counts(self):
        self.punch(self.worker, 17, 0)
        self.assertEqual(self.left(self.worker), 2)

    def test_a_day_shift_is_never_held_back(self):
        worker = self.person("CI-2", self.day_shift)
        self.assertEqual(self.left(worker), 2)

    def test_other_weekdays_and_the_setting_off_are_unaffected(self):
        from django.test import override_settings

        with override_settings(MEAL_REQUIRE_CLOCK_IN_NIGHT_WEEKDAYS=[(self.today.weekday() + 1) % 7]):
            self.assertEqual(self.left(self.worker), 2)
        with override_settings(MEAL_REQUIRE_CLOCK_IN_NIGHT_WEEKDAYS=[]):
            self.assertEqual(self.left(self.worker), 2)

    def test_a_saved_punch_asks_for_their_meal_switch_to_be_rechecked(self):
        from unittest import mock

        with mock.patch.object(self.gating, "refresh") as refresh, mock.patch.object(self.gating.timezone, "now", return_value=self.evening):
            self.gating.refresh_for_clock_in(self.worker)
        refresh.assert_called_once_with(self.worker.pk)

    def test_the_recheck_is_skipped_for_day_staff_and_when_the_rule_is_off(self):
        from unittest import mock

        from django.test import override_settings

        day_worker = self.person("CI-3", self.day_shift)
        with mock.patch.object(self.gating, "refresh") as refresh, mock.patch.object(self.gating.timezone, "now", return_value=self.evening):
            self.gating.refresh_for_clock_in(day_worker)
            with override_settings(MEAL_REQUIRE_CLOCK_IN_NIGHT_WEEKDAYS=[]):
                self.gating.refresh_for_clock_in(self.worker)
        refresh.assert_not_called()


class SundayNightTerminalAlertTests(TestCase):
    def setUp(self):
        from meals.management.commands.check_meal_terminals import check

        self.check = check
        get_user_model().objects.create_superuser(username="sunday-alert-admin", email="s@example.com", password="pw")
        self.device = BiometricDevice.objects.create(name="Sunday Meal Terminal", serial_number="SUN001", location="Canteen", device_type="face", purpose="meal_ticket")

    def test_a_terminal_that_drops_out_on_sunday_night_is_noticed(self):
        sunday = timezone.make_aware(datetime(2026, 10, 11, 23, 30))  # Sunday
        BiometricDevice.objects.filter(pk=self.device.pk).update(is_online=False, last_sync_at=sunday - timedelta(minutes=20))
        self.assertEqual(self.check(sunday), ["offline: Sunday Meal Terminal"])

    def test_early_monday_morning_is_still_part_of_sunday_night(self):
        monday = timezone.make_aware(datetime(2026, 10, 12, 3, 0))  # Monday
        BiometricDevice.objects.filter(pk=self.device.pk).update(is_online=False, last_sync_at=monday - timedelta(minutes=20))
        self.assertEqual(self.check(monday), ["offline: Sunday Meal Terminal"])

    def test_other_nights_are_unchanged(self):
        tuesday = timezone.make_aware(datetime(2026, 10, 13, 23, 30))
        BiometricDevice.objects.filter(pk=self.device.pk).update(is_online=False, last_sync_at=tuesday - timedelta(minutes=20))
        self.assertEqual(self.check(tuesday), [])
        saturday = timezone.make_aware(datetime(2026, 10, 10, 23, 30))
        self.assertEqual(self.check(saturday), [])


class AbsentButCollectedTests(TestCase):
    """Someone who was absent but collected a ticket: raised the next day, then charged back or let go."""

    DAY = date(2026, 10, 7)

    def setUp(self):
        MealTicketRate.objects.create(amount=Decimal("700.00"), effective_from=date(2026, 9, 1))
        self.device = MealDevice.objects.create(name="Canteen", serial_number="MEALABS", active=True)
        self.shift = Shift.objects.create(name="Absent Day", start_time="07:00", end_time="19:00")
        self.person = Employee.objects.create(employee_id="000801", first_name="Was", last_name="Absent", status="active")
        EmployeeMealEntitlement.objects.create(employee=self.person, tickets_per_work_day=2, effective_from=date(2026, 9, 1), reason="t")
        EmployeeRosterDay.objects.create(employee=self.person, date=self.DAY, status=RosterDayStatus.WORK, shift=self.shift)
        DailyAttendance.objects.create(employee=self.person, date=self.DAY, shift=self.shift, status="absent")
        event = MealEvent.objects.create(employee=self.person, device=self.device, timestamp=timezone.make_aware(datetime(2026, 10, 7, 12, 30)), external_event_id="abs-1", source_system="device")
        MealCollection.objects.create(event=event, employee=self.person, work_date=self.DAY, sequence_number=1, entitlement_snapshot=2, rate_snapshot=Decimal("700.00"), status="within_entitlement")
        self.hr = get_user_model().objects.create_user(username="absent-ticket-hr", password="pw")
        self.hr.user_permissions.add(Permission.objects.get(codename="charge_back_meal_tickets"), Permission.objects.get(codename="record_meal_operations"))
        self.client = APIClient()
        self.client.force_authenticate(self.hr)

    def report(self):
        return self.client.get(f"/api/meals/shift-report/?date={self.DAY.isoformat()}").json()

    def row(self):
        return self.report()["rows"][0]

    def decide(self, decision, reason="Collected while absent", **extra):
        return self.client.post("/api/meals/shift-report/decide/", {"employee_number": "000801", "work_date": self.DAY.isoformat(), "decision": decision, "reason": reason, **extra}, format="json")

    def test_it_is_flagged_as_the_alarm_and_waits_for_a_decision(self):
        data = self.report()
        row = data["rows"][0]
        self.assertEqual((row["result"], row["needs_decision"], row["needs_a_look"]), ("absent_collected", True, True))
        self.assertEqual((data["totals"]["absent_collected"], data["totals"]["waiting_decision"]), (1, 1))
        self.assertEqual([(w["employee_number"], w["work_date"], w["tickets"]) for w in data["waiting"]], [("000801", "2026-10-07", 1)])

    def test_charging_it_back_records_the_charge_and_clears_the_alarm(self):
        response = self.decide("charge")
        self.assertEqual(response.status_code, 200, response.content)
        from meals.models import MealChargeback

        chargeback = MealChargeback.objects.get(employee=self.person)
        self.assertEqual((chargeback.work_date, chargeback.quantity, chargeback.amount), (self.DAY, 1, Decimal("700.00")))
        self.assertIn("absent", chargeback.reason)
        data = self.report()
        self.assertEqual((data["rows"][0]["needs_decision"], data["rows"][0]["review"]["decision"], data["totals"]["waiting_decision"]), (False, "charged", 0))
        self.assertEqual(float(data["rows"][0]["review"]["amount"]), 700.0)

    def test_letting_it_go_needs_a_reason_and_clears_the_alarm_without_a_charge(self):
        self.assertEqual(self.decide("waive", reason="  ").status_code, 400)
        self.assertEqual(self.decide("waive", reason="Was on an errand for the company").status_code, 200)
        from meals.models import MealChargeback

        self.assertFalse(MealChargeback.objects.exists())
        data = self.report()
        self.assertEqual((data["rows"][0]["review"]["decision"], data["totals"]["waiting_decision"]), ("waived", 0))

    def test_a_case_can_only_be_decided_once(self):
        self.decide("waive")
        again = self.decide("charge")
        self.assertEqual(again.status_code, 400)
        self.assertIn("already been decided", again.json()["detail"])

    def test_cancelling_the_charge_puts_it_back_on_the_alarm_list(self):
        self.decide("charge")
        from meals.models import MealChargeback
        from meals.services import cancel_chargeback

        cancel_chargeback(MealChargeback.objects.get(), self.hr, "Offence withdrawn")
        data = self.report()
        self.assertEqual((data["rows"][0]["needs_decision"], data["totals"]["waiting_decision"]), (True, 1))
        self.assertEqual(self.decide("waive", reason="Agreed to let it go").status_code, 200)  # and it can be decided again

    def test_someone_who_was_present_has_nothing_to_decide(self):
        DailyAttendance.objects.filter(employee=self.person).update(status="present", actual_clock_in=timezone.make_aware(datetime(2026, 10, 7, 7, 0)))
        self.assertEqual(self.decide("charge").status_code, 400)
        self.assertEqual(self.report()["rows"][0]["result"], "collected")

    def test_the_waiting_list_reaches_back_over_the_last_two_weeks(self):
        old = timezone.localdate() - timedelta(days=5)
        DailyAttendance.objects.create(employee=self.person, date=old, shift=self.shift, status="absent")
        event = MealEvent.objects.create(employee=self.person, device=self.device, timestamp=timezone.make_aware(datetime.combine(old, datetime.min.time().replace(hour=12))), external_event_id="abs-old", source_system="device")
        MealCollection.objects.create(event=event, employee=self.person, work_date=old, sequence_number=1, entitlement_snapshot=2, rate_snapshot=Decimal("700.00"), status="within_entitlement")
        waiting = self.client.get(f"/api/meals/shift-report/?date={self.DAY.isoformat()}").json()["waiting"]
        self.assertIn(old.isoformat(), [w["work_date"] for w in waiting])

    def test_only_people_who_may_charge_back_can_decide(self):
        viewer = get_user_model().objects.create_user(username="absent-ticket-viewer", password="pw")
        viewer.user_permissions.add(Permission.objects.get(codename="record_meal_operations"))
        self.client.force_authenticate(viewer)
        self.assertEqual(self.client.get(f"/api/meals/shift-report/?date={self.DAY.isoformat()}").status_code, 200)  # may look
        self.assertEqual(self.decide("charge").status_code, 403)  # may not decide


from datetime import date, datetime, timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.contrib.auth import get_user_model

from audit.models import AuditSeverity
from audit.services import AuditService
from attendance.models import (
    AttendanceException,
    EmployeeRosterDay,
    RosterDayStatus,
    
)
from employees.models import BiometricIdentity
from notifications.services import NotificationService
from payroll.models import (
    EmployeePayrollStatus,
    PayrollLineItem,
    PayrollLineItemType,
    PayrollPeriod,
    PayrollPeriodStatus,
)
from payroll.services import recalculate_employee_payroll

from .models import (
    EmployeeMealEntitlement,
    MealAbsencePenalty,
    MealAbsencePenaltyStatus,
    MealAbsencePenaltyType,
    MealCollection,
    MealCollectionStatus,
    MealDevice,
    MealEntitlementRule,
    MealEvent,
    MealExcessException,
    MealExcessStatus,
    MealTicketRate,
)

class MealService:
    @staticmethod
    def suggested_entitlement(employee, work_date):
        years = (work_date.year - employee.employment_date.year) if employee.employment_date else 0
        for rule in MealEntitlementRule.objects.filter(active=True):
            if rule.employment_type and rule.employment_type != employee.employment_type: continue
            if rule.employment_category and rule.employment_category != employee.employment_category: continue
            if rule.minimum_years_of_service is not None and years < rule.minimum_years_of_service: continue
            return rule.tickets_per_work_day, rule
        return 0, None

    @staticmethod
    def approved_entitlement(employee, work_date):
        entitlement = (
            EmployeeMealEntitlement.objects
            .filter(
                employee=employee,
                effective_from__lte=work_date,
            )
            .filter(
                Q(effective_to__isnull=True) |
                Q(effective_to__gte=work_date)
            )
            .first()
        )

        return entitlement.tickets_per_work_day if entitlement else 0


    @staticmethod
    def absence_penalty_reduction(employee, work_date):
        penalties = MealAbsencePenalty.objects.filter(
            employee=employee,
            status=MealAbsencePenaltyStatus.ACTIVE,
        )

        total_reduction = 0

        for penalty in penalties:
            applies = False

            if penalty.penalty_type in {
                MealAbsencePenaltyType.SINGLE_ABSENCE,
                MealAbsencePenaltyType.TWO_CONSECUTIVE,
                MealAbsencePenaltyType.THREE_MONTHLY,
            }:
                applies = work_date.isoformat() in penalty.target_work_dates

            if applies:
                total_reduction += penalty.tickets_to_reduce_per_work_day

        return total_reduction


    @staticmethod
    def create_single_absence_penalty(attendance_exception, actor=None):
        if attendance_exception.exception_type != "absence":
            raise ValueError("Only absence exceptions can create meal absence penalties.")

        if attendance_exception.status != "approved":
            raise ValueError(
                "Only approved absence exceptions can create meal absence penalties."
            )

        work_date = attendance_exception.attendance.date
        employee = attendance_exception.attendance.employee

        penalty, created = MealAbsencePenalty.objects.get_or_create(
            employee=employee,
            penalty_type=MealAbsencePenaltyType.SINGLE_ABSENCE,
            source_absence_dates=[work_date.isoformat()],
            defaults={
                "tickets_to_reduce_per_work_day": 1,
                "work_days_to_apply": 1,
                "status": MealAbsencePenaltyStatus.ACTIVE,
                "approved_by": actor,
                "approved_at": timezone.now(),
            },
        )

        return penalty, created

    @classmethod
    def sync_absence_penalties(cls, employee, reference_date, actor=None):
        candidate_absences = list(
            AttendanceException.objects
            .filter(
                attendance__employee=employee,
                attendance__date__lte=reference_date,
                exception_type="absence",
                status="approved",
            )
            .select_related("attendance")
            .order_by("attendance__date", "id")
        )

        roster_work_dates = list(
            EmployeeRosterDay.objects
            .filter(
                employee=employee,
                status=RosterDayStatus.WORK,
                date__lte=reference_date,
            )
            .order_by("date")
            .values_list("date", flat=True)
        )

        work_date_positions = {
            work_date: position
            for position, work_date in enumerate(roster_work_dates)
        }

        approved_absences = [
            absence
            for absence in candidate_absences
            if absence.attendance.date in work_date_positions
        ]

        if not approved_absences:
            return []

        month_absences = [
            absence
            for absence in approved_absences
            if (
                absence.attendance.date.year == reference_date.year
                and absence.attendance.date.month == reference_date.month
            )
        ]

        created_penalties = []
        used_absence_ids = set()

        for index in range(len(approved_absences) - 1):
            first = approved_absences[index]
            second = approved_absences[index + 1]

            first_date = first.attendance.date
            second_date = second.attendance.date

            first_position = work_date_positions.get(first_date)
            second_position = work_date_positions.get(second_date)

            if first_position is None or second_position is None:
                continue

            if second_position != first_position + 1:
                continue

            if first.id in used_absence_ids or second.id in used_absence_ids:
                continue

            source_dates = [
                first_date.isoformat(),
                second_date.isoformat(),
            ]

            # If these absences previously created unused single-day penalties,
            # cancel them so the same two absences are not counted twice.
            MealAbsencePenalty.objects.filter(
                employee=employee,
                penalty_type=MealAbsencePenaltyType.SINGLE_ABSENCE,
                source_absence_dates__in=[
                    [first_date.isoformat()],
                    [second_date.isoformat()],
                ],
                work_days_applied=0,
                status__in=[
                    MealAbsencePenaltyStatus.PENDING,
                    MealAbsencePenaltyStatus.ACTIVE,
                ],
            ).update(
                status=MealAbsencePenaltyStatus.CANCELLED,
            )

            penalty, created = MealAbsencePenalty.objects.get_or_create(
                employee=employee,
                penalty_type=MealAbsencePenaltyType.TWO_CONSECUTIVE,
                source_absence_dates=source_dates,
                defaults={
                    "tickets_to_reduce_per_work_day": 1,
                    "work_days_to_apply": 2,
                    "status": MealAbsencePenaltyStatus.ACTIVE,
                    "approved_by": actor,
                    "approved_at": timezone.now(),
                },
            )
            cls.resolve_short_absence_penalty_roster(penalty)

            used_absence_ids.add(first.id)
            used_absence_ids.add(second.id)

            if created:
                created_penalties.append(penalty)

        # Any approved absence that was not part of a consecutive pair
        # keeps the ordinary one-day penalty.
        for absence in approved_absences:
            if absence.id in used_absence_ids:
                continue

            penalty, created = cls.create_single_absence_penalty(
                absence,
                actor=actor,
            )
            cls.resolve_short_absence_penalty_roster(penalty)

            if created:
                created_penalties.append(penalty)


        # Three approved absences within the same calendar month
        # trigger one roster-week meal penalty.
        if len(month_absences) >= 3:
            trigger_absences = month_absences[:3]

            source_dates = [
                absence.attendance.date.isoformat()
                for absence in trigger_absences
            ]

            penalty, created = MealAbsencePenalty.objects.get_or_create(
                employee=employee,
                penalty_type=MealAbsencePenaltyType.THREE_MONTHLY,
                source_absence_dates=source_dates,
                defaults={
                    "tickets_to_reduce_per_work_day": 1,

                    # The exact number of affected WORK days will come
                    # from the employee's next actual roster week.
                    "work_days_to_apply": None,

                    "status": MealAbsencePenaltyStatus.ACTIVE,
                    "approved_by": actor,
                    "approved_at": timezone.now(),
                },
            )

            cls.resolve_three_monthly_penalty_roster(penalty)

            if created:
                created_penalties.append(penalty)



        return created_penalties

    @staticmethod
    def resolve_three_monthly_penalty_roster(penalty):
        if penalty.penalty_type != MealAbsencePenaltyType.THREE_MONTHLY:
            raise ValueError(
                "Only three-monthly absence penalties use the roster-week resolver."
            )

        if not penalty.source_absence_dates:
            raise ValueError(
                "The penalty has no source absence dates."
            )

        # Do not recalculate an already-resolved penalty.
        # This preserves the original target dates for audit history.
        if penalty.target_work_dates:
            return penalty

        third_absence_date = max(
            date.fromisoformat(value)
            for value in penalty.source_absence_dates
        )

        future_work_dates = list(
            EmployeeRosterDay.objects
            .filter(
                employee=penalty.employee,
                status=RosterDayStatus.WORK,
                date__gt=third_absence_date,
            )
            .order_by("date")
            .values_list("date", flat=True)
        )

        if not future_work_dates:
            return penalty

        first_work_date = future_work_dates[0]

        roster_week_end = first_work_date + timedelta(days=6)

        target_dates = [
            work_date
            for work_date in future_work_dates
            if work_date <= roster_week_end
        ]

        penalty.target_work_dates = [
            work_date.isoformat()
            for work_date in target_dates
        ]
        penalty.work_days_to_apply = len(target_dates)

        penalty.save(
            update_fields=[
                "target_work_dates",
                "work_days_to_apply",
                "updated_at",
            ]
        )

        return penalty

    @staticmethod
    def resolve_short_absence_penalty_roster(penalty):
        if penalty.penalty_type not in {
            MealAbsencePenaltyType.SINGLE_ABSENCE,
            MealAbsencePenaltyType.TWO_CONSECUTIVE,
        }:
            raise ValueError(
                "Only single and two-consecutive absence penalties "
                "use the short penalty roster resolver."
            )

        if not penalty.source_absence_dates:
            raise ValueError(
                "The penalty has no source absence dates."
            )

        if not penalty.work_days_to_apply:
            raise ValueError(
                "The penalty has no work-day quantity to apply."
            )

        last_absence_date = max(
            date.fromisoformat(value)
            for value in penalty.source_absence_dates
        )

        existing_dates = {
            date.fromisoformat(value)
            for value in penalty.target_work_dates
        }

        needed = penalty.work_days_to_apply - len(existing_dates)

        if needed <= 0:
            return penalty

        future_work_dates = (
            EmployeeRosterDay.objects
            .filter(
                employee=penalty.employee,
                status=RosterDayStatus.WORK,
                date__gt=last_absence_date,
            )
            .exclude(
                date__in=existing_dates,
            )
            .order_by("date")
            .values_list("date", flat=True)[:needed]
        )

        new_dates = list(future_work_dates)

        if not new_dates:
            return penalty

        combined_dates = sorted(
            existing_dates.union(new_dates)
        )

        penalty.target_work_dates = [
            work_date.isoformat()
            for work_date in combined_dates
        ]

        penalty.save(
            update_fields=[
                "target_work_dates",
                "updated_at",
            ]
        )

        return penalty   
    
    @staticmethod
    def rate_for(work_date):
        rate = MealTicketRate.objects.filter(active=True, effective_from__lte=work_date).filter(Q(effective_to__isnull=True) | Q(effective_to__gte=work_date)).first()
        if not rate: raise ValueError("No active meal ticket rate applies to this work date.")
        return rate

    @staticmethod
    def resolve_work_day(employee, timestamp):
        local = timezone.localtime(timestamp)
        work_date = local.date()
        roster = EmployeeRosterDay.objects.select_related("shift").filter(
            employee=employee,
            date=work_date,
        ).first()
        previous = (
            EmployeeRosterDay.objects
            .select_related("shift")
            .filter(
                employee=employee,
                date=work_date - timedelta(days=1),
                status=RosterDayStatus.WORK,
                shift__is_overnight=True,
            )
            .first()
        )
        if previous:
            shift_start = timezone.make_aware(
                datetime.combine(previous.date, previous.shift.start_time),
                local.tzinfo,
            )
            shift_end = timezone.make_aware(
                datetime.combine(
                    previous.date + timedelta(days=1),
                    previous.shift.end_time,
                ),
                local.tzinfo,
            )
            if shift_start <= local < shift_end:
                return previous.date, previous
        return work_date, roster

    @classmethod
    def ingest(cls, *, system, source_identifier, device_serial_number, external_user_id, external_event_id, timestamp, verification_type="unknown", raw_payload=None):
        if not timezone.is_aware(timestamp): raise ValueError("Meal timestamps must be timezone-aware.")
        device = MealDevice.objects.filter(serial_number=device_serial_number, active=True).first()
        if not device: raise ValueError("Unknown meal device.")
        identity = BiometricIdentity.objects.select_related("employee").filter(system=system, source_identifier=source_identifier, external_user_id=str(external_user_id), is_active=True).first()
        if not identity: raise ValueError("Unmapped biometric identity.")
        with transaction.atomic():
            event, created = MealEvent.objects.get_or_create(device=device, external_event_id=str(external_event_id), defaults={"employee": identity.employee, "timestamp": timestamp, "verification_type": verification_type, "source_system": system, "raw_payload": raw_payload or {}})
            if not created: return event.collection, False
            work_date, roster = cls.resolve_work_day(identity.employee, timestamp)
            if roster and roster.status == RosterDayStatus.WORK:
                base_entitlement = cls.approved_entitlement(
                    identity.employee,
                    work_date,
                )

                absence_reduction = cls.absence_penalty_reduction(
                    identity.employee,
                    work_date,
                )

                entitlement = max(
                    base_entitlement - absence_reduction,
                    0,
                )
            else:
                entitlement = 0
            rate = cls.rate_for(work_date)
            sequence = MealCollection.objects.select_for_update().filter(employee=identity.employee, work_date=work_date).count() + 1
            collection = MealCollection.objects.create(event=event, employee=identity.employee, work_date=work_date, shift=roster.shift if roster else None, sequence_number=sequence, entitlement_snapshot=entitlement, rate_snapshot=rate.amount, status=MealCollectionStatus.WITHIN if sequence <= entitlement else (MealCollectionStatus.REST_DAY if entitlement == 0 else MealCollectionStatus.EXCESS))
            if sequence > entitlement: cls._sync_excess(identity.employee, work_date, entitlement, sequence, rate.amount)
            return collection, True

    @staticmethod
    def _sync_excess(employee, work_date, entitlement, count, rate):
        exception, created = MealExcessException.objects.get_or_create(employee=employee, work_date=work_date, defaults={"entitlement_snapshot": entitlement, "collected_quantity": count, "excess_quantity": count-entitlement, "rate_snapshot": rate, "proposed_deduction": Decimal(count-entitlement)*rate})
        if not created and exception.status == MealExcessStatus.PENDING:
            exception.collected_quantity, exception.excess_quantity, exception.proposed_deduction = count, count-entitlement, Decimal(count-entitlement)*rate
            exception.save(update_fields=["collected_quantity", "excess_quantity", "proposed_deduction", "updated_at"])

    @staticmethod
    @transaction.atomic
    def approve(exception, period, actor, comment=""):
        if exception.status != MealExcessStatus.PENDING:
            raise ValueError(
                "This meal excess has already been decided."
            )

        if (
            period.year != exception.work_date.year
            or period.month != exception.work_date.month
        ):
            raise ValueError(
                "Meal excess must be deducted in the payroll period "
                "for its work date."
            )

        if period.status in {
            PayrollPeriodStatus.APPROVED,
            PayrollPeriodStatus.PAID,
            PayrollPeriodStatus.CLOSED,
        }:
            raise ValueError(
                "This payroll period cannot accept meal deductions."
            )

        payroll = (
            period.employee_payrolls
            .filter(employee=exception.employee)
            .first()
        )
        if payroll is None:
            raise ValueError(
                "Generate this employee's payroll record before approving the meal excess."
            )

        if payroll.status in {
            EmployeePayrollStatus.APPROVED,
            EmployeePayrollStatus.PAID,
        }:
            raise ValueError(
                "This employee payroll record cannot accept meal deductions."
            )

        exception.status = MealExcessStatus.APPROVED
        exception.reviewer = actor
        exception.reviewed_at = timezone.now()
        exception.comment = comment.strip()
        exception.payroll_period = period
        exception.save()

        line, _ = PayrollLineItem.objects.get_or_create(
            payroll=payroll,
            source_type="meal_excess",
            source_reference=str(exception.pk),
            is_system_generated=True,
            defaults={
                "item_type": PayrollLineItemType.DEDUCTION,
                "code": "MEAL_EXCESS",
                "description": (
                    f"Excess meal tickets for "
                    f"{exception.work_date}"
                ),
                "amount": exception.proposed_deduction,
                "metadata": {
                    "meal_excess_id": exception.pk,
                    "work_date": (
                        exception.work_date.isoformat()
                    ),
                    "entitlement": (
                        exception.entitlement_snapshot
                    ),
                    "excess_quantity": (
                        exception.excess_quantity
                    ),
                    "rate": str(exception.rate_snapshot),
                },
            },
        )

        recalculate_employee_payroll(payroll)

        exception.payroll = payroll
        exception.payroll_line_item = line
        exception.status = MealExcessStatus.DEDUCTED
        exception.save()

        AuditService.log(
            event_type="meals.excess_approved",
            module="meals",
            employee=exception.employee,
            actor=actor,
            object=exception,
            severity=AuditSeverity.SUCCESS,
            title="Meal excess approved",
            description="Full meal excess deduction approved.",
            metadata={
                "exception": exception.pk,
                "amount": str(exception.proposed_deduction),
            },
        )

        return exception

    @staticmethod
    def cancel(exception, actor, comment):
        if exception.status != MealExcessStatus.PENDING: raise ValueError("This meal excess has already been decided.")
        if not comment.strip(): raise ValueError("A cancellation reason is required.")
        exception.status, exception.reviewer, exception.reviewed_at, exception.comment = MealExcessStatus.CANCELLED, actor, timezone.now(), comment.strip(); exception.save()
        AuditService.log(event_type="meals.excess_cancelled", module="meals", employee=exception.employee, actor=actor, object=exception, severity=AuditSeverity.WARNING, title="Meal excess cancelled", description="Meal excess deduction cancelled.", metadata={"exception":exception.pk,"reason":comment})
        for user in get_user_model().objects.filter(is_superuser=True): NotificationService.create(recipient=user, event_type="meals.excess_cancelled", title="Meal excess cancelled", message=f"{exception.employee.full_name}: {comment.strip()}", severity="warning", employee=exception.employee, related_url="/meals")
        return exception

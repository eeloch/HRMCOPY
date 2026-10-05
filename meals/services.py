from dataclasses import dataclass, field
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
    BiometricDevice,
    EmployeeRosterDay,
    RosterDayStatus,
    
)
from employees.models import BiometricIdentity, Employee
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

def months_of_service(employment_date, as_of):
    if not employment_date:
        return 0
    months = (as_of.year - employment_date.year) * 12 + (as_of.month - employment_date.month)
    if as_of.day < employment_date.day:
        months -= 1
    return max(months, 0)


def _add_months(start_date, months):
    month_index = start_date.month - 1 + months
    year = start_date.year + month_index // 12
    month = month_index % 12 + 1
    day = min(start_date.day, [31, 29 if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0) else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1])
    return date(year, month, day)


@dataclass(frozen=True)
class MealIngestionResult:
    status: str
    reason: str = ""
    collection_id: int | None = None


@dataclass
class MealIngestionSummary:
    created: int = 0
    duplicate: int = 0
    unmapped_employee: int = 0
    unknown_device: int = 0
    revoked_access: int = 0
    invalid: int = 0
    results: list = field(default_factory=list)

    def add(self, result):
        setattr(self, result.status, getattr(self, result.status) + 1)
        self.results.append(result)


class MealService:
    @staticmethod
    def suggested_entitlement(employee, work_date):
        months = months_of_service(employee.employment_date, work_date)
        for rule in MealEntitlementRule.objects.filter(active=True):
            if rule.employment_type and rule.employment_type != employee.employment_type: continue
            if rule.employment_category and rule.employment_category != employee.employment_category: continue
            if rule.position_id and rule.position_id != employee.position_id: continue
            if rule.minimum_months_of_service is not None and months < rule.minimum_months_of_service: continue
            return rule.tickets_per_work_day, rule
        return 0, None

    @staticmethod
    def employees_due_for_meal_review(as_of, window_days=14):
        cutoff_start = as_of - timedelta(days=window_days)
        employees = Employee.objects.filter(status="active", employment_date__isnull=False)
        due = []
        for employee in employees:
            months = months_of_service(employee.employment_date, as_of)
            if months != 6:
                continue
            anniversary = _add_months(employee.employment_date, 6)
            if cutoff_start <= anniversary <= as_of:
                due.append(employee)
        return due

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
        if not device:
            # The Biometric Devices page is the source of truth: the gateway already
            # routed this scan here *because* the terminal is registered there with
            # purpose=meal_ticket. Its MealDevice mirror can go missing (deleted by
            # hand, or deactivated by a purpose change and back) - rebuild it rather
            # than dropping every scan the terminal ever sends. A serial that isn't a
            # registered meal terminal is still rejected.
            registered = BiometricDevice.objects.filter(serial_number=device_serial_number, purpose="meal_ticket").first()
            if not registered: raise ValueError("Unknown meal device.")
            device, _ = MealDevice.objects.update_or_create(serial_number=device_serial_number, defaults={"name": registered.name, "active": True})
        identity = BiometricIdentity.objects.select_related("employee").filter(system=system, source_identifier=source_identifier, external_user_id=str(external_user_id), is_active=True).first()
        if not identity:
            revoked_identity = BiometricIdentity.objects.select_related("employee").filter(system=system, source_identifier=source_identifier, external_user_id=str(external_user_id), is_active=False).first()
            if revoked_identity:
                cls._notify_revoked_meal_access_attempt(revoked_identity, device)
                raise ValueError("Access has been revoked for this identity.")
            raise ValueError("Unmapped biometric identity.")
        with transaction.atomic():
            event, created = MealEvent.objects.get_or_create(device=device, external_event_id=str(external_event_id), defaults={"employee": identity.employee, "timestamp": timestamp, "verification_type": verification_type, "source_system": system, "raw_payload": raw_payload or {}})
            if not created: return event.collection, False
            work_date, roster = cls.resolve_work_day(identity.employee, timestamp)
            # No roster row at all (never put on a shift plan) is not the same as a roster
            # that says REST: nobody has told the system this person's schedule yet. It gets
            # its own status so the reviewer can see why, but it still opens a decision so it
            # is never invisible - the reviewer can Waive it for a genuine scheduling gap.
            unscheduled = roster is None
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
            sequence = MealCollection.objects.select_for_update().filter(employee=identity.employee, work_date=work_date, voided_at__isnull=True).count() + 1
            if unscheduled:
                status = MealCollectionStatus.UNSCHEDULED
            elif sequence <= entitlement:
                status = MealCollectionStatus.WITHIN
            elif entitlement == 0:
                status = MealCollectionStatus.REST_DAY
            else:
                status = MealCollectionStatus.EXCESS
            collection = MealCollection.objects.create(event=event, employee=identity.employee, work_date=work_date, shift=roster.shift if roster else None, sequence_number=sequence, entitlement_snapshot=entitlement, rate_snapshot=rate.amount, status=status)
            if unscheduled:
                cls._notify_unscheduled_meal_collection(identity.employee, device)
            if sequence > entitlement:
                cls._cover_excess_ticket(collection, entitlement, rate.amount)
                from .authorizations import apply_to_new_excess

                apply_to_new_excess(collection)  # decided at once when a supervisor authorised it in advance
            return collection, True

    @classmethod
    def ingest_many(cls, records):
        """Batch wrapper around ingest() for the meal-ticket gateway bridge, classifying
        each failure the same way the attendance ingestion summary does."""
        summary = MealIngestionSummary()
        for record in records:
            try:
                collection, created = cls.ingest(**record)
                summary.add(MealIngestionResult("created" if created else "duplicate", collection_id=collection.pk))
            except ValueError as exc:
                message = str(exc)
                if message == "Unknown meal device.":
                    summary.add(MealIngestionResult("unknown_device", message))
                elif message == "Access has been revoked for this identity.":
                    summary.add(MealIngestionResult("revoked_access", message))
                elif message == "Unmapped biometric identity.":
                    summary.add(MealIngestionResult("unmapped_employee", message))
                else:
                    summary.add(MealIngestionResult("invalid", message))
        return summary

    @staticmethod
    def _notify_revoked_meal_access_attempt(revoked_identity, device):
        """One notification per employee per day - mirrors the attendance
        equivalent (attendance/integrations/ingestion.py)."""
        from notifications.models import Notification, NotificationSeverity

        employee = revoked_identity.employee
        already_notified_today = Notification.objects.filter(
            event_type="meals.revoked_access_attempt",
            employee=employee,
            created_at__date=timezone.now().date(),
        ).exists()
        if already_notified_today:
            return

        for user in get_user_model().objects.filter(is_superuser=True):
            NotificationService.create(
                recipient=user,
                event_type="meals.revoked_access_attempt",
                title="Revoked employee attempted to collect a meal ticket",
                message=f"{employee.full_name} ({employee.employee_id}) scanned at {device.name}, but their access was revoked.",
                severity=NotificationSeverity.WARNING,
                employee=employee,
                related_url="/meals",
            )

    @staticmethod
    def _notify_unscheduled_meal_collection(employee, device):
        """One notification per employee per day - mirrors _notify_revoked_meal_access_attempt.
        Flags the real fix (assign a shift plan) alongside the excess decision."""
        from notifications.models import Notification, NotificationSeverity

        already_notified_today = Notification.objects.filter(
            event_type="meals.unscheduled_collection",
            employee=employee,
            created_at__date=timezone.now().date(),
        ).exists()
        if already_notified_today:
            return

        for user in get_user_model().objects.filter(is_superuser=True):
            NotificationService.create(
                recipient=user,
                event_type="meals.unscheduled_collection",
                title="Meal collected with no roster assigned",
                message=f"{employee.full_name} ({employee.employee_id}) scanned at {device.name}, but has no shift plan or roster for today, so their meal entitlement can't be worked out. Assign them a shift plan.",
                severity=NotificationSeverity.WARNING,
                employee=employee,
                related_url="/meals",
            )

    @staticmethod
    def _cover_excess_ticket(collection, entitlement, rate):
        """Every ticket beyond the entitlement is decided on its own.

        It opens its own pending decision (Accept / Waive / Decline apply to that one
        ticket) and is never folded into another - neither an earlier decision (that
        used to make a further extra meal go unreviewed and uncharged while the vendor
        was still billed) nor another ticket's still-open one (declining one of three
        extra scans used to decline all three).
        """
        collected_today = MealCollection.objects.filter(employee=collection.employee, work_date=collection.work_date, voided_at__isnull=True).count()
        exception = MealExcessException.objects.create(employee=collection.employee, work_date=collection.work_date, entitlement_snapshot=entitlement, collected_quantity=collected_today, excess_quantity=1, rate_snapshot=rate, proposed_deduction=rate)
        collection.excess_exception = exception
        collection.save(update_fields=["excess_exception"])

    @staticmethod
    @transaction.atomic
    def decline(exception, actor, reason, *, notify=True):
        """Reject a non-entitled meal: the tickets beyond the employee's entitlement
        are voided (so the vendor isn't billed for them) and nothing is deducted
        from the employee. Contrast cancel(), which waives the deduction but leaves
        the tickets standing, i.e. the company still pays the vendor.

        notify=False skips the per-superuser notification - used by the bulk decision
        endpoint, which sends one summary notification for the whole batch instead of
        one per ticket."""
        reason = (reason or "").strip()
        if exception.status != MealExcessStatus.PENDING: raise ValueError("This meal excess has already been decided.")
        now = timezone.now()
        excess_tickets = list(MealCollection.objects.select_for_update().filter(excess_exception=exception, voided_at__isnull=True))
        for ticket in excess_tickets:
            ticket.voided_at, ticket.voided_by, ticket.void_reason = now, actor, f"Excess declined: {reason}" if reason else "Excess declined"
            ticket.save(update_fields=["voided_at", "voided_by", "void_reason"])
        exception.status, exception.reviewer, exception.reviewed_at, exception.comment = MealExcessStatus.DECLINED, actor, now, reason
        exception.save()
        from .gating import refresh

        refresh(exception.employee_id)
        AuditService.log(event_type="meals.excess_declined", module="meals", employee=exception.employee, actor=actor, object=exception, severity=AuditSeverity.WARNING, title="Meal excess declined", description=f"{len(excess_tickets)} ticket(s) beyond entitlement declined; vendor not billed, no deduction.", metadata={"exception": exception.pk, "reason": reason, "tickets_voided": [t.pk for t in excess_tickets]})
        if notify:
            for user in get_user_model().objects.filter(is_superuser=True): NotificationService.create(recipient=user, event_type="meals.excess_declined", title="Meal excess declined", message=f"{exception.employee.full_name}: {reason or 'No reason given'}", severity="warning", employee=exception.employee, related_url="/meals")
        return exception

    @staticmethod
    @transaction.atomic
    def void_collection(collection, actor, reason):
        """Void a ticket that shouldn't count (a test or accidental scan).

        Kept on record, never deleted. It drops out of the vendor's amount owed and
        of the employee's daily count. If it was an over-entitlement ticket, its
        own pending decision shrinks by one (cancelled when nothing is left);
        decisions already made for other tickets are untouched. Refused once its
        excess has been approved for a payroll deduction, since voiding would
        silently leave the deduction standing.
        """
        reason = (reason or "").strip()
        if collection.voided_at: raise ValueError("This ticket has already been voided.")
        exception = MealExcessException.objects.select_for_update().filter(pk=collection.excess_exception_id).first() if collection.excess_exception_id else None
        if exception and exception.status == MealExcessStatus.DEDUCTED:
            raise ValueError("This ticket's excess was already deducted in payroll, so it can't be voided. Reverse that deduction first.")
        collection.voided_at, collection.voided_by, collection.void_reason = timezone.now(), actor, reason
        collection.save(update_fields=["voided_at", "voided_by", "void_reason"])
        if exception and exception.status == MealExcessStatus.APPROVED:
            # accepted but not yet in any payroll: voiding the ticket withdraws the acceptance
            exception.status, exception.reviewer, exception.reviewed_at, exception.comment = MealExcessStatus.CANCELLED, actor, timezone.now(), f"Ticket voided: {reason}" if reason else "Ticket voided"
            exception.save()
        elif exception and exception.status == MealExcessStatus.PENDING:
            exception.excess_quantity, exception.collected_quantity = exception.excess_quantity - 1, max(exception.collected_quantity - 1, 0)
            if exception.excess_quantity > 0:
                exception.proposed_deduction = Decimal(exception.excess_quantity) * exception.rate_snapshot
                exception.save(update_fields=["collected_quantity", "excess_quantity", "proposed_deduction", "updated_at"])
            else:
                exception.status, exception.reviewer, exception.reviewed_at, exception.comment = MealExcessStatus.CANCELLED, actor, timezone.now(), f"Ticket voided: {reason}" if reason else "Ticket voided"
                exception.save()
        AuditService.log(event_type="meals.ticket_voided", module="meals", employee=collection.employee, actor=actor, object=collection, severity=AuditSeverity.WARNING, title="Meal ticket voided", description=f"Meal ticket for {collection.work_date} voided.", metadata={"collection": collection.pk, "reason": reason, "rate": str(collection.rate_snapshot)})
        from .gating import refresh

        refresh(collection.employee_id)  # a voided ticket may free one: switch them back on at the terminal
        return collection

    LATE_ENTITLEMENT_GRACE_DAYS = 14

    @classmethod
    def late_entitlement(cls, exception):
        """What this person is entitled to on the excess's work date NOW, when that is more than the system knew
        when the ticket was collected - the new-starter case: the scan happened while the entitlement (or the
        shift allocation) had not been entered yet, so the ticket was recorded as an excess against zero.

        Counts only when the roster now says they work that day, and only when the entitlement is one the system
        did not have then: a rule applies from its own effective dates, except that a person's very first
        entitlement, entered after the ticket was collected and starting within a fortnight of it, is taken to
        cover the days before it (it was just entered late). 0 means nothing to balance against."""
        employee, work_date = exception.employee, exception.work_date
        roster = EmployeeRosterDay.objects.filter(employee=employee, date=work_date).first()
        if roster is None or roster.status != RosterDayStatus.WORK:
            return 0
        base = cls.approved_entitlement(employee, work_date)
        if base == 0:
            first = EmployeeMealEntitlement.objects.filter(employee=employee).order_by("effective_from", "id").first()
            if (
                first is not None
                and first.created_at > exception.created_at
                and 0 < (first.effective_from - work_date).days <= cls.LATE_ENTITLEMENT_GRACE_DAYS
                and not EmployeeMealEntitlement.objects.filter(employee=employee, effective_from__lte=work_date).exists()
            ):
                base = first.tickets_per_work_day
        return max(base - cls.absence_penalty_reduction(employee, work_date), 0)

    @classmethod
    def _late_cover(cls, exception, lock=False):
        """(entitlement now, the day's valid tickets in order, rank by ticket id, this excess's tickets the late
        entitlement covers). A ticket's place is its rank among the day's valid tickets: one declined or voided
        earlier no longer counts, so the next may now be the person's first (it keeps the number it was given)."""
        entitlement = cls.late_entitlement(exception)
        if entitlement <= exception.entitlement_snapshot:
            return entitlement, [], {}, []
        queryset = MealCollection.objects.filter(employee=exception.employee, work_date=exception.work_date, voided_at__isnull=True).order_by("event__timestamp", "id")
        day_tickets = list(queryset.select_for_update() if lock else queryset)
        rank = {ticket.pk: place for place, ticket in enumerate(day_tickets, start=1)}
        covered = [ticket for ticket in day_tickets if ticket.excess_exception_id == exception.pk and rank[ticket.pk] <= entitlement]
        return entitlement, day_tickets, rank, covered

    @classmethod
    def would_balance(cls, exception):
        """True when Accept would clear (at least part of) this excess instead of charging it."""
        return exception.status == MealExcessStatus.PENDING and bool(cls._late_cover(exception)[3])

    @classmethod
    def balance_against_late_entitlement(cls, exception, actor):
        """Clear the tickets of this excess that the person's entitlement, entered late, covers: no charge, no
        deduction. Whatever is still beyond the entitlement stays an excess (and Accept charges it). Returns how
        many tickets were balanced."""
        entitlement, day_tickets, rank, covered = cls._late_cover(exception, lock=True)
        tickets = [ticket for ticket in day_tickets if ticket.excess_exception_id == exception.pk]
        if not covered:
            return 0
        for ticket in covered:
            ticket.status, ticket.entitlement_snapshot, ticket.excess_exception, ticket.sequence_number = MealCollectionStatus.WITHIN, entitlement, None, rank[ticket.pk]
            ticket.save(update_fields=["status", "entitlement_snapshot", "excess_exception", "sequence_number"])
        remaining = len(tickets) - len(covered)
        note = f"Balanced against the entitlement added later ({entitlement} per day): no deduction."
        if remaining == 0:
            exception.status, exception.reviewer, exception.reviewed_at, exception.comment = MealExcessStatus.CANCELLED, actor, timezone.now(), note
            exception.save()
        else:
            exception.entitlement_snapshot, exception.excess_quantity = entitlement, remaining
            exception.proposed_deduction = Decimal(remaining) * exception.rate_snapshot
            exception.save()
        AuditService.log(event_type="meals.excess_balanced", module="meals", employee=exception.employee, actor=actor, object=exception, severity=AuditSeverity.SUCCESS, title="Meal excess balanced against late entitlement", description=f"{len(covered)} ticket(s) on {exception.work_date} fall within the entitlement entered afterwards; no deduction.", metadata={"exception": exception.pk, "entitlement": entitlement, "tickets_balanced": [t.pk for t in covered], "tickets_still_excess": remaining})
        from .gating import refresh

        refresh(exception.employee_id)
        return len(covered)

    @staticmethod
    @transaction.atomic
    def approve(exception, period, actor, comment=""):
        """Accept a non-entitled meal: the employee is charged, in that month's payroll.

        Works at any time. If the month's payroll record for the employee already
        exists it is deducted immediately; otherwise the excess is held as accepted
        and deducted automatically when that month's payroll is generated (see
        apply_accepted_excess_for_period), so HR needn't wait for payroll to decide.
        A payroll period or record that is already approved/paid is never altered.
        """
        if exception.status != MealExcessStatus.PENDING:
            raise ValueError(
                "This meal excess has already been decided."
            )

        # The entitlement (or shift) may have been entered after the ticket was collected: balance that first, so
        # Accept never charges someone for a meal they are entitled to.
        exception.balanced_tickets = MealService.balance_against_late_entitlement(exception, actor)
        if exception.status != MealExcessStatus.PENDING:
            return exception

        period = period or PayrollPeriod.objects.filter(year=exception.work_date.year, month=exception.work_date.month).first()
        payroll = None
        if period is not None:
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

            payroll = period.employee_payrolls.filter(employee=exception.employee).first()
            if payroll is not None and payroll.status in {
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

        if payroll is not None:
            MealService.apply_to_payroll(exception, payroll)

        AuditService.log(
            event_type="meals.excess_approved",
            module="meals",
            employee=exception.employee,
            actor=actor,
            object=exception,
            severity=AuditSeverity.SUCCESS,
            title="Meal excess accepted",
            description=(
                "Meal excess accepted and deducted from payroll."
                if payroll is not None
                else "Meal excess accepted; it will be deducted when that month's payroll is generated."
            ),
            metadata={
                "exception": exception.pk,
                "amount": str(exception.proposed_deduction),
                "deducted_now": payroll is not None,
            },
        )

        return exception

    @staticmethod
    def apply_to_payroll(exception, payroll):
        """Add an accepted excess to an employee's payroll as a deduction (idempotent)."""
        line, _ = PayrollLineItem.objects.get_or_create(
            payroll=payroll,
            source_type="meal_excess",
            source_reference=str(exception.pk),
            is_system_generated=True,
            defaults={
                "item_type": PayrollLineItemType.DEDUCTION,
                "code": "MEAL_EXCESS",
                "description": f"Excess meal tickets for {exception.work_date}",
                "amount": exception.proposed_deduction,
                "metadata": {
                    "meal_excess_id": exception.pk,
                    "work_date": exception.work_date.isoformat(),
                    "entitlement": exception.entitlement_snapshot,
                    "excess_quantity": exception.excess_quantity,
                    "rate": str(exception.rate_snapshot),
                },
            },
        )
        recalculate_employee_payroll(payroll)
        exception.payroll, exception.payroll_line_item, exception.payroll_period = payroll, line, payroll.payroll_period
        exception.status = MealExcessStatus.DEDUCTED
        exception.save()
        return line

    @staticmethod
    def apply_accepted_excess_for_period(period):
        """Deduct every accepted-but-not-yet-deducted excess for this period's month
        from the matching employee payroll records. Called when payroll is generated."""
        applied = 0
        for exception in MealExcessException.objects.filter(status=MealExcessStatus.APPROVED, work_date__year=period.year, work_date__month=period.month).select_related("employee"):
            payroll = period.employee_payrolls.filter(employee=exception.employee).exclude(status__in=[EmployeePayrollStatus.APPROVED, EmployeePayrollStatus.PAID]).first()
            if payroll is not None:
                MealService.apply_to_payroll(exception, payroll)
                applied += 1
        return applied

    @staticmethod
    def cancel(exception, actor, comment, *, notify=True):
        if exception.status != MealExcessStatus.PENDING: raise ValueError("This meal excess has already been decided.")
        comment = (comment or "").strip()
        exception.status, exception.reviewer, exception.reviewed_at, exception.comment = MealExcessStatus.CANCELLED, actor, timezone.now(), comment; exception.save()
        AuditService.log(event_type="meals.excess_cancelled", module="meals", employee=exception.employee, actor=actor, object=exception, severity=AuditSeverity.WARNING, title="Meal excess waived", description="Meal excess deduction waived.", metadata={"exception":exception.pk,"reason":comment})
        if notify:
            for user in get_user_model().objects.filter(is_superuser=True): NotificationService.create(recipient=user, event_type="meals.excess_cancelled", title="Meal excess waived", message=f"{exception.employee.full_name}: {comment or 'No reason given'}", severity="warning", employee=exception.employee, related_url="/meals")
        return exception


def apply_import_meal_entitlement(employee, tickets_per_day, *, actor=None, reason="Employee bulk import: NO OF MEALS PER DAY", refresh_terminals=True):
    """Set one employee's meal entitlement as the Bulk Import's NO OF MEALS PER DAY column says.

    Same safety rule as salary and accommodation on this same import: a blank or unreadable cell means "the sheet
    says nothing about meals" and the person's existing entitlement is left exactly as it is - it is never possible
    for this column to erase someone's allocation by being empty (2026-09-23's salary wipe was exactly this mistake
    on a different column). A row is written only when the number in the sheet actually differs from what is
    already in force today.

    A bulk caller passes refresh_terminals=False and runs meals.gating.reconcile once for everyone it changed,
    instead of re-checking terminals person by person.

    Returns "created" (nobody had an entitlement yet), "changed", "unchanged", or None (nothing in the sheet to
    apply)."""
    if tickets_per_day is None:
        return None
    today = timezone.localdate()
    current = (
        EmployeeMealEntitlement.objects.filter(employee=employee, effective_from__lte=today)
        .filter(Q(effective_to__isnull=True) | Q(effective_to__gte=today))
        .order_by("-effective_from", "-id")
        .first()
    )
    if current is not None and current.tickets_per_work_day == tickets_per_day:
        return "unchanged"
    with transaction.atomic():
        if current is not None:
            if current.effective_from == today:
                # Already touched today (e.g. a second import run the same day): update that row in place
                # instead of leaving a zero-length gap behind it.
                current.tickets_per_work_day, current.reason, current.set_by = tickets_per_day, reason, actor
                current.save(update_fields=["tickets_per_work_day", "reason", "set_by"])
                return "changed"
            current.effective_to = today - timedelta(days=1)
            current.save(update_fields=["effective_to"])
        EmployeeMealEntitlement.objects.create(
            employee=employee, tickets_per_work_day=tickets_per_day, effective_from=today, reason=reason, set_by=actor,
        )
    if refresh_terminals:
        from .gating import refresh

        refresh(employee)  # switch them on/off at a meal terminal straight away if this changed what they're owed today
    return "created" if current is None else "changed"


def allocate_vendor_claim(claim):
    """Spread a vendor claim over the days it covers, in step with the tickets issued each day, so a week's claim
    shows correctly whether you look by week, month or day.

    A claim can never be paid for more tickets than were issued: any excess is reported, not allocated.
    Returns {"days": {date: {"issued", "issued_amount", "claimed", "claimed_amount"}}, "issued", "claimed", "over_claimed"}."""
    from django.db.models import Count, Sum

    rows = {
        row["work_date"]: row
        for row in MealCollection.objects.filter(work_date__gte=claim.date_from, work_date__lte=claim.date_to, voided_at__isnull=True)
        .values("work_date").annotate(n=Count("id"), amount=Sum("rate_snapshot"))
    }
    issued = sum(row["n"] for row in rows.values())
    payable = min(claim.quantity, issued)
    shares = {}
    if issued:
        floors = {day: payable * row["n"] // issued for day, row in rows.items()}
        leftover = payable - sum(floors.values())
        by_remainder = sorted(rows, key=lambda day: ((payable * rows[day]["n"]) % issued, day), reverse=True)
        for day in by_remainder[:leftover]:
            floors[day] += 1
        shares = floors
    days = {}
    for day, row in rows.items():
        claimed = shares.get(day, 0)
        days[day] = {
            "issued": row["n"],
            "issued_amount": row["amount"] or Decimal("0"),
            "claimed": claimed,
            "claimed_amount": (row["amount"] or Decimal("0")) * claimed / row["n"],
        }
    return {"days": days, "issued": issued, "claimed": payable, "over_claimed": max(claim.quantity - issued, 0)}


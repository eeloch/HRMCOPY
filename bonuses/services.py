import calendar
from datetime import date
from decimal import Decimal

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.db import IntegrityError, transaction
from django.db.models import Count, Q
from django.utils import timezone

from audit.models import AuditSeverity
from audit.services import AuditService
from notifications.models import NotificationSeverity
from notifications.services import NotificationService
from payroll.models import EmployeePayrollStatus, PayrollLineItem, PayrollLineItemType, PayrollPeriod, PayrollPeriodStatus
from payroll.services import recalculate_employee_payroll

from .models import Bonus, BonusKind, BonusStatus, EmployeeOfTheMonth, EotmStatus

LOCKED_PERIOD = {PayrollPeriodStatus.APPROVED, PayrollPeriodStatus.PAID, PayrollPeriodStatus.CLOSED}
LOCKED_RECORD = {EmployeePayrollStatus.APPROVED, EmployeePayrollStatus.PAID}
MONTHS = ["", "January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]


def month_label(year, month):
    return f"{MONTHS[month]} {year}"


def _check_month(year, month, what):
    if not (isinstance(year, int) and isinstance(month, int) and 2000 <= year <= 2100 and 1 <= month <= 12):
        raise ValueError(f"Choose a valid {what}.")


class BonusService:
    @staticmethod
    def record(*, employee, amount, reason, performance_year, performance_month, pay_year=None, pay_month=None, kind=BonusKind.PERFORMANCE, actor, reward_type=None, occurrence=None):
        amount = Decimal(amount)
        if amount <= 0:
            raise ValueError("The bonus amount must be more than zero.")
        if not str(reason or "").strip():
            raise ValueError("Say what this person did that went beyond the call of duty.")
        if employee.status != "active":
            raise ValueError("Bonuses can only be recorded for active employees.")
        pay_year, pay_month = pay_year or performance_year, pay_month or performance_month
        _check_month(performance_year, performance_month, "month for the good work")
        _check_month(pay_year, pay_month, "payroll month")
        bonus = Bonus.objects.create(employee=employee, kind=kind, amount=amount, reason=reason.strip(), performance_year=performance_year, performance_month=performance_month, pay_year=pay_year, pay_month=pay_month, recorded_by=actor, reward_type=reward_type, occurrence=occurrence)
        BonusService._log("bonus.recorded", bonus, actor, AuditSeverity.INFO, "Bonus recorded", f"A bonus of {amount:,.2f} was recorded for {employee.full_name} for {month_label(performance_year, performance_month)}.")
        return bonus

    @staticmethod
    def approve(bonus, *, actor, comment="", pay_year=None, pay_month=None):
        with transaction.atomic():
            bonus = Bonus.objects.select_for_update().select_related("employee").get(pk=bonus.pk)
            if bonus.status != BonusStatus.PROPOSED:
                raise ValueError("Only a bonus that is awaiting approval can be approved.")
            if pay_year and pay_month:
                _check_month(pay_year, pay_month, "payroll month")
                bonus.pay_year, bonus.pay_month = pay_year, pay_month
            period = PayrollPeriod.objects.filter(year=bonus.pay_year, month=bonus.pay_month).first()
            if period is not None and period.status in LOCKED_PERIOD:
                raise ValueError(f"{month_label(bonus.pay_year, bonus.pay_month)} payroll is already approved, so it cannot take a new bonus. Choose a later payroll month.")
            bonus.status, bonus.decided_by, bonus.decided_at, bonus.decision_comment = BonusStatus.APPROVED, actor, timezone.now(), comment.strip()
            bonus.save()
            BonusService._log("bonus.approved", bonus, actor, AuditSeverity.SUCCESS, "Bonus approved", f"{bonus.employee.full_name}'s bonus of {bonus.amount:,.2f} was approved for the {month_label(bonus.pay_year, bonus.pay_month)} payroll.")
            # If that month's payroll already exists and is still open, add it now; otherwise it is added when payroll is generated.
            if period is not None:
                BonusService._apply(bonus, period, actor)
        return bonus

    @staticmethod
    def decline(bonus, *, actor, comment):
        if not str(comment or "").strip():
            raise ValueError("Give a reason for declining the bonus.")
        with transaction.atomic():
            bonus = Bonus.objects.select_for_update().select_related("employee").get(pk=bonus.pk)
            if bonus.status != BonusStatus.PROPOSED:
                raise ValueError("Only a bonus that is awaiting approval can be declined.")
            bonus.status, bonus.decided_by, bonus.decided_at, bonus.decision_comment = BonusStatus.DECLINED, actor, timezone.now(), comment.strip()
            bonus.save()
            BonusService._log("bonus.declined", bonus, actor, AuditSeverity.WARNING, "Bonus declined", f"{bonus.employee.full_name}'s bonus of {bonus.amount:,.2f} was declined.")
        return bonus

    @staticmethod
    def cancel(bonus, *, actor):
        with transaction.atomic():
            bonus = Bonus.objects.select_for_update().select_related("employee").get(pk=bonus.pk)
            if bonus.status not in {BonusStatus.PROPOSED, BonusStatus.APPROVED}:
                raise ValueError("Only a bonus that is not yet in payroll can be cancelled.")
            bonus.status = BonusStatus.CANCELLED
            bonus.save()
            BonusService._log("bonus.cancelled", bonus, actor, AuditSeverity.WARNING, "Bonus cancelled", f"{bonus.employee.full_name}'s bonus of {bonus.amount:,.2f} was cancelled.")
        return bonus

    @staticmethod
    def apply_for_period(period):
        """Add every approved bonus for this payroll month to that person's payroll as an earning.
        Called when payroll is generated; safe to repeat."""
        if period.status in LOCKED_PERIOD:
            return 0
        applied = 0
        for bonus in Bonus.objects.filter(status=BonusStatus.APPROVED, pay_year=period.year, pay_month=period.month).select_related("employee"):
            if BonusService._apply(bonus, period, None):
                applied += 1
        return applied

    @staticmethod
    def _apply(bonus, period, actor):
        bonus = Bonus.objects.select_related("reward_type").get(pk=bonus.pk)
        payroll = period.employee_payrolls.select_for_update().filter(employee=bonus.employee).exclude(status__in=LOCKED_RECORD).first()
        if payroll is None or bonus.status != BonusStatus.APPROVED:
            return False
        if bonus.kind == BonusKind.EMPLOYEE_OF_MONTH:
            eotm = EmployeeOfTheMonth.objects.filter(bonus=bonus).select_related("department").first()
            title = f"Employee of the Month - {eotm.department.name}" if eotm else "Employee of the Month"
        elif bonus.kind == BonusKind.POLICY_REWARD and bonus.reward_type_id:
            title = bonus.reward_type.name
        else:
            title = "Performance bonus" if bonus.kind == BonusKind.PERFORMANCE else "Bonus"
        line = PayrollLineItem.objects.create(
            payroll=payroll, item_type=PayrollLineItemType.EARNING, code="BONUS" if bonus.kind != BonusKind.EMPLOYEE_OF_MONTH else "EOTM",
            description=f"{title} ({month_label(bonus.performance_year, bonus.performance_month)})", amount=bonus.amount,
            source_type="bonus", source_reference=str(bonus.pk), metadata={"bonus_id": bonus.pk, "reason": bonus.reason[:300]}, is_system_generated=True,
        )
        recalculate_employee_payroll(payroll)
        bonus.status, bonus.payroll, bonus.payroll_line_item = BonusStatus.PAID, payroll, line
        bonus.save(update_fields=["status", "payroll", "payroll_line_item", "updated_at"])
        BonusService._log("bonus.applied", bonus, actor, AuditSeverity.SUCCESS, "Bonus added to payroll", f"{bonus.amount:,.2f} was added to {bonus.employee.full_name}'s {period.display_name} payroll.")
        return True

    @staticmethod
    def _log(event, bonus, actor, severity, title, description):
        AuditService.log(event_type=event, module="bonuses", employee=bonus.employee, actor=actor, object=bonus, severity=severity, title=title, description=description, metadata={"bonus_id": bonus.pk, "amount": str(bonus.amount), "status": bonus.status})


class EmployeeOfTheMonthService:
    @staticmethod
    def propose(*, department, year, month, employee, reason, reward_amount=None, actor):
        _check_month(year, month, "month")
        if not str(reason or "").strip():
            raise ValueError("Say why this person is the Employee of the Month.")
        if employee.status != "active":
            raise ValueError("Only an active employee can be Employee of the Month.")
        if employee.department_id != department.pk:
            raise ValueError(f"{employee.full_name} is not in {department.name}.")
        if reward_amount is not None and Decimal(reward_amount) < 0:
            raise ValueError("The reward cannot be negative.")
        if EmployeeOfTheMonth.objects.filter(department=department, year=year, month=month, status__in=[EotmStatus.PROPOSED, EotmStatus.APPROVED]).exists():
            raise ValueError(f"{department.name} already has an Employee of the Month for {month_label(year, month)}. Cancel or decline it first.")
        reward = Decimal(reward_amount) if reward_amount else None
        entry = EmployeeOfTheMonth.objects.create(department=department, year=year, month=month, employee=employee, reason=reason.strip(), reward_amount=reward if reward and reward > 0 else None, recorded_by=actor)
        EmployeeOfTheMonthService._log("eotm.proposed", entry, actor, AuditSeverity.INFO, "Employee of the Month proposed", f"{employee.full_name} was proposed as {department.name} Employee of the Month for {month_label(year, month)}.")
        return entry

    @staticmethod
    def approve(entry, *, actor, comment="", pay_year=None, pay_month=None):
        with transaction.atomic():
            entry = EmployeeOfTheMonth.objects.select_for_update().select_related("employee", "department").get(pk=entry.pk)
            if entry.status != EotmStatus.PROPOSED:
                raise ValueError("Only a proposal that is awaiting approval can be approved.")
            bonus = None
            if entry.reward_amount:
                bonus = Bonus.objects.create(employee=entry.employee, kind=BonusKind.EMPLOYEE_OF_MONTH, amount=entry.reward_amount, reason=entry.reason, performance_year=entry.year, performance_month=entry.month, pay_year=pay_year or entry.year, pay_month=pay_month or entry.month, recorded_by=entry.recorded_by)
                entry.bonus = bonus
            entry.status, entry.decided_by, entry.decided_at, entry.decision_comment = EotmStatus.APPROVED, actor, timezone.now(), comment.strip()
            entry.save()
            if bonus is not None:
                entry_link = entry  # the bonus needs the award linked before it can be titled in payroll
                BonusService.approve(bonus, actor=actor, comment=f"Employee of the Month: {entry_link.department.name}")
            EmployeeOfTheMonthService._log("eotm.approved", entry, actor, AuditSeverity.SUCCESS, "Employee of the Month approved", f"{entry.employee.full_name} is {entry.department.name} Employee of the Month for {month_label(entry.year, entry.month)}.")
        return entry

    @staticmethod
    def decline(entry, *, actor, comment):
        if not str(comment or "").strip():
            raise ValueError("Give a reason for declining.")
        with transaction.atomic():
            entry = EmployeeOfTheMonth.objects.select_for_update().select_related("employee", "department").get(pk=entry.pk)
            if entry.status != EotmStatus.PROPOSED:
                raise ValueError("Only a proposal that is awaiting approval can be declined.")
            entry.status, entry.decided_by, entry.decided_at, entry.decision_comment = EotmStatus.DECLINED, actor, timezone.now(), comment.strip()
            entry.save()
            EmployeeOfTheMonthService._log("eotm.declined", entry, actor, AuditSeverity.WARNING, "Employee of the Month declined", f"{entry.employee.full_name}'s proposal for {entry.department.name} was declined.")
        return entry

    @staticmethod
    def cancel(entry, *, actor):
        with transaction.atomic():
            # "bonus" is a nullable FK (a LEFT JOIN) - Postgres refuses FOR UPDATE across an outer join
            # unless it is scoped to just the base table with `of`.
            entry = EmployeeOfTheMonth.objects.select_for_update(of=("self",)).select_related("employee", "department", "bonus").get(pk=entry.pk)
            if entry.status not in {EotmStatus.PROPOSED, EotmStatus.APPROVED}:
                raise ValueError("This entry is already closed.")
            if entry.bonus_id and entry.bonus.status == BonusStatus.PAID:
                raise ValueError("The reward is already in payroll. Reverse it in payroll before cancelling.")
            if entry.bonus_id:
                BonusService.cancel(entry.bonus, actor=actor)
            entry.status = EotmStatus.CANCELLED
            entry.save()
            EmployeeOfTheMonthService._log("eotm.cancelled", entry, actor, AuditSeverity.WARNING, "Employee of the Month cancelled", f"{entry.department.name}'s {month_label(entry.year, entry.month)} entry for {entry.employee.full_name} was cancelled.")
        return entry

    @staticmethod
    def _log(event, entry, actor, severity, title, description):
        AuditService.log(event_type=event, module="bonuses", employee=entry.employee, actor=actor, object=entry, severity=severity, title=title, description=description, metadata={"eotm_id": entry.pk, "department": entry.department.name, "status": entry.status})


class AttendanceRewardService:
    """The policy's automatic rewards: "No absence" and "No lateness" for a whole month.

    Run after a month has ended (the daily attendance job calls it in the first days of the month). Everyone who
    earned a reward gets a proposed bonus - the first-time or second-time amount from the policy - and the people who
    approve bonuses are notified once. Nothing is paid until a person approves it in Payroll > Bonuses.
    """

    RULES = ("no_absence", "no_lateness")

    @staticmethod
    def previous_month(today=None):
        today = today or timezone.localdate()
        return (today.year - 1, 12) if today.month == 1 else (today.year, today.month - 1)

    @staticmethod
    def earners(year, month):
        """{rule: [employees]} who earned each automatic reward for that (finished) month."""
        from attendance.models import DailyAttendance
        from employees.models import Employee

        first, last = date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])
        counts = {
            row["employee_id"]: row
            for row in DailyAttendance.objects.filter(date__range=(first, last)).values("employee_id").annotate(
                worked=Count("id", filter=Q(status__in=["present", "late", "incomplete"])),
                late=Count("id", filter=Q(status="late")),
                absent=Count("id", filter=Q(status="absent")),
            )
        }
        if not counts:
            raise ValueError(f"No attendance has been processed for {month_label(year, month)} yet.")
        employees = Employee.objects.filter(status="active").filter(Q(employment_date__isnull=True) | Q(employment_date__lte=first)).select_related("department").order_by("employee_id")
        result = {rule: [] for rule in AttendanceRewardService.RULES}
        for employee in employees:
            row = counts.get(employee.pk)
            if not row or row["worked"] < 1:
                continue
            if row["absent"] == 0:
                result["no_absence"].append(employee)
            if row["late"] == 0:
                result["no_lateness"].append(employee)
        return result

    @staticmethod
    def propose_for_month(year, month, *, dry_run=False, today=None):
        from offences.models import RewardType

        today = today or timezone.localdate()
        first_year, first_month = (int(part) for part in settings.ATTENDANCE_REWARDS_FIRST_MONTH.split("-"))
        if (year, month) < (first_year, first_month):
            raise ValueError(f"Attendance rewards start from {month_label(first_year, first_month)}; {month_label(year, month)} is before the attendance system was fully in use.")
        if date(year, month, calendar.monthrange(year, month)[1]) >= today:
            raise ValueError(f"{month_label(year, month)} has not finished yet.")
        earners = AttendanceRewardService.earners(year, month)
        summary = {"month": month_label(year, month), "proposed": {}, "already_proposed": 0, "missing_reward": []}
        for rule, people in earners.items():
            reward = RewardType.objects.filter(auto_rule=rule, active=True).first()
            if reward is None:
                summary["missing_reward"].append(rule)
                continue
            proposed = 0
            for employee in people:
                if Bonus.objects.filter(employee=employee, reward_type=reward, performance_year=year, performance_month=month, status__in=[BonusStatus.PROPOSED, BonusStatus.APPROVED, BonusStatus.PAID]).exists():
                    summary["already_proposed"] += 1
                    continue
                earlier = Bonus.objects.filter(employee=employee, reward_type=reward).exclude(status__in=[BonusStatus.DECLINED, BonusStatus.CANCELLED]).filter(Q(performance_year__lt=year) | Q(performance_year=year, performance_month__lt=month)).count()
                occurrence = earlier + 1
                text, amount = reward.reward_for(occurrence)
                if not amount:
                    continue
                proposed += 1
                if dry_run:
                    continue
                reason = f"{reward.name} for {month_label(year, month)} - {text} ({'first' if occurrence == 1 else 'second or later'} time). Reward under the Disciplinary Action Policy."
                try:
                    with transaction.atomic():
                        BonusService.record(employee=employee, amount=amount, reason=reason, performance_year=year, performance_month=month, kind=BonusKind.POLICY_REWARD, actor=None, reward_type=reward, occurrence=occurrence)
                except IntegrityError:
                    proposed -= 1
                    summary["already_proposed"] += 1
            summary["proposed"][reward.name] = proposed
        if not dry_run and any(summary["proposed"].values()):
            AttendanceRewardService.notify_approvers(summary)
        return summary

    @staticmethod
    def notify_approvers(summary):
        permission = Permission.objects.filter(content_type__app_label="bonuses", codename="approve_bonus").first()
        approvers = get_user_model().objects.filter(is_active=True).filter(Q(is_superuser=True) | Q(user_permissions=permission) | Q(groups__permissions=permission)).distinct()
        total = sum(summary["proposed"].values())
        detail = ", ".join(f"{count} for \"{name}\"" for name, count in summary["proposed"].items() if count)
        for user in approvers:
            NotificationService.create(
                recipient=user,
                event_type="bonuses.attendance_rewards_pending",
                title=f"{total} attendance reward{'s' if total != 1 else ''} awaiting approval",
                message=f"{summary['month']}: {detail}. Review and approve them in Payroll > Bonuses.",
                severity=NotificationSeverity.INFO,
                related_url="/bonuses",
            )

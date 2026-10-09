"""An audit entry whenever an employee's status, exit date, employment date or employment type changes.

These decide pay, meals, terminal access and what shows in payroll, and until now nothing recorded who changed them or
when (2026-10-09: one person flipped between active and inactive five times in a week, and the history had to be
rebuilt from nightly backups). Every save through the app, the admin or an import is covered. A bulk .update() skips
Django signals, so scripts that use it must log for themselves."""

from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver

from employees.models import Employee

TRACKED = {
    "status": "status",
    "exit_date": "exit date",
    "employment_date": "employment date",
    "employment_type": "employment type",
}


@receiver(pre_save, sender=Employee)
def remember_before(sender, instance, **kwargs):
    instance._audit_before = None
    if instance.pk:
        instance._audit_before = Employee.objects.filter(pk=instance.pk).values(*TRACKED).first()


@receiver(post_save, sender=Employee)
def log_changes(sender, instance, created, **kwargs):
    before = getattr(instance, "_audit_before", None)
    instance._audit_before = None
    if created or not before:
        return
    changes = {field: (before[field], getattr(instance, field)) for field in TRACKED if before[field] != getattr(instance, field)}
    if not changes:
        return
    from audit.models import AuditSeverity
    from audit.services import AuditService
    from core.actor import get_actor

    actor = get_actor()
    shown = lambda value: "none" if value in (None, "") else str(value)  # noqa: E731
    summary = "; ".join(f"{TRACKED[field]}: {shown(old)} to {shown(new)}" for field, (old, new) in changes.items())
    status_changed = "status" in changes
    AuditService.log(
        event_type="employees.status_changed" if status_changed else "employees.record_changed",
        module="employees", employee=instance, actor=actor, object=instance,
        severity=AuditSeverity.WARNING if status_changed else AuditSeverity.INFO,
        title=f"Employee {'status' if status_changed else 'record'} changed",
        description=f"{instance.full_name} ({instance.employee_id}) - {summary}." + ("" if actor else " Made outside the app screens (a script or a system job)."),
        metadata={"changes": {TRACKED[field]: {"from": None if old is None else str(old), "to": None if new is None else str(new)} for field, (old, new) in changes.items()}},
    )

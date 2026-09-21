from django.db import transaction
from django.utils import timezone

from audit.models import AuditSeverity
from audit.services import AuditService

from .models import MealExtraAuthorization

MAX_EXTRA = 5


def active_on(employee, work_date):
    return MealExtraAuthorization.objects.filter(employee=employee, work_date=work_date, cancelled_at__isnull=True)


def extra_allowed(employee, work_date):
    """How many extra tickets have been authorised for this person on this day (used or not)."""
    return sum(a.quantity for a in active_on(employee, work_date))


def status_of(authorization, today=None):
    today = today or timezone.localdate()
    if authorization.cancelled_at:
        return "cancelled"
    if authorization.used >= authorization.quantity:
        return "collected"
    if authorization.work_date < today:
        return "not used"
    return "waiting"


def authorise(*, employee, quantity, pays, reason="", actor, work_date=None):
    if employee.status != "active":
        raise ValueError("Only active staff can be authorised an extra ticket.")
    if not isinstance(quantity, int) or not 1 <= quantity <= MAX_EXTRA:
        raise ValueError(f"Authorise between 1 and {MAX_EXTRA} tickets.")
    if pays not in ("employee", "company"):
        raise ValueError("Say who pays for the extra ticket: the employee or the company.")
    work_date = work_date or timezone.localdate()
    with transaction.atomic():
        authorization = MealExtraAuthorization.objects.create(employee=employee, work_date=work_date, quantity=quantity, pays=pays, reason=reason.strip(), authorised_by=actor)
        AuditService.log(event_type="meals.extra_authorised", module="meals", employee=employee, actor=actor, object=authorization, severity=AuditSeverity.INFO, title="Extra meal ticket authorised", description=f"{quantity} extra ticket(s) authorised for {employee.full_name} on {work_date}; {'employee pays' if pays == 'employee' else 'company pays'}.", metadata={"quantity": quantity, "pays": pays, "reason": reason})
    _refresh_terminal()
    return authorization


def cancel(authorization, *, actor):
    with transaction.atomic():
        authorization = MealExtraAuthorization.objects.select_for_update().select_related("employee").get(pk=authorization.pk)
        if authorization.cancelled_at or authorization.used >= authorization.quantity:
            raise ValueError("This authorisation is already finished.")
        if authorization.used:
            authorization.quantity = authorization.used  # what was already collected stands; the rest is withdrawn
        else:
            authorization.cancelled_at = timezone.now()
        authorization.save()
        AuditService.log(event_type="meals.extra_authorisation_cancelled", module="meals", employee=authorization.employee, actor=actor, object=authorization, severity=AuditSeverity.WARNING, title="Extra meal ticket authorisation withdrawn", description=f"The unused extra ticket authorisation for {authorization.employee.full_name} on {authorization.work_date} was withdrawn.")
    _refresh_terminal()
    return authorization


def apply_to_new_excess(collection):
    """A person scanned beyond their entitlement: if they were authorised, use one authorisation and decide the
    ticket straight away (employee pays = accept, company pays = waive). Anything that cannot be decided
    automatically (e.g. that month's payroll is already approved) is left pending for a person to decide."""
    from .services import MealService

    with transaction.atomic():
        authorization = (
            MealExtraAuthorization.objects.select_for_update()
            .filter(employee=collection.employee, work_date=collection.work_date, cancelled_at__isnull=True)
            .order_by("created_at", "id")
            .first()
        )
        while authorization is not None and authorization.used >= authorization.quantity:
            authorization = (
                MealExtraAuthorization.objects.select_for_update()
                .filter(employee=collection.employee, work_date=collection.work_date, cancelled_at__isnull=True, created_at__gt=authorization.created_at)
                .order_by("created_at", "id")
                .first()
            )
        if authorization is None or collection.excess_exception_id is None:
            return None
        exception = collection.excess_exception
        note = f"Authorised in advance{': ' + authorization.reason if authorization.reason else ''}"
        try:
            with transaction.atomic():
                if authorization.pays == "employee":
                    MealService.approve(exception, None, authorization.authorised_by, note)
                else:
                    MealService.cancel(exception, authorization.authorised_by, note)
        except ValueError:
            return None
        authorization.used += 1
        authorization.save(update_fields=["used"])
        return authorization


def _refresh_terminal():
    """Switch the person on at the terminal now rather than at the next 30-second check."""
    try:
        from .gating import reconcile

        reconcile()
    except Exception:
        pass

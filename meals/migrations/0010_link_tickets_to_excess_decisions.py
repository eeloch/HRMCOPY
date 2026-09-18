from decimal import Decimal

from django.db import migrations


def link_tickets_and_reopen_undecided(apps, schema_editor):
    """Give each existing over-entitlement ticket the decision it belongs to.

    Until now there was one decision per employee per day, and a later extra ticket
    was silently folded into it. So per employee/day: the earliest tickets are
    attributed to the decision(s) that exist, up to the quantity each covered, and
    any live ticket beyond that was never actually decided - it gets a new pending
    decision so it finally shows up for review.
    """
    Collection = apps.get_model("meals", "MealCollection")
    Exception_ = apps.get_model("meals", "MealExcessException")
    days = set(Collection.objects.exclude(status="within_entitlement").values_list("employee_id", "work_date"))
    for employee_id, work_date in days:
        tickets = list(Collection.objects.filter(employee_id=employee_id, work_date=work_date).exclude(status="within_entitlement").order_by("event__timestamp", "id"))
        position = 0
        for exception in Exception_.objects.filter(employee_id=employee_id, work_date=work_date).order_by("id"):
            for ticket in tickets[position:position + exception.excess_quantity]:
                ticket.excess_exception_id = exception.id
                ticket.save(update_fields=["excess_exception"])
            position += exception.excess_quantity
        undecided = [t for t in tickets[position:] if t.voided_at is None]
        if not undecided:
            continue
        collected_today = Collection.objects.filter(employee_id=employee_id, work_date=work_date, voided_at__isnull=True).count()
        rate = undecided[0].rate_snapshot
        exception = Exception_.objects.create(
            employee_id=employee_id, work_date=work_date, entitlement_snapshot=undecided[0].entitlement_snapshot,
            collected_quantity=collected_today, excess_quantity=len(undecided), rate_snapshot=rate,
            proposed_deduction=Decimal(len(undecided)) * rate, status="pending",
        )
        for ticket in undecided:
            ticket.excess_exception_id = exception.id
            ticket.save(update_fields=["excess_exception"])


class Migration(migrations.Migration):
    dependencies = [("meals", "0009_ticket_excess_decision_link")]
    operations = [migrations.RunPython(link_tickets_and_reopen_undecided, migrations.RunPython.noop)]

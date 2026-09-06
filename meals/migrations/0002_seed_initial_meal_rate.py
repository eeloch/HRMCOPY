from datetime import date

from django.db import migrations


def seed_initial_rate(apps, schema_editor):
    MealTicketRate = apps.get_model("meals", "MealTicketRate")
    MealTicketRate.objects.get_or_create(
        amount="700.00",
        effective_from=date(2026, 9, 3),
        defaults={"active": True},
    )


class Migration(migrations.Migration):
    dependencies = [("meals", "0001_initial")]
    operations = [migrations.RunPython(seed_initial_rate, migrations.RunPython.noop)]

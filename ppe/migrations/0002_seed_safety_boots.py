from django.db import migrations


def seed_safety_boots(apps, schema_editor):
    PPEType = apps.get_model("ppe", "PPEType")
    PPEType.objects.get_or_create(
        code="SAFETY_BOOT",
        defaults={
            "name": "Safety Boots",
            "description": "Factory safety footwear. Cost is configured and snapshotted per issue.",
            "potentially_employee_deductible": True,
            "active": True,
        },
    )


class Migration(migrations.Migration):
    dependencies = [("ppe", "0001_initial")]

    operations = [migrations.RunPython(seed_safety_boots, migrations.RunPython.noop)]

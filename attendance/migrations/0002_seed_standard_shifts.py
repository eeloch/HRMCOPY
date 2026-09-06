from datetime import time

from django.db import migrations


def seed_standard_shifts(apps, schema_editor):
    Shift = apps.get_model("attendance", "Shift")

    Shift.objects.get_or_create(
        name="Day Shift",
        defaults={
            "start_time": time(7, 0),
            "end_time": time(19, 0),
            "is_overnight": False,
            "active": True,
        },
    )
    Shift.objects.get_or_create(
        name="Night Shift",
        defaults={
            "start_time": time(19, 0),
            "end_time": time(7, 0),
            "is_overnight": True,
            "active": True,
        },
    )


class Migration(migrations.Migration):
    dependencies = [("attendance", "0001_initial")]

    operations = [
        migrations.RunPython(seed_standard_shifts, migrations.RunPython.noop),
    ]

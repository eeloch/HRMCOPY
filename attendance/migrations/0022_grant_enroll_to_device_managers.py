"""Enrolling people on a terminal used to be part of "manage biometric devices". It is now its own permission, so
that nobody loses what they could do today, everyone who has manage_devices (directly or through a group) is given
the new one. Untick it in Settings for anyone who should only manage the devices."""

from django.contrib.auth.management import create_permissions
from django.db import migrations


def grant(apps, schema_editor):
    app_config = apps.get_app_config("attendance")
    app_config.models_module = True  # create_permissions skips an app without models; the migration's stub has none set
    create_permissions(app_config, apps=apps, verbosity=0)
    app_config.models_module = None
    Permission = apps.get_model("auth", "Permission")
    manage = Permission.objects.filter(content_type__app_label="attendance", codename="manage_devices").first()
    enroll = Permission.objects.filter(content_type__app_label="attendance", codename="enroll_biometric_users").first()
    if manage is None or enroll is None:
        return
    for user in manage.user_set.all():
        user.user_permissions.add(enroll)
    for group in manage.group_set.all():
        group.permissions.add(enroll)


class Migration(migrations.Migration):
    dependencies = [
        ("attendance", "0021_enroll_biometric_users_permission"),
        ("auth", "0012_alter_user_first_name_max_length"),
    ]
    operations = [migrations.RunPython(grant, migrations.RunPython.noop)]

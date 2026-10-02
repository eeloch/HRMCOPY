from django.conf import settings
from django.db import migrations


def require_change_for_existing_accounts(apps, schema_editor):
    """Nobody could change their own password until now, so every existing non-superuser login is still on the
    temporary password an administrator issued. Each of them is asked to choose their own at next sign-in."""
    app_label, model_name = settings.AUTH_USER_MODEL.split(".")
    User = apps.get_model(app_label, model_name)
    AccountSecurity = apps.get_model("core", "AccountSecurity")
    for user in User.objects.filter(is_superuser=False):
        AccountSecurity.objects.update_or_create(user=user, defaults={"must_change_password": True})


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(require_change_for_existing_accounts, migrations.RunPython.noop),
    ]

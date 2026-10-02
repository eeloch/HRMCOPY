from django.conf import settings
from django.db import models


class AccountSecurity(models.Model):
    """Per-login security state. A row with must_change_password=True means the person is still on the
    temporary password an administrator issued and has to choose their own before doing anything else."""

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="account_security")
    must_change_password = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.user} (must change password: {self.must_change_password})"


def set_must_change_password(user, value):
    AccountSecurity.objects.update_or_create(user=user, defaults={"must_change_password": value})


def must_change_password(user):
    return AccountSecurity.objects.filter(user=user, must_change_password=True).exists()

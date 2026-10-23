"""Keys for outside systems (the company ERP) that read from the HRM on their own, with no person logged in.

A key is shown once when it is made and only its hash is kept, so it cannot be read back - a lost key is replaced, not
recovered. A key can be switched off at any time and can be held to a list of internet addresses."""

import hashlib
import secrets

from django.db import models

KEY_PREFIX_LENGTH = 8


def hash_key(raw_key):
    return hashlib.sha256(raw_key.encode()).hexdigest()


class IntegrationKey(models.Model):
    name = models.CharField(max_length=100, help_text="Which system uses it, e.g. 'Company ERP'.")
    prefix = models.CharField(max_length=KEY_PREFIX_LENGTH, unique=True, help_text="The start of the key, to recognise it.")
    key_hash = models.CharField(max_length=64, unique=True)
    active = models.BooleanField(default=True)
    allowed_ips = models.TextField(blank=True, help_text="Internet addresses allowed to use the key, one per line or comma separated. Empty: any address.")
    created_by = models.CharField(max_length=150, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    last_used_ip = models.CharField(max_length=64, blank=True)
    request_count = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.name} ({self.prefix}...){'' if self.active else ' - switched off'}"

    @classmethod
    def issue(cls, name, *, allowed_ips="", created_by=""):
        """Make a key. Returns (key record, the key itself) - the key is not stored and cannot be shown again."""
        raw = "rhrm_" + secrets.token_urlsafe(32)
        record = cls.objects.create(name=name, prefix=raw[5:5 + KEY_PREFIX_LENGTH], key_hash=hash_key(raw), allowed_ips=allowed_ips, created_by=created_by)
        return record, raw

    def ip_allowed(self, ip):
        allowed = [item.strip() for item in self.allowed_ips.replace(",", "\n").splitlines() if item.strip()]
        return not allowed or ip in allowed

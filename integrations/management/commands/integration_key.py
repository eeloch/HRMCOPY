"""Make, list and switch off API keys for outside systems.

  manage.py integration_key create "Company ERP" [--allow-ip 1.2.3.4]   # prints the key ONCE
  manage.py integration_key list
  manage.py integration_key revoke <prefix>
  manage.py integration_key allow-ip <prefix> "1.2.3.4, 5.6.7.8"        # empty string = any address"""

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from audit.models import AuditSeverity
from audit.services import AuditService
from integrations.models import IntegrationKey


class Command(BaseCommand):
    help = "Manage API keys for outside systems (the company ERP)."

    def add_arguments(self, parser):
        parser.add_argument("action", choices=["create", "list", "revoke", "allow-ip"])
        parser.add_argument("value", nargs="?", help="create: the system's name; revoke / allow-ip: the key prefix")
        parser.add_argument("ips", nargs="?", default=None, help="allow-ip: the addresses")
        parser.add_argument("--allow-ip", default="", help="create: addresses allowed to use the key")

    def handle(self, *args, **options):
        action, value = options["action"], options["value"]
        if action == "list":
            for key in IntegrationKey.objects.all():
                self.stdout.write(f"{key.prefix}  {key.name}  {'ON' if key.active else 'OFF'}  ips: {key.allowed_ips or 'any'}  last used: {key.last_used_at or 'never'} from {key.last_used_ip or '-'}  requests: {key.request_count}")
            return
        if not value:
            raise CommandError("Say which: the system's name (create) or the key prefix.")
        if action == "create":
            record, raw = IntegrationKey.issue(value, allowed_ips=options["allow_ip"], created_by="command line")
            AuditService.log(event_type="integrations.key_created", module="integrations", actor=None, object=record, severity=AuditSeverity.WARNING, title="API key created", description=f"API key {record.prefix}... created for {record.name}.", metadata={"prefix": record.prefix, "allowed_ips": record.allowed_ips})
            self.stdout.write(self.style.SUCCESS(f"Key for {record.name} (shown once - store it safely, it cannot be shown again):"))
            self.stdout.write(raw)
            return
        key = IntegrationKey.objects.filter(prefix=value).first()
        if key is None:
            raise CommandError(f"No key starts with {value}.")
        if action == "revoke":
            key.active, key.revoked_at = False, timezone.now()
            key.save(update_fields=["active", "revoked_at"])
            AuditService.log(event_type="integrations.key_revoked", module="integrations", actor=None, object=key, severity=AuditSeverity.WARNING, title="API key switched off", description=f"API key {key.prefix}... for {key.name} was switched off.", metadata={"prefix": key.prefix})
            self.stdout.write(f"Switched off {key}.")
        else:
            key.allowed_ips = options["ips"] or ""
            key.save(update_fields=["allowed_ips"])
            self.stdout.write(f"{key}: allowed addresses now {key.allowed_ips or 'any'}.")

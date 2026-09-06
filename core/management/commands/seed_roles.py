"""Create the standard HRM job-role Groups and give each its permissions.

Run this once after `migrate` (and again any time a new permission code is
added to a model's Meta.permissions - it's idempotent, so re-running is
always safe). It only sets up Groups; it never touches which users belong
to them - assign staff to a Group from /admin/ or the Django shell:

    user.groups.add(Group.objects.get(name="Payroll Officer"))

is_staff/is_superuser accounts are unaffected and unnecessary for any of
these roles - see the readiness audit for why superuser access should stay
reserved for actual system administrators.
"""

from django.contrib.auth.models import Group, Permission
from django.core.management.base import BaseCommand

# Each entry is (app_label, codename) so two apps can never collide on a
# codename. Every one of these must already exist as a Meta.permissions
# entry on some model - see attendance/payroll/documents/leave/ppe/meals
# models.py.
ROLES = {
    "Timekeeper": [
        ("attendance", "manage_shifts"),
        ("attendance", "manage_roster"),
        ("attendance", "review_attendanceexception"),
    ],
    "Supervisor": [
        ("attendance", "review_attendanceexception"),
        ("attendance", "review_overtime"),
        ("leave", "approve_leave"),
    ],
    "HR Manager": [
        ("documents", "view_documents"),
        ("documents", "manage_documents"),
        ("leave", "approve_leave"),
        ("attendance", "manage_roster"),
        ("attendance", "manage_shifts"),
        ("attendance", "review_attendanceexception"),
    ],
    "Payroll Officer": [
        ("payroll", "view_payroll"),
        ("payroll", "manage_payroll"),
    ],
    "PPE/Store Officer": [
        ("ppe", "record_ppe_issue"),
        ("ppe", "review_ppe_deduction"),
    ],
    "Meals Coordinator": [
        ("meals", "record_meal_operations"),
        ("meals", "review_meal_excess"),
        ("meals", "manage_meal_configuration"),
    ],
}


class Command(BaseCommand):
    help = "Create/update the standard HRM Groups (Timekeeper, Supervisor, HR Manager, ...) with their permissions."

    def handle(self, *args, **options):
        for role_name, codenames in ROLES.items():
            group, created = Group.objects.get_or_create(name=role_name)

            permissions = []
            for app_label, codename in codenames:
                try:
                    permissions.append(
                        Permission.objects.get(content_type__app_label=app_label, codename=codename)
                    )
                except Permission.DoesNotExist:
                    self.stderr.write(
                        self.style.WARNING(
                            f"Skipping {app_label}.{codename} for '{role_name}' - "
                            "no such permission exists (run migrate first?)."
                        )
                    )

            group.permissions.set(permissions)

            verb = "Created" if created else "Updated"
            self.stdout.write(self.style.SUCCESS(f"{verb} '{role_name}' with {len(permissions)} permission(s)."))

"""Delete a duplicate employee record for good, once its useful history has been moved to the record being kept.

    manage.py remove_duplicate_employee 001308 --keep 000217            # shows what it would delete, changes nothing
    manage.py remove_duplicate_employee 001308 --keep 000217 --confirm  # deletes

Permanent: there is no undo (take a backup first if in doubt). It refuses unless the duplicate is inactive and everything
that still hangs off it is generated or superseded data (roster days, shift/plan assignments, a meal allocation, a
terminal switch state, an attendance row with no punches). Terminal identities, payroll, meal tickets, punches, leave,
documents and the like must have been moved to the kept record first; if any remain the command lists them and stops."""

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from audit.services import AuditService
from employees.models import Employee

CLEARABLE = {"EmployeeRosterDay", "ShiftAssignment", "ShiftPlanAssignment", "MealTerminalUserState", "EmployeeMealEntitlement", "DailyAttendance"}
# Audit rows about the duplicate go with it only when they are its own; the merge record lives on the kept employee.
IGNORED = {"AuditEvent"}


class Command(BaseCommand):
    help = "Permanently delete a duplicate employee after its history has been moved to the kept record."

    def add_arguments(self, parser):
        parser.add_argument("duplicate", help="Staff number of the duplicate, e.g. 001308")
        parser.add_argument("--keep", required=True, help="Staff number of the record to keep, e.g. 000217")
        parser.add_argument("--confirm", action="store_true", help="Really delete (default is a dry run)")

    def handle(self, *args, **options):
        try:
            duplicate = Employee.objects.get(employee_id=options["duplicate"])
            keep = Employee.objects.get(employee_id=options["keep"])
        except Employee.DoesNotExist as error:
            raise CommandError(f"No such employee: {error}")
        if duplicate.pk == keep.pk:
            raise CommandError("The duplicate and the record to keep are the same employee.")
        if duplicate.status == "active":
            raise CommandError(f"{duplicate.employee_id} is still active: mark it inactive first.")

        clearable, blockers = {}, {}
        for relation in Employee._meta.related_objects:
            if not relation.one_to_many:
                continue
            name = relation.related_model.__name__
            rows = getattr(duplicate, relation.get_accessor_name()).all()
            count = rows.count()
            if not count or name in IGNORED:
                continue
            if name == "DailyAttendance" and self._has_punches(duplicate):
                blockers[name] = count
            elif name in CLEARABLE:
                clearable[name] = (relation, count)
            else:
                blockers[name] = count
        if blockers:
            self.stdout.write("Still attached to the duplicate - move or resolve these first:")
            for name, count in blockers.items():
                self.stdout.write(f"    {name}: {count}")
            raise CommandError("Refusing to delete.")

        self.stdout.write(f"Duplicate: {duplicate.employee_id} {duplicate.full_name} (inactive). Kept: {keep.employee_id} {keep.full_name}.")
        for name, (_relation, count) in clearable.items():
            self.stdout.write(f"    would delete {count} {name} row(s)")
        if not options["confirm"]:
            self.stdout.write("DRY RUN - nothing deleted. Add --confirm to delete permanently.")
            return
        summary = {name: count for name, (_relation, count) in clearable.items()}
        with transaction.atomic():
            for name, (relation, _count) in clearable.items():
                getattr(duplicate, relation.get_accessor_name()).all().delete()
            AuditService.log(
                event_type="employees.duplicate_deleted", module="employees", employee=keep,
                description=f"Duplicate {duplicate.employee_id} {duplicate.full_name} deleted; its history had been moved to {keep.employee_id}.",
                metadata={"deleted_staff_number": duplicate.employee_id, "kept": keep.employee_id, "rows_deleted": summary},
            )
            duplicate.delete()
        self.stdout.write(f"DELETED {options['duplicate']} and {sum(summary.values())} leftover row(s).")

    @staticmethod
    def _has_punches(employee):
        from attendance.models import AttendanceEvent

        return AttendanceEvent.objects.filter(employee=employee).exists()

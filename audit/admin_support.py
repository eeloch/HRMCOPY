"""Shared building blocks for the Django admin of the money apps (payroll, advances, deferred funds,
bonuses, meals, notifications, audit).

The admin is where a superuser goes to find and correct wrong entries, so these helpers keep every list
readable (staff number, name, naira amounts, status badges) and keep every correction safe:

* nothing that is money is bulk-deleted;
* corrections go through the app's own services (never re-implemented maths) and every one leaves an
  AuditEvent, marked ``via: django-admin``;
* a corrective action first shows a confirmation page listing exactly which rows it will touch, and asks
  for a reason where the service wants one.
"""

import json
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from django.contrib import admin, messages
from django.contrib.admin.helpers import ACTION_CHECKBOX_NAME
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils.html import format_html, format_html_join

from audit.models import AuditSeverity
from audit.services import AuditService

CONFIRM_TEMPLATE = "admin/audit/confirm_action.html"
LIST_LIMIT = 100  # rows named on a confirmation page / in the messages before "and N more"


# ---------------------------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------------------------
def naira(value, signed=False):
    """65000 -> 'N 65,000.00'. With signed=True a plus sign is shown for money in ('+N 6,500.00')."""
    if value is None or value == "":
        return "-"
    try:
        amount = Decimal(str(value))
    except InvalidOperation:
        return str(value)
    sign = "-" if amount < 0 else ("+" if signed and amount > 0 else "")
    return f"{sign}N {abs(amount):,.2f}"


def month_label(year, month):
    """(2026, 9) -> 'Sep 2026'. Tolerates a month that is not 1-12 (some columns have no validator)."""
    try:
        return date(int(year), int(month), 1).strftime("%b %Y")
    except (TypeError, ValueError):
        return f"{year}-{month}"


_TONES = {
    "green": ("#1f6b35", "#e3f4e8"),
    "amber": ("#7a4b00", "#fdf0d5"),
    "red": ("#8a1c1c", "#fbe4e4"),
    "blue": ("#1b4f8a", "#e2eefb"),
    "grey": ("#454545", "#ececec"),
}


def badge(label, tone="grey"):
    """A small coloured pill. Tones: green (done/good), amber (waiting), red (declined/cancelled/bad), blue (in progress), grey."""
    color, background = _TONES.get(tone, _TONES["grey"])
    return format_html(
        '<span style="display:inline-block;padding:1px 9px;border-radius:10px;font-size:11px;font-weight:600;white-space:nowrap;color:{};background:{}">{}</span>',
        color,
        background,
        label,
    )


SEVERITY_TONES = {"info": "blue", "success": "green", "warning": "amber", "error": "red"}


def pretty_json(data):
    """A JSON blob as a small key/value table (nested values as indented JSON) instead of one long line."""
    if not data:
        return "-"
    if not isinstance(data, dict):
        return format_html("<pre style=\"margin:0\">{}</pre>", json.dumps(data, indent=2, ensure_ascii=False, default=str))

    def cell(value):
        if isinstance(value, (dict, list)):
            return format_html("<pre style=\"margin:0\">{}</pre>", json.dumps(value, indent=2, ensure_ascii=False, default=str))
        return "-" if value in (None, "") else str(value)

    rows = format_html_join(
        "",
        '<tr><th style="text-align:left;vertical-align:top;padding:2px 14px 2px 0;white-space:nowrap">{}</th><td style="padding:2px 0">{}</td></tr>',
        ((key, cell(data[key])) for key in sorted(data, key=str)),
    )
    return format_html("<table>{}</table>", rows)


# ---------------------------------------------------------------------------------------------
# Employee columns / search
# ---------------------------------------------------------------------------------------------
def employee_columns(path="employee"):
    """Three list_display callables (staff number, full name, department) for a row that concerns an employee.

    ``path`` is how the row reaches the employee, e.g. "employee", "payroll__employee", "account__employee".
    Use as ``staff_number, employee_name, department = employee_columns("payroll__employee")`` in the class body.
    """

    def employee_of(obj):
        for part in path.split("__"):
            obj = getattr(obj, part, None)
            if obj is None:
                return None
        return obj

    @admin.display(description="Staff No.", ordering=f"{path}__employee_id")
    def staff_number(self, obj):
        employee = employee_of(obj)
        return employee.employee_id if employee else "-"

    @admin.display(description="Employee", ordering=f"{path}__first_name")
    def employee_name(self, obj):
        employee = employee_of(obj)
        return employee.full_name if employee else "-"

    @admin.display(description="Department", ordering=f"{path}__department__name")
    def department(self, obj):
        employee = employee_of(obj)
        return employee.department.name if employee and employee.department_id else "-"

    return staff_number, employee_name, department


def employee_search_fields(path="employee"):
    """Search by staff number and first / middle / last name."""
    return (f"{path}__employee_id", f"{path}__first_name", f"{path}__middle_name", f"{path}__last_name")


# ---------------------------------------------------------------------------------------------
# Safety mixins
# ---------------------------------------------------------------------------------------------
class NoBulkDeleteMixin:
    """No 'Delete selected' on a money model. A single row may only be deleted where ``can_delete_object`` says so."""

    def can_delete_object(self, request, obj):
        return False

    def has_delete_permission(self, request, obj=None):
        if obj is None:
            return False  # also removes the site-wide delete_selected action from the list
        return super().has_delete_permission(request, obj) and self.can_delete_object(request, obj)


class ReadOnlyAdminMixin(NoBulkDeleteMixin):
    """The rows can be looked at (and acted on with the admin actions) but not added, edited or deleted here."""

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


def perm_checker(codename):
    """``has_approve_permission = perm_checker("advances.approve_salary_advance")`` makes an action follow the app's own permission."""

    def check(self, request):
        return request.user.has_perm(codename)

    return check


def any_perm_checker(*codenames):
    def check(self, request):
        return any(request.user.has_perm(codename) for codename in codenames)

    return check


# ---------------------------------------------------------------------------------------------
# Audit trail
# ---------------------------------------------------------------------------------------------
def log_admin_event(request, *, module, event_type, title, description="", object=None, employee=None, severity=AuditSeverity.INFO, metadata=None):
    """One AuditEvent for something done from the admin: the actor is the logged-in user and ``via`` says where it came from."""
    return AuditService.log(
        event_type=event_type,
        module=module,
        actor=request.user,
        employee=employee,
        object=object,
        severity=severity,
        title=title,
        description=description,
        metadata={"via": "django-admin", **(metadata or {})},
    )


def plain(value):
    """Something an AuditEvent's JSON metadata can hold."""
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, models.Model):
        return value.pk
    if isinstance(value, (list, tuple, set, models.QuerySet)):
        return [plain(item) for item in value]
    if isinstance(value, dict):
        return {str(key): plain(item) for key, item in value.items()}
    return str(value)


class AdminAuditMixin:
    """Every add / change / delete made through the admin form is written to the audit trail with before and after values.

    Set ``audit_module`` on the admin. Override ``audit_employee`` if the employee is not ``obj.employee``.
    """

    audit_module = "admin"

    def audit_employee(self, obj):
        return getattr(obj, "employee", None) if hasattr(obj, "employee_id") else None

    def _audit_name(self):
        return self.model._meta.verbose_name

    def save_model(self, request, obj, form, change):
        before = {name: plain(form.initial.get(name)) for name in form.changed_data} if change else {}
        super().save_model(request, obj, form, change)
        if change and not form.changed_data:
            return
        after = {name: plain(form.cleaned_data.get(name)) for name in form.changed_data}
        noun = self._audit_name()
        log_admin_event(
            request,
            module=self.audit_module,
            event_type=f"admin.{self.model._meta.model_name}.{'changed' if change else 'added'}",
            title=f"{noun.capitalize()} {'changed' if change else 'added'} in Django admin",
            description=f"{obj} ({'fields changed: ' + ', '.join(sorted(after)) if change else 'new record'}).",
            object=obj,
            employee=self.audit_employee(obj),
            severity=AuditSeverity.WARNING,
            metadata={"id": obj.pk, "before": before, "after": after},
        )

    def delete_model(self, request, obj):
        label, pk, employee = str(obj), obj.pk, self.audit_employee(obj)
        super().delete_model(request, obj)
        log_admin_event(
            request,
            module=self.audit_module,
            event_type=f"admin.{self.model._meta.model_name}.deleted",
            title=f"{self._audit_name().capitalize()} deleted in Django admin",
            description=f"{label} was deleted.",
            employee=employee,
            severity=AuditSeverity.WARNING,
            metadata={"id": pk, "record": label},
        )


# ---------------------------------------------------------------------------------------------
# Corrective actions: confirm, run through a service, report, audit
# ---------------------------------------------------------------------------------------------
def _error_text(error):
    if isinstance(error, ValidationError):
        return " ".join(error.messages)
    return str(error)


def run_bulk(modeladmin, request, queryset, *, module, name, verb, fn, reason="", severity=AuditSeverity.INFO):
    """Apply ``fn(obj)`` to every selected row. A row the service refuses (it raises ValueError) is skipped, not fatal.

    ``fn`` may return a short string describing what happened to that row. Shows the outcome with messages and
    writes ONE summary AuditEvent (the services write their own per-row events too).
    """
    done, skipped = [], []
    for obj in queryset:
        try:
            with transaction.atomic():
                detail = fn(obj)
            done.append((obj, detail))
        except (ValueError, ValidationError) as error:
            skipped.append((obj, _error_text(error)))

    if done:
        modeladmin.message_user(request, f"{verb}: {len(done)} of {len(done) + len(skipped)} selected.", messages.SUCCESS)
        details = [f"{obj}: {detail}" for obj, detail in done if detail]
        for line in details[:LIST_LIMIT]:
            modeladmin.message_user(request, line, messages.INFO)
    else:
        modeladmin.message_user(request, f"Nothing was done ({verb.lower()}): none of the {len(skipped)} selected could be processed.", messages.WARNING)
    for obj, why in skipped[:10]:
        modeladmin.message_user(request, f"Skipped {obj}: {why}", messages.WARNING)
    if len(skipped) > 10:
        modeladmin.message_user(request, f"...and {len(skipped) - 10} more skipped.", messages.WARNING)

    log_admin_event(
        request,
        module=module,
        event_type=f"admin.{module}.{name}",
        title=f"{verb} from Django admin",
        description=f"{verb}: {len(done)} done, {len(skipped)} skipped.",
        severity=severity if done else AuditSeverity.WARNING,
        metadata={
            "action": name,
            "reason": reason,
            "done": [{"id": obj.pk, "record": str(obj), "detail": detail} for obj, detail in done[:200]],
            "skipped": [{"id": obj.pk, "record": str(obj), "why": why} for obj, why in skipped[:200]],
            "done_count": len(done),
            "skipped_count": len(skipped),
        },
    )
    return done, skipped


def confirm_and_run(modeladmin, request, queryset, *, action, title, intro, submit_label, run, reason=None, reason_label="Reason", reason_help=""):
    """Two steps in one admin action: a confirmation page naming the rows, then ``run(reason_text)`` on the confirmed POST.

    ``reason`` is None (no box), "optional" or "required". Returns the confirmation page (a response) or None when done,
    in which case Django redirects back to the list showing the messages ``run`` left.
    """
    error = ""
    if request.POST.get("confirm") == "yes":
        text = (request.POST.get("reason") or "").strip()
        if reason == "required" and not text:
            error = f"{reason_label} is required."
        else:
            run(text)
            return None

    total = queryset.count()
    lines = [str(obj) for obj in queryset[:LIST_LIMIT]]
    opts = modeladmin.model._meta
    context = {
        **modeladmin.admin_site.each_context(request),
        "title": title,
        "opts": opts,
        "intro": intro,
        "lines": lines,
        "more": max(total - len(lines), 0),
        "total": total,
        "action": action,
        "selected": request.POST.getlist(ACTION_CHECKBOX_NAME),
        "select_across": request.POST.get("select_across", "0"),
        "reason_mode": reason or "none",
        "reason_label": reason_label,
        "reason_help": reason_help,
        "reason_value": request.POST.get("reason", ""),
        "error": error,
        "submit_label": submit_label,
        "cancel_url": reverse(f"admin:{opts.app_label}_{opts.model_name}_changelist"),
    }
    return TemplateResponse(request, CONFIRM_TEMPLATE, context)


def service_action(*, name, label, perm, module, verb, call, intro, reason=None, reason_label="Reason", reason_help="", submit_label=None, severity=AuditSeverity.INFO):
    """Build a confirm-then-run admin action around an existing service call.

    ``call(obj, actor, reason_text)`` runs the service for one row (raising ValueError refuses that row) and may
    return a short result string. ``perm`` is the short permission name: the admin must define ``has_<perm>_permission``
    (see ``perm_checker``) so the action follows the same permission the API uses.
    """

    def action(modeladmin, request, queryset):
        def run(text):
            run_bulk(modeladmin, request, queryset, module=module, name=name, verb=verb, reason=text, severity=severity, fn=lambda obj: call(obj, request.user, text))

        return confirm_and_run(
            modeladmin,
            request,
            queryset,
            action=name,
            title=label,
            intro=intro,
            submit_label=submit_label or label,
            run=run,
            reason=reason,
            reason_label=reason_label,
            reason_help=reason_help,
        )

    action.__name__ = name
    return admin.action(description=label, permissions=[perm])(action)

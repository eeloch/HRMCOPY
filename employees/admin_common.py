"""Building blocks shared by the Django admin pages of the employee-centred apps
(employees, leave, offences, ppe, documents, accommodation).

The admin is where the superuser goes to find and correct wrong entries, so every list should say WHO a row is
about (staff number + name, never "object (771)"), be searchable by staff number and any part of the name, and never
run one query per row. Bulk actions must go through the same service functions the API uses, write an audit event and
ask for confirmation first.

Nothing here touches models, migrations or business rules.
"""

from django.contrib import admin, messages
from django.contrib.admin import helpers
from django.template import engines
from django.template.response import TemplateResponse
from django.utils.html import format_html

from audit.models import AuditSeverity
from audit.services import AuditService

# The search box on every employee-related list: staff number and first / middle / last name.
EMPLOYEE_SEARCH_FIELDS = (
    "employee__employee_id",
    "employee__first_name",
    "employee__middle_name",
    "employee__last_name",
)

# (text colour, background) - readable on both the light and the dark admin theme.
BADGE_TONES = {
    "green": ("#0f5132", "#d1e7dd"),
    "amber": ("#664d03", "#fff3cd"),
    "red": ("#842029", "#f8d7da"),
    "blue": ("#084298", "#cfe2ff"),
    "grey": ("#41464b", "#e2e3e5"),
}


def badge(text, tone="grey"):
    """A small coloured pill."""
    colour, background = BADGE_TONES.get(tone, BADGE_TONES["grey"])
    return format_html(
        '<span style="display:inline-block;padding:1px 9px;border-radius:10px;font-size:11px;font-weight:600;'
        'white-space:nowrap;color:{};background:{}">{}</span>',
        colour,
        background,
        text,
    )


def status_badge(obj, tones, field="status"):
    """A pill for a choices field: the human label, coloured by `tones` {value: tone}."""
    value = getattr(obj, field)
    return badge(getattr(obj, f"get_{field}_display")(), tones.get(value, "grey"))


class HRAdminMixin:
    """Defaults every list in this slice shares: 50 per page, save buttons on top, no bulk hard delete when
    `no_bulk_delete` is set (evidence records and anything whose deletion cascades)."""

    list_per_page = 50
    save_on_top = True
    no_bulk_delete = False

    def get_actions(self, request):
        actions = super().get_actions(request)
        if self.no_bulk_delete:
            actions.pop("delete_selected", None)
        return actions


class EmployeeColumnsMixin:
    """Staff number, name and department as their own columns for any model with an `employee` foreign key.
    Pair with list_select_related = ("employee", "employee__department")."""

    @admin.display(description="Staff No.", ordering="employee__employee_id")
    def staff_number(self, obj):
        return obj.employee.employee_id

    @admin.display(description="Employee", ordering="employee__first_name")
    def employee_name(self, obj):
        return obj.employee.full_name

    @admin.display(description="Department", ordering="employee__department__name")
    def department(self, obj):
        department = obj.employee.department
        return department.name if department else "-"


def adopt(obj, saved):
    """Make the unsaved form instance `obj` the same row as `saved`, a record a service function just created.

    Lets an admin "add" page go through the service (audit, derived fields) instead of a raw insert while the
    admin machinery - which keeps using `obj` afterwards for the success message and the history entry - still
    works."""
    for field in obj._meta.concrete_fields:
        setattr(obj, field.attname, getattr(saved, field.attname))
    obj._state.adding = False
    obj._state.db = saved._state.db
    return obj


class AuditedAdminMixin:
    """Every edit or deletion made on the admin form leaves an audit event (field NAMES only - never the values,
    so salary and bank details cannot leak into the log)."""

    audit_module = ""
    audit_prefix = ""

    def audit_employee(self, obj):
        return getattr(obj, "employee", None)

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        if change and not form.changed_data:
            return
        verb = "updated" if change else "created"
        changed = sorted(form.changed_data) if change else []
        AuditService.log(
            event_type=f"{self.audit_prefix}.admin_{verb}",
            module=self.audit_module,
            employee=self.audit_employee(obj),
            actor=request.user,
            object=obj,
            severity=AuditSeverity.INFO,
            title=f"{self.model._meta.verbose_name.capitalize()} {verb} in the admin",
            description=f"{obj} was {verb} in the Django admin." + (f" Changed: {', '.join(changed)}." if changed else ""),
            metadata={"via": "admin", "changed_fields": changed},
        )

    def delete_model(self, request, obj):
        AuditService.log(
            event_type=f"{self.audit_prefix}.admin_deleted",
            module=self.audit_module,
            employee=self.audit_employee(obj),
            actor=request.user,
            object=obj,
            severity=AuditSeverity.WARNING,
            title=f"{self.model._meta.verbose_name.capitalize()} deleted in the admin",
            description=f"{obj} was deleted in the Django admin.",
            metadata={"via": "admin", "deleted_id": obj.pk, "was": str(obj)},
        )
        super().delete_model(request, obj)


def log_action(request, *, event_type, module, employee=None, obj=None, severity=AuditSeverity.INFO, title, description, metadata=None):
    """Audit event for a bulk admin action (one per affected record so each employee's history shows it)."""
    return AuditService.log(
        event_type=event_type,
        module=module,
        employee=employee,
        actor=request.user,
        object=obj,
        severity=severity,
        title=title,
        description=description,
        metadata={"via": "admin", **(metadata or {})},
    )


def report(modeladmin, request, done, skipped=(), errors=(), verb="updated"):
    """One summary message per action: what changed, what was left alone and why, what failed."""
    if done:
        modeladmin.message_user(request, f"{len(done)} {verb}: {_names(done)}.", messages.SUCCESS)
    if skipped:
        modeladmin.message_user(request, f"{len(skipped)} left as they were: {_names(skipped)}.", messages.WARNING)
    for error in list(errors)[:10]:
        modeladmin.message_user(request, error, messages.ERROR)
    if len(errors) > 10:
        modeladmin.message_user(request, f"... and {len(errors) - 10} more problems.", messages.ERROR)
    if not (done or skipped or errors):
        modeladmin.message_user(request, "Nothing to do.", messages.INFO)


def _names(items, limit=8):
    items = [str(item) for item in items]
    text = "; ".join(items[:limit])
    return text + (f"; and {len(items) - limit} more" if len(items) > limit else "")


# --------------------------------------------------------------------------------------------------------------------
# Confirmation step for bulk actions.
#
# A changelist action posts straight away. For anything that changes status, revokes something or needs a reason we
# show a page first: what is about to happen, to whom, the reason / choice to fill in, and a Confirm button that
# re-posts the very same action with confirm_step=1.

_CONFIRM_TEMPLATE = None

_CONFIRM_SOURCE = """{% extends "admin/base_site.html" %}
{% block breadcrumbs %}<div class="breadcrumbs"><a href="{% url 'admin:index' %}">Home</a> &rsaquo; <a href="{{ cancel_url }}">{{ opts.verbose_name_plural|capfirst }}</a> &rsaquo; {{ title }}</div>{% endblock %}
{% block content %}
<h1>{{ title }}</h1>
<form method="post" action="{{ form_url }}">{% csrf_token %}
  <p style="font-size:14px">{{ question }}</p>
  {% if notes %}<ul>{% for note in notes %}<li>{{ note }}</li>{% endfor %}</ul>{% endif %}
  <h3>{{ count }} selected</h3>
  <ul>{% for label in labels %}<li>{{ label }}</li>{% endfor %}{% if more %}<li>... and {{ more }} more</li>{% endif %}</ul>
  {% for field in fields %}
    <p>
      <label for="confirm_{{ field.name }}" style="font-weight:600">{{ field.label }}{% if field.required %} *{% endif %}</label><br>
      {% if field.kind == "textarea" %}
        <textarea id="confirm_{{ field.name }}" name="confirm_{{ field.name }}" rows="3" style="width:32em;max-width:100%">{{ field.value }}</textarea>
      {% elif field.kind == "select" %}
        <select id="confirm_{{ field.name }}" name="confirm_{{ field.name }}">
          <option value="">---------</option>
          {% for value, text in field.choices %}<option value="{{ value }}"{% if value == field.value %} selected{% endif %}>{{ text }}</option>{% endfor %}
        </select>
      {% else %}
        <input id="confirm_{{ field.name }}" name="confirm_{{ field.name }}" value="{{ field.value }}" style="width:32em;max-width:100%">
      {% endif %}
      {% if field.error %}<br><span class="errornote" style="color:#ba2121">{{ field.error }}</span>{% endif %}
    </p>
  {% endfor %}
  {% for pk in selected %}<input type="hidden" name="{{ checkbox_name }}" value="{{ pk }}">{% endfor %}
  <input type="hidden" name="action" value="{{ action }}">
  <input type="hidden" name="select_across" value="{{ select_across }}">
  <input type="hidden" name="index" value="0">
  <input type="hidden" name="confirm_step" value="1">
  <input type="submit" value="{{ confirm_label }}" class="default"{% if danger %} style="background:#ba2121"{% endif %}>
  <a href="{{ cancel_url }}" class="button cancel-link">No, take me back</a>
</form>
{% endblock %}
"""


class ConfirmField:
    """One input on the confirmation page: kind is text, textarea or select (choices = [(value, label), ...])."""

    def __init__(self, name, label, kind="text", required=False, choices=()):
        self.name, self.label, self.kind, self.required = name, label, kind, required
        self.choices = [(str(value), text) for value, text in choices]
        self.value, self.error = "", ""


def confirm_step(modeladmin, request, queryset, *, action, title, question, notes=(), fields=(), confirm_label="Confirm", danger=False):
    """Returns (values, response). When `response` is not None the caller returns it straight away (the page to
    confirm, or the same page again with a validation error); otherwise the user confirmed and `values` holds what
    they typed, keyed by field name."""
    global _CONFIRM_TEMPLATE
    fields = list(fields)
    if request.POST.get("confirm_step") == "1":
        values, invalid = {}, False
        for field in fields:
            field.value = (request.POST.get(f"confirm_{field.name}") or "").strip()
            values[field.name] = field.value
            if field.required and not field.value:
                field.error, invalid = "This is required.", True
            elif field.value and field.kind == "select" and field.value not in {str(value) for value, _label in field.choices}:
                field.error, invalid = "Choose one of the options.", True
        if not invalid:
            return values, None

    if _CONFIRM_TEMPLATE is None:
        _CONFIRM_TEMPLATE = engines["django"].from_string(_CONFIRM_SOURCE)
    selected = request.POST.getlist(helpers.ACTION_CHECKBOX_NAME)
    shown = list(queryset[:25])
    total = queryset.count()
    context = {
        **modeladmin.admin_site.each_context(request),
        "title": title,
        "opts": modeladmin.model._meta,
        "question": question,
        "notes": list(notes),
        "fields": fields,
        "labels": [str(obj) for obj in shown],
        "count": total,
        "more": max(total - len(shown), 0),
        "selected": selected,
        "select_across": "1" if request.POST.get("select_across") == "1" else "0",
        "checkbox_name": helpers.ACTION_CHECKBOX_NAME,
        "action": action,
        "form_url": request.get_full_path(),
        "cancel_url": request.get_full_path(),
        "confirm_label": confirm_label,
        "danger": danger,
    }
    return None, TemplateResponse(request, _CONFIRM_TEMPLATE, context)

"""Django admin for the attendance records a superuser has to look up and correct.

Covers AttendanceEvent (raw punches), DailyAttendance, AttendanceException, OvertimeRecord and EmployeeRosterDay.

Design rules, so this stays safe to use under pressure:

* Every row says who it is about (staff number + full name + department) and can be found by staff number or any
  part of the name.
* Data the system derives (raw punches, calculated minutes, review decisions, money snapshots) is read-only in the
  change forms. It is changed through the list actions, and every action calls the same service / serializer the
  HRM screens use - no attendance, payroll or overtime maths is re-implemented here.
* Each action opens a confirmation page first (showing what will change and what will be skipped), reports what it
  did through the admin messages, and writes an audit event (audit.services.AuditService) naming the superuser.
* Raw punches are evidence: they cannot be added, edited or deleted from here. Overtime records are financial
  records: they can only be reviewed, never edited or deleted.
"""

import json
import logging
from collections import Counter
from datetime import timedelta
from functools import lru_cache, reduce

from django import forms
from django.contrib import admin, messages
from django.contrib.admin import helpers
from django.core.exceptions import ObjectDoesNotExist
from django.db import transaction
from django.template import engines
from django.template.response import TemplateResponse
from django.urls import NoReverseMatch, reverse
from django.utils import timezone
from django.utils.html import format_html, format_html_join
from django.utils.safestring import mark_safe
from rest_framework.exceptions import ValidationError as APIValidationError

from attendance.models import (
    AttendanceEvent,
    AttendanceException,
    DailyAttendance,
    EmployeeRosterDay,
    OvertimeRecord,
    OvertimePaymentStatus,
    OvertimeStatus,
    RosterDaySource,
    RosterDayStatus,
    Shift,
)
from attendance.serializers import ExceptionDecisionSerializer
from attendance.services.leave import approved_leave_employee_ids
from attendance.services.overtime import approve_overtime, mark_overtime_paid, reject_overtime
from attendance.services.processing import CAPTURE_WINDOW_HOURS, process_employee_attendance, shift_schedule
from attendance.services.roster import get_employee_roster_day
from attendance.views.exceptions import ExceptionDecisionAPIView
from audit.models import AuditSeverity
from audit.services import AuditService
from meals.models import MealAbsencePenalty, MealAbsencePenaltyStatus
from meals.services import MealService
from payroll.models import PayrollPeriod
from payroll.services.attendance import LOCKED_PERIOD_STATUSES

logger = logging.getLogger(__name__)

# The most rows one action will touch, so a "select all 40,000" click cannot tie the server up.
MAX_ACTION_ROWS = 1000
PREVIEW_LIMIT = 15
EMPLOYEE_SEARCH = ("employee_id", "first_name", "middle_name", "last_name")


# ---------------------------------------------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------------------------------------------

# (text colour, background): readable on both the light and the dark admin theme.
TONES = {
    "green": ("#0f5132", "#d1e7dd"),
    "amber": ("#664d03", "#fff3cd"),
    "red": ("#842029", "#f8d7da"),
    "blue": ("#084298", "#cfe2ff"),
    "purple": ("#432874", "#e5dbf7"),
    "grey": ("#41464b", "#e2e3e5"),
}


def badge(text, tone="grey"):
    colour, background = TONES.get(tone, TONES["grey"])
    return format_html(
        '<span style="display:inline-block;padding:1px 9px;border-radius:10px;font-size:11px;font-weight:600;'
        'white-space:nowrap;color:{};background:{}">{}</span>',
        colour,
        background,
        text,
    )


def choice_badge(obj, field, tones):
    return badge(getattr(obj, f"get_{field}_display")(), tones.get(getattr(obj, field), "grey"))


ATTENDANCE_TONES = {"present": "green", "late": "amber", "absent": "red", "leave": "blue", "incomplete": "purple"}
EXCEPTION_STATUS_TONES = {"pending": "amber", "approved": "green", "waived": "grey", "held": "purple"}
EXCEPTION_TYPE_TONES = {
    "absence": "red",
    "late": "amber",
    "early_departure": "amber",
    "missing_clock_in": "purple",
    "missing_clock_out": "purple",
    "hostel_violation": "blue",
    "leave_punch_conflict": "blue",
}
OVERTIME_TONES = {"pending": "amber", "approved": "green", "rejected": "red"}
PAYMENT_TONES = {"pending": "amber", "paid": "green"}
ROSTER_STATUS_TONES = {"work": "green", "rest": "grey"}
ROSTER_SOURCE_TONES = {"generated": "grey", "manual": "blue", "override": "amber"}


def fmt_date(value):
    return value.strftime("%a %d %b %Y") if value else None


def fmt_datetime(value):
    return timezone.localtime(value).strftime("%a %d %b %Y  %H:%M:%S") if value else None


def fmt_time(value, work_date=None):
    """Local clock time; when it falls on another calendar day than the work date (night shifts), the day is shown too."""
    if not value:
        return None
    local = timezone.localtime(value)
    if work_date is not None and local.date() != work_date:
        return local.strftime("%d %b %H:%M")
    return local.strftime("%H:%M")


def fmt_hours(minutes):
    return f"{minutes // 60}h {minutes % 60:02d}m" if minutes else None


def minutes_cell(minutes, tone="red"):
    """A minutes value that is highlighted only when it is not zero."""
    if not minutes:
        return None
    colour, _ = TONES[tone]
    return format_html('<b style="color:{}">{}</b>', colour, minutes)


def employee_of(obj, path):
    return reduce(getattr, path.split("__"), obj)


def search_fields_for(prefix):
    return tuple(f"{prefix}__{name}" for name in EMPLOYEE_SEARCH)


def admin_link(obj, text=None):
    if obj is None:
        return None
    try:
        url = reverse(f"admin:{obj._meta.app_label}_{obj._meta.model_name}_change", args=[obj.pk])
    except NoReverseMatch:
        return text or str(obj)
    return format_html('<a href="{}">{}</a>', url, text or str(obj))


# The three columns every list here starts with. `path` is how the row reaches its Employee.
def col_staff(path="employee"):
    @admin.display(description="Staff No.", ordering=f"{path}__employee_id")
    def staff_number(self, obj):
        return employee_of(obj, path).employee_id

    return staff_number


def col_name(path="employee"):
    @admin.display(description="Employee", ordering=f"{path}__first_name")
    def employee_name(self, obj):
        return employee_of(obj, path).full_name

    return employee_name


def col_department(path="employee"):
    @admin.display(description="Department", ordering=f"{path}__department__name")
    def department(self, obj):
        dept = employee_of(obj, path).department
        return dept.name if dept else None

    return department


# ---------------------------------------------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------------------------------------------

def audit(request, event_type, title, description="", *, employee=None, obj=None, severity=AuditSeverity.INFO, metadata=None):
    return AuditService.log(
        event_type=event_type,
        module="attendance",
        employee=employee,
        actor=request.user,
        object=obj,
        severity=severity,
        title=title,
        description=description,
        metadata={"via": "django_admin", **(metadata or {})},
    )


def _plain(value):
    if value is None:
        return None
    if isinstance(value, (bool, int, float, str)):
        return value
    return str(value)


class AuditedAdminMixin:
    """Adds the HRM audit trail to hand edits and deletes made on the admin change form, and the shared defaults."""

    list_per_page = 50
    save_on_top = True
    empty_value_display = "-"
    audit_key = "record"  # event types are attendance.<audit_key>_edited / _created / _deleted
    audit_noun = "record"
    employee_path = "employee"

    def _audit_employee(self, obj):
        try:
            return employee_of(obj, self.employee_path)
        except ObjectDoesNotExist:
            return None

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        changes = {
            name: {"from": _plain(form.initial.get(name)), "to": _plain(form.cleaned_data.get(name))}
            for name in form.changed_data
        }
        audit(
            request,
            f"attendance.{self.audit_key}_{'edited' if change else 'created'}",
            f"Attendance {self.audit_noun} {'edited' if change else 'created'} in the admin",
            f"{request.user.get_username()} {'edited' if change else 'created'} {self.audit_noun} {obj}.",
            employee=self._audit_employee(obj),
            obj=obj,
            severity=AuditSeverity.WARNING if change else AuditSeverity.INFO,
            metadata={"id": obj.pk, "changes": changes},
        )

    def delete_model(self, request, obj):
        label, pk, employee = str(obj), obj.pk, self._audit_employee(obj)
        super().delete_model(request, obj)
        audit(
            request,
            f"attendance.{self.audit_key}_deleted",
            f"Attendance {self.audit_noun} deleted in the admin",
            f"{request.user.get_username()} deleted {self.audit_noun} {label}.",
            employee=employee,
            severity=AuditSeverity.WARNING,
            metadata={"id": pk, "label": label},
        )

    def delete_queryset(self, request, queryset):
        rows = list(queryset[:200])
        total = queryset.count()
        labels = [f"#{row.pk} {row}" for row in rows]
        super().delete_queryset(request, queryset)
        audit(
            request,
            f"attendance.{self.audit_key}_bulk_deleted",
            f"{total} attendance {self.audit_noun}(s) deleted in the admin",
            f"{request.user.get_username()} deleted {total} {self.audit_noun}(s).",
            severity=AuditSeverity.WARNING,
            metadata={"count": total, "labels": labels, "truncated": total > len(labels)},
        )


# ---------------------------------------------------------------------------------------------------------------
# Confirmation-page machinery for the list actions
# ---------------------------------------------------------------------------------------------------------------

CONFIRM_TEMPLATE = """{% extends "admin/base_site.html" %}
{% block breadcrumbs %}
<div class="breadcrumbs">
<a href="{% url 'admin:index' %}">Home</a>
&rsaquo; <a href="{% url 'admin:app_list' app_label=opts.app_label %}">{{ opts.app_config.verbose_name }}</a>
&rsaquo; <a href="{{ changelist_url }}">{{ opts.verbose_name_plural|capfirst }}</a>
&rsaquo; {{ title }}
</div>
{% endblock %}
{% block content %}
<div id="content-main">
<p>{{ intro }}</p>
{% if warning %}<ul class="messagelist"><li class="warning">{{ warning }}</li></ul>{% endif %}
<h2>{{ eligible_count }} row(s) will be changed</h2>
<ul>
{% for line in preview %}<li>{{ line }}</li>{% endfor %}
{% if more %}<li>... and {{ more }} more</li>{% endif %}
</ul>
{% if skipped_count %}
<h3>{{ skipped_count }} selected row(s) will be left alone</h3>
<ul>
{% for line in skipped %}<li>{{ line }}</li>{% endfor %}
{% if skipped_more %}<li>... and {{ skipped_more }} more</li>{% endif %}
</ul>
{% endif %}
<form method="post">
{% csrf_token %}
<input type="hidden" name="action" value="{{ action }}">
<input type="hidden" name="confirm" value="yes">
<input type="hidden" name="select_across" value="{{ select_across }}">
{% for pk in selected %}<input type="hidden" name="_selected_action" value="{{ pk }}">{% endfor %}
{{ form.as_div }}
<div class="submit-row">
<input type="submit" class="default" value="{{ submit }}">
<a href="{{ changelist_url }}" class="button cancel-link">No, take me back</a>
</div>
</form>
</div>
{% endblock %}
"""


@lru_cache(maxsize=1)
def _confirm_template():
    return engines["django"].from_string(CONFIRM_TEMPLATE)


class ReasonForm(forms.Form):
    reason = forms.CharField(
        label="Reason / comment",
        required=False,
        widget=forms.Textarea(attrs={"rows": 3, "cols": 70}),
    )

    def __init__(self, *args, reason_required=False, reason_help="", **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["reason"].required = reason_required
        if reason_help:
            self.fields["reason"].help_text = reason_help


class WorkDayForm(ReasonForm):
    shift = forms.ModelChoiceField(label="Shift", queryset=Shift.objects.none(), help_text="The shift the person works on these days.")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["shift"].queryset = Shift.objects.filter(active=True).order_by("name")


class ActionRefused(Exception):
    """A row that must be left alone (the reason is shown to the superuser), as opposed to a failure."""


def explain(error):
    if isinstance(error, APIValidationError):
        detail = error.detail

        def flatten(value):
            if isinstance(value, dict):
                return [text for item in value.values() for text in flatten(item)]
            if isinstance(value, (list, tuple)):
                return [text for item in value for text in flatten(item)]
            return [str(value)]

        return " ".join(flatten(detail))
    return str(error)


def _short_list(pairs, limit=5):
    shown = [f"{obj} ({why})" for obj, why in pairs[:limit]]
    text = "; ".join(shown)
    if len(pairs) > limit:
        text += f"; and {len(pairs) - limit} more"
    return text


class ConfirmedActionsMixin:
    """Runs an action behind a confirmation page and reports the outcome consistently."""

    def _confirmed_action(
        self,
        request,
        queryset,
        *,
        action,
        title,
        intro,
        eligible,
        apply,
        form_class=ReasonForm,
        form_kwargs=None,
        warning="",
        submit="Yes, go ahead",
    ):
        total = queryset.count()
        if total > MAX_ACTION_ROWS:
            self.message_user(
                request,
                f"{total} rows are selected; an action handles at most {MAX_ACTION_ROWS} at a time. Narrow the filters and try again.",
                messages.ERROR,
            )
            return None

        rows = list(queryset)
        to_change, skipped = [], []
        for obj in rows:
            ok, why = eligible(obj)
            if ok:
                to_change.append(obj)
            else:
                skipped.append((obj, why))

        if not to_change:
            self.message_user(
                request,
                f"Nothing to do: none of the {len(rows)} selected row(s) can take this action. {_short_list(skipped)}",
                messages.WARNING,
            )
            return None

        confirmed = request.POST.get("confirm") == "yes"
        form = form_class(request.POST if confirmed else None, **(form_kwargs or {}))
        if confirmed and form.is_valid():
            apply(request, to_change, skipped, form.cleaned_data)
            return None

        context = {
            **self.admin_site.each_context(request),
            "title": title,
            "opts": self.model._meta,
            "changelist_url": reverse(f"admin:{self.model._meta.app_label}_{self.model._meta.model_name}_changelist"),
            "intro": intro,
            "warning": warning,
            "action": action,
            "submit": submit,
            "form": form,
            "select_across": request.POST.get("select_across", "0"),
            "selected": request.POST.getlist(helpers.ACTION_CHECKBOX_NAME),
            "eligible_count": len(to_change),
            "preview": [str(obj) for obj in to_change[:PREVIEW_LIMIT]],
            "more": max(0, len(to_change) - PREVIEW_LIMIT),
            "skipped_count": len(skipped),
            "skipped": [f"{obj} - {why}" for obj, why in skipped[:PREVIEW_LIMIT]],
            "skipped_more": max(0, len(skipped) - PREVIEW_LIMIT),
        }
        return TemplateResponse(request, _confirm_template(), context)

    def _report(self, request, verb, done, skipped=(), failed=(), extra=""):
        skipped, failed = list(skipped), list(failed)
        parts = []
        if done:
            parts.append(f"{verb} {len(done)} row(s).")
        if skipped:
            parts.append(f"Left alone {len(skipped)}: {_short_list(skipped)}.")
        if failed:
            parts.append(f"FAILED {len(failed)}: {_short_list(failed)}.")
        if extra:
            parts.append(extra)
        if done and not failed:
            level = messages.SUCCESS
        elif done:
            level = messages.WARNING
        else:
            level = messages.ERROR if failed else messages.WARNING
        self.message_user(request, " ".join(parts) or "Nothing was changed.", level)


# ---------------------------------------------------------------------------------------------------------------
# Shared re-processing (DailyAttendance and AttendanceEvent actions)
# ---------------------------------------------------------------------------------------------------------------

class _KeepReviewedDay(Exception):
    """Raised inside the transaction to undo a re-process that would have removed a day carrying reviewed exceptions."""


def _snapshot(attendance):
    return {
        "shift": attendance.shift_id,
        "clock_in": attendance.actual_clock_in.isoformat() if attendance.actual_clock_in else None,
        "clock_out": attendance.actual_clock_out.isoformat() if attendance.actual_clock_out else None,
        "late_minutes": attendance.late_minutes,
        "early_departure_minutes": attendance.early_departure_minutes,
        "worked_minutes": attendance.worked_minutes,
        "overtime_minutes": attendance.overtime_minutes,
        "status": attendance.status,
    }


def reprocess_day(employee, work_date, *, now, leave_cache):
    """Re-run the standard attendance processing for one employee's work date.

    Returns (outcome, attendance_or_None, before_snapshot, after_snapshot). The maths is entirely
    attendance.services.processing.process_employee_attendance; this only classifies what happened.
    """
    roster_day = get_employee_roster_day(employee, work_date)
    if roster_day is None:
        return "no_roster", None, None, None
    if roster_day.status != RosterDayStatus.WORK or roster_day.shift is None:
        return "rest_day", None, None, None

    before = DailyAttendance.objects.filter(employee=employee, date=work_date).first()
    before_snap = _snapshot(before) if before else None
    had_reviewed = before is not None and before.exceptions.exclude(status="pending").exists()
    if work_date not in leave_cache:
        leave_cache[work_date] = set(approved_leave_employee_ids(work_date))
    try:
        with transaction.atomic():
            result = process_employee_attendance(employee, work_date, now=now, leave_ids=leave_cache[work_date])
            if before is not None and result is None and had_reviewed:
                raise _KeepReviewedDay
    except _KeepReviewedDay:
        return "kept_reviewed", before, before_snap, before_snap

    if before is None and result is None:
        return "nothing_to_record", None, None, None
    if before is None:
        return "created", result, None, _snapshot(result)
    if result is None:
        return "removed", None, before_snap, None
    after_snap = _snapshot(result)
    return ("changed" if after_snap != before_snap else "unchanged"), result, before_snap, after_snap


def _reviewed_mismatches(attendance):
    """Approved deductions that the freshly re-processed day no longer supports (processing never touches reviewed rows)."""
    out = []
    for exception in attendance.exceptions.filter(status="approved"):
        kind = exception.exception_type
        if (kind == "absence" and attendance.status != "absent") or (kind == "late" and not attendance.late_minutes) or (
            kind == "early_departure" and not attendance.early_departure_minutes
        ):
            out.append(f"{attendance.employee.employee_id} {attendance.date:%d %b %Y}: approved {kind.replace('_', ' ')} but the day now reads {attendance.status}")
    return out


OUTCOME_TEXT = {
    "changed": "recalculated (values changed)",
    "unchanged": "already correct",
    "created": "day record created",
    "removed": "day record removed (shift not finished and nobody punched)",
    "kept_reviewed": "left as is (re-processing would have removed a day that has reviewed exceptions)",
    "no_roster": "no roster day",
    "rest_day": "roster says rest day",
    "nothing_to_record": "nothing to record yet",
}


def run_reprocess(request, admin_obj, pairs, *, source):
    """Re-process each (employee, work_date) pair, then message + audit. `pairs` is an iterable of unique pairs."""
    now = timezone.now()
    leave_cache, outcomes, changes, warnings, failed = {}, Counter(), [], [], []
    for employee, work_date in pairs:
        try:
            outcome, attendance, before, after = reprocess_day(employee, work_date, now=now, leave_cache=leave_cache)
        except Exception as error:  # noqa: BLE001 - one bad row must not stop the rest; it is reported below
            logger.exception("Admin re-process failed for %s on %s", employee.employee_id, work_date)
            failed.append((f"{employee.employee_id} {employee.full_name} {work_date:%d %b %Y}", str(error)))
            continue
        outcomes[outcome] += 1
        if outcome in {"changed", "created", "removed"}:
            changes.append({"staff_number": employee.employee_id, "date": work_date.isoformat(), "outcome": outcome, "before": before, "after": after})
        if attendance is not None and outcome in {"changed", "created"}:
            warnings.extend(_reviewed_mismatches(attendance))

    worked = sum(outcomes.values())
    only_employee = {employee.pk for employee, _ in pairs}
    audit(
        request,
        "attendance.admin_reprocessed",
        "Attendance re-processed from the admin",
        f"{request.user.get_username()} re-processed {worked} work day(s) from the raw punches ({source}).",
        employee=next(iter({employee for employee, _ in pairs}), None) if len(only_employee) == 1 else None,
        severity=AuditSeverity.WARNING if failed else AuditSeverity.SUCCESS,
        metadata={
            "source": source,
            "outcomes": dict(outcomes),
            "changes": changes[:100],
            "changes_truncated": len(changes) > 100,
            "failed": [{"row": row, "error": why} for row, why in failed],
        },
    )
    summary = "; ".join(f"{count} {OUTCOME_TEXT[key]}" for key, count in outcomes.items()) or "no work days were processed"
    extra = ""
    if warnings:
        extra = f"CHECK: {len(warnings)} day(s) now disagree with an already-approved deduction ({'; '.join(warnings[:3])}{'; ...' if len(warnings) > 3 else ''}). Use Reopen on those exceptions."
    admin_obj._report(request, "Re-processed", [1] * worked if worked else [], failed=failed, extra=f"Result: {summary}. {extra}".strip())


# ---------------------------------------------------------------------------------------------------------------
# AttendanceEvent - raw punches, view only
# ---------------------------------------------------------------------------------------------------------------

def _punch_work_dates(event, cache):
    """The rostered work dates whose punch-capture window contains this punch (a night shift's punch after
    midnight belongs to the previous day's shift)."""
    if event.pk in cache:
        return cache[event.pk]
    dates = []
    punched = event.timestamp
    window = timedelta(hours=CAPTURE_WINDOW_HOURS)
    local_day = timezone.localtime(punched).date()
    for candidate in (local_day - timedelta(days=1), local_day):
        roster_day = get_employee_roster_day(event.employee, candidate)
        if roster_day is None or roster_day.status != RosterDayStatus.WORK or roster_day.shift is None:
            continue
        start, end = shift_schedule(roster_day.shift, candidate)
        if start - window <= punched <= end + window:
            dates.append(candidate)
    cache[event.pk] = dates
    return dates


@admin.register(AttendanceEvent)
class AttendanceEventAdmin(ConfirmedActionsMixin, admin.ModelAdmin):
    list_per_page = 50
    save_on_top = True
    empty_value_display = "-"
    show_full_result_count = False  # the table is huge and the unfiltered COUNT(*) is slow
    date_hierarchy = "timestamp"
    ordering = ("-timestamp", "-id")
    list_select_related = ("employee", "employee__department", "device")
    list_display = (
        "staff_number", "employee_name", "department", "punch_time", "verification_type",
        "device_name", "device_serial", "external_event_id", "imported_at",
    )
    list_display_links = ("staff_number", "employee_name")
    list_filter = (
        ("timestamp", admin.DateFieldListFilter),
        "verification_type",
        ("device", admin.RelatedFieldListFilter),
        ("employee__department", admin.RelatedFieldListFilter),
    )
    search_fields = search_fields_for("employee") + ("device__serial_number", "device__name", "external_event_id")
    actions = ["reprocess_days"]
    readonly_fields = ("employee_link", "payload_pretty")
    fieldsets = (
        ("Punch", {
            "fields": ("employee_link", "timestamp", "verification_type"),
            "description": "A raw punch from a terminal. It is evidence and cannot be edited or deleted here. "
                           "If a punch is wrong or missing, fix the roster or the device data and re-process the day.",
        }),
        ("Terminal", {"fields": ("device", "external_event_id")}),
        ("System data", {"fields": ("imported_at", "payload_pretty")}),
    )

    staff_number = col_staff()
    employee_name = col_name()
    department = col_department()

    @admin.display(description="Punch time (local)", ordering="timestamp")
    def punch_time(self, obj):
        return fmt_datetime(obj.timestamp)

    @admin.display(description="Terminal", ordering="device__name")
    def device_name(self, obj):
        return obj.device.name if obj.device else None

    @admin.display(description="Serial", ordering="device__serial_number")
    def device_serial(self, obj):
        return obj.device.serial_number if obj.device else None

    @admin.display(description="Employee")
    def employee_link(self, obj):
        return admin_link(obj.employee, f"{obj.employee.employee_id} - {obj.employee.full_name}")

    @admin.display(description="Raw payload from the terminal")
    def payload_pretty(self, obj):
        if not obj.raw_payload:
            return None
        return format_html('<pre style="max-height:24em;overflow:auto;margin:0">{}</pre>', json.dumps(obj.raw_payload, indent=2, sort_keys=True, default=str))

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_reprocess_permission(self, request):
        return request.user.has_perm("attendance.change_dailyattendance")

    @admin.action(description="Re-process the work days these punches belong to", permissions=["reprocess"])
    def reprocess_days(self, request, queryset):
        cache = {}

        def eligible(event):
            if _punch_work_dates(event, cache):
                return True, ""
            return False, "no rostered shift covers this punch (within the capture window)"

        def apply(request, events, skipped, data):
            pairs = {}
            for event in events:
                for work_date in _punch_work_dates(event, cache):
                    pairs.setdefault((event.employee_id, work_date), (event.employee, work_date))
            run_reprocess(request, self, list(pairs.values()), source=f"{len(events)} selected punch(es)")

        return self._confirmed_action(
            request,
            queryset,
            action="reprocess_days",
            title="Re-process work days",
            intro="Each selected punch belongs to a work day (a night-shift punch after midnight belongs to the day the shift "
                  "started). Those days are re-calculated from their raw punches with the normal attendance processing. "
                  "Reviewed exceptions are never touched.",
            eligible=eligible,
            apply=apply,
            form_class=forms.Form,
            submit="Yes, re-process",
        )


# ---------------------------------------------------------------------------------------------------------------
# DailyAttendance
# ---------------------------------------------------------------------------------------------------------------

class PendingExceptionsFilter(admin.SimpleListFilter):
    title = "needs review"
    parameter_name = "needs_review"

    def lookups(self, request, model_admin):
        return [("yes", "Has pending exceptions"), ("no", "No pending exceptions")]

    def queryset(self, request, queryset):
        pending = AttendanceException.objects.filter(status="pending").values("attendance_id")
        if self.value() == "yes":
            return queryset.filter(pk__in=pending)
        if self.value() == "no":
            return queryset.exclude(pk__in=pending)
        return queryset


@admin.register(DailyAttendance)
class DailyAttendanceAdmin(ConfirmedActionsMixin, AuditedAdminMixin, admin.ModelAdmin):
    audit_key = "daily"
    audit_noun = "daily attendance"
    date_hierarchy = "date"
    ordering = ("-date", "employee__employee_id")
    list_select_related = ("employee", "employee__department", "shift")
    list_display = (
        "staff_number", "employee_name", "department", "work_date", "shift_name", "status_badge",
        "scheduled", "clock_in", "clock_out", "late", "early", "worked", "overtime", "exception_chips",
    )
    list_display_links = ("staff_number", "employee_name")
    list_filter = (
        ("date", admin.DateFieldListFilter),
        "status",
        ("shift", admin.RelatedFieldListFilter),
        ("shift__is_overnight", admin.BooleanFieldListFilter),
        ("employee__department", admin.RelatedFieldListFilter),
        PendingExceptionsFilter,
    )
    search_fields = search_fields_for("employee")
    autocomplete_fields = ("employee",)
    actions = ["reprocess_selected"]
    readonly_fields = ("processed_at", "exceptions_overview", "raw_punches")
    fieldsets = (
        ("Who and when", {
            "fields": ("employee", "date", "shift"),
            "description": "The date is the SHIFT's work date. For a night shift the clock-out is on the next calendar day.",
        }),
        ("Schedule", {"fields": ("scheduled_start", "scheduled_end")}),
        ("Punches", {"fields": ("actual_clock_in", "actual_clock_out", "raw_punches")}),
        ("Calculated by the system", {
            "fields": ("status", "late_minutes", "early_departure_minutes", "worked_minutes", "overtime_minutes", "processed_at"),
            "description": "These values are calculated from the raw punches and are OVERWRITTEN every time the day is processed "
                           "(automatically for today and yesterday). To fix them properly, correct the roster or the punches and use "
                           "the list action 'Re-process selected days'. Hand edits here are logged in the audit trail.",
        }),
        ("Exceptions", {"fields": ("exceptions_overview",)}),
    )

    staff_number = col_staff()
    employee_name = col_name()
    department = col_department()

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related("exceptions")

    def get_readonly_fields(self, request, obj=None):
        fields = super().get_readonly_fields(request, obj)
        return fields + ("employee", "date") if obj else fields

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "shift":
            kwargs["required"] = False  # leave-only / unrostered rows have no shift; do not make fixing them impossible
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    @admin.display(description="Work date", ordering="date")
    def work_date(self, obj):
        return fmt_date(obj.date)

    @admin.display(description="Shift", ordering="shift__name")
    def shift_name(self, obj):
        return obj.shift.name if obj.shift else None

    @admin.display(description="Status", ordering="status")
    def status_badge(self, obj):
        return choice_badge(obj, "status", ATTENDANCE_TONES)

    @admin.display(description="Scheduled")
    def scheduled(self, obj):
        if not obj.scheduled_start or not obj.scheduled_end:
            return None
        return f"{fmt_time(obj.scheduled_start)}-{fmt_time(obj.scheduled_end, obj.date)}"

    @admin.display(description="In", ordering="actual_clock_in")
    def clock_in(self, obj):
        return fmt_time(obj.actual_clock_in, obj.date)

    @admin.display(description="Out", ordering="actual_clock_out")
    def clock_out(self, obj):
        return fmt_time(obj.actual_clock_out, obj.date)

    @admin.display(description="Late (min)", ordering="late_minutes")
    def late(self, obj):
        return minutes_cell(obj.late_minutes, "amber")

    @admin.display(description="Early out (min)", ordering="early_departure_minutes")
    def early(self, obj):
        return minutes_cell(obj.early_departure_minutes, "amber")

    @admin.display(description="Worked", ordering="worked_minutes")
    def worked(self, obj):
        return fmt_hours(obj.worked_minutes)

    @admin.display(description="Overtime (min)", ordering="overtime_minutes")
    def overtime(self, obj):
        return minutes_cell(obj.overtime_minutes, "blue")

    @admin.display(description="Exceptions")
    def exception_chips(self, obj):
        rows = list(obj.exceptions.all())
        if not rows:
            return None
        return format_html_join(
            " ",
            "{}",
            (
                (badge(f"{row.get_exception_type_display()} - {row.get_status_display()}", EXCEPTION_STATUS_TONES.get(row.status, "grey")),)
                for row in rows
            ),
        )

    @admin.display(description="Exceptions on this day")
    def exceptions_overview(self, obj):
        if not obj.pk:
            return None
        rows = list(obj.exceptions.all())
        if not rows:
            return "None."
        return format_html_join(
            mark_safe("<br>"),
            "{} {} - {} min - {}",
            (
                (
                    admin_link(row, row.get_exception_type_display()),
                    choice_badge(row, "status", EXCEPTION_STATUS_TONES),
                    row.minutes_affected,
                    row.proposed_deduction,
                )
                for row in rows
            ),
        )

    @admin.display(description="Raw punches in this shift's capture window")
    def raw_punches(self, obj):
        if not obj.pk or not obj.scheduled_start or not obj.scheduled_end:
            return None
        window = timedelta(hours=CAPTURE_WINDOW_HOURS)
        events = list(
            AttendanceEvent.objects.filter(
                employee_id=obj.employee_id,
                timestamp__gte=obj.scheduled_start - window,
                timestamp__lte=obj.scheduled_end + window,
            ).select_related("device").order_by("timestamp")[:50]
        )
        if not events:
            return "No punches in the window - that is why a finished day reads absent."
        return format_html_join(
            mark_safe("<br>"),
            "{} &nbsp; {} &nbsp; {}",
            (
                (admin_link(event, fmt_datetime(event.timestamp)), event.get_verification_type_display(), event.device.name if event.device else "no terminal")
                for event in events
            ),
        )

    def has_reprocess_permission(self, request):
        return self.has_change_permission(request)

    @admin.action(description="Re-process selected days from the raw punches", permissions=["change"])
    def reprocess_selected(self, request, queryset):
        def eligible(attendance):
            return True, ""

        def apply(request, rows, skipped, data):
            pairs = [(row.employee, row.date) for row in rows]
            run_reprocess(request, self, pairs, source=f"{len(rows)} selected day(s)")

        return self._confirmed_action(
            request,
            queryset,
            action="reprocess_selected",
            title="Re-process selected days",
            intro="Each selected day is re-calculated from its raw punches with the normal attendance processing: clock in/out, "
                  "late, early departure, worked and overtime minutes, status, and the PENDING exceptions. Exceptions that were "
                  "already approved, waived or held are never changed. If a rostered day has no roster entry or is a rest day "
                  "it is left as it is.",
            warning="Hand edits made to these days on the change form will be replaced by the recalculated values.",
            eligible=eligible,
            apply=apply,
            form_class=forms.Form,
            submit="Yes, re-process",
        )


# ---------------------------------------------------------------------------------------------------------------
# AttendanceException
# ---------------------------------------------------------------------------------------------------------------

def _decide_exception(request, pk, decision, comment):
    """Approve / waive / hold one pending exception exactly as ExceptionDecisionAPIView does: the same serializer
    (validation, status, reviewer, timestamp), the meal-penalty sync for approved absences and the same audit event."""
    with transaction.atomic():
        exception = (
            AttendanceException.objects.select_for_update(of=("self",))
            .select_related("attendance", "attendance__employee")
            .get(pk=pk)
        )
        serializer = ExceptionDecisionSerializer(
            exception,
            data={"decision": decision, "comment": comment},
            context={"request": request},
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        if exception.exception_type == "absence" and exception.status == "approved":
            MealService.sync_absence_penalties(exception.attendance.employee, exception.attendance.date, actor=request.user)
        ExceptionDecisionAPIView._log_decision(exception, request.user)
    return exception


def _reopen_blocker(exception):
    """Why an already-reviewed exception must not simply go back to pending, or None."""
    work_date = exception.attendance.date
    period = PayrollPeriod.objects.filter(year=work_date.year, month=work_date.month).first()
    if period is not None and period.status in LOCKED_PERIOD_STATUSES:
        return f"payroll for {period.display_name} is already {period.get_status_display().lower()}"
    if exception.exception_type == "absence" and exception.status == "approved":
        iso = work_date.isoformat()
        for penalty in MealAbsencePenalty.objects.filter(employee=exception.attendance.employee).exclude(status=MealAbsencePenaltyStatus.CANCELLED):
            if iso in (penalty.source_absence_dates or []):
                return f"a meal-ticket penalty ({penalty.get_status_display().lower()}) was created from this absence; cancel that penalty first"
    return None


def _reopen_exception(request, pk, reason):
    with transaction.atomic():
        exception = (
            AttendanceException.objects.select_for_update(of=("self",))
            .select_related("attendance", "attendance__employee")
            .get(pk=pk)
        )
        if exception.status == "pending":
            raise ActionRefused("already pending")
        blocker = _reopen_blocker(exception)
        if blocker:
            raise ActionRefused(blocker)
        previous = {
            "status": exception.status,
            "reviewed_by": exception.reviewed_by,
            "reviewed_at": exception.reviewed_at.isoformat() if exception.reviewed_at else None,
            "admin_comment": exception.admin_comment,
        }
        who = request.user.get_full_name() or request.user.get_username()
        note = f"Reopened from {previous['status']} by {who}: {reason}"
        if previous["admin_comment"]:
            note += f" (previous comment: {previous['admin_comment']})"
        exception.status = "pending"
        exception.reviewed_by = ""
        exception.reviewed_at = None
        exception.admin_comment = note
        exception.save(update_fields=["status", "reviewed_by", "reviewed_at", "admin_comment"])
        audit(
            request,
            "attendance.exception_reopened",
            "Attendance exception reopened",
            f"{exception.get_exception_type_display()} on {exception.attendance.date:%d %b %Y} was sent back to pending by {who}: {reason}",
            employee=exception.attendance.employee,
            obj=exception,
            severity=AuditSeverity.WARNING,
            metadata={
                "exception_id": exception.pk,
                "exception_type": exception.exception_type,
                "attendance_date": exception.attendance.date.isoformat(),
                "previous": previous,
                "reason": reason,
            },
        )
    return exception


class DecisionForm(ReasonForm):
    pass


@admin.register(AttendanceException)
class AttendanceExceptionAdmin(ConfirmedActionsMixin, AuditedAdminMixin, admin.ModelAdmin):
    audit_key = "exception"
    audit_noun = "attendance exception"
    employee_path = "attendance__employee"
    date_hierarchy = "attendance__date"
    ordering = ("-attendance__date", "attendance__employee__employee_id", "-id")
    list_select_related = ("attendance", "attendance__employee", "attendance__employee__department", "attendance__shift")
    list_display = (
        "staff_number", "employee_name", "department", "work_date", "shift_name", "type_badge",
        "minutes_affected", "proposed_deduction", "status_badge", "reviewed_by", "reviewed_at",
    )
    list_display_links = ("staff_number", "employee_name")
    list_filter = (
        "status",
        "exception_type",
        ("attendance__date", admin.DateFieldListFilter),
        ("attendance__employee__department", admin.RelatedFieldListFilter),
        ("attendance__shift", admin.RelatedFieldListFilter),
    )
    search_fields = search_fields_for("attendance__employee")
    autocomplete_fields = ("attendance",)
    actions = ["approve_selected", "waive_selected", "hold_selected", "reopen_selected"]
    readonly_fields = ("status", "reviewed_by", "reviewed_at", "created_at", "day_summary")
    fieldsets = (
        ("Day", {"fields": ("attendance", "day_summary")}),
        ("What happened", {
            "fields": ("exception_type", "minutes_affected", "proposed_deduction"),
            "description": "Payroll works from the minutes (late / early departure) or the daily rate (absence); "
                           "the proposed deduction is only a guide for the reviewer.",
        }),
        ("Review", {
            "fields": ("status", "reviewed_by", "reviewed_at", "employee_reason", "supervisor_comment", "admin_comment"),
            "description": "The decision can only be changed with the list actions (Approve / Waive / Hold / Reopen), so the "
                           "meal-ticket penalties, payroll and the audit trail stay consistent. Comments can be edited here.",
        }),
        ("System", {"fields": ("created_at",)}),
    )

    staff_number = col_staff("attendance__employee")
    employee_name = col_name("attendance__employee")
    department = col_department("attendance__employee")

    def get_readonly_fields(self, request, obj=None):
        fields = super().get_readonly_fields(request, obj)
        return fields + ("attendance",) if obj else fields

    @admin.display(description="Work date", ordering="attendance__date")
    def work_date(self, obj):
        return fmt_date(obj.attendance.date)

    @admin.display(description="Shift", ordering="attendance__shift__name")
    def shift_name(self, obj):
        return obj.attendance.shift.name if obj.attendance.shift else None

    @admin.display(description="Type", ordering="exception_type")
    def type_badge(self, obj):
        return choice_badge(obj, "exception_type", EXCEPTION_TYPE_TONES)

    @admin.display(description="Status", ordering="status")
    def status_badge(self, obj):
        return choice_badge(obj, "status", EXCEPTION_STATUS_TONES)

    @admin.display(description="The day")
    def day_summary(self, obj):
        if not obj.pk:
            return None
        day = obj.attendance
        bits = [f"{day.get_status_display()}"]
        if day.actual_clock_in or day.actual_clock_out:
            bits.append(f"in {fmt_time(day.actual_clock_in, day.date) or '-'}, out {fmt_time(day.actual_clock_out, day.date) or '-'}")
        if day.late_minutes:
            bits.append(f"late {day.late_minutes} min")
        if day.early_departure_minutes:
            bits.append(f"left {day.early_departure_minutes} min early")
        return format_html("{} - {}", admin_link(day, f"{fmt_date(day.date)}"), ", ".join(bits))

    def has_review_permission(self, request):
        return request.user.has_perm("attendance.review_attendanceexception")

    # -- decisions --------------------------------------------------------------------------------------------

    def _decision_action(self, request, queryset, decision, *, action, title, intro, verb, reason_required, warning=""):
        def eligible(exception):
            if exception.status == "pending":
                return True, ""
            return False, f"already {exception.get_status_display().lower()}"

        def apply(request, rows, skipped, data):
            done, failed, refused = [], [], list(skipped)
            for row in rows:
                try:
                    _decide_exception(request, row.pk, decision, data.get("reason", ""))
                    done.append(row)
                except ActionRefused as error:
                    refused.append((row, str(error)))
                except APIValidationError as error:
                    refused.append((row, explain(error)))
                except Exception as error:  # noqa: BLE001 - reported per row, the rest still go through
                    logger.exception("Admin exception decision failed for %s", row.pk)
                    failed.append((row, str(error)))
            self._report(request, verb, done, refused, failed)

        return self._confirmed_action(
            request,
            queryset,
            action=action,
            title=title,
            intro=intro,
            eligible=eligible,
            apply=apply,
            form_class=DecisionForm,
            form_kwargs={
                "reason_required": reason_required,
                "reason_help": "Required. Saved on the exception and in the audit trail." if reason_required else "Optional. Saved on the exception.",
            },
            warning=warning,
            submit=f"Yes, {verb.lower()} them",
        )

    @admin.action(description="Approve deduction for selected (pending only)", permissions=["review"])
    def approve_selected(self, request, queryset):
        return self._decision_action(
            request, queryset, "approved", action="approve_selected", title="Approve selected exceptions", verb="Approved", reason_required=False,
            intro="The deduction stands: payroll will deduct for these exceptions. Approved absences also apply the meal-ticket absence penalties, "
                  "exactly as when approving on the Exceptions page. Only PENDING exceptions are changed.",
        )

    @admin.action(description="Waive selected (pending only)", permissions=["review"])
    def waive_selected(self, request, queryset):
        return self._decision_action(
            request, queryset, "waived", action="waive_selected", title="Waive selected exceptions", verb="Waived", reason_required=True,
            intro="No deduction is made for these exceptions. A reason is required. Only PENDING exceptions are changed.",
        )

    @admin.action(description="Hold / investigate selected (pending only)", permissions=["review"])
    def hold_selected(self, request, queryset):
        return self._decision_action(
            request, queryset, "held", action="hold_selected", title="Hold selected exceptions", verb="Held", reason_required=True,
            intro="Park these exceptions for investigation. A reason is required. Only PENDING exceptions are changed.",
        )

    @admin.action(description="Reopen selected (back to pending)", permissions=["review"])
    def reopen_selected(self, request, queryset):
        def eligible(exception):
            if exception.status == "pending":
                return False, "already pending"
            blocker = _reopen_blocker(exception)
            return (False, blocker) if blocker else (True, "")

        def apply(request, rows, skipped, data):
            done, failed, refused = [], [], list(skipped)
            for row in rows:
                try:
                    _reopen_exception(request, row.pk, data["reason"].strip())
                    done.append(row)
                except ActionRefused as error:
                    refused.append((row, str(error)))
                except Exception as error:  # noqa: BLE001
                    logger.exception("Admin exception reopen failed for %s", row.pk)
                    failed.append((row, str(error)))
            self._report(
                request, "Reopened", done, refused, failed,
                extra="Payroll deductions for a reopened exception disappear the next time the payroll attendance sync runs." if done else "",
            )

        return self._confirmed_action(
            request,
            queryset,
            action="reopen_selected",
            title="Reopen selected exceptions",
            intro="Send approved / waived / held exceptions back to PENDING so they can be decided again. The previous decision is "
                  "kept in the audit trail. Exceptions are left alone when payroll for that month is already approved/paid/closed, "
                  "or when an approved absence has produced a meal-ticket penalty (cancel the penalty first).",
            eligible=eligible,
            apply=apply,
            form_class=DecisionForm,
            form_kwargs={"reason_required": True, "reason_help": "Required. Why is this being reopened?"},
            submit="Yes, reopen them",
        )


# ---------------------------------------------------------------------------------------------------------------
# OvertimeRecord - reviewed with the same services the Overtime API uses; never hand-edited or deleted
# ---------------------------------------------------------------------------------------------------------------

@admin.register(OvertimeRecord)
class OvertimeRecordAdmin(ConfirmedActionsMixin, AuditedAdminMixin, admin.ModelAdmin):
    audit_key = "overtime"
    audit_noun = "overtime record"
    date_hierarchy = "work_date"
    ordering = ("-work_date", "employee__employee_id")
    list_select_related = ("employee", "employee__department", "shift", "reviewed_by")
    list_display = (
        "staff_number", "employee_name", "department", "work_day", "shift_name", "potential_overtime_minutes",
        "approved_overtime_minutes", "status_badge", "payable_amount", "payment_due_date", "payment_badge", "reviewer",
    )
    list_display_links = ("staff_number", "employee_name")
    list_filter = (
        "status",
        "payment_status",
        ("work_date", admin.DateFieldListFilter),
        ("employee__department", admin.RelatedFieldListFilter),
        ("shift", admin.RelatedFieldListFilter),
    )
    search_fields = search_fields_for("employee")
    raw_id_fields = ("attendance", "employee", "shift", "reviewed_by", "paid_by")
    actions = ["approve_selected", "reject_selected", "mark_paid_selected"]
    fieldsets = (
        ("Who and when", {"fields": ("employee", "attendance", "shift", "work_date")}),
        ("Overtime", {
            "fields": ("scheduled_end", "actual_clock_out", "threshold_minutes_snapshot", "potential_overtime_minutes", "approved_overtime_minutes"),
        }),
        ("Review", {"fields": ("status", "reviewed_by", "reviewed_at", "review_comment")}),
        ("Pay calculation (snapshot taken at approval)", {
            "fields": (
                "basic_salary_snapshot", "roster_work_days_snapshot", "daily_rate_snapshot", "scheduled_shift_minutes_snapshot",
                "normal_hourly_rate_snapshot", "overtime_multiplier_snapshot", "overtime_hourly_rate_snapshot", "payable_amount",
            ),
        }),
        ("Payment", {"fields": ("payment_due_date", "payment_status", "paid_at", "paid_by", "payment_note")}),
        ("System", {"fields": ("created_at", "updated_at")}),
    )

    staff_number = col_staff()
    employee_name = col_name()
    department = col_department()

    @admin.display(description="Work date", ordering="work_date")
    def work_day(self, obj):
        return fmt_date(obj.work_date)

    @admin.display(description="Shift", ordering="shift__name")
    def shift_name(self, obj):
        return obj.shift.name

    @admin.display(description="Status", ordering="status")
    def status_badge(self, obj):
        return choice_badge(obj, "status", OVERTIME_TONES)

    @admin.display(description="Payment", ordering="payment_status")
    def payment_badge(self, obj):
        return choice_badge(obj, "payment_status", PAYMENT_TONES)

    @admin.display(description="Reviewed by", ordering="reviewed_by__username")
    def reviewer(self, obj):
        return obj.reviewed_by.get_username() if obj.reviewed_by else None

    # Financial snapshots are immutable once reviewed (see attendance.services.overtime): the whole page is view-only.
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_review_permission(self, request):
        return request.user.has_perm("attendance.review_overtime")

    def _review_action(self, request, queryset, service, *, action, title, intro, verb, eligible, reason_required, reason_help):
        def apply(request, rows, skipped, data):
            done, failed, refused = [], [], list(skipped)
            for row in rows:
                try:
                    service(row.pk, actor=request.user, text=data.get("reason", "").strip())
                    done.append(row)
                except ObjectDoesNotExist:
                    refused.append((row, "no longer exists"))
                except ValueError as error:
                    refused.append((row, str(error)))
                except Exception as error:  # noqa: BLE001
                    logger.exception("Admin overtime %s failed for %s", action, row.pk)
                    failed.append((row, str(error)))
            self._report(request, verb, done, refused, failed)

        return self._confirmed_action(
            request,
            queryset,
            action=action,
            title=title,
            intro=intro,
            eligible=eligible,
            apply=apply,
            form_class=ReasonForm,
            form_kwargs={"reason_required": reason_required, "reason_help": reason_help},
            submit=f"Yes, {verb.lower()} them",
        )

    @admin.action(description="Approve selected overtime (full potential minutes)", permissions=["review"])
    def approve_selected(self, request, queryset):
        return self._review_action(
            request, queryset, lambda pk, actor, text: approve_overtime(pk, actor=actor, comment=text),
            action="approve_selected", title="Approve selected overtime", verb="Approved", reason_required=False,
            reason_help="Optional comment saved on each record.",
            eligible=lambda row: (True, "") if row.status == OvertimeStatus.PENDING and row.payment_status != OvertimePaymentStatus.PAID else (False, f"already {row.get_status_display().lower()}"),
            intro="Approves the FULL potential overtime minutes of each pending record and takes the pay snapshot (salary, roster days, "
                  "rates, payable amount) exactly as the Overtime page does. To approve fewer minutes use the Overtime page. "
                  "Records whose month has no complete roster cannot be priced and are left alone.",
        )

    @admin.action(description="Reject selected overtime", permissions=["review"])
    def reject_selected(self, request, queryset):
        return self._review_action(
            request, queryset, lambda pk, actor, text: reject_overtime(pk, actor=actor, comment=text),
            action="reject_selected", title="Reject selected overtime", verb="Rejected", reason_required=True,
            reason_help="Required. Saved on each record and in the audit trail.",
            eligible=lambda row: (True, "") if row.status == OvertimeStatus.PENDING and row.payment_status != OvertimePaymentStatus.PAID else (False, f"already {row.get_status_display().lower()}"),
            intro="Rejects the selected pending overtime records: nothing will be paid for them. A rejection cannot be undone here.",
        )

    @admin.action(description="Mark selected as paid", permissions=["review"])
    def mark_paid_selected(self, request, queryset):
        return self._review_action(
            request, queryset, lambda pk, actor, text: mark_overtime_paid(pk, actor=actor, note=text),
            action="mark_paid_selected", title="Mark selected overtime as paid", verb="Marked paid", reason_required=False,
            reason_help="Optional payment note (reference, channel...).",
            eligible=lambda row: (True, "") if row.status == OvertimeStatus.APPROVED and row.payment_status != OvertimePaymentStatus.PAID else (False, "only approved, unpaid overtime can be marked paid"),
            intro="Records that the approved overtime has been paid. This only records the payment; it does not move any money.",
        )


# ---------------------------------------------------------------------------------------------------------------
# EmployeeRosterDay
# ---------------------------------------------------------------------------------------------------------------

def _override_roster_day(request, pk, status, shift, note):
    """Set one roster day the way RosterOverrideAPIView does (source becomes 'override', same audit event)."""
    with transaction.atomic():
        row = EmployeeRosterDay.objects.select_for_update(of=("self",)).select_related("employee", "shift").get(pk=pk)
        new_shift = shift if status == RosterDayStatus.WORK else None
        if row.status == status and row.shift_id == (new_shift.pk if new_shift else None):
            raise ActionRefused(f"already {status}" + (f" on {new_shift.name}" if new_shift else ""))
        previous = {"status": row.status, "shift": row.shift.name if row.shift else None, "source": row.source}
        row.status = status
        row.shift = new_shift
        row.source = RosterDaySource.OVERRIDE
        if note:
            row.notes = note
        row.updated_by = request.user
        row.save()
        audit(
            request,
            "attendance.roster_day_overridden",
            "Roster day updated",
            f"Roster for {row.employee.full_name} was manually set to {row.status} on {row.date}.",
            employee=row.employee,
            obj=row,
            severity=AuditSeverity.SUCCESS,
            metadata={
                "date": row.date.isoformat(),
                "status": row.status,
                "shift": row.shift.name if row.shift else None,
                "source": row.source,
                "created": False,
                "previous": previous,
                "note": note,
            },
        )
    return row


@admin.register(EmployeeRosterDay)
class EmployeeRosterDayAdmin(ConfirmedActionsMixin, AuditedAdminMixin, admin.ModelAdmin):
    audit_key = "roster_day"
    audit_noun = "roster day"
    date_hierarchy = "date"
    ordering = ("-date", "employee__employee_id")
    list_select_related = ("employee", "employee__department", "shift", "updated_by")
    list_display = (
        "staff_number", "employee_name", "department", "day", "status_badge", "shift_name",
        "source_badge", "short_notes", "updated_by", "updated_at",
    )
    list_display_links = ("staff_number", "employee_name")
    list_filter = (
        ("date", admin.DateFieldListFilter),
        "status",
        "source",
        ("shift", admin.RelatedFieldListFilter),
        ("employee__department", admin.RelatedFieldListFilter),
    )
    search_fields = search_fields_for("employee") + ("notes",)
    autocomplete_fields = ("employee",)
    raw_id_fields = ("created_by", "updated_by")
    actions = ["mark_rest", "mark_work"]
    readonly_fields = ("created_by", "updated_by", "created_at", "updated_at")
    fieldsets = (
        ("Who and when", {"fields": ("employee", "date")}),
        ("Expectation", {
            "fields": ("status", "shift", "source", "notes"),
            "description": "A WORK day needs a shift; a REST day must have none. Editing a generated day makes it a manual override "
                           "so the shift-plan generator will not overwrite it. Past dates change the payroll expected-days count.",
        }),
        ("System", {"fields": ("created_by", "updated_by", "created_at", "updated_at")}),
    )

    staff_number = col_staff()
    employee_name = col_name()
    department = col_department()

    def get_readonly_fields(self, request, obj=None):
        fields = super().get_readonly_fields(request, obj)
        return fields + ("employee", "date") if obj else fields

    @admin.display(description="Date", ordering="date")
    def day(self, obj):
        return fmt_date(obj.date)

    @admin.display(description="Status", ordering="status")
    def status_badge(self, obj):
        return choice_badge(obj, "status", ROSTER_STATUS_TONES)

    @admin.display(description="Shift", ordering="shift__name")
    def shift_name(self, obj):
        return obj.shift.name if obj.shift else None

    @admin.display(description="Source", ordering="source")
    def source_badge(self, obj):
        return choice_badge(obj, "source", ROSTER_SOURCE_TONES)

    @admin.display(description="Notes")
    def short_notes(self, obj):
        return (obj.notes[:60] + "...") if len(obj.notes) > 60 else (obj.notes or None)

    def save_model(self, request, obj, form, change):
        if change:
            obj.updated_by = request.user
            if {"status", "shift"} & set(form.changed_data) and obj.source == RosterDaySource.GENERATED:
                obj.source = RosterDaySource.OVERRIDE
        else:
            obj.created_by = obj.updated_by = request.user
        super().save_model(request, obj, form, change)

    def has_manage_permission(self, request):
        return request.user.has_perm("attendance.manage_roster")

    def _roster_action(self, request, queryset, status, *, action, title, intro, verb, warning, form_class, eligible_note):
        def eligible(row):
            return (True, "") if row.status != status or status == RosterDayStatus.WORK else (False, eligible_note)

        def apply(request, rows, skipped, data):
            shift = data.get("shift")
            done, failed, refused = [], [], list(skipped)
            for row in rows:
                try:
                    _override_roster_day(request, row.pk, status, shift, data.get("reason", "").strip())
                    done.append(row)
                except ActionRefused as error:
                    refused.append((row, str(error)))
                except Exception as error:  # noqa: BLE001
                    logger.exception("Admin roster override failed for %s", row.pk)
                    failed.append((row, str(error)))
            extra = ""
            if done and status == RosterDayStatus.REST:
                pending = AttendanceException.objects.filter(
                    status="pending",
                    attendance__in=DailyAttendance.objects.filter(
                        employee__in=[row.employee_id for row in done], date__in=[row.date for row in done],
                    ),
                ).count()
                if pending:
                    extra = (
                        f"CHECK: {pending} pending attendance exception(s) exist for employees/dates in this selection. "
                        "Processing leaves rest days alone, so waive any that no longer apply."
                    )
            self._report(request, verb, done, refused, failed, extra=extra)

        return self._confirmed_action(
            request,
            queryset,
            action=action,
            title=title,
            intro=intro,
            eligible=eligible,
            apply=apply,
            form_class=form_class,
            form_kwargs={"reason_required": False, "reason_help": "Optional. Replaces the day's note when filled in."},
            warning=warning,
            submit=f"Yes, {verb.lower()}",
        )

    @admin.action(description="Mark selected as REST day", permissions=["manage"])
    def mark_rest(self, request, queryset):
        return self._roster_action(
            request, queryset, RosterDayStatus.REST, action="mark_rest", title="Mark selected days as rest days", verb="Marked as rest day",
            intro="The selected days become rest days (no shift), recorded as manual overrides, exactly like the Roster page override.",
            warning="Rest days are not expected at work: no absence is raised for them and payroll's expected work days go down.",
            form_class=ReasonForm, eligible_note="already a rest day",
        )

    @admin.action(description="Mark selected as WORK day (choose shift)", permissions=["manage"])
    def mark_work(self, request, queryset):
        return self._roster_action(
            request, queryset, RosterDayStatus.WORK, action="mark_work", title="Mark selected days as work days", verb="Marked as work day",
            intro="The selected days become work days on the shift you choose, recorded as manual overrides, exactly like the Roster page override.",
            warning="Work days are expected at work: an absence is raised for them if nobody punches, and payroll's expected work days go up.",
            form_class=WorkDayForm, eligible_note="",
        )

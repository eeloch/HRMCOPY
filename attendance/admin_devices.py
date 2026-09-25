"""Admin for the biometric terminals, their command outbox and the shift set-up.

Written for the person who opens /admin/ to see what the terminals are doing or to correct a wrong entry, so rows
read as sentences ("renumber 1604 -> 62 slots [0]") instead of "DeviceCommand object (123)".

The command payload / result JSON can hold names, ids and slot lists, and the device's own replies may carry a
"record" (a biometric template). Nothing in this module ever prints those raw: list columns show a short summary
built from a few known keys, and the raw JSON is shown only through `scrub`, in a collapsed read-only fieldset.
"""

import json
import re
from collections import Counter
from datetime import timedelta

from django.contrib import admin, messages
from django.db.models import Count, Q
from django.urls import NoReverseMatch, reverse
from django.utils import timezone
from django.utils.html import format_html, format_html_join
from django.utils.text import Truncator
from django.utils.timesince import timesince

from employees.models import Employee

from .models import BiometricDevice, DeviceCommand, Shift, ShiftAssignment, ShiftPlan, ShiftPlanAssignment

# ---- small display helpers ------------------------------------------------------------------------------------

BADGE = '<span style="display:inline-block;padding:1px 9px;border-radius:10px;background:{};color:#fff;font-size:11px;font-weight:600;white-space:nowrap">{}</span>'
GREEN, RED, AMBER, BLUE, GREY = "#2e7d32", "#b3261e", "#9a6700", "#1d6fb8", "#6b6b6b"

STATUS_COLOURS = {"pending": AMBER, "sent": BLUE, "acked": GREEN, "failed": RED}

# Keys whose value may be a biometric template or a raw device reply: never shown, not even in the raw view.
HIDDEN_KEYS = {"record", "records", "template", "templates", "feature", "features", "photo", "image", "face", "finger"}
RAW_LIST_LIMIT = 25
RAW_TEXT_LIMIT = 200

# A terminal reconnects about every 30 s and last_sync_at is stamped on each connect, so a raw "connected" flag with
# a last_sync_at far older than that means the gateway died without clearing it (nothing else ever resets the flag).
STALE_FLAG_AFTER = timedelta(minutes=10)


def badge(colour, text):
    return format_html(BADGE, colour, text)


def format_duration(delta):
    """'12 s', '3 min 5 s', '2 h 5 min', '3 d 4 h' - short enough for a table cell."""
    seconds = max(int(delta.total_seconds()), 0)
    if seconds < 1:
        return "under 1 s"
    if seconds < 60:
        return f"{seconds} s"
    minutes, seconds = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes} min {seconds} s" if seconds else f"{minutes} min"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours} h {minutes} min" if minutes else f"{hours} h"
    days, hours = divmod(hours, 24)
    return f"{days} d {hours} h" if hours else f"{days} d"


def ago(value):
    return f"{timesince(value, depth=1)} ago" if value else "never"


def admin_url(name, *args):
    try:
        return reverse(name, args=args)
    except NoReverseMatch:
        return None


def scrub(value, depth=0):
    """A copy of JSON `value` that is safe to show: biometric-looking keys removed, long lists and texts cut."""
    if isinstance(value, dict):
        shown, hidden = {}, 0
        for key, item in value.items():
            if str(key).lower() in HIDDEN_KEYS:
                hidden += 1
            elif depth >= 6:
                shown[key] = "<nested data not shown>"
            else:
                shown[key] = scrub(item, depth + 1)
        if hidden:
            shown["_note"] = f"{hidden} field(s) holding device data are not shown"
        return shown
    if isinstance(value, (list, tuple)):
        if depth >= 6:
            return "<nested data not shown>"
        shown = [scrub(item, depth + 1) for item in value[:RAW_LIST_LIMIT]]
        if len(value) > RAW_LIST_LIMIT:
            shown.append(f"... and {len(value) - RAW_LIST_LIMIT} more")
        return shown
    if isinstance(value, str) and len(value) > RAW_TEXT_LIMIT:
        return f"<long text hidden: {len(value)} characters>"
    return value


def raw_json_html(value):
    if not value:
        return "-"
    text = json.dumps(scrub(value), indent=2, ensure_ascii=False, default=str)
    return format_html('<pre style="margin:0;white-space:pre-wrap;max-width:60em">{}</pre>', text)


def _list_text(values, limit=6):
    values = list(values) if isinstance(values, (list, tuple)) else []
    text = ",".join(str(item) for item in values[:limit])
    if len(values) > limit:
        text += f",+{len(values) - limit}"
    return f"[{text}]"


def _payload(command):
    return command.payload if isinstance(command.payload, dict) else {}


def employee_pk(payload):
    """The Employee primary key a command was queued for, or None. Never raises on odd JSON."""
    value = payload.get("employee_id") if isinstance(payload, dict) else None
    if isinstance(value, bool) or value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def command_summary(command, device_names=None):
    """One short line describing what a command asks the terminal to do. Built from a handful of known payload keys;
    it never repeats the payload wholesale and never touches a "record"."""
    try:
        return Truncator(_command_summary(command, device_names or {})).chars(160)
    except Exception:  # a summary column must never take the whole changelist down
        return "(payload could not be summarised)"


def _command_summary(command, names):
    payload = _payload(command)
    kind = command.command_type

    def terminal(pk):
        return names.get(pk) or (f"terminal #{pk}" if pk is not None else "a terminal")

    if kind == "set_user_enabled":
        text = f"switch {'ON' if payload.get('enabled') else 'OFF'} {payload.get('enrollid', '?')}"
        if payload.get("legacy"):
            text += " (legacy)"
    elif kind == "clone_enrollment":
        if payload.get("renumber"):
            text = f"renumber {payload.get('from_id', '?')} -> {payload.get('to_id', '?')} slots {_list_text(payload.get('slots'))}"
        elif payload.get("name_probe"):
            text = f"name probe of {len(payload.get('items') or [])} ids"
        elif payload.get("pushes"):
            pushes = [push for push in payload["pushes"] if isinstance(push, dict)]
            parts = [f"{terminal(push.get('target_device_id'))} {_list_text(push.get('backupnums'))}" for push in pushes[:3]]
            more = f"; +{len(pushes) - 3} more" if len(pushes) > 3 else ""
            text = f"slot relay to {'; '.join(parts)}{more}"
        else:
            targets = ", ".join(terminal(pk) for pk in (payload.get("target_device_ids") or [])[:4])
            text = f"clone {payload.get('biometric_type', 'face')} of id {payload.get('enrollid', '?')} to {targets or 'other terminals'}"
    elif kind == "purge_user":
        text = f"purge id {payload.get('enrollid', '?')}"
        if payload.get("reason"):
            text += f" - {payload['reason']}"
    elif kind == "delete_user":
        text = f"delete id {payload.get('enrollid', '?')}"
    elif kind == "enroll_user":
        text = f"enroll id {payload.get('enrollid', '?')} ({payload.get('biometric_type', 'face')})"
    elif kind == "refresh_enrolled_ids":
        text = "list enrolled ids"
    elif kind == "list_user_slots":
        text = "list every enrolled slot"
    else:
        text = command.get_command_type_display()
    attempts = payload.get("attempts")
    if isinstance(attempts, int) and attempts > 0:
        text += f" (interrupted {attempts}x)"
    return text


def result_summary(command):
    """What came back, in words. Counts only: no ids, no record."""
    try:
        return Truncator(_result_summary(command)).chars(240)
    except Exception:
        return "(result could not be summarised)"


def _result_summary(command):
    result = command.result if isinstance(command.result, dict) else {}
    if not result:
        return ""
    if result.get("detail"):
        return str(result["detail"])
    kind, payload = command.command_type, _payload(command)
    if kind == "list_user_slots" and "slots" in result:
        return f"{len(result.get('slots') or [])} slots on the terminal, read in {result.get('pages', '?')} page(s)"
    if kind == "refresh_enrolled_ids" and isinstance(result.get("record"), list):
        return f"{len(result['record'])} ids enrolled on the terminal"
    if kind == "clone_enrollment":
        if payload.get("name_probe"):
            return f"{len(result.get('names') or {})} names read, {len(result.get('missing') or [])} not answered"
        if payload.get("renumber"):
            if isinstance(result.get("failed"), dict):
                return f"moved {_list_text(result.get('moved'))} then stopped: {result['failed'].get('reason', 'failed')}"
            return f"moved {_list_text(result.get('moved'))}; old entry {'removed' if result.get('old_entry_removed') else 'kept'}"
        parts = []
        if "pushed" in result:
            parts.append(f"pushed {result['pushed']}")
        if "cloned_to" in result:
            parts.append(f"copied to {len(result.get('cloned_to') or [])} terminal(s)")
        failed = result.get("failed")
        if isinstance(failed, list) and failed:
            reasons = Counter(str(item.get("reason", "failed")) for item in failed if isinstance(item, dict))
            parts.append("failed " + ", ".join(f"{reason} x{count}" for reason, count in reasons.most_common(3)))
        offline = result.get("skipped_offline")
        if offline:
            parts.append(f"{len(offline)} terminal(s) offline")
        busy = result.get("skipped_busy")
        if busy:
            parts.append(f"{len(busy)} busy")
        return "; ".join(parts)
    # A one-shot reply from the terminal itself: only its verdict and message, never the rest of it.
    said = []
    if "result" in result:
        said.append("accepted" if result.get("result") else "refused")
    if result.get("msg"):
        said.append(str(result["msg"]))
    if result.get("reason") not in (None, ""):
        said.append(f"reason {result['reason']}")
    return "terminal " + ", ".join(said) if said else ""


# Retrying a failed command means queueing a fresh copy. Two kinds are held back, because a stale copy does harm.
RETRY_MAX_BATCH = 200
RETRY_DESTRUCTIVE_MAX_AGE = timedelta(hours=24)
RETRY_NEVER = {
    "set_user_enabled": "switches are re-issued from the current meal state; an old one could undo a newer switch",
}
RETRY_DESTRUCTIVE = ("delete_user", "purge_user")


def retry_blocked_reason(command, now=None):
    """None when a failed command may be copied again, else why not."""
    now = now or timezone.now()
    if command.status != "failed":
        return "not failed, so there is nothing to retry"
    if command.command_type in RETRY_NEVER:
        return RETRY_NEVER[command.command_type]
    if command.command_type in RETRY_DESTRUCTIVE and now - (command.completed_at or command.created_at) > RETRY_DESTRUCTIVE_MAX_AGE:
        return "a delete that failed more than a day ago (the id may belong to someone else by now)"
    return None


def _same_job(left, right):
    strip = lambda payload: {key: value for key, value in (payload or {}).items() if key != "attempts"}  # noqa: E731
    return strip(left) == strip(right)


class ManagePermissionMixin:
    """Actions that change what the terminals will be asked to do need the same right as the Devices page."""

    def has_manage_permission(self, request):
        return request.user.has_perm("attendance.manage_devices")


# ---- biometric terminals -------------------------------------------------------------------------------------


class ReachableFilter(admin.SimpleListFilter):
    title = "reachable now"
    parameter_name = "reachable"

    def lookups(self, request, model_admin):
        return [("yes", "Reachable"), ("no", "Not reachable")]

    def queryset(self, request, queryset):
        cutoff = timezone.now() - BiometricDevice.ONLINE_GRACE
        if self.value() == "yes":
            return queryset.filter(Q(is_online=True) | Q(last_sync_at__gte=cutoff))
        if self.value() == "no":
            return queryset.filter(is_online=False).filter(Q(last_sync_at__isnull=True) | Q(last_sync_at__lt=cutoff))
        return queryset


@admin.register(BiometricDevice)
class BiometricDeviceAdmin(ManagePermissionMixin, admin.ModelAdmin):
    list_display = ("name", "serial_number", "purpose", "location", "model", "ip_address", "last_seen", "reachable", "queue")
    list_display_links = ("name",)
    list_filter = ("purpose", "device_type", ReachableFilter, "is_online")
    search_fields = ("name", "serial_number", "ip_address", "location")
    search_help_text = "Terminal name, serial number, IP address or location."
    ordering = ("name",)
    actions = ("queue_slot_listing", "cancel_background_jobs")
    readonly_fields = ("reachable", "last_seen", "ip_address", "is_online", "last_sync_at", "queue", "slot_listing", "recent_commands")

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(
            pending_count=Count("commands", filter=Q(commands__status="pending")),
            sent_count=Count("commands", filter=Q(commands__status="sent")),
        )

    def get_fieldsets(self, request, obj=None):
        terminal = ("Terminal", {"fields": ("name", "serial_number", "model", "location", "device_type", "purpose")})
        if obj is None:
            return [terminal]
        return [
            terminal,
            ("Status (kept up to date by the gateway, not editable here)", {
                "fields": ("reachable", "last_seen", "ip_address", "is_online", "last_sync_at", "queue", "slot_listing"),
            }),
            ("Latest commands", {"fields": ("recent_commands",)}),
        ]

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        if {"name", "serial_number", "purpose"} & set(form.changed_data):
            messages.warning(
                request,
                "The meal terminal record that the Meals module uses is only updated when a terminal is saved from the Devices page of the app, "
                "not from here. If you changed the name, serial number or purpose of a meal terminal, save it once there as well.",
            )

    # -- columns --

    @admin.display(description="Last seen", ordering="last_sync_at")
    def last_seen(self, obj):
        return ago(obj.last_sync_at)

    @admin.display(description="Reachable", ordering="last_sync_at")
    def reachable(self, obj):
        if not obj.is_reachable:
            return badge(RED, "Offline")
        if obj.is_online and (obj.last_sync_at is None or timezone.now() - obj.last_sync_at > STALE_FLAG_AFTER):
            return badge(AMBER, "Flag stale")
        return badge(GREEN, "Reachable")

    @admin.display(description="Queue", ordering="pending_count")
    def queue(self, obj):
        pending = getattr(obj, "pending_count", None)
        sent = getattr(obj, "sent_count", None)
        if pending is None or sent is None:
            counts = obj.commands.aggregate(
                pending=Count("pk", filter=Q(status="pending")), sent=Count("pk", filter=Q(status="sent")),
            )
            pending, sent = counts["pending"], counts["sent"]
        if not pending and not sent:
            return "-"
        base = admin_url("admin:attendance_devicecommand_changelist")
        text = f"{pending} pending" + (f", {sent} running" if sent else "")
        return format_html('<a href="{}?device__id__exact={}&amp;status__in=pending,sent">{}</a>', base, obj.pk, text) if base else text

    @admin.display(description="Slot listing")
    def slot_listing(self, obj):
        if not obj.pk:
            return "-"
        last = obj.commands.filter(command_type="list_user_slots", status="acked").order_by("-id").first()
        waiting = obj.commands.filter(command_type="list_user_slots", status__in=("pending", "sent")).exists()
        text = "none read yet"
        if last is not None:
            text = f"{len((last.result or {}).get('slots') or [])} slots, read {ago(last.completed_at)}"
        return f"{text} (a new listing is already queued)" if waiting else text

    @admin.display(description="Latest commands")
    def recent_commands(self, obj):
        if not obj.pk:
            return "-"
        commands = list(obj.commands.order_by("-id")[:8])
        if not commands:
            return "None yet."
        names = {obj.pk: obj.name}
        rows = format_html_join(
            "", "<tr><td>#{}</td><td>{}</td><td>{}</td><td>{}</td></tr>",
            ((c.pk, badge(STATUS_COLOURS.get(c.status, GREY), c.get_status_display()), command_summary(c, names), ago(c.created_at)) for c in commands),
        )
        link = admin_url("admin:attendance_devicecommand_changelist")
        more = format_html('<p><a href="{}?device__id__exact={}">All commands for this terminal</a></p>', link, obj.pk) if link else ""
        return format_html('<table>{}</table>{}', rows, more)

    # -- actions --

    @admin.action(description="Queue a fresh slot listing", permissions=["manage"])
    def queue_slot_listing(self, request, queryset):
        queued, waiting = [], []
        for device in queryset:
            if DeviceCommand.objects.filter(device=device, command_type="list_user_slots", status__in=("pending", "sent")).exists():
                waiting.append(device.name)
                continue
            DeviceCommand.objects.create(device=device, command_type="list_user_slots", payload={}, requested_by=request.user)
            queued.append(device.name)
        if queued:
            self.message_user(request, f"Queued a slot listing for: {', '.join(queued)}.", messages.SUCCESS)
        if waiting:
            self.message_user(request, f"Already has a listing waiting or running, left alone: {', '.join(waiting)}.", messages.WARNING)

    @admin.action(description="Cancel pending background jobs for selected terminals", permissions=["manage"])
    def cancel_background_jobs(self, request, queryset):
        device_ids = list(queryset.order_by().values_list("pk", flat=True))
        pending = DeviceCommand.objects.filter(device_id__in=device_ids, status="pending", command_type__in=DeviceCommand.BACKGROUND_TYPES)
        by_type = dict(pending.order_by().values_list("command_type").annotate(n=Count("pk")))
        detail = f"Cancelled by {request.user.get_username()}: background jobs cleared for this terminal."
        cancelled = pending.update(status="failed", result={"detail": detail}, completed_at=timezone.now())
        if not cancelled:
            self.message_user(request, "No pending background jobs (relays, purges, slot listings) on the selected terminals.", messages.INFO)
            return
        breakdown = ", ".join(f"{name} {count}" for name, count in sorted(by_type.items()))
        self.message_user(
            request,
            f"Cancelled {cancelled} pending background job(s) on {len(device_ids)} terminal(s) ({breakdown}). "
            "They are kept, marked failed. A running command is not touched; the next sync can queue new ones.",
            messages.SUCCESS,
        )


# ---- the command outbox --------------------------------------------------------------------------------------


class JobKindFilter(admin.SimpleListFilter):
    title = "kind of job"
    parameter_name = "kind"

    def lookups(self, request, model_admin):
        return [
            ("renumber", "Renumber (move to staff number)"),
            ("slot_relay", "Slot relay (copy credentials)"),
            ("name_probe", "Name probe"),
            ("switch_off", "Switch OFF"),
            ("switch_on", "Switch ON"),
            ("background", "Any background job"),
            ("interrupted", "Interrupted at least once"),
        ]

    def queryset(self, request, queryset):
        value = self.value()
        if value == "renumber":
            return queryset.filter(command_type="clone_enrollment", payload__has_key="renumber")
        if value == "slot_relay":
            return queryset.filter(command_type="clone_enrollment", payload__has_key="pushes")
        if value == "name_probe":
            return queryset.filter(command_type="clone_enrollment", payload__has_key="name_probe")
        if value == "switch_off":
            return queryset.filter(command_type="set_user_enabled", payload__enabled=False)
        if value == "switch_on":
            return queryset.filter(command_type="set_user_enabled", payload__enabled=True)
        if value == "background":
            return queryset.filter(command_type__in=DeviceCommand.BACKGROUND_TYPES)
        if value == "interrupted":
            return queryset.filter(payload__has_key="attempts")
        return queryset


@admin.register(DeviceCommand)
class DeviceCommandAdmin(ManagePermissionMixin, admin.ModelAdmin):
    """Commands are queued and answered by the system (the app, the planners, the gateway), so nothing here can be
    added, edited or deleted: the history stays true. Two actions are offered: cancel what is still waiting, and
    retry what failed by queueing a fresh copy."""

    list_display = ("number", "terminal", "command_type", "status_badge", "person", "summary", "created_at", "sent_at", "completed_at", "duration")
    list_display_links = ("number", "summary")
    list_filter = (("device", admin.RelatedOnlyFieldListFilter), "command_type", "status", JobKindFilter, ("created_at", admin.DateFieldListFilter))
    search_fields = ("device__name", "device__serial_number")
    search_help_text = "Terminal name or serial, the person's staff number or name, a terminal id (enrollid), or a command type."
    list_select_related = ("device", "requested_by")
    ordering = ("-created_at", "-id")
    list_per_page = 50
    show_full_result_count = False
    actions = ("cancel_pending", "retry_failed")

    fieldsets = (
        ("Command", {"fields": ("terminal", "command_type", "status_badge", "person", "summary", "requested_by")}),
        ("Timing", {"fields": ("created_at", "sent_at", "completed_at", "duration")}),
        ("Outcome", {"fields": ("outcome",)}),
        ("Raw data (technical, device data hidden)", {"fields": ("payload_json", "result_json"), "classes": ("collapse",)}),
    )
    readonly_fields = (
        "terminal", "command_type", "status_badge", "person", "summary", "requested_by", "created_at", "sent_at", "completed_at",
        "duration", "outcome", "payload_json", "result_json",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    # -- one query for the people / terminals on the page, not one per row --

    def get_changelist_instance(self, request):
        changelist = super().get_changelist_instance(request)
        rows = list(changelist.result_list)
        wanted = {pk for pk in (employee_pk(_payload(row)) for row in rows) if pk is not None}
        people = {employee.pk: employee for employee in Employee.objects.filter(pk__in=wanted).only("id", "employee_id", "first_name", "middle_name", "last_name")}
        names = dict(BiometricDevice.objects.values_list("pk", "name"))
        for row in rows:
            row._people, row._device_names = people, names
        return changelist

    def get_search_results(self, request, queryset, search_term):
        for term in search_term.split():
            match = Q(device__name__icontains=term) | Q(device__serial_number__icontains=term) | Q(command_type__icontains=term)
            who = Q(employee_id__iexact=term)
            if len(term) >= 2:  # a single letter would match half the staff
                who |= Q(first_name__icontains=term) | Q(last_name__icontains=term) | Q(middle_name__icontains=term)
            people = list(Employee.objects.filter(who).values_list("pk", flat=True)[:300])
            if people:
                match |= Q(payload__employee_id__in=people)
            if re.fullmatch(r"[0-9]{1,9}", term):
                number = int(term)  # a terminal id: the one queued for, or one end of a renumber
                match |= Q(payload__enrollid=number) | Q(payload__from_id=number) | Q(payload__to_id=number)
            queryset = queryset.filter(match)
        return queryset, False

    # -- columns --

    @admin.display(description="No.", ordering="id")
    def number(self, obj):
        return f"#{obj.pk}"

    @admin.display(description="Terminal", ordering="device__name")
    def terminal(self, obj):
        return obj.device.name

    @admin.display(description="Status", ordering="status")
    def status_badge(self, obj):
        return badge(STATUS_COLOURS.get(obj.status, GREY), obj.get_status_display())

    @admin.display(description="Person")
    def person(self, obj):
        payload = _payload(obj)
        pk = employee_pk(payload)
        if pk is not None:
            people = getattr(obj, "_people", None)
            employee = people.get(pk) if people is not None else Employee.objects.filter(pk=pk).first()
            if employee is not None:
                url = admin_url("admin:employees_employee_change", employee.pk)
                label = f"{employee.employee_id} - {employee.full_name}"
                return format_html('<a href="{}">{}</a>', url, label) if url else label
            gone = f"employee #{pk} (no longer in HRM)"
            return f"{payload['name']} - {gone}" if payload.get("name") else gone
        if payload.get("enrollid") is not None:
            return f"terminal id {payload['enrollid']}"
        return "-"

    @admin.display(description="What it does")
    def summary(self, obj):
        return command_summary(obj, getattr(obj, "_device_names", None) or dict(BiometricDevice.objects.values_list("pk", "name")))

    @admin.display(description="Took / waiting")
    def duration(self, obj):
        now = timezone.now()
        if obj.completed_at and obj.sent_at:
            return format_duration(obj.completed_at - obj.sent_at)
        if obj.status == "pending":
            return f"waiting {format_duration(now - obj.created_at)}"
        if obj.status == "sent" and obj.sent_at:
            return f"running {format_duration(now - obj.sent_at)}"
        return "-"

    @admin.display(description="Result")
    def outcome(self, obj):
        return result_summary(obj) or "-"

    @admin.display(description="Payload (as queued)")
    def payload_json(self, obj):
        return raw_json_html(obj.payload)

    @admin.display(description="Result (as received)")
    def result_json(self, obj):
        return raw_json_html(obj.result)

    # -- actions --

    @admin.action(description="Cancel selected pending commands", permissions=["manage"])
    def cancel_pending(self, request, queryset):
        selected = queryset.count()
        detail = f"Cancelled by {request.user.get_username()}"
        # The status test is part of the UPDATE itself: a command the gateway picked up a moment ago is not touched.
        cancelled = queryset.filter(status="pending").update(status="failed", result={"detail": detail}, completed_at=timezone.now())
        if cancelled:
            self.message_user(request, f"Cancelled {cancelled} pending command(s); they stay in the list, marked failed.", messages.SUCCESS)
        if selected - cancelled:
            self.message_user(request, f"{selected - cancelled} selected command(s) were not pending, so they were left alone.", messages.WARNING)

    @admin.action(description="Retry selected failed commands (queues fresh copies)", permissions=["manage"])
    def retry_failed(self, request, queryset):
        now = timezone.now()
        selected = list(queryset.select_related("device")[: RETRY_MAX_BATCH * 5])
        skipped, queued = Counter(), 0
        for command in selected:
            if queued >= RETRY_MAX_BATCH:
                skipped[f"over the limit of {RETRY_MAX_BATCH} per action (the terminals work one job at a time; run it again once they have drained)"] += 1
                continue
            reason = retry_blocked_reason(command, now)
            if reason:
                skipped[reason] += 1
                continue
            payload = {key: value for key, value in _payload(command).items() if key != "attempts"}
            waiting = DeviceCommand.objects.filter(device=command.device, command_type=command.command_type, status__in=("pending", "sent"))
            if any(_same_job(other.payload, payload) for other in waiting):
                skipped["the same job is already waiting or running"] += 1
                continue
            DeviceCommand.objects.create(device=command.device, command_type=command.command_type, payload=payload, requested_by=request.user)
            queued += 1
        if queued:
            self.message_user(request, f"Queued {queued} fresh cop{'y' if queued == 1 else 'ies'} as pending. The original rows are unchanged.", messages.SUCCESS)
        for reason, count in skipped.items():
            self.message_user(request, f"Not retried ({count}): {reason}.", messages.WARNING)
        if not queued and not skipped:
            self.message_user(request, "Nothing was selected.", messages.INFO)


# ---- shifts ----------------------------------------------------------------------------------------------------


def _clock(value):
    return value.strftime("%H:%M") if hasattr(value, "strftime") else str(value)[:5]


def _employee_link(employee, text):
    url = admin_url("admin:employees_employee_change", employee.pk)
    return format_html('<a href="{}">{}</a>', url, text) if url else text


def _current_filter(prefix, today):
    """Q for 'assigned today' on the relation `prefix` ('' for the model itself)."""
    return Q(**{f"{prefix}start_date__lte": today}) & (Q(**{f"{prefix}end_date__isnull": True}) | Q(**{f"{prefix}end_date__gte": today}))


class AssignmentStateFilter(admin.SimpleListFilter):
    title = "state"
    parameter_name = "state"

    def lookups(self, request, model_admin):
        return [("current", "Current"), ("upcoming", "Starts later"), ("ended", "Ended")]

    def queryset(self, request, queryset):
        today = timezone.localdate()
        if self.value() == "current":
            return queryset.filter(_current_filter("", today))
        if self.value() == "upcoming":
            return queryset.filter(start_date__gt=today)
        if self.value() == "ended":
            return queryset.filter(end_date__lt=today)
        return queryset


def _state_badge(obj):
    today = timezone.localdate()
    if obj.start_date > today:
        return badge(BLUE, "Starts later")
    if obj.end_date is not None and obj.end_date < today:
        return badge(GREY, "Ended")
    return badge(GREEN, "Current")


@admin.register(Shift)
class ShiftAdmin(admin.ModelAdmin):
    list_display = ("name", "times", "hours", "is_overnight", "active", "people_now", "overnight_check")
    list_filter = ("active", "is_overnight")
    search_fields = ("name",)
    ordering = ("start_time", "name")

    def get_queryset(self, request):
        today = timezone.localdate()
        return super().get_queryset(request).annotate(people_count=Count("assignments", filter=_current_filter("assignments__", today)))

    @admin.display(description="Times", ordering="start_time")
    def times(self, obj):
        return f"{_clock(obj.start_time)} - {_clock(obj.end_time)}"

    @admin.display(description="Length")
    def hours(self, obj):
        start, end = obj.start_time, obj.end_time
        minutes = (end.hour * 60 + end.minute) - (start.hour * 60 + start.minute)
        if minutes <= 0:
            minutes += 24 * 60
        return f"{minutes / 60:g} h"

    @admin.display(description="People on it today", ordering="people_count")
    def people_now(self, obj):
        return obj.people_count

    @admin.display(description="Check")
    def overnight_check(self, obj):
        crosses = obj.end_time <= obj.start_time
        if crosses and not obj.is_overnight:
            return badge(RED, "Crosses midnight, overnight is off")
        if obj.is_overnight and not crosses:
            return badge(RED, "Overnight is on, times do not cross midnight")
        return "ok"


@admin.register(ShiftAssignment)
class ShiftAssignmentAdmin(admin.ModelAdmin):
    list_display = ("staff_no", "employee_name", "shift", "start_date", "end_date", "state", "assigned_by", "created_at")
    list_display_links = ("staff_no", "employee_name")
    list_filter = (AssignmentStateFilter, ("shift", admin.RelatedOnlyFieldListFilter), ("employee__department", admin.RelatedOnlyFieldListFilter), ("start_date", admin.DateFieldListFilter))
    search_fields = ("employee__employee_id", "employee__first_name", "employee__last_name", "shift__name", "assigned_by")
    search_help_text = "Staff number, employee name, shift name or who assigned it."
    list_select_related = ("employee", "shift")
    autocomplete_fields = ("employee", "shift")
    date_hierarchy = "start_date"
    ordering = ("-start_date", "-id")
    fields = ("employee", "shift", "start_date", "end_date", "assigned_by", "created_at")
    readonly_fields = ("created_at",)

    @admin.display(description="Staff no.", ordering="employee__employee_id")
    def staff_no(self, obj):
        return obj.employee.employee_id

    @admin.display(description="Employee", ordering="employee__first_name")
    def employee_name(self, obj):
        return obj.employee.full_name

    @admin.display(description="State")
    def state(self, obj):
        return _state_badge(obj)


WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def weekday_text(days):
    """[0,1,2,3,4,5] -> 'Mon-Sat'; [0,2] -> 'Mon, Wed'. Ignores anything that is not 0-6."""
    days = sorted({day for day in (days if isinstance(days, list) else []) if isinstance(day, int) and not isinstance(day, bool) and 0 <= day <= 6})
    if not days:
        return "-"
    runs, start = [], 0
    for index in range(1, len(days) + 1):
        if index == len(days) or days[index] != days[index - 1] + 1:
            runs.append(days[start:index])
            start = index
    return ", ".join(f"{WEEKDAYS[run[0]]}-{WEEKDAYS[run[-1]]}" if len(run) >= 3 else ", ".join(WEEKDAYS[day] for day in run) for run in runs)


@admin.register(ShiftPlan)
class ShiftPlanAdmin(admin.ModelAdmin):
    list_display = ("name", "kind", "shifts", "days", "anchor", "active", "people_now")
    list_filter = ("kind", "active")
    search_fields = ("name", "description")
    autocomplete_fields = ("shift", "day_shift", "night_shift")
    ordering = ("name",)

    def get_queryset(self, request):
        today = timezone.localdate()
        return super().get_queryset(request).select_related("shift", "day_shift", "night_shift").annotate(
            people_count=Count("assignments", filter=_current_filter("assignments__", today)),
        )

    @admin.display(description="Shift(s)")
    def shifts(self, obj):
        if obj.kind == "rotation":
            return f"Day: {obj.day_shift.name if obj.day_shift else '?'} / Night: {obj.night_shift.name if obj.night_shift else '?'}"
        return obj.shift.name if obj.shift else "?"

    @admin.display(description="Working days")
    def days(self, obj):
        return weekday_text(obj.working_weekdays) if obj.kind == "fixed" else "weekly rotation"

    @admin.display(description="Anchor Monday", ordering="anchor_monday")
    def anchor(self, obj):
        if obj.anchor_monday is None:
            return "-"
        if obj.anchor_monday.weekday() != 0:
            return format_html("{} {}", obj.anchor_monday, badge(RED, "not a Monday"))
        return str(obj.anchor_monday)

    @admin.display(description="People on it today", ordering="people_count")
    def people_now(self, obj):
        return obj.people_count


@admin.register(ShiftPlanAssignment)
class ShiftPlanAssignmentAdmin(admin.ModelAdmin):
    list_display = ("staff_no", "employee_name", "plan", "group", "start_date", "end_date", "state", "assigned_by", "created_at")
    list_display_links = ("staff_no", "employee_name")
    list_filter = (AssignmentStateFilter, ("plan", admin.RelatedOnlyFieldListFilter), "group", ("employee__department", admin.RelatedOnlyFieldListFilter), ("start_date", admin.DateFieldListFilter))
    search_fields = ("employee__employee_id", "employee__first_name", "employee__last_name", "plan__name", "assigned_by")
    search_help_text = "Staff number, employee name, plan name or who assigned it."
    list_select_related = ("employee", "plan")
    autocomplete_fields = ("employee", "plan")
    date_hierarchy = "start_date"
    ordering = ("-start_date", "-id")
    fields = ("employee", "plan", "group", "start_date", "end_date", "assigned_by", "created_at")
    readonly_fields = ("created_at",)

    @admin.display(description="Staff no.", ordering="employee__employee_id")
    def staff_no(self, obj):
        return obj.employee.employee_id

    @admin.display(description="Employee", ordering="employee__first_name")
    def employee_name(self, obj):
        return obj.employee.full_name

    @admin.display(description="State")
    def state(self, obj):
        return _state_badge(obj)

"""A friendlier review queue for attendance exceptions: filter on the server, page through the results, decide many
at once, and apply ready-made rules ("waive lateness within the allowance"...) to everything that matches.

Every decision goes through the same steps as the single-case review (the status and reviewer are recorded, an absence
that is approved updates the meal penalties, and each decision is written to the audit trail)."""

from datetime import date, timedelta
from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from attendance.models import AttendanceException
from attendance.serializers import AttendanceExceptionSerializer
from meals.services import MealService

from .exceptions import CanReviewAttendanceExceptions, ExceptionDecisionAPIView

MAX_PAGE_SIZE = 200
MAX_BULK_IDS = 300
MAX_MATCHING_IDS = 6000
TYPES = [value for value, _ in AttendanceException.EXCEPTION_TYPES]


def go_live_date():
    return date.fromisoformat(settings.ATTENDANCE_GO_LIVE_DATE)


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _day(value):
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


def filtered_queryset(params, *, default_status="pending"):
    """The exceptions matching the page's filters. `status`: pending (default), reviewed (anything decided), all, or one status."""
    queryset = AttendanceException.objects.select_related("attendance", "attendance__employee", "attendance__employee__department", "attendance__shift")
    state = params.get("status") or default_status
    if state == "reviewed":
        queryset = queryset.exclude(status="pending")
    elif state != "all":
        queryset = queryset.filter(status=state)
    if params.get("type") in TYPES:
        queryset = queryset.filter(exception_type=params["type"])
    if types := [t for t in (params.get("types") or "").split(",") if t in TYPES]:
        queryset = queryset.filter(exception_type__in=types)
    if (start := _day(params.get("date_from"))) is not None:
        queryset = queryset.filter(attendance__date__gte=start)
    if (end := _day(params.get("date_to"))) is not None:
        queryset = queryset.filter(attendance__date__lte=end)
    if (department := _int(params.get("department"))) is not None:
        queryset = queryset.filter(attendance__employee__department_id=department)
    if (minimum := _int(params.get("min_minutes"))) is not None:
        queryset = queryset.filter(minutes_affected__gte=minimum)
    if (maximum := _int(params.get("max_minutes"))) is not None:
        queryset = queryset.filter(minutes_affected__lte=maximum)
    if term := (params.get("search") or "").strip():
        queryset = queryset.filter(
            Q(attendance__employee__first_name__icontains=term) | Q(attendance__employee__last_name__icontains=term)
            | Q(attendance__employee__employee_id__icontains=term) | Q(attendance__employee__department__name__icontains=term)
        )
    if state == "pending":
        return queryset.order_by("attendance__date", "attendance__employee__first_name", "id")
    return queryset.order_by("-reviewed_at", "-id")


def rule_definitions():
    """Ready-made rules: a filter on pending exceptions plus the decision and the reason recorded with it."""
    live = go_live_date()
    allowance = settings.ATTENDANCE_LATE_ALLOWANCE_MINUTES
    long_late = settings.ATTENDANCE_LONG_LATE_MINUTES
    before = (live - timedelta(days=1)).isoformat()
    return [
        {
            "key": "before_go_live", "label": f"Waive everything before {live:%d %b %Y}",
            "description": "The attendance terminals were still being rolled out, so these days are not a reliable record (on the first day nearly everyone shows absent).",
            "decision": "waived", "filters": {"date_to": before},
            "comment": f"Before the attendance terminals went live on {live:%d %b %Y}: the record for these days is not reliable.",
        },
        {
            "key": "short_lateness", "label": f"Waive lateness of up to {allowance} minutes",
            "description": f"Arriving up to {allowance} minutes late is within the allowance. Only from {live:%d %b %Y} on.",
            "decision": "waived", "filters": {"type": "late", "max_minutes": str(allowance), "date_from": live.isoformat()},
            "comment": f"Within the {allowance}-minute lateness allowance.",
        },
        {
            "key": "long_lateness", "label": f"Approve lateness of more than {long_late} minutes",
            "description": f"Arriving more than {long_late} minutes late is a clear case. The proposed deduction goes into payroll. Only from {live:%d %b %Y} on.",
            "decision": "approved", "filters": {"type": "late", "min_minutes": str(long_late + 1), "date_from": live.isoformat()},
            "comment": f"Late by more than {long_late} minutes.",
        },
        {
            "key": "missing_punches", "label": "Hold missing clock-ins and clock-outs",
            "description": f"A missing punch is usually the terminal or a forgotten scan, so it is held for someone to check rather than charged. Only from {live:%d %b %Y} on.",
            "decision": "held", "filters": {"types": "missing_clock_in,missing_clock_out", "date_from": live.isoformat()},
            "comment": "Missing punch - check the terminal and ask the employee before deciding.",
        },
        {
            "key": "absences_hold", "label": "Hold absences to investigate",
            "description": f"An absence is not charged until someone has checked it (an approved absence also reduces meal tickets). Only from {live:%d %b %Y} on.",
            "decision": "held", "filters": {"type": "absence", "date_from": live.isoformat()},
            "comment": "Absence - investigate (leave, permission, device problem) before deciding.",
        },
    ]


def decide(exception, decision, comment, user=None, *, reviewer_name=None):
    """One decision, exactly as the single-case review records it. Caller holds the row lock.
    `user` is the signed-in reviewer; the command line passes reviewer_name instead."""
    exception.status = decision
    exception.admin_comment = comment
    exception.reviewed_at = timezone.now()
    exception.reviewed_by = reviewer_name or (user.get_full_name() or user.username)
    exception.save()
    if exception.exception_type == "absence" and decision == "approved":
        MealService.sync_absence_penalties(exception.attendance.employee, exception.attendance.date, actor=user)
    ExceptionDecisionAPIView._log_decision(exception, user)


class ExceptionQueueAPIView(APIView):
    """A page of exceptions for the given filters, with the totals the page shows above it."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        queryset = filtered_queryset(request.query_params)
        page_size = min(max(_int(request.query_params.get("page_size")) or 50, 1), MAX_PAGE_SIZE)
        page = max(_int(request.query_params.get("page")) or 1, 1)
        count = queryset.count()
        start = (page - 1) * page_size
        by_type = {row["exception_type"]: row["total"] for row in queryset.order_by().values("exception_type").annotate(total=Count("id"))}
        return Response({
            "count": count,
            "page": page,
            "page_size": page_size,
            "total_proposed_deduction": str(Decimal(queryset.aggregate(total=Sum("proposed_deduction"))["total"] or 0).quantize(Decimal("0.01"))),
            "by_type": by_type,
            "results": AttendanceExceptionSerializer(queryset[start:start + page_size], many=True).data,
        })


class ExceptionMatchingIdsAPIView(APIView):
    """Every pending exception that matches the filters (for "select all N"), capped."""

    permission_classes = [IsAuthenticated, CanReviewAttendanceExceptions]

    def get(self, request):
        ids = list(filtered_queryset(request.query_params, default_status="pending").filter(status="pending").values_list("id", flat=True)[:MAX_MATCHING_IDS + 1])
        return Response({"ids": ids[:MAX_MATCHING_IDS], "truncated": len(ids) > MAX_MATCHING_IDS})


class ExceptionRulesAPIView(APIView):
    """The ready-made rules, each with how many pending exceptions it would decide right now."""

    permission_classes = [IsAuthenticated, CanReviewAttendanceExceptions]

    def get(self, request):
        rules = rule_definitions()
        for rule in rules:
            rule["matching"] = filtered_queryset(rule["filters"]).filter(status="pending").count()
        return Response({"results": rules})


class ExceptionBulkDecisionAPIView(APIView):
    """Decide many pending exceptions at once (up to 300 a call; the page sends bigger selections in several calls)."""

    permission_classes = [IsAuthenticated, CanReviewAttendanceExceptions]

    def post(self, request):
        ids = request.data.get("ids")
        decision = request.data.get("decision")
        comment = str(request.data.get("comment") or "").strip()
        if not isinstance(ids, list) or not ids or not all(isinstance(value, int) for value in ids):
            return Response({"detail": "Choose at least one case."}, status=status.HTTP_400_BAD_REQUEST)
        if len(ids) > MAX_BULK_IDS:
            return Response({"detail": f"At most {MAX_BULK_IDS} cases can be decided in one request."}, status=status.HTTP_400_BAD_REQUEST)
        if decision not in ("approved", "waived", "held"):
            return Response({"detail": "Choose approve, waive or hold."}, status=status.HTTP_400_BAD_REQUEST)
        if decision in ("waived", "held") and not comment:
            return Response({"detail": "A reason is required when waiving or holding."}, status=status.HTTP_400_BAD_REQUEST)
        decided, skipped = 0, 0
        with transaction.atomic():
            rows = AttendanceException.objects.select_for_update(of=("self",)).select_related("attendance", "attendance__employee").filter(pk__in=ids)
            for exception in rows:
                if exception.status != "pending":
                    skipped += 1
                    continue
                decide(exception, decision, comment, request.user)
                decided += 1
        return Response({"decided": decided, "skipped": skipped + (len(set(ids)) - rows.count())})

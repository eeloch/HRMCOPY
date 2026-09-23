from datetime import date

from django.http import HttpResponse

from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from audit.models import AuditSeverity
from audit.services import AuditService

from .pptx_builder import build_weekly_report_pptx
from .services import build_weekly_report


def _parse_week_start(request):
    raw = request.query_params.get("week_start")
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return "invalid"


class WeeklyReportSummaryAPIView(APIView):
    """The same numbers the slide deck uses, as JSON - so the page can show a preview before generating
    the file."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        week_start = _parse_week_start(request)
        if week_start == "invalid":
            return Response({"detail": "week_start must be an ISO date (YYYY-MM-DD)."}, status=status.HTTP_400_BAD_REQUEST)

        data = build_weekly_report(week_start)
        return Response({
            "week_start": data.week_start,
            "week_end": data.week_end,
            "week_number": data.week_number,
            "workforce": {
                "total_employees": data.total_employees,
                "active_employees": data.active_employees,
                "inactive_employees": data.inactive_employees,
                "by_department": [{"name": r.name, "count": r.count} for r in data.by_department],
                "by_employment_type": [{"name": r.name, "count": r.count} for r in data.by_employment_type],
                "new_hires": len(data.new_hires),
                "exits": len(data.exits),
            },
            "attendance": {
                "present": data.attendance_present,
                "late": data.attendance_late,
                "absent": data.attendance_absent,
                "on_leave": data.attendance_on_leave,
                "night_shift": data.night_shift_records,
                "overtime": data.overtime_records,
            },
            "leave": {
                "submitted": data.leave_submitted,
                "approved": data.leave_approved,
                "pending": data.leave_pending,
                "rejected": data.leave_rejected,
                "cancelled": data.leave_cancelled,
                "days_approved": data.leave_days_approved,
            },
            "meals": {
                "total": data.meal_collections,
                "within_entitlement": data.meal_within_entitlement,
                "excess": data.meal_excess,
            },
            "comparison": {
                "previous_week_start": data.previous_week_start,
                "previous_week_end": data.previous_week_end,
                "attendance_rate": round(data.attendance_rate, 1),
                "previous_attendance_rate": round(data.previous_attendance_rate, 1),
                "headline": [
                    {"label": r.label, "previous": r.previous, "current": r.current, "growth_pct": round(r.growth_pct, 1)}
                    for r in data.headline_comparison
                ],
            },
        })


class WeeklyReportPresentationAPIView(APIView):
    """Generates and downloads the Weekly HR Report as a .pptx."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        week_start = _parse_week_start(request)
        if week_start == "invalid":
            return Response({"detail": "week_start must be an ISO date (YYYY-MM-DD)."}, status=status.HTTP_400_BAD_REQUEST)

        data = build_weekly_report(week_start)
        content = build_weekly_report_pptx(data)

        AuditService.log(
            event_type="reports.weekly_report_generated",
            module="reports",
            actor=request.user,
            severity=AuditSeverity.INFO,
            title="Weekly HR report generated",
            description=f"Weekly HR report generated for week {data.week_number} ({data.week_start} - {data.week_end}).",
            metadata={"week_start": data.week_start.isoformat(), "week_end": data.week_end.isoformat()},
        )

        response = HttpResponse(content, content_type="application/vnd.openxmlformats-officedocument.presentationml.presentation")
        filename = f"Week {data.week_number} HR Report.pptx"
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response

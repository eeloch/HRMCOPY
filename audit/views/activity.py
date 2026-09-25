from datetime import date

from django.db.models import Q

from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from audit.models import AuditEvent, AuditSeverity
from audit.serializers import AuditEventSerializer


# Modules whose events carry money (amounts, deductions, salary figures). A user only sees a module's events when they
# hold at least one of its permissions; a superuser sees everything. (2026-09-25 review: any login could read every
# bonus, advance and payroll adjustment amount through this feed.)
FINANCIAL_MODULE_PERMISSIONS = {
    "payroll": ("payroll.view_payroll", "payroll.manage_payroll"),
    "bonuses": ("bonuses.view_bonuses", "bonuses.record_bonus", "bonuses.approve_bonus"),
    "advances": ("advances.record_salary_advance", "advances.approve_salary_advance", "advances.pay_salary_advance"),
    "deferred_funds": ("deferredfunds.view_deferred_funds", "deferredfunds.manage_deferred_funds", "deferredfunds.approve_deferred_withdrawal", "deferredfunds.pay_deferred_withdrawal"),
    "ppe": ("ppe.record_ppe_issue", "ppe.review_ppe_deduction"),
    "offences": ("offences.record_employee_offences", "offences.review_employee_offences", "offences.manage_offence_configuration"),
    "meals": ("meals.record_meal_operations", "meals.review_meal_excess", "meals.manage_meal_configuration"),
}


def hidden_financial_modules(user):
    """The financial modules this user may not read audit events for."""
    if user.is_superuser:
        return []
    return [module for module, permissions in FINANCIAL_MODULE_PERMISSIONS.items() if not any(user.has_perm(code) for code in permissions)]


class AuditActivityAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        queryset = self._get_queryset(request)
        if isinstance(queryset, Response):
            return queryset

        page, page_size = self._get_pagination(request)
        count = queryset.count()
        start = (page - 1) * page_size
        events = queryset[start : start + page_size]

        return Response(
            {
                "count": count,
                "page": page,
                "page_size": page_size,
                "total_pages": max(1, (count + page_size - 1) // page_size),
                "results": AuditEventSerializer(events, many=True).data,
            }
        )

    @staticmethod
    def _get_queryset(request):
        events = AuditEvent.objects.select_related(
            "employee",
            "employee__department",
            "actor",
            "content_type",
        ).order_by("-created_at")

        hidden = hidden_financial_modules(request.user)
        if hidden:
            events = events.exclude(module__in=hidden)

        search = request.query_params.get("search", "").strip()
        employee = request.query_params.get("employee")
        module = request.query_params.get("module")
        severity = request.query_params.get("severity")
        date_from = request.query_params.get("date_from")
        date_to = request.query_params.get("date_to")

        if search:
            events = events.filter(
                Q(title__icontains=search)
                | Q(description__icontains=search)
                | Q(event_type__icontains=search)
                | Q(employee__employee_id__icontains=search)
                | Q(employee__first_name__icontains=search)
                | Q(employee__last_name__icontains=search)
                | Q(actor__username__icontains=search)
            )

        if employee:
            try:
                events = events.filter(employee_id=int(employee))
            except ValueError:
                return Response(
                    {"detail": "Employee must be a valid employee ID."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        if module:
            events = events.filter(module=module)
        if severity:
            valid_severities = {value for value, _ in AuditSeverity.choices}
            if severity not in valid_severities:
                return Response(
                    {"detail": "Severity must be a valid audit severity."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            events = events.filter(severity=severity)

        for parameter, lookup in ((date_from, "created_at__date__gte"), (date_to, "created_at__date__lte")):
            if parameter:
                try:
                    events = events.filter(**{lookup: date.fromisoformat(parameter)})
                except ValueError:
                    return Response(
                        {"detail": "Dates must use YYYY-MM-DD format."},
                        status=status.HTTP_400_BAD_REQUEST,
                    )

        return events

    @staticmethod
    def _get_pagination(request):
        try:
            page = max(1, int(request.query_params.get("page", 1)))
            page_size = int(request.query_params.get("page_size", 20))
        except ValueError:
            return 1, 20

        return page, min(max(page_size, 1), 100)

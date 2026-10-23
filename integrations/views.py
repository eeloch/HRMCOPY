"""The employee feed for the company ERP: read-only, by API key, newest changes on request.

GET /api/integrations/erp/employees/?updated_since=<ISO time>&page=<n>&page_size=<n>
GET /api/integrations/erp/employees/<staff number>/
Salary and bank details are never included."""

from datetime import datetime

from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework.response import Response
from rest_framework.views import APIView

from employees.models import Employee

from .authentication import HasIntegrationKey, IntegrationKeyAuthentication

DEFAULT_PAGE_SIZE = 200
MAX_PAGE_SIZE = 500


def employee_record(employee):
    return {
        "staff_number": employee.employee_id,
        "first_name": employee.first_name,
        "middle_name": employee.middle_name or "",
        "last_name": employee.last_name,
        "full_name": employee.full_name,
        "department": employee.department.name if employee.department_id else None,
        "position": employee.position.name if employee.position_id else None,
        "employment_type": employee.employment_type or None,
        "employment_category": employee.employment_category or None,
        "status": employee.status,
        "gender": employee.gender or None,
        "employment_date": employee.employment_date.isoformat() if employee.employment_date else None,
        "exit_date": employee.exit_date.isoformat() if employee.exit_date else None,
        "phone": employee.phone or None,
        "email": employee.email or None,
        "updated_at": employee.updated_at.isoformat() if employee.updated_at else None,
    }


class IntegrationView(APIView):
    authentication_classes = [IntegrationKeyAuthentication]
    permission_classes = [HasIntegrationKey]


class ERPEmployeeListAPIView(IntegrationView):
    def get(self, request):
        employees = Employee.objects.select_related("department", "position").order_by("updated_at", "id")
        since_text = request.query_params.get("updated_since", "").strip()
        if since_text:
            since = parse_datetime(since_text.replace(" ", "+"))
            if since is None:
                try:
                    since = datetime.fromisoformat(since_text)
                except ValueError:
                    return Response({"detail": "updated_since must be an ISO date-time, e.g. 2026-10-23T09:00:00+01:00."}, status=400)
            if timezone.is_naive(since):
                since = timezone.make_aware(since)
            employees = employees.filter(updated_at__gt=since)
        if request.query_params.get("status") in ("active", "inactive", "suspended", "terminated"):
            employees = employees.filter(status=request.query_params["status"])
        try:
            page = max(int(request.query_params.get("page", 1)), 1)
            page_size = min(max(int(request.query_params.get("page_size", DEFAULT_PAGE_SIZE)), 1), MAX_PAGE_SIZE)
        except ValueError:
            return Response({"detail": "page and page_size must be numbers."}, status=400)
        total = employees.count()
        rows = list(employees[(page - 1) * page_size: page * page_size])
        return Response({
            "count": total,
            "page": page,
            "page_size": page_size,
            "next_page": page + 1 if page * page_size < total else None,
            # Send this back as updated_since next time to get only what changed after this call.
            "server_time": timezone.now().isoformat(),
            "results": [employee_record(employee) for employee in rows],
        })


class ERPEmployeeDetailAPIView(IntegrationView):
    def get(self, request, staff_number):
        employee = get_object_or_404(Employee.objects.select_related("department", "position"), employee_id=staff_number)
        return Response(employee_record(employee))

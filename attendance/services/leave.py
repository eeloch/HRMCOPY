from leave.models import LeaveRequest, LeaveStatus


def approved_leave_employee_ids(on_date):
    """Return employees with an approved leave decision covering the given date."""
    return LeaveRequest.objects.filter(
        status__in=[LeaveStatus.APPROVED, LeaveStatus.PARTIALLY_APPROVED],
        approved_start_date__lte=on_date,
        approved_end_date__gte=on_date,
    ).values_list("employee_id", flat=True).distinct()

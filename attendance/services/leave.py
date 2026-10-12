from leave.models import LeaveRequest, LeaveStatus


def approved_leave_employee_ids(on_date):
    """Return employees with an approved leave decision covering the given date."""
    return LeaveRequest.objects.filter(
        status__in=[LeaveStatus.APPROVED, LeaveStatus.PARTIALLY_APPROVED],
        approved_start_date__lte=on_date,
        approved_end_date__gte=on_date,
    ).values_list("employee_id", flat=True).distinct()


def leave_dates(employee, start, end):
    """The dates between start and end covered by the employee's approved leave."""
    from datetime import timedelta

    dates = set()
    for approved_start, approved_end in LeaveRequest.objects.filter(
        employee=employee,
        status__in=[LeaveStatus.APPROVED, LeaveStatus.PARTIALLY_APPROVED],
        approved_start_date__lte=end,
        approved_end_date__gte=start,
    ).values_list("approved_start_date", "approved_end_date"):
        day = max(approved_start, start)
        while day <= min(approved_end, end):
            dates.add(day)
            day += timedelta(days=1)
    return dates

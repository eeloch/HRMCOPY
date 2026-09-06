from django.db import transaction
from django.utils import timezone

from leave.models import LeaveRequest, LeaveStatus
from leave.services.balance import LeaveBalanceService
from leave.services.request import LeaveRequestService


class LeaveApprovalError(ValueError):
    pass


class LeaveApprovalService:
    """Safely decide a pending leave request without duplicating balance state."""

    @classmethod
    def approve(cls, request_id, acting_user):
        with transaction.atomic():
            leave_request = cls._lock_pending_request(request_id)
            cls._validate_balance(leave_request, leave_request.total_days)
            cls._apply_decision(
                leave_request,
                acting_user,
                status=LeaveStatus.APPROVED,
                approved_start_date=leave_request.start_date,
                approved_end_date=leave_request.end_date,
                approved_days=leave_request.total_days,
            )
            return leave_request

    @classmethod
    def partially_approve(cls, request_id, acting_user, approved_start_date, approved_end_date):
        with transaction.atomic():
            leave_request = cls._lock_pending_request(request_id)
            if not approved_start_date or not approved_end_date:
                raise LeaveApprovalError("Approved start date and end date are required.")
            if (
                approved_start_date < leave_request.start_date
                or approved_end_date > leave_request.end_date
            ):
                raise LeaveApprovalError("The approved range must be within the requested leave period.")
            try:
                approved_days = LeaveRequestService.calculate_leave_days(
                    approved_start_date,
                    approved_end_date,
                    leave_request.duration_type,
                )
            except ValueError as error:
                raise LeaveApprovalError(str(error)) from error
            if approved_days <= 0:
                raise LeaveApprovalError("Approved leave days must be greater than zero.")
            if approved_days > leave_request.total_days:
                raise LeaveApprovalError(
                    "Approved leave days cannot exceed the original requested amount."
                )

            cls._validate_balance(leave_request, approved_days)
            decision_status = (
                LeaveStatus.APPROVED
                if approved_days == leave_request.total_days
                else LeaveStatus.PARTIALLY_APPROVED
            )
            cls._apply_decision(
                leave_request,
                acting_user,
                status=decision_status,
                approved_start_date=approved_start_date,
                approved_end_date=approved_end_date,
                approved_days=approved_days,
            )
            return leave_request

    @classmethod
    def reject(cls, request_id, acting_user, rejection_reason):
        with transaction.atomic():
            leave_request = cls._lock_pending_request(request_id)
            if not rejection_reason or not rejection_reason.strip():
                raise LeaveApprovalError("A rejection reason is required.")
            cls._apply_decision(
                leave_request,
                acting_user,
                status=LeaveStatus.REJECTED,
                rejection_reason=rejection_reason.strip(),
            )
            return leave_request

    @staticmethod
    def _lock_pending_request(request_id):
        try:
            leave_request = LeaveRequest.objects.select_for_update().select_related(
                "employee",
                "leave_type",
                "requested_by",
            ).get(pk=request_id)
        except LeaveRequest.DoesNotExist as error:
            raise error
        if leave_request.status != LeaveStatus.PENDING:
            raise LeaveApprovalError("Only pending leave requests can be decided.")
        return leave_request

    @staticmethod
    def _validate_balance(leave_request, days):
        balance = LeaveBalanceService.get_balance(
            leave_request.employee,
            leave_request.leave_type,
            year=leave_request.start_date.year,
            as_of=leave_request.start_date,
        )
        if not balance["matched_policy"]:
            raise LeaveApprovalError("No active leave policy matches this employee.")
        if days > balance["remaining_days"]:
            raise LeaveApprovalError("The employee no longer has enough leave entitlement.")

    @staticmethod
    def _apply_decision(
        leave_request,
        acting_user,
        *,
        status,
        approved_start_date=None,
        approved_end_date=None,
        approved_days=None,
        rejection_reason="",
    ):
        leave_request.status = status
        leave_request.approved_by = acting_user
        leave_request.approved_at = timezone.now()
        leave_request.approved_start_date = approved_start_date
        leave_request.approved_end_date = approved_end_date
        leave_request.approved_days = approved_days
        leave_request.rejection_reason = rejection_reason
        leave_request.save()

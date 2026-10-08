from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db.models import Count, Max, Q, Sum
from django.shortcuts import get_object_or_404
from django.utils import timezone

from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from employees.models import Employee
from notifications.services import NotificationService

from .models import (
    EmployeeMealEntitlement,
    MealCollection,
    MealDevice,
    MealEntitlementRule,
    MealExcessException,
    MealTicketRate,
    MealChargeback,
    MealVendorClaim,
    MealVendorPayment,
)
from .serializers import (
    EmployeeMealEntitlementSerializer,
    MealDeviceSerializer,
    MealEntitlementRuleSerializer,
    MealTicketRateSerializer,
    MealVendorClaimSerializer,
    MealVendorPaymentSerializer,
)
from decimal import Decimal

from .services import MealService, allocate_vendor_claim, cancel_chargeback, charge_back_tickets, chargeable_tickets
from payroll.models import PayrollPeriod

CARD_VERIFICATION_MODE = 3  # confirmed 2026-09-23: matches meals/bridge.py's CARD_VERIFICATION_MODE


def _card_verified_map(rows):
    """Whether the collection that produced each pending exception was a card scan - see
    meals/bridge.py's _notify_card_gating_bypass for why that matters here. One query for the
    whole page instead of one per row."""
    if not rows:
        return {}
    keys = {(x.employee_id, x.work_date, x.collected_quantity) for x in rows}
    employee_ids = {key[0] for key in keys}
    work_dates = {key[1] for key in keys}
    by_key = {}
    for collection in (
        MealCollection.objects.filter(employee_id__in=employee_ids, work_date__in=work_dates)
        .select_related("event")
    ):
        by_key[(collection.employee_id, collection.work_date, collection.sequence_number)] = collection
    return {
        x.pk: bool((collection := by_key.get((x.employee_id, x.work_date, x.collected_quantity))))
        and collection.event.raw_payload.get("mode") == CARD_VERIFICATION_MODE
        for x in rows
    }


class CanViewMealOperations(BasePermission):
    def has_permission(self, request, view):
        return (
            request.user.has_perm("meals.record_meal_operations")
            or request.user.has_perm("meals.review_meal_excess")
            or request.user.has_perm("meals.manage_meal_configuration")
        )



class CanViewVendorPayments(BasePermission):
    """The vendor figures have their own permission, so accounts can see them without any other meal access.
    Everyone who could see them before (any meal permission) still can."""

    def has_permission(self, request, view):
        return request.user.has_perm("meals.view_meal_vendor_payments") or request.user.has_perm("meals.record_meal_vendor_payments") or CanViewMealOperations().has_permission(request, view)


class CanRecordVendorPayments(BasePermission):
    def has_permission(self, request, view):
        return request.user.has_perm("meals.record_meal_vendor_payments") or request.user.has_perm("meals.manage_meal_configuration")


class CanChargeBackMealTickets(BasePermission):
    """Charging an employee for tickets they already took: its own right; anyone who reviews meal excess may too."""

    def has_permission(self, request, view):
        return request.user.has_perm("meals.charge_back_meal_tickets") or request.user.has_perm("meals.review_meal_excess")


class CanSeeChargebacks(BasePermission):
    def has_permission(self, request, view):
        return CanChargeBackMealTickets().has_permission(request, view) or CanViewMealOperations().has_permission(request, view)


class CanReviewMealExcess(BasePermission):
    def has_permission(self, request, view):
        return request.user.has_perm("meals.review_meal_excess")


class CanManageMealConfiguration(BasePermission):
    def has_permission(self, request, view):
        return request.user.has_perm("meals.manage_meal_configuration")


class MealEntitlementListCreateAPIView(APIView):
    def get_permissions(self):
        permission = (
            CanManageMealConfiguration
            if self.request.method == "POST"
            else CanViewMealOperations
        )
        return [IsAuthenticated(), permission()]

    def get(self, request):
        entitlements = EmployeeMealEntitlement.objects.select_related(
            "employee", "set_by"
        ).order_by("employee_id", "-effective_from", "-id")
        if employee := request.query_params.get("employee"):
            entitlements = entitlements.filter(employee_id=employee)
        return Response({
            "count": entitlements.count(),
            "results": EmployeeMealEntitlementSerializer(entitlements, many=True).data,
        })

    def post(self, request):
        serializer = EmployeeMealEntitlementSerializer(
            data=request.data,
            context={"request": request},
        )
        serializer.is_valid(raise_exception=True)
        entitlement = serializer.save(set_by=request.user)
        return Response(
            EmployeeMealEntitlementSerializer(entitlement).data,
            status=201,
        )


class MealTicketRateListCreateAPIView(APIView):
    def get_permissions(self):
        permission = (
            CanManageMealConfiguration
            if self.request.method == "POST"
            else CanViewMealOperations
        )
        return [IsAuthenticated(), permission()]

    def get(self, request):
        rates = MealTicketRate.objects.order_by("-effective_from", "-id")
        return Response({
            "count": rates.count(),
            "results": MealTicketRateSerializer(rates, many=True).data,
        })

    def post(self, request):
        serializer = MealTicketRateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(
            MealTicketRateSerializer(serializer.save()).data,
            status=201,
        )


class MealEntitlementRuleListCreateAPIView(APIView):
    def get_permissions(self):
        permission = (
            CanManageMealConfiguration
            if self.request.method == "POST"
            else CanViewMealOperations
        )
        return [IsAuthenticated(), permission()]

    def get(self, request):
        rules = MealEntitlementRule.objects.select_related("position").order_by("priority", "id")
        return Response({
            "count": rules.count(),
            "results": MealEntitlementRuleSerializer(rules, many=True).data,
        })

    def post(self, request):
        serializer = MealEntitlementRuleSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(
            MealEntitlementRuleSerializer(serializer.save()).data,
            status=201,
        )


class MealEntitlementRuleDetailAPIView(APIView):
    permission_classes = [IsAuthenticated, CanManageMealConfiguration]

    def patch(self, request, pk):
        rule = get_object_or_404(MealEntitlementRule, pk=pk)
        serializer = MealEntitlementRuleSerializer(rule, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        return Response(MealEntitlementRuleSerializer(serializer.save()).data)


class MealReviewRemindersAPIView(APIView):
    permission_classes = [IsAuthenticated, CanManageMealConfiguration]

    def get(self, request):
        employees = MealService.employees_due_for_meal_review(timezone.localdate())
        return Response({
            "count": len(employees),
            "results": [
                {
                    "id": employee.pk,
                    "employee_id": employee.employee_id,
                    "name": employee.full_name,
                    "employment_date": employee.employment_date,
                    "position": employee.position.name if employee.position_id else "",
                }
                for employee in employees
            ],
        })


class MealVendorPeriodAPIView(APIView):
    permission_classes = [IsAuthenticated, CanViewVendorPayments]

    def get(self, request, payroll_period_id):
        period = get_object_or_404(PayrollPeriod, pk=payroll_period_id)

        collections = MealCollection.objects.filter(
            work_date__gte=period.start_date,
            work_date__lte=period.end_date,
            voided_at__isnull=True,
        )
        tickets_issued = collections.count()
        amount_owed = collections.aggregate(total=Sum("rate_snapshot"))["total"] or 0

        payments = MealVendorPayment.objects.filter(payroll_period=period).select_related("recorded_by")
        total_paid = payments.aggregate(total=Sum("amount"))["total"] or 0

        return Response({
            "payroll_period": period.pk,
            "tickets_issued": tickets_issued,
            "amount_owed": amount_owed,
            "total_paid": total_paid,
            "balance": amount_owed - total_paid,
            "payments": MealVendorPaymentSerializer(payments, many=True).data,
        })


class MealVendorPaymentListCreateAPIView(APIView):
    permission_classes = [IsAuthenticated, CanRecordVendorPayments]

    def post(self, request):
        serializer = MealVendorPaymentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        payment = serializer.save(recorded_by=request.user)
        return Response(
            MealVendorPaymentSerializer(payment).data,
            status=201,
        )


VENDOR_GROUPINGS = ("week", "month", "day")
MAX_VENDOR_RANGE_DAYS = 800


def _vendor_bucket(day, group_by):
    """The (start, end) of the week (Monday to Sunday), month or day a date falls in."""
    if group_by == "day":
        return day, day
    if group_by == "month":
        start = day.replace(day=1)
        end = (start.replace(year=start.year + 1, month=1) if start.month == 12 else start.replace(month=start.month + 1)) - timedelta(days=1)
        return start, end
    start = day - timedelta(days=day.weekday())
    return start, start + timedelta(days=6)


class MealVendorSummaryAPIView(APIView):
    """What the vendor is owed and has been paid, over any date range, grouped by week, month or day.

    Tickets are issued when staff scan (voided ones never count) but the vendor is paid for the ones it actually
    CLAIMED: a claim is a count for any dates (a day's tally or a week's invoice) and is spread over those days.
    Days with no claim yet are owed as issued and flagged, so nothing silently drops out. A payment counts on the
    first day it says it covers, or on its payment date when it covers nothing in particular."""

    permission_classes = [IsAuthenticated, CanViewVendorPayments]

    def get(self, request):
        params = request.query_params
        group_by = params.get("group_by", "week")
        if group_by not in VENDOR_GROUPINGS:
            return Response({"detail": "group_by must be week, month or day."}, status=400)
        today = timezone.localdate()
        date_to = _date_param(params.get("date_to")) or today
        date_from = _date_param(params.get("date_from")) or _vendor_bucket(date_to - timedelta(days=7 * 7), "week")[0]
        if date_from > date_to:
            return Response({"detail": "The start date is after the end date."}, status=400)
        if (date_to - date_from).days > MAX_VENDOR_RANGE_DAYS:
            return Response({"detail": "Choose a range of at most about two years."}, status=400)

        buckets = {}

        def bucket_for(day):
            start, end = _vendor_bucket(day, group_by)
            key = start.isoformat()
            if key not in buckets:
                buckets[key] = {"key": key, "start": max(start, date_from).isoformat(), "end": min(end, date_to).isoformat(),
                                "tickets": 0, "claimed": 0, "unclaimed": 0, "awaiting_claim": 0, "owed": Decimal("0"), "paid": Decimal("0"), "_covered_tickets": 0}
            return buckets[key]

        cursor = date_from
        while cursor <= date_to:  # every period in the range shows, even one with nothing in it
            bucket_for(cursor)
            cursor = _vendor_bucket(cursor, group_by)[1] + timedelta(days=1)

        issued_by_day = {
            row["work_date"]: row
            for row in MealCollection.objects.filter(work_date__gte=date_from, work_date__lte=date_to, voided_at__isnull=True)
            .values("work_date").annotate(n=Count("id"), amount=Sum("rate_snapshot"))
        }
        claims = list(MealVendorClaim.objects.select_related("recorded_by").filter(date_from__lte=date_to, date_to__gte=date_from))
        covered_days = set()
        claims_out = []
        for claim in claims:
            allocation = allocate_vendor_claim(claim)
            for day, values in allocation["days"].items():
                if not date_from <= day <= date_to:
                    continue
                covered_days.add(day)
                bucket = bucket_for(day)
                bucket["claimed"] += values["claimed"]
                bucket["unclaimed"] += values["issued"] - values["claimed"]
                bucket["_covered_tickets"] += values["issued"]
                bucket["owed"] += values["claimed_amount"]
            row = MealVendorClaimSerializer(claim).data
            row.update({"issued": allocation["issued"], "payable": allocation["claimed"], "unclaimed": allocation["issued"] - allocation["claimed"], "over_claimed": allocation["over_claimed"]})
            claims_out.append(row)
            # days of the claim with no tickets at all still count as covered
            day = max(claim.date_from, date_from)
            while day <= min(claim.date_to, date_to):
                covered_days.add(day)
                day += timedelta(days=1)

        for day, row in issued_by_day.items():
            bucket = bucket_for(day)
            bucket["tickets"] += row["n"]
            if day not in covered_days:  # nobody has told us what was claimed for this day yet: owed as issued
                bucket["awaiting_claim"] += row["n"]
                bucket["owed"] += row["amount"] or 0

        payments = []
        for payment in MealVendorPayment.objects.select_related("recorded_by").order_by("-payment_date", "-id"):
            counts_on = payment.covers_from or payment.payment_date
            if not date_from <= counts_on <= date_to:
                continue
            bucket_for(counts_on)["paid"] += payment.amount
            payments.append(payment)

        rows_out = sorted(buckets.values(), key=lambda bucket: bucket["key"], reverse=True)
        for bucket in rows_out:
            bucket.pop("_covered_tickets")
            bucket["balance"] = bucket["owed"] - bucket["paid"]
            bucket["claim_status"] = "" if bucket["tickets"] == 0 else "none" if bucket["awaiting_claim"] == bucket["tickets"] else "partial" if bucket["awaiting_claim"] else "claimed"
        totals = {key: sum(b[key] for b in rows_out) for key in ("tickets", "claimed", "unclaimed", "awaiting_claim", "owed", "paid")}
        totals["balance"] = totals["owed"] - totals["paid"]
        return Response({"date_from": date_from.isoformat(), "date_to": date_to.isoformat(), "group_by": group_by, "totals": totals, "buckets": rows_out,
                         "claims": sorted(claims_out, key=lambda c: (c["date_from"], c["id"]), reverse=True), "payments": MealVendorPaymentSerializer(payments, many=True).data})


class MealVendorClaimCreateAPIView(APIView):
    permission_classes = [IsAuthenticated, CanRecordVendorPayments]

    def post(self, request):
        serializer = MealVendorClaimSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        claim = serializer.save(recorded_by=request.user)
        return Response(MealVendorClaimSerializer(claim).data, status=201)


class MealVendorClaimDetailAPIView(APIView):
    """Remove a claim that was entered wrongly (then enter the right one)."""

    permission_classes = [IsAuthenticated, CanRecordVendorPayments]

    def delete(self, request, pk):
        get_object_or_404(MealVendorClaim, pk=pk).delete()
        return Response(status=204)

class MealMultipleTicketsAPIView(APIView):
    """Who took EXTRA tickets - more than they are entitled to on a day - over any dates: how many people, how many
    person-days, how many extra tickets, and who, with what became of each extra one (charged, waived, still waiting).
    A person entitled to two who took two is not listed. A ticket with no entitlement behind it (a new starter, a rest
    day, no shift yet) counts as extra."""

    permission_classes = [IsAuthenticated, CanViewVendorPayments]
    MAX_ROWS = 500

    def get(self, request):
        params = request.query_params
        today = timezone.localdate()
        date_to = _date_param(params.get("date_to")) or today
        date_from = _date_param(params.get("date_from")) or date_to - timedelta(days=6)
        try:
            minimum = max(int(params.get("min", 1)), 1)  # extra tickets in the day
        except ValueError:
            minimum = 1
        if date_from > date_to:
            return Response({"detail": "The start date is after the end date."}, status=400)
        if (date_to - date_from).days > MAX_VENDOR_RANGE_DAYS:
            return Response({"detail": "Choose a range of at most about two years."}, status=400)

        groups = list(
            MealCollection.objects.filter(work_date__gte=date_from, work_date__lte=date_to, voided_at__isnull=True)
            .values("employee_id", "work_date")
            .annotate(
                tickets=Count("id"),
                extra=Count("id", filter=~Q(status="within_entitlement")),
                entitled=Max("entitlement_snapshot"),
                charged=Count("id", filter=Q(excess_exception__status__in=["approved", "deducted"])),
                waived=Count("id", filter=Q(excess_exception__status="cancelled")),
                waiting=Count("id", filter=Q(excess_exception__status="pending")),
            )
            .filter(extra__gte=minimum).order_by("-work_date", "-extra", "employee_id")
        )
        days = {}
        for group in groups:
            day = days.setdefault(group["work_date"], {"date": group["work_date"].isoformat(), "people": 0, "extra_tickets": 0})
            day["people"] += 1
            day["extra_tickets"] += group["extra"]
        shown = groups[: self.MAX_ROWS]
        people = {e.pk: e for e in Employee.objects.filter(pk__in={g["employee_id"] for g in shown})}
        rows = []
        for group in shown:
            employee = people.get(group["employee_id"])
            rows.append({
                "employee_number": employee.employee_id if employee else "", "employee_name": employee.full_name if employee else "",
                "work_date": group["work_date"].isoformat(), "tickets": group["tickets"], "entitled": group["entitled"] or 0, "extra": group["extra"],
                "charged": group["charged"], "waived": group["waived"], "waiting": group["waiting"],
            })
        return Response({
            "date_from": date_from.isoformat(), "date_to": date_to.isoformat(), "min_extra": minimum,
            "totals": {"people": len({g["employee_id"] for g in groups}), "person_days": len(groups), "extra_tickets": sum(g["extra"] for g in groups)},
            "days": sorted(days.values(), key=lambda day: day["date"], reverse=True), "rows": rows, "truncated": len(groups) > self.MAX_ROWS,
        })


def _chargeback_row(chargeback):
    return {
        "id": chargeback.pk, "employee_number": chargeback.employee.employee_id, "employee_name": chargeback.employee.full_name,
        "work_date": chargeback.work_date.isoformat(), "quantity": chargeback.quantity, "amount": chargeback.amount, "reason": chargeback.reason,
        "reference": chargeback.reference, "status": chargeback.status, "pay_year": chargeback.pay_year, "pay_month": chargeback.pay_month,
        "created_by_name": chargeback.created_by.get_full_name() or chargeback.created_by.get_username() if chargeback.created_by_id else "",
        "created_at": chargeback.created_at, "cancel_reason": chargeback.cancel_reason,
    }


class MealChargebackListCreateAPIView(APIView):
    """Charge an employee for meal tickets they already took (e.g. a penalty for an offence), and list what was charged."""

    def get_permissions(self):
        return [IsAuthenticated(), CanChargeBackMealTickets() if self.request.method == "POST" else CanSeeChargebacks()]

    def get(self, request):
        params = request.query_params
        chargebacks = MealChargeback.objects.select_related("employee", "created_by")
        if date_from := _date_param(params.get("date_from")):
            chargebacks = chargebacks.filter(work_date__gte=date_from)
        if date_to := _date_param(params.get("date_to")):
            chargebacks = chargebacks.filter(work_date__lte=date_to)
        if params.get("status") in ("pending", "deducted", "cancelled"):
            chargebacks = chargebacks.filter(status=params["status"])
        if search := params.get("search", "").strip():
            chargebacks = _employee_search(chargebacks, search)
        rows = [_chargeback_row(c) for c in chargebacks[:300]]
        return Response({"results": rows, "count": chargebacks.count()})

    def post(self, request):
        data = request.data
        employee = Employee.objects.filter(employee_id=str(data.get("employee_number", "")).strip()).first()
        if employee is None:
            return Response({"detail": "No employee has that staff number."}, status=400)
        work_date = _date_param(data.get("work_date"))
        if work_date is None:
            return Response({"detail": "Choose the date the tickets were taken."}, status=400)
        try:
            quantity = int(data.get("quantity", 1))
            pay_year = int(data["pay_year"]) if data.get("pay_year") else None
            pay_month = int(data["pay_month"]) if data.get("pay_month") else None
            chargeback = charge_back_tickets(employee, work_date, quantity, data.get("reason", ""), request.user, reference=data.get("reference", ""), pay_year=pay_year, pay_month=pay_month)
        except (ValueError, TypeError) as exc:
            return Response({"detail": str(exc)}, status=400)
        return Response(_chargeback_row(chargeback), status=201)


class MealChargebackLookupAPIView(APIView):
    """What can be charged back for one person on one day - so the form shows it before anything is saved."""

    permission_classes = [IsAuthenticated, CanChargeBackMealTickets]

    def get(self, request):
        employee = Employee.objects.filter(employee_id=request.query_params.get("employee", "").strip()).first()
        work_date = _date_param(request.query_params.get("date"))
        if employee is None:
            return Response({"detail": "No employee has that staff number."}, status=404)
        if work_date is None:
            return Response({"detail": "Choose the date the tickets were taken."}, status=400)
        try:
            rate = MealService.rate_for(work_date).amount
        except ValueError:
            rate = None
        return Response({"employee_name": employee.full_name, "employee_status": employee.status, "work_date": work_date.isoformat(), "rate": rate, **chargeable_tickets(employee, work_date)})


class MealChargebackCancelAPIView(APIView):
    permission_classes = [IsAuthenticated, CanChargeBackMealTickets]

    def post(self, request, pk):
        chargeback = get_object_or_404(MealChargeback.objects.select_related("employee", "created_by"), pk=pk)
        try:
            cancel_chargeback(chargeback, request.user, request.data.get("reason", ""))
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=400)
        return Response(_chargeback_row(chargeback))


class MealDeviceListAPIView(APIView):
    """Read-only: devices are registered on the Biometric Devices page
    (purpose=meal_ticket) and mirrored here automatically - see
    attendance.views.devices.sync_meal_device. There's deliberately no
    create/update here, since a MealDevice with no matching BiometricDevice
    is one the AiFace gateway can't route scans to.
    """

    permission_classes = [IsAuthenticated, CanViewMealOperations]

    def get(self, request):
        devices = MealDevice.objects.order_by("name")
        return Response({
            "count": devices.count(),
            "results": MealDeviceSerializer(devices, many=True).data,
        })


class MealExcessCancelAPIView(APIView):
    permission_classes = [IsAuthenticated, CanReviewMealExcess]

    def post(self, request, pk):
        exception = get_object_or_404(
            MealExcessException,
            pk=pk,
        )

        reason = str(request.data.get("reason", "")).strip()

        try:
            MealService.cancel(
                exception,
                request.user,
                reason,
            )
        except ValueError as exc:
            return Response(
                {"detail": str(exc)},
                status=400,
            )

        return Response(
            {
                "id": exception.pk,
                "status": exception.status,
                "reviewer": exception.reviewer_id,
                "reviewed_at": exception.reviewed_at,
                "comment": exception.comment,
            }
        )


class MealExcessApproveAPIView(APIView):
    permission_classes = [IsAuthenticated, CanReviewMealExcess]

    def post(self, request, pk):
        exception = get_object_or_404(
            MealExcessException,
            pk=pk,
        )

        payroll_period_id = request.data.get("payroll_period")
        period = get_object_or_404(PayrollPeriod, pk=payroll_period_id) if payroll_period_id else None

        comment = str(
            request.data.get("comment", "")
        ).strip()

        try:
            MealService.approve(
                exception,
                period,
                request.user,
                comment,
            )
        except ValueError as exc:
            return Response(
                {"detail": str(exc)},
                status=400,
            )

        return Response(
            {
                "id": exception.pk,
                "status": exception.status,
                "reviewer": exception.reviewer_id,
                "reviewed_at": exception.reviewed_at,
                "comment": exception.comment,
                "payroll_period": exception.payroll_period_id,
                "payroll": exception.payroll_id,
                "payroll_line_item": (
                    exception.payroll_line_item_id
                ),
                "proposed_deduction": (
                    exception.proposed_deduction
                ),
                "balanced_tickets": getattr(exception, "balanced_tickets", 0),
            }
        )   

def _date_param(value):
    from datetime import date

    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


def _employee_search(queryset, term, prefix="employee__"):
    """Staff number or any part of a name; several words must all match somewhere in the name."""
    for word in term.split():
        queryset = queryset.filter(
            Q(**{f"{prefix}employee_id__icontains": word})
            | Q(**{f"{prefix}first_name__icontains": word})
            | Q(**{f"{prefix}middle_name__icontains": word})
            | Q(**{f"{prefix}last_name__icontains": word})
        )
    return queryset


class MealOperationsAPIView(APIView):
    """The Meals page's data. Everything is optional and defaults to what the page always showed (the newest 100
    collections and every pending decision), so older callers keep working:

      date_from, date_to   work dates (YYYY-MM-DD) for collections and decisions
      search               staff number or part of a name
      device               a terminal name (collections)
      status               collections: within | excess | voided | all
      decisions            pending (default) | decided | all
      page, page_size      collections, 50 per page by default, at most 200
      exceptions_page, exceptions_page_size   decisions, 50 per page by default, at most 200
    """

    permission_classes = [IsAuthenticated, CanViewMealOperations]

    def get(self, request):
        params = request.query_params
        employee = params.get("employee")
        date_from, date_to = _date_param(params.get("date_from")), _date_param(params.get("date_to"))
        search = params.get("search", "").strip()
        device = params.get("device", "").strip()
        status_filter = params.get("status", "").strip()
        decisions = params.get("decisions", "pending")
        try:
            page = max(1, int(params.get("page", 1)))
            page_size = min(max(int(params.get("page_size", 100 if "page" not in params and "page_size" not in params else 50)), 1), 200)
        except ValueError:
            page, page_size = 1, 100
        try:
            exceptions_page = max(1, int(params.get("exceptions_page", 1)))
            exceptions_page_size = min(max(int(params.get("exceptions_page_size", 50)), 1), 200)
        except ValueError:
            exceptions_page, exceptions_page_size = 1, 50

        collections = (
            MealCollection.objects
            .select_related("employee", "event__device", "excess_exception")
            .order_by("-event__timestamp")
        )
        exceptions = MealExcessException.objects.select_related("employee", "reviewer").order_by("-work_date", "-created_at")
        if decisions == "pending":
            exceptions = exceptions.filter(status="pending")
        elif decisions == "decided":
            exceptions = exceptions.exclude(status="pending")

        if employee:
            collections = collections.filter(employee_id=employee)
        if date_from:
            collections, exceptions = collections.filter(work_date__gte=date_from), exceptions.filter(work_date__gte=date_from)
        if date_to:
            collections, exceptions = collections.filter(work_date__lte=date_to), exceptions.filter(work_date__lte=date_to)
        if search:
            collections, exceptions = _employee_search(collections, search), _employee_search(exceptions, search)
        if device:
            collections = collections.filter(event__device__name=device)
        if status_filter == "voided":
            collections = collections.filter(voided_at__isnull=False)
        elif status_filter == "within":
            collections = collections.filter(voided_at__isnull=True, status="within_entitlement")
        elif status_filter == "excess":
            collections = collections.filter(voided_at__isnull=True).exclude(status="within_entitlement")

        total = collections.count()
        start = (page - 1) * page_size
        shown = list(collections[start:start + page_size])
        live = collections.filter(voided_at__isnull=True)
        in_range_within = live.filter(status="within_entitlement").count()
        in_range_total = live.count()
        exceptions_total = exceptions.count()
        exceptions_start = (exceptions_page - 1) * exceptions_page_size
        exception_rows = list(exceptions[exceptions_start:exceptions_start + exceptions_page_size])
        card_verified = _card_verified_map(exception_rows)

        # The metric cards need real, unambiguous totals for today - not something derived client-side
        # from whichever rows happen to still be in the window.
        today = timezone.localdate()
        today_collections = MealCollection.objects.filter(work_date=today, voided_at__isnull=True)
        today_within = today_collections.filter(status="within_entitlement").count()
        today_total = today_collections.count()

        return Response(
            {
                "summary": {
                    "today_collections": today_total,
                    "today_within_entitlement": today_within,
                    "today_excess": today_total - today_within,
                    # All-time, not just today: an unresolved excess from an earlier day still needs a
                    # decision, so this is a backlog count, not a "today" count - shown separately on
                    # purpose rather than made to look like it should match the totals above.
                    "pending_review": MealExcessException.objects.filter(status="pending").count(),
                },
                "range_summary": {
                    "collections": in_range_total,
                    "within_entitlement": in_range_within,
                    "excess": in_range_total - in_range_within,
                    "voided": total - in_range_total,
                },
                "collections_total": total,
                "page": page,
                "page_size": page_size,
                "collections": [
                    {
                        "id": x.pk,
                        "employee": x.employee_id,
                        "employee_number": x.employee.employee_id,
                        "employee_name": x.employee.full_name,
                        "work_date": x.work_date,
                        "timestamp": x.event.timestamp,
                        "device": x.event.device.name,
                        "entitlement": x.entitlement_snapshot,
                        "sequence": x.sequence_number,
                        "rate": x.rate_snapshot,
                        "status": x.status,
                        "voided": x.voided_at is not None,
                        "void_reason": x.void_reason,
                        "excess_id": x.excess_exception_id,
                        "excess_status": x.excess_exception.status if x.excess_exception_id else None,
                    }
                    for x in shown
                ],
                "exceptions_total": exceptions_total,
                "exceptions_page": exceptions_page,
                "exceptions_page_size": exceptions_page_size,
                "exceptions": [
                    {
                        "id": x.pk,
                        "employee": x.employee_id,
                        "employee_number": x.employee.employee_id,
                        "employee_name": x.employee.full_name,
                        "work_date": x.work_date,
                        "entitlement": x.entitlement_snapshot,
                        "collected_quantity": x.collected_quantity,
                        "excess_quantity": x.excess_quantity,
                        "rate": x.rate_snapshot,
                        "proposed_deduction": x.proposed_deduction,
                        "status": x.status,
                        "reviewer": x.reviewer_id,
                        "reviewed_at": x.reviewed_at,
                        "comment": x.comment,
                        # Card verification does not respect the terminal's enable/disable state the way
                        # face verification does (confirmed 2026-09-23) - flagged so a reviewer isn't left
                        # guessing why gating didn't stop this one.
                        "card_verified": card_verified.get(x.pk, False),
                        # The entitlement or shift was entered after this scan: Accept clears it instead of charging.
                        "balances_on_accept": MealService.would_balance(x),
                    }
                    for x in exception_rows
                ],
            }
        )


class MealExcessDeclineAPIView(APIView):
    permission_classes = [IsAuthenticated, CanReviewMealExcess]

    def post(self, request, pk):
        exception = get_object_or_404(MealExcessException, pk=pk)
        try:
            MealService.decline(exception, request.user, str(request.data.get("reason", "")))
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=400)
        return Response({"id": exception.pk, "status": exception.status, "reviewer": exception.reviewer_id, "reviewed_at": exception.reviewed_at, "comment": exception.comment})


MAX_BULK_DECISION_IDS = 300


class MealExcessBulkDecisionAPIView(APIView):
    """Accept/waive/decline several pending excess tickets in one request, instead of one HTTP round
    trip per ticket. Capped per call (the frontend chunks a larger selection) so one request can't run
    long enough to risk a proxy/worker timeout with a 2000+ case backlog."""

    permission_classes = [IsAuthenticated, CanReviewMealExcess]

    ACTIONS = {"accept": "approve", "waive": "cancel", "decline": "decline"}

    def post(self, request):
        action = request.data.get("action")
        if action not in self.ACTIONS:
            return Response({"detail": "action must be one of accept, waive, decline."}, status=400)
        ids = request.data.get("ids")
        if not isinstance(ids, list) or not ids:
            return Response({"detail": "ids must be a non-empty list."}, status=400)
        if len(ids) > MAX_BULK_DECISION_IDS:
            return Response({"detail": f"At most {MAX_BULK_DECISION_IDS} at a time. Send this in smaller batches."}, status=400)

        reason = str(request.data.get("reason", "")).strip()
        exceptions = {x.pk: x for x in MealExcessException.objects.select_related("employee").filter(pk__in=ids)}

        succeeded, failed, balanced = [], [], []
        for raw_id in ids:
            try:
                item_id = int(raw_id)
            except (TypeError, ValueError):
                failed.append({"id": raw_id, "error": "Not a valid id."})
                continue
            exception = exceptions.get(item_id)
            if exception is None:
                failed.append({"id": item_id, "error": "Not found."})
                continue
            try:
                if action == "accept":
                    MealService.approve(exception, None, request.user, "")
                    if getattr(exception, "balanced_tickets", 0):
                        balanced.append(item_id)
                elif action == "waive":
                    MealService.cancel(exception, request.user, reason, notify=False)
                else:
                    MealService.decline(exception, request.user, reason, notify=False)
                succeeded.append(item_id)
            except ValueError as exc:
                failed.append({"id": item_id, "error": str(exc)})

        if succeeded and action in ("waive", "decline"):
            title = "Meal excess waived (bulk)" if action == "waive" else "Meal excess declined (bulk)"
            event_type = "meals.excess_cancelled" if action == "waive" else "meals.excess_declined"
            message = f"{len(succeeded)} ticket(s) {'waived' if action == 'waive' else 'declined'} in bulk by {request.user.get_full_name() or request.user.username}."
            for user in get_user_model().objects.filter(is_superuser=True):
                NotificationService.create(recipient=user, event_type=event_type, title=title, message=message, severity="warning", related_url="/meals")

        return Response({"succeeded": succeeded, "failed": failed, "balanced": balanced})


MAX_PENDING_IDS = 5000


class MealExcessPendingIdsAPIView(APIView):
    """Every id matching the review tab's current filters, for 'select all N across every page' - capped
    so a runaway filter can't hand the browser more than it can sanely act on in one sitting."""

    permission_classes = [IsAuthenticated, CanViewMealOperations]

    def get(self, request):
        params = request.query_params
        date_from, date_to = _date_param(params.get("date_from")), _date_param(params.get("date_to"))
        search = params.get("search", "").strip()
        decisions = params.get("decisions", "pending")

        exceptions = MealExcessException.objects.order_by("-work_date", "-created_at")
        if decisions == "pending":
            exceptions = exceptions.filter(status="pending")
        elif decisions == "decided":
            exceptions = exceptions.exclude(status="pending")
        if date_from:
            exceptions = exceptions.filter(work_date__gte=date_from)
        if date_to:
            exceptions = exceptions.filter(work_date__lte=date_to)
        if search:
            exceptions = _employee_search(exceptions, search)

        ids = list(exceptions.values_list("id", flat=True)[:MAX_PENDING_IDS])
        return Response({"ids": ids, "truncated": exceptions.count() > len(ids)})


class MealCollectionVoidAPIView(APIView):
    permission_classes = [IsAuthenticated, CanReviewMealExcess]

    def post(self, request, pk):
        collection = get_object_or_404(MealCollection.objects.select_related("employee"), pk=pk)
        try:
            MealService.void_collection(collection, request.user, str(request.data.get("reason", "")))
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=400)
        return Response({"id": collection.pk, "voided": True, "void_reason": collection.void_reason})


class EmployeeMealsProfileAPIView(APIView):
    permission_classes = [IsAuthenticated, CanViewMealOperations]

    def get(self, request, pk):
        employee = get_object_or_404(Employee, pk=pk)

        collections = (
            MealCollection.objects
            .filter(employee=employee)
            .select_related("event__device", "shift")
            .order_by("-event__timestamp")[:20]
        )

        exceptions = (
            MealExcessException.objects
            .filter(employee=employee)
            .select_related("reviewer", "payroll_period", "payroll")
            .order_by("-work_date", "-created_at")[:20]
        )
        reference_date = timezone.localdate()

        approved_entitlement = MealService.approved_entitlement(
            employee,
            reference_date,
        )

        suggested_entitlement, suggested_rule = (
            MealService.suggested_entitlement(
                employee,
                reference_date,
            )
        )

        active_entitlement = (
            employee.meal_entitlements
            .filter(effective_from__lte=reference_date)
            .filter(
                Q(effective_to__isnull=True)
                | Q(effective_to__gte=reference_date)
            )
            .order_by("-effective_from", "-id")
            .first()
        )

        today_date, today_roster = MealService.resolve_work_day(employee, timezone.now())
        return Response(
            {
                "employee": {
                    "id": employee.pk,
                    "employee_id": employee.employee_id,
                    "name": employee.full_name,
                },
                # A scan only earns a ticket on a rostered WORK day, so show what today looks like.
                "today": {
                    "work_date": today_date,
                    "roster_status": today_roster.status if today_roster else "none",
                    "shift": today_roster.shift.name if today_roster and today_roster.shift else None,
                },
                "entitlement": {
                    "approved": approved_entitlement,
                    "effective_from": (
                        active_entitlement.effective_from
                        if active_entitlement
                        else None
                    ),
                    "effective_to": (
                        active_entitlement.effective_to
                        if active_entitlement
                        else None
                    ),
                    "reason": (
                        active_entitlement.reason
                        if active_entitlement
                        else ""
                    ),
                    "is_exceptional_override": (
                        active_entitlement.is_exceptional_override
                        if active_entitlement
                        else False
                    ),
                },
                "suggestion": {
                    "tickets_per_work_day": suggested_entitlement,
                    "rule_id": (
                        suggested_rule.pk
                        if suggested_rule
                        else None
                    ),
                    "reason": (
                        suggested_rule.description
                        if suggested_rule
                        else ""
                    ),
                },
                "collections": [
                    {
                        "id": x.pk,
                        "work_date": x.work_date,
                        "timestamp": x.event.timestamp,
                        "device": x.event.device.name,
                        "shift": (
                            x.shift.name
                            if x.shift
                            else None
                        ),
                        "sequence": x.sequence_number,
                        "entitlement": x.entitlement_snapshot,
                        "rate": x.rate_snapshot,
                        "status": x.status,
                        "voided": x.voided_at is not None,
                        "void_reason": x.void_reason,
                    }
                    for x in collections
                ],
                "exceptions": [
                    {
                        "id": x.pk,
                        "work_date": x.work_date,
                        "entitlement": x.entitlement_snapshot,
                        "collected_quantity": x.collected_quantity,
                        "excess_quantity": x.excess_quantity,
                        "rate": x.rate_snapshot,
                        "proposed_deduction": x.proposed_deduction,
                        "status": x.status,
                        "reviewer": x.reviewer_id,
                        "reviewed_at": x.reviewed_at,
                        "comment": x.comment,
                        "payroll_period": x.payroll_period_id,
                        "payroll": x.payroll_id,
                    }
                    for x in exceptions
                ],
            }
        )


class MealExtraAuthorizationListCreateAPIView(APIView):
    """Extra tickets a supervisor has authorised. Anyone who can see meal operations can list; authorising needs the review permission."""

    def get_permissions(self):
        return [IsAuthenticated(), CanReviewMealExcess() if self.request.method == "POST" else CanViewMealOperations()]

    @staticmethod
    def _row(authorization, today):
        from .authorizations import status_of

        return {
            "id": authorization.pk, "employee": authorization.employee_id, "employee_name": authorization.employee.full_name, "employee_number": authorization.employee.employee_id,
            "work_date": authorization.work_date, "quantity": authorization.quantity, "used": authorization.used, "pays": authorization.pays,
            "pays_label": "Employee pays" if authorization.pays == "employee" else "Company pays", "reason": authorization.reason, "status": status_of(authorization, today),
            "authorised_by": (authorization.authorised_by.get_full_name() or authorization.authorised_by.username) if authorization.authorised_by else None, "created_at": authorization.created_at,
        }

    def get(self, request):
        from .models import MealExtraAuthorization

        today = timezone.localdate()
        rows = MealExtraAuthorization.objects.select_related("employee", "authorised_by").filter(work_date__gte=today - timedelta(days=7))[:100]
        return Response({"today": today, "results": [self._row(a, today) for a in rows]})

    def post(self, request):
        from .authorizations import authorise

        employee = get_object_or_404(Employee, pk=request.data.get("employee"))
        try:
            quantity = int(request.data.get("quantity", 1))
        except (TypeError, ValueError):
            return Response({"detail": "The number of tickets must be a whole number."}, status=400)
        try:
            authorization = authorise(employee=employee, quantity=quantity, pays=str(request.data.get("pays", "")), reason=str(request.data.get("reason", "")), actor=request.user)
        except ValueError as error:
            return Response({"detail": str(error)}, status=400)
        return Response(self._row(authorization, timezone.localdate()), status=201)


class MealExtraAuthorizationCancelAPIView(APIView):
    permission_classes = [IsAuthenticated, CanReviewMealExcess]

    def post(self, request, pk):
        from .authorizations import cancel
        from .models import MealExtraAuthorization

        authorization = get_object_or_404(MealExtraAuthorization, pk=pk)
        try:
            cancel(authorization, actor=request.user)
        except ValueError as error:
            return Response({"detail": str(error)}, status=400)
        return Response({"id": authorization.pk})

from django.shortcuts import get_object_or_404

from rest_framework import status
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import EmployeePPEIssue, PPEType
from .serializers import EmployeePPEIssueSerializer, PPEIssueCreateSerializer, PPEDecisionSerializer, PPETypeSerializer
from .services import PPEDeductionService


class CanRecordPPE(BasePermission):
    def has_permission(self, request, view):
        return request.user.has_perm("ppe.record_ppe_issue") or request.user.has_perm("ppe.review_ppe_deduction")


class CanReviewPPE(BasePermission):
    def has_permission(self, request, view):
        return request.user.has_perm("ppe.review_ppe_deduction") or request.user.has_perm("payroll.manage_payroll")


class PPETypeListAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        types = PPEType.objects.filter(active=True).order_by("name")
        return Response({"results": PPETypeSerializer(types, many=True).data})


class PPEIssueListCreateAPIView(APIView):
    def get_permissions(self):
        if self.request.method == "POST":
            return [IsAuthenticated(), CanRecordPPE()]
        return [IsAuthenticated()]

    def get(self, request):
        issues = EmployeePPEIssue.objects.select_related("employee", "employee__department", "ppe_type", "target_payroll_period", "deducted_payroll", "payroll_line_item").order_by("-issue_date", "-id")
        if employee := request.query_params.get("employee"):
            issues = issues.filter(employee_id=employee)
        if status_filter := request.query_params.get("status"):
            issues = issues.filter(deduction_status=status_filter)
        if request.query_params.get("review_queue") == "true":
            issues = issues.filter(employee_deductible=True).exclude(deduction_status="deducted")
        return Response({"count": issues.count(), "results": EmployeePPEIssueSerializer(issues, many=True).data})

    def post(self, request):
        serializer = PPEIssueCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        issue = PPEDeductionService.issue(**serializer.validated_data, actor=request.user)
        return Response(EmployeePPEIssueSerializer(issue).data, status=status.HTTP_201_CREATED)


class PPEIssueDetailAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, issue_id):
        issue = get_object_or_404(EmployeePPEIssue.objects.select_related("employee", "ppe_type", "target_payroll_period", "deducted_payroll", "payroll_line_item"), pk=issue_id)
        return Response(EmployeePPEIssueSerializer(issue).data)


class PPEDecisionAPIView(APIView):
    permission_classes = [IsAuthenticated, CanReviewPPE]
    action = None

    def post(self, request, issue_id):
        serializer = PPEDecisionSerializer(data=request.data, context={"action": self.action})
        serializer.is_valid(raise_exception=True)
        issue = get_object_or_404(EmployeePPEIssue.objects.select_related("employee", "ppe_type"), pk=issue_id)
        try:
            if self.action == "approve":
                issue = PPEDeductionService.approve(issue, payroll_period=serializer.validated_data["payroll_period"], actor=request.user, comment=serializer.validated_data.get("comment", ""))
            elif self.action == "hold":
                issue = PPEDeductionService.hold(issue, actor=request.user, comment=serializer.validated_data["comment"])
            else:
                issue = PPEDeductionService.defer(issue, payroll_period=serializer.validated_data["payroll_period"], actor=request.user, comment=serializer.validated_data.get("comment", ""))
        except ValueError as error:
            return Response({"detail": str(error)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(EmployeePPEIssueSerializer(issue).data)


class PPEApproveAPIView(PPEDecisionAPIView):
    action = "approve"


class PPEHoldAPIView(PPEDecisionAPIView):
    action = "hold"


class PPEDeferAPIView(PPEDecisionAPIView):
    action = "defer"

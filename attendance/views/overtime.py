from rest_framework import status
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from attendance.models import OvertimeRecord
from attendance.serializers import OvertimeApprovalSerializer, OvertimePaymentSerializer, OvertimeRecordSerializer, OvertimeRejectionSerializer
from attendance.services.overtime import approve_overtime, mark_overtime_paid, reject_overtime


class CanReviewOvertime(BasePermission):
    def has_permission(self, request, view):
        return request.user.has_perm("attendance.review_overtime")


class OvertimeRecordListAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        records = OvertimeRecord.objects.select_related("employee", "employee__department", "shift", "reviewed_by").all()
        for key in ("status", "payment_status", "employee", "work_date"):
            if request.query_params.get(key):
                records = records.filter(**{key: request.query_params[key]})
        return Response({"results": OvertimeRecordSerializer(records, many=True).data})


class OvertimeDecisionAPIView(APIView):
    permission_classes = [IsAuthenticated, CanReviewOvertime]

    def post(self, request, record_id, decision):
        if decision not in {"approve", "reject"}:
            return Response({"detail": "Unsupported overtime decision."}, status=status.HTTP_404_NOT_FOUND)
        serializer_class = OvertimeApprovalSerializer if decision == "approve" else OvertimeRejectionSerializer
        serializer = serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            if decision == "approve":
                record = approve_overtime(record_id, actor=request.user, approved_minutes=serializer.validated_data.get("approved_overtime_minutes"), comment=serializer.validated_data.get("comment", ""))
            else:
                record = reject_overtime(record_id, actor=request.user, comment=serializer.validated_data["comment"])
        except OvertimeRecord.DoesNotExist:
            return Response({"detail": "Overtime record not found."}, status=status.HTTP_404_NOT_FOUND)
        except ValueError as error:
            return Response({"detail": str(error)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(OvertimeRecordSerializer(record).data)


class OvertimeMarkPaidAPIView(APIView):
    permission_classes = [IsAuthenticated, CanReviewOvertime]

    def post(self, request, record_id):
        serializer = OvertimePaymentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            record = mark_overtime_paid(record_id, actor=request.user, note=serializer.validated_data.get("payment_note", ""))
        except OvertimeRecord.DoesNotExist:
            return Response({"detail": "Overtime record not found."}, status=status.HTTP_404_NOT_FOUND)
        except ValueError as error:
            return Response({"detail": str(error)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(OvertimeRecordSerializer(record).data)

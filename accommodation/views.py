from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from employees.models import Employee

from .models import Building, Room, RoomAssignment
from .services import AccommodationService, build_overview, import_workbook


class CanViewAccommodation(BasePermission):
    def has_permission(self, request, view):
        return request.user.has_perm("accommodation.view_accommodation") or request.user.has_perm("accommodation.manage_accommodation")


class CanManageAccommodation(BasePermission):
    def has_permission(self, request, view):
        return request.user.has_perm("accommodation.manage_accommodation")


def fail(message, code=status.HTTP_400_BAD_REQUEST):
    return Response({"detail": str(message)}, status=code)


class OverviewAPIView(APIView):
    permission_classes = [IsAuthenticated, CanViewAccommodation]

    def get(self, request):
        return Response(build_overview())


class PeopleAPIView(APIView):
    """Active staff with where they live; ?where=inside|outside|none, ?q=search."""

    permission_classes = [IsAuthenticated, CanViewAccommodation]

    def get(self, request):
        people = Employee.objects.filter(status="active").select_related("department", "room_assignment__room__building").order_by("employee_id")
        where, query = request.query_params.get("where", ""), request.query_params.get("q", "").strip().lower()
        results = []
        for employee in people:
            assignment = getattr(employee, "room_assignment", None)
            if assignment:
                kind = assignment.room.building.kind
                place = f"{assignment.room.building.name} {assignment.room.name}" + (f", bed {assignment.bed_number}" if assignment.bed_number else "")
            elif employee.lives_in_external_accommodation:
                kind, place = "external", employee.external_accommodation_address or "Outside - place not recorded"
            elif employee.lives_in_company_hostel:
                kind, place = "company", employee.hostel_room_number or "Company accommodation - no room recorded"
            else:
                kind, place = "none", ""
            group = {"company": "inside", "external": "outside", "none": "none"}[kind]
            if where and group != where:
                continue
            if query and query not in f"{employee.full_name} {employee.employee_id} {place}".lower():
                continue
            results.append({"id": employee.pk, "employee_id": employee.employee_id, "name": employee.full_name, "department": employee.department.name if employee.department else None, "where": group, "place": place, "room": assignment.room_id if assignment else None})
        return Response({"count": len(results), "results": results})


class BuildingCreateAPIView(APIView):
    permission_classes = [IsAuthenticated, CanManageAccommodation]

    def post(self, request):
        name, kind = str(request.data.get("name", "")).strip(), request.data.get("kind", "company")
        if not name or kind not in ("company", "external"):
            return fail("Give the building a name and choose company or outside.")
        if Building.objects.filter(name__iexact=name).exists():
            return fail("A building with that name already exists.")
        building = Building.objects.create(name=name, kind=kind, address=str(request.data.get("address", "")).strip())
        return Response({"id": building.pk}, status=status.HTTP_201_CREATED)


class RoomCreateAPIView(APIView):
    permission_classes = [IsAuthenticated, CanManageAccommodation]

    def post(self, request):
        building = get_object_or_404(Building, pk=request.data.get("building"))
        name = str(request.data.get("name", "")).strip()
        if not name:
            return fail("Give the room a name or number.")
        if Room.objects.filter(building=building, name__iexact=name).exists():
            return fail(f"{building.name} already has a room called {name}.")
        capacity = request.data.get("capacity")
        room = Room.objects.create(building=building, name=name, capacity=int(capacity) if str(capacity or "").isdigit() else None)
        return Response({"id": room.pk}, status=status.HTTP_201_CREATED)


class RoomDetailAPIView(APIView):
    permission_classes = [IsAuthenticated, CanManageAccommodation]

    def patch(self, request, room_id):
        room = get_object_or_404(Room, pk=room_id)
        if "capacity" in request.data:
            value = request.data["capacity"]
            if value in (None, ""):
                room.capacity = None
            elif str(value).isdigit() and 0 < int(value) <= 100:
                room.capacity = int(value)
                if room.assignments.count() > room.capacity:
                    return fail(f"{room.assignments.count()} people are already in this room, so it cannot take fewer than that.")
            else:
                return fail("Capacity must be a whole number between 1 and 100.")
            room.capacity_estimated = False
        if "active" in request.data:
            if not request.data["active"] and room.assignments.exists():
                return fail("Move the people out of this room before closing it.")
            room.active = bool(request.data["active"])
        if "notes" in request.data:
            room.notes = str(request.data["notes"])[:255]
        room.save()
        return Response({"id": room.pk})


class AssignAPIView(APIView):
    permission_classes = [IsAuthenticated, CanManageAccommodation]

    def post(self, request):
        employee = get_object_or_404(Employee, pk=request.data.get("employee"))
        room = get_object_or_404(Room, pk=request.data.get("room"))
        bed = request.data.get("bed")
        try:
            AccommodationService.assign(employee, room, bed_number=int(bed) if str(bed or "").isdigit() else None, actor=request.user)
        except ValueError as error:
            return fail(error)
        return Response({"ok": True})


class UnassignAPIView(APIView):
    permission_classes = [IsAuthenticated, CanManageAccommodation]

    def post(self, request):
        employee = get_object_or_404(Employee, pk=request.data.get("employee"))
        AccommodationService.unassign(employee, actor=request.user, outside=bool(request.data.get("outside")))
        return Response({"ok": True})


class ImportAPIView(APIView):
    """Upload the staff spreadsheet. dry_run=true (default) only reports what would change."""

    permission_classes = [IsAuthenticated, CanManageAccommodation]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        upload = request.FILES.get("file")
        if upload is None or not upload.name.lower().endswith(".xlsx"):
            return fail("Upload the staff spreadsheet as an .xlsx file.")
        dry_run = str(request.data.get("dry_run", "true")).lower() != "false"
        try:
            report = import_workbook(upload, dry_run=dry_run, actor=request.user)
        except ValueError as error:
            return fail(error)
        return Response(report.as_dict())

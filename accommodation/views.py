from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from employees.models import Employee

from .models import Building, Room, RoomAssignment
from .services import AccommodationService, build_overview, import_room_setup, import_workbook, split_room_name


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
            results.append({"id": employee.pk, "employee_id": employee.employee_id, "name": employee.full_name, "department": employee.department.name if employee.department else None, "gender": employee.gender, "where": group, "place": place, "room": assignment.room_id if assignment else None})
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
    """Add a room. `building` (id) or `building_name` (an existing or new company building), a name, and optionally
    a gender ("male" / "female") and the number of beds."""

    permission_classes = [IsAuthenticated, CanManageAccommodation]

    def post(self, request):
        name = str(request.data.get("name", "")).strip()
        if not name:
            return fail("Give the room a name or number.")
        gender = str(request.data.get("gender", "") or "").strip().lower()
        if gender not in ("", "male", "female"):
            return fail("Choose male or female for the room.")
        capacity = request.data.get("capacity")
        if capacity not in (None, "") and not (str(capacity).isdigit() and 0 < int(capacity) <= 100):
            return fail("The number of beds must be a whole number from 1 to 100.")
        if request.data.get("building"):
            building = get_object_or_404(Building, pk=request.data.get("building"))
        else:
            building_name = str(request.data.get("building_name", "")).strip()
            if not building_name:
                building_name, name = split_room_name(name)
            building = Building.objects.filter(name__iexact=building_name).first() or Building.objects.create(name=building_name, kind="company", sort_order=50)
        if Room.objects.filter(building=building, name__iexact=name).exists():
            return fail(f"{building.name} already has a room called {name}.")
        room = Room.objects.create(building=building, name=name, gender=gender, capacity=int(capacity) if capacity not in (None, "") else None)
        return Response({"id": room.pk}, status=status.HTTP_201_CREATED)


class RoomDetailAPIView(APIView):
    permission_classes = [IsAuthenticated, CanManageAccommodation]

    def patch(self, request, room_id):
        room = get_object_or_404(Room.objects.select_related("building"), pk=room_id)
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
        if "gender" in request.data:
            gender = str(request.data["gender"] or "").lower()
            if gender not in ("", "male", "female"):
                return fail("Choose male or female for the room.")
            wrong = [a.employee.full_name for a in room.assignments.select_related("employee") if gender and a.employee.gender and a.employee.gender != gender]
            if wrong:
                return fail(f"{', '.join(wrong[:3])} {'is' if len(wrong) == 1 else 'are'} in this room and not {gender}. Move them first.")
            room.gender = gender
        if "name" in request.data:
            name = str(request.data["name"]).strip()
            if not name:
                return fail("The room needs a name.")
            if Room.objects.filter(building=room.building, name__iexact=name).exclude(pk=room.pk).exists():
                return fail(f"{room.building.name} already has a room called {name}.")
            room.name = name
        if "active" in request.data:
            if not request.data["active"] and room.assignments.exists():
                return fail("Move the people out of this room before closing it.")
            room.active = bool(request.data["active"])
        if "notes" in request.data:
            room.notes = str(request.data["notes"])[:255]
        room.save()
        return Response({"id": room.pk})

    def delete(self, request, room_id):
        room = get_object_or_404(Room.objects.select_related("building"), pk=room_id)
        count = room.assignments.count()
        if count:
            return fail(f"{count} {'person is' if count == 1 else 'people are'} still in {room.name}. Move them to another room first, then remove it.")
        building = room.building
        room.delete()
        if not building.rooms.exists() and building.kind == "company":
            building.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


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


class RoomSetupImportAPIView(APIView):
    """Upload the accommodation tracker (.xlsb or .xlsx). The rooms then match its Room Setup sheet. dry_run=true (default) only reports."""

    permission_classes = [IsAuthenticated, CanManageAccommodation]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        upload = request.FILES.get("file")
        if upload is None or not upload.name.lower().endswith((".xlsx", ".xlsb")):
            return fail("Upload the accommodation tracker as an .xlsb or .xlsx file.")
        dry_run = str(request.data.get("dry_run", "true")).lower() != "false"
        try:
            report = import_room_setup(upload, dry_run=dry_run, actor=request.user)
        except ValueError as error:
            return fail(error)
        except Exception:
            return fail("That file could not be read. Check it is the accommodation tracker workbook.")
        return Response(report.as_dict())

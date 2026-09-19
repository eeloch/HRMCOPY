import re
from collections import defaultdict
from dataclasses import dataclass, field

from django.db import transaction
from openpyxl import load_workbook

from audit.models import AuditSeverity
from audit.services import AuditService
from employees.models import Employee

from .models import Building, BuildingKind, Room, RoomAssignment

BED_PATTERN = re.compile(r"^(?P<room>.*?)\s*-\s*Bed\s*(?P<bed>\d+)\s*$", re.IGNORECASE)
MAIN_HOSTEL = "Main Hostel"


def parse_room_label(label):
    """'Room 301 - Bed 1' -> ('Main Hostel', 'Room 301', 1). Buildings come from how the rooms are named:
    'Lodge 2 Rm005' is Lodge 2, 'Security Rm 005' is Security, 'Admin G2' is Admin, 'Room 301' is the main hostel."""
    match = BED_PATTERN.match(str(label or "").strip())
    if not match:
        return None
    room, bed = re.sub(r"\s+", " ", match.group("room")).strip(), int(match.group("bed"))
    lodge = re.match(r"^(Lodge\s*\d+)\s*(.+)$", room, re.IGNORECASE)
    if lodge:
        return re.sub(r"\s+", " ", lodge.group(1)).title(), lodge.group(2).strip(), bed
    security = re.match(r"^Security\s*(.+)$", room, re.IGNORECASE)
    if security:
        return "Security Quarters", security.group(1).strip(), bed
    admin = re.match(r"^Admin\s*(.+)$", room, re.IGNORECASE)
    if admin:
        return "Admin", admin.group(1).strip(), bed
    number = re.match(r"^Room\s*(\d+)$", room, re.IGNORECASE)
    if number:
        return MAIN_HOSTEL, f"Room {number.group(1)}", bed
    return MAIN_HOSTEL, room, bed


def sync_employee_flags(employee):
    """Keep the older 'lives in hostel / outside' fields on the employee in step with where they are placed."""
    assignment = getattr(employee, "room_assignment", None)
    inside, outside, room_text, address = False, False, "", ""
    if assignment is not None:
        building = assignment.room.building
        if building.kind == BuildingKind.COMPANY:
            inside, room_text = True, f"{building.name} {assignment.room.name}".replace(f"{MAIN_HOSTEL} ", "")
        else:
            outside, address = True, building.address or building.name
    else:
        inside, outside, room_text, address = employee.lives_in_company_hostel, employee.lives_in_external_accommodation, employee.hostel_room_number, employee.external_accommodation_address
    Employee.objects.filter(pk=employee.pk).update(lives_in_company_hostel=inside, lives_in_external_accommodation=outside, hostel_room_number=room_text if inside else "", external_accommodation_address=address if outside else "")


class AccommodationService:
    @staticmethod
    def assign(employee, room, *, bed_number=None, actor=None, enforce_capacity=True):
        with transaction.atomic():
            if employee.status != "active":
                raise ValueError("Only active staff can be given a room.")
            room = Room.objects.select_for_update().select_related("building").get(pk=room.pk)
            if not room.active:
                raise ValueError("This room is not in use.")
            if bed_number is not None:
                taken = RoomAssignment.objects.filter(room=room, bed_number=bed_number).exclude(employee=employee).select_related("employee").first()
                if taken:
                    raise ValueError(f"Bed {bed_number} in {room.name} is already taken by {taken.employee.full_name}.")
            elif enforce_capacity and room.capacity is not None and RoomAssignment.objects.filter(room=room).exclude(employee=employee).count() >= room.capacity:
                raise ValueError(f"{room.name} is full ({room.capacity} of {room.capacity}).")
            existing = RoomAssignment.objects.filter(employee=employee).select_related("room__building").first()
            previous = f"{existing.room.building.name} {existing.room.name}" if existing else None
            assignment, _ = RoomAssignment.objects.update_or_create(employee=employee, defaults={"room": room, "bed_number": bed_number, "assigned_by": actor})
            sync_employee_flags(Employee.objects.get(pk=employee.pk))
            AuditService.log(event_type="accommodation.assigned", module="accommodation", employee=employee, actor=actor, object=assignment, severity=AuditSeverity.INFO, title="Room assigned", description=f"{employee.full_name} placed in {room.building.name} {room.name}" + (f", bed {bed_number}" if bed_number else "") + (f" (was {previous})." if previous else "."), metadata={"room_id": room.pk, "bed": bed_number})
        return assignment

    @staticmethod
    def unassign(employee, *, actor=None, outside=False):
        """Take someone out of their room. `outside` records that they live in accommodation outside the company."""
        with transaction.atomic():
            existing = RoomAssignment.objects.filter(employee=employee).select_related("room__building").first()
            if existing:
                label = f"{existing.room.building.name} {existing.room.name}"
                existing.delete()
                AuditService.log(event_type="accommodation.unassigned", module="accommodation", employee=employee, actor=actor, severity=AuditSeverity.INFO, title="Room vacated", description=f"{employee.full_name} left {label}.")
            employee = Employee.objects.get(pk=employee.pk)
            Employee.objects.filter(pk=employee.pk).update(lives_in_company_hostel=False, hostel_room_number="", lives_in_external_accommodation=outside, external_accommodation_address="" if not outside else employee.external_accommodation_address)


SEQUENCE_NOTE = "Added to keep the room numbers in sequence. Set its capacity, or close it if it does not exist."
NUMBERED_ROOM = re.compile(r"^(?P<prefix>\D*?)(?P<number>\d+)$")


def fill_room_sequence(building=None):
    """Add the empty rooms missing from a run of consecutive room numbers, so 001, 002, 006 becomes
    001 ... 006. A run is one floor of one style of name (Room 001-011, Room 301-314, Rm 003-011).
    Numbers are only filled between two rooms that exist, never before the first or after the last.
    Returns the names added."""
    added = []
    for current in ([building] if building else list(Building.objects.all())):
        groups = defaultdict(dict)
        for room in current.rooms.all():
            match = NUMBERED_ROOM.match(room.name)
            if match:
                number, width = int(match.group("number")), len(match.group("number"))
                groups[(match.group("prefix"), width, number // 100)][number] = room
        for (prefix, width, _floor), rooms in groups.items():
            for number in range(min(rooms), max(rooms) + 1):
                if number not in rooms:
                    name = f"{prefix}{number:0{width}d}"
                    _room, created = Room.objects.get_or_create(building=current, name=name, defaults={"capacity": None, "notes": SEQUENCE_NOTE})
                    if created:
                        added.append(f"{current.name} {name}")
    return added


BUILDING_ORDER = [MAIN_HOSTEL, "Lodge 1", "Lodge 2", "Security Quarters", "Admin"]


def apply_import_placement(employee, placement, room_label, *, actor=None):
    """Place one employee as the Bulk Import's Accommodation / ROOM ALLOCATED columns say.
    Returns what happened: inside, inside_no_bed, vacated, outside or none (None = left alone)."""
    if not placement:
        return None
    parsed = parse_room_label(room_label) if room_label else None
    if placement == "inside" and parsed:
        building_name, room_name, bed = parsed
        building, _ = Building.objects.get_or_create(name=building_name, defaults={"kind": BuildingKind.COMPANY, "sort_order": BUILDING_ORDER.index(building_name) if building_name in BUILDING_ORDER else 50})
        room, created = Room.objects.get_or_create(building=building, name=room_name, defaults={"capacity": bed, "capacity_estimated": True})
        # A room's size is a guess from the highest bed seen until someone sets it.
        if not created and room.capacity_estimated and (room.capacity is None or bed > room.capacity):
            room.capacity = bed
            room.save(update_fields=["capacity"])
        if employee.status != "active":
            AccommodationService.unassign(employee, actor=actor)  # they have left: the bed is free again
            return "vacated"
        try:
            AccommodationService.assign(employee, room, bed_number=bed, actor=actor)
            return "inside"
        except ValueError:
            AccommodationService.assign(employee, room, actor=actor, enforce_capacity=False)  # bed already taken: place without a bed number
            return "inside_no_bed"
    AccommodationService.unassign(employee, actor=actor, outside=placement == "outside" and employee.status == "active")
    return "outside" if placement == "outside" else "none"


@dataclass
class ImportReport:
    dry_run: bool = True
    active_rows: int = 0
    placed_inside: int = 0
    outside_unplaced: int = 0
    no_accommodation: int = 0
    buildings_created: int = 0
    rooms_created: int = 0
    moved: int = 0
    unchanged: int = 0
    unmatched_ids: list = field(default_factory=list)
    unreadable_rooms: list = field(default_factory=list)
    bed_conflicts: list = field(default_factory=list)
    mixed_gender_rooms: list = field(default_factory=list)
    freed_beds_from_inactive: int = 0
    rooms_added_for_sequence: list = field(default_factory=list)

    def as_dict(self):
        return self.__dict__.copy()


def _header_map(sheet):
    headers = next(sheet.iter_rows(min_row=1, max_row=1, values_only=True))
    return {str(name).strip().lower(): index for index, name in enumerate(headers) if name is not None}


def _find_sheet(workbook):
    for sheet in workbook.worksheets:
        headers = {str(c).strip().lower() for c in next(sheet.iter_rows(min_row=1, max_row=1, values_only=True)) if c is not None}
        if {"id", "room allocated", "employment status"} <= headers:
            return sheet
    raise ValueError("No sheet with the columns ID, EMPLOYMENT STATUS, Accommodation and ROOM ALLOCATED was found.")


def import_workbook(file, *, dry_run, actor=None):
    """Apply the staff spreadsheet: room allocated = inside the company accommodation (in that bed);
    Accommodation YES with no room = outside accommodation, place not recorded; NO = no accommodation."""
    sheet = _find_sheet(load_workbook(file, data_only=True, read_only=False))
    cols = _header_map(sheet)
    need = ["id", "employment status", "accommodation", "room allocated"]
    missing = [name for name in need if name not in cols]
    if missing:
        raise ValueError(f"Missing column(s): {', '.join(missing)}.")
    report = ImportReport(dry_run=dry_run)
    rows = [row for row in sheet.iter_rows(min_row=2, values_only=True) if row[cols["id"]]]

    capacity = defaultdict(int)  # highest bed number seen per room, counting people who have since left
    for row in rows:
        parsed = parse_room_label(row[cols["room allocated"]])
        if parsed:
            capacity[(parsed[0], parsed[1])] = max(capacity[(parsed[0], parsed[1])], parsed[2])
            if str(row[cols["employment status"]]).strip().lower() != "active":
                report.freed_beds_from_inactive += 1

    with transaction.atomic():
        buildings, rooms = {}, {}
        genders = defaultdict(set)
        sort = {name: index for index, name in enumerate([MAIN_HOSTEL, "Lodge 1", "Lodge 2", "Security Quarters", "Admin"])}

        def building_for(name):
            if name not in buildings:
                building, created = Building.objects.get_or_create(name=name, defaults={"kind": BuildingKind.COMPANY, "sort_order": sort.get(name, 50)})
                report.buildings_created += created
                buildings[name] = building
            return buildings[name]

        def room_for(building_name, room_name):
            key = (building_name, room_name)
            if key not in rooms:
                building = building_for(building_name)
                room, created = Room.objects.get_or_create(building=building, name=room_name, defaults={"capacity": capacity[key] or None, "capacity_estimated": True})
                report.rooms_created += created
                rooms[key] = room
            return rooms[key]

        for row in rows:
            if str(row[cols["employment status"]]).strip().lower() != "active":
                continue
            report.active_rows += 1
            employee_id = str(row[cols["id"]]).strip()
            employee = Employee.objects.filter(employee_id=employee_id).first()
            if employee is None:
                report.unmatched_ids.append(employee_id)
                continue
            label = row[cols["room allocated"]]
            has_label = bool(label and str(label).strip())
            parsed = parse_room_label(label) if has_label else None
            if has_label and not parsed:
                report.unreadable_rooms.append({"employee_id": employee_id, "value": str(label).strip()})
            if parsed:
                building_name, room_name, bed = parsed
                room = room_for(building_name, room_name)
                if "gender" in cols and row[cols["gender"]]:
                    genders[(building_name, room_name)].add(str(row[cols["gender"]]).strip())
                current = RoomAssignment.objects.filter(employee=employee).select_related("room").first()
                if current and current.room_id == room.pk and current.bed_number == bed:
                    report.unchanged += 1
                    report.placed_inside += 1
                    sync_employee_flags(employee)
                    continue
                clash = RoomAssignment.objects.filter(room=room, bed_number=bed).exclude(employee=employee).first()
                if clash:
                    report.bed_conflicts.append({"employee_id": employee_id, "room": f"{building_name} {room_name}", "bed": bed, "already": clash.employee.employee_id})
                    bed = None
                if current:
                    report.moved += 1
                RoomAssignment.objects.update_or_create(employee=employee, defaults={"room": room, "bed_number": bed, "assigned_by": actor})
                sync_employee_flags(Employee.objects.get(pk=employee.pk))
                report.placed_inside += 1
            elif str(row[cols["accommodation"]]).strip().upper() == "YES":
                RoomAssignment.objects.filter(employee=employee).delete()
                AccommodationService.unassign(employee, actor=actor, outside=True)
                report.outside_unplaced += 1
            else:
                RoomAssignment.objects.filter(employee=employee).delete()
                AccommodationService.unassign(employee, actor=actor, outside=False)
                report.no_accommodation += 1
        report.mixed_gender_rooms = [f"{b} {r}" for (b, r), g in genders.items() if len(g) > 1]
        report.rooms_added_for_sequence = fill_room_sequence()
        if not dry_run:
            AuditService.log(event_type="accommodation.imported", module="accommodation", actor=actor, severity=AuditSeverity.INFO, title="Accommodation spreadsheet imported", description=f"{report.placed_inside} placed in rooms, {report.outside_unplaced} outside, {report.no_accommodation} with none.", metadata=report.as_dict() | {"unmatched_ids": report.unmatched_ids[:50]})
        else:
            transaction.set_rollback(True)
    return report


def build_overview():
    """Everything the accommodation screen needs in one go."""
    assignments = list(RoomAssignment.objects.select_related("employee", "employee__department", "room", "room__building"))
    by_room = defaultdict(list)
    for assignment in assignments:
        by_room[assignment.room_id].append(assignment)
    buildings = []
    total_capacity = total_occupied = 0
    for building in Building.objects.prefetch_related("rooms"):
        room_rows = []
        for room in building.rooms.all():
            people = sorted(by_room.get(room.pk, []), key=lambda a: (a.bed_number is None, a.bed_number or 0, a.employee.employee_id))
            occupied = len(people)
            capacity = room.capacity
            if not room.active:
                state = "closed"
            elif capacity is None:
                state = "unknown"
            elif occupied > capacity:
                state = "over"
            elif occupied == capacity:
                state = "full"
            elif occupied == 0:
                state = "empty"
            else:
                state = "space"
            if room.active and building.kind == BuildingKind.COMPANY:
                total_occupied += occupied
                total_capacity += capacity or occupied
            room_rows.append({
                "id": room.pk, "name": room.name, "capacity": capacity, "capacity_estimated": room.capacity_estimated, "occupied": occupied,
                "free": None if capacity is None else max(capacity - occupied, 0), "state": state, "active": room.active, "notes": room.notes,
                "occupants": [{"employee": a.employee.pk, "employee_id": a.employee.employee_id, "name": a.employee.full_name, "bed": a.bed_number, "department": a.employee.department.name if a.employee.department else None} for a in people],
            })
        room_rows.sort(key=lambda r: [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", r["name"])])
        buildings.append({"id": building.pk, "name": building.name, "kind": building.kind, "address": building.address, "rooms": room_rows, "occupied": sum(r["occupied"] for r in room_rows), "capacity": sum(r["capacity"] or 0 for r in room_rows if r["active"]), "full_rooms": sum(r["state"] == "full" for r in room_rows), "empty_rooms": sum(r["state"] == "empty" for r in room_rows), "rooms_with_space": sum(r["state"] == "space" for r in room_rows)})

    active = Employee.objects.filter(status="active")
    placed_ids = {a.employee_id for a in assignments}
    inside_placed = sum(1 for a in assignments if a.room.building.kind == BuildingKind.COMPANY and a.employee.status == "active")
    outside_placed = sum(1 for a in assignments if a.room.building.kind == BuildingKind.EXTERNAL and a.employee.status == "active")
    outside_unplaced = active.filter(lives_in_external_accommodation=True).exclude(pk__in=placed_ids).count()
    inside_unplaced = active.filter(lives_in_company_hostel=True).exclude(pk__in=placed_ids).count()
    active_count = active.count()
    return {
        "summary": {
            "active_staff": active_count,
            "inside": inside_placed + inside_unplaced,
            "inside_with_room": inside_placed,
            "inside_without_room": inside_unplaced,
            "outside": outside_placed + outside_unplaced,
            "outside_placed": outside_placed,
            "outside_unplaced": outside_unplaced,
            "none": active_count - (inside_placed + inside_unplaced + outside_placed + outside_unplaced),
            "beds_total": total_capacity, "beds_occupied": total_occupied, "beds_free": max(total_capacity - total_occupied, 0),
            "rooms_full": sum(b["full_rooms"] for b in buildings if b["kind"] == "company"),
            "rooms_with_space": sum(b["rooms_with_space"] for b in buildings if b["kind"] == "company"),
            "rooms_empty": sum(b["empty_rooms"] for b in buildings if b["kind"] == "company"),
        },
        "buildings": buildings,
    }

import io

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase
from openpyxl import Workbook
from rest_framework.test import APIClient

from employees.models import Employee

from .models import Building, Room, RoomAssignment
from .services import AccommodationService, build_overview, import_workbook, parse_room_label

HEADERS = ["ID", "NAME", "GENDER", "EMPLOYMENT STATUS", "Accommodation ", "ROOM ALLOCATED"]


def workbook(rows):
    book = Workbook()
    sheet = book.active
    sheet.title = "Employee_DB"
    sheet.append(HEADERS)
    for row in rows:
        sheet.append(row)
    buffer = io.BytesIO()
    book.save(buffer)
    buffer.seek(0)
    return buffer


class RoomLabelTests(SimpleTestCase):
    def test_labels_map_to_buildings_rooms_and_beds(self):
        self.assertEqual(parse_room_label("Room 301 - Bed 1"), ("Main Hostel", "Room 301", 1))
        self.assertEqual(parse_room_label("Room101 - Bed 1"), ("Main Hostel", "Room 101", 1))
        self.assertEqual(parse_room_label("Lodge 1 Rm001 - Bed 5"), ("Lodge 1", "Rm001", 5))
        self.assertEqual(parse_room_label("Security Rm 006 - Bed 2"), ("Security Quarters", "Rm 006", 2))
        self.assertEqual(parse_room_label("Admin G2 - Bed 3"), ("Admin", "G2", 3))
        self.assertIsNone(parse_room_label("    "))
        self.assertIsNone(parse_room_label("somewhere"))


class ImportTests(TestCase):
    def setUp(self):
        for number in ("000001", "000002", "000003", "000004", "000005"):
            Employee.objects.create(employee_id=number, first_name="Staff", last_name=number)
        Employee.objects.filter(employee_id="000005").update(status="inactive")

    rows = [
        ["000001", "A", "Female", "Active", "YES", "Room 301 - Bed 1"],
        ["000002", "B", "Male", "Active", "YES", "Room 301 - Bed 2"],
        ["000003", "C", "Male", "Active", "YES", None],
        ["000004", "D", "Male", "Active", "NO", None],
        ["000005", "E", "Male", "Inactive", "YES", "Room 301 - Bed 4"],
        ["000999", "Nobody", "Male", "Active", "YES", "Room 301 - Bed 3"],
    ]

    def test_a_preview_changes_nothing(self):
        report = import_workbook(workbook(self.rows), dry_run=True)
        self.assertEqual((report.placed_inside, report.outside_unplaced, report.no_accommodation), (2, 1, 1))
        self.assertEqual(report.unmatched_ids, ["000999"])
        self.assertFalse(Room.objects.exists())
        self.assertFalse(RoomAssignment.objects.exists())

    def test_apply_places_people_and_sets_the_inside_outside_flags(self):
        import_workbook(workbook(self.rows), dry_run=False)
        room = Room.objects.get(name="Room 301")
        self.assertEqual(room.building.name, "Main Hostel")
        self.assertEqual(room.capacity, 4)  # highest bed seen, including the person who has left
        self.assertTrue(room.capacity_estimated)
        one, three, four = (Employee.objects.get(employee_id=n) for n in ("000001", "000003", "000004"))
        self.assertTrue(one.lives_in_company_hostel and not one.lives_in_external_accommodation)
        self.assertEqual(one.room_assignment.bed_number, 1)
        self.assertTrue(three.lives_in_external_accommodation and not three.lives_in_company_hostel)
        self.assertFalse(four.lives_in_company_hostel or four.lives_in_external_accommodation)
        self.assertFalse(RoomAssignment.objects.filter(employee__employee_id="000005").exists())

    def test_importing_again_changes_nothing_and_a_move_is_followed(self):
        import_workbook(workbook(self.rows), dry_run=False)
        again = import_workbook(workbook(self.rows), dry_run=False)
        self.assertEqual((again.unchanged, again.moved), (2, 0))
        moved = [["000001", "A", "Female", "Active", "YES", "Room 301 - Bed 3"]]
        report = import_workbook(workbook(moved), dry_run=False)
        self.assertEqual(report.moved, 1)
        self.assertEqual(Employee.objects.get(employee_id="000001").room_assignment.bed_number, 3)

    def test_someone_who_no_longer_has_a_room_is_moved_outside(self):
        import_workbook(workbook(self.rows), dry_run=False)
        import_workbook(workbook([["000001", "A", "Female", "Active", "YES", None]]), dry_run=False)
        person = Employee.objects.get(employee_id="000001")
        self.assertFalse(RoomAssignment.objects.filter(employee=person).exists())
        self.assertTrue(person.lives_in_external_accommodation and not person.lives_in_company_hostel)

    def test_a_sheet_without_the_right_columns_is_refused(self):
        book = Workbook()
        book.active.append(["Name", "Phone"])
        buffer = io.BytesIO()
        book.save(buffer)
        buffer.seek(0)
        with self.assertRaises(ValueError):
            import_workbook(buffer, dry_run=True)


class AssignAndOverviewTests(TestCase):
    def setUp(self):
        self.building = Building.objects.create(name="Lodge 1")
        self.room = Room.objects.create(building=self.building, name="Rm001", capacity=2)
        self.a, self.b, self.c = (Employee.objects.create(employee_id=f"00000{n}", first_name="S", last_name=str(n)) for n in (1, 2, 3))

    def test_beds_cannot_be_double_booked_and_a_full_room_refuses_more(self):
        AccommodationService.assign(self.a, self.room, bed_number=1)
        with self.assertRaises(ValueError):
            AccommodationService.assign(self.b, self.room, bed_number=1)
        AccommodationService.assign(self.b, self.room)
        with self.assertRaises(ValueError):
            AccommodationService.assign(self.c, self.room)

    def test_overview_counts_full_and_empty_rooms(self):
        Room.objects.create(building=self.building, name="Rm002", capacity=3)
        Room.objects.create(building=self.building, name="Rm003", capacity=None)
        AccommodationService.assign(self.a, self.room, bed_number=1)
        AccommodationService.assign(self.b, self.room, bed_number=2)
        overview = build_overview()
        states = {r["name"]: r["state"] for r in overview["buildings"][0]["rooms"]}
        self.assertEqual(states, {"Rm001": "full", "Rm002": "empty", "Rm003": "unknown"})
        self.assertEqual(overview["summary"]["inside_with_room"], 2)
        self.assertEqual(overview["summary"]["none"], 1)

    def test_a_person_can_only_be_in_one_place_and_leaving_frees_the_bed(self):
        other = Room.objects.create(building=self.building, name="Rm002", capacity=2)
        AccommodationService.assign(self.a, self.room, bed_number=1)
        AccommodationService.assign(self.a, other, bed_number=1)
        self.assertEqual(RoomAssignment.objects.filter(employee=self.a).count(), 1)
        AccommodationService.unassign(self.a)
        self.assertFalse(RoomAssignment.objects.exists())

    def test_permissions_and_upload_endpoint(self):
        client = APIClient()
        viewer = get_user_model().objects.create_user("viewer", password="pw")
        viewer.user_permissions.add(Permission.objects.get(codename="view_accommodation"))
        client.force_authenticate(viewer)
        self.assertEqual(client.get("/api/accommodation/").status_code, 200)
        self.assertEqual(client.post("/api/accommodation/assign/", {"employee": self.a.pk, "room": self.room.pk}, format="json").status_code, 403)
        manager = get_user_model().objects.create_user("manager", password="pw")
        manager.user_permissions.add(Permission.objects.get(codename="manage_accommodation"))
        client.force_authenticate(manager)
        upload = SimpleUploadedFile("staff.xlsx", workbook([["000001", "A", "Female", "Active", "YES", "Room 301 - Bed 1"]]).read())
        preview = client.post("/api/accommodation/import/", {"file": upload}, format="multipart")
        self.assertEqual((preview.status_code, preview.json()["dry_run"], preview.json()["placed_inside"]), (200, True, 1))
        outsider = get_user_model().objects.create_user("outsider", password="pw")
        client.force_authenticate(outsider)
        self.assertEqual(client.get("/api/accommodation/").status_code, 403)


class RoomSequenceTests(TestCase):
    def rooms(self, building, names):
        for name in names:
            Room.objects.create(building=building, name=name, capacity=4)

    def test_gaps_inside_a_run_are_filled_with_empty_rooms_in_order(self):
        from .services import fill_room_sequence
        hostel = Building.objects.create(name="Main Hostel")
        self.rooms(hostel, ["Room 001", "Room 002", "Room 006", "Room 301", "Room 304"])
        added = fill_room_sequence()
        self.assertEqual(added, ["Main Hostel Room 003", "Main Hostel Room 004", "Main Hostel Room 005", "Main Hostel Room 302", "Main Hostel Room 303"])
        names = [r["name"] for r in build_overview()["buildings"][0]["rooms"]]
        self.assertEqual(names, ["Room 001", "Room 002", "Room 003", "Room 004", "Room 005", "Room 006", "Room 301", "Room 302", "Room 303", "Room 304"])
        self.assertIsNone(Room.objects.get(name="Room 003").capacity)

    def test_it_never_invents_rooms_before_the_first_or_after_the_last_and_keeps_floors_apart(self):
        from .services import fill_room_sequence
        lodge = Building.objects.create(name="Lodge 2")
        self.rooms(lodge, ["Rm003", "Rm005", "Rm101"])
        self.assertEqual(fill_room_sequence(), ["Lodge 2 Rm004"])
        self.assertFalse(Room.objects.filter(name__in=["Rm001", "Rm002", "Rm102", "Rm006", "Rm050"]).exists())

    def test_running_it_twice_adds_nothing_more_and_closed_rooms_stay_out_of_the_bed_count(self):
        from .services import fill_room_sequence
        hostel = Building.objects.create(name="Main Hostel")
        self.rooms(hostel, ["Room 001", "Room 003"])
        fill_room_sequence()
        self.assertEqual(fill_room_sequence(), [])
        Room.objects.filter(name="Room 002").update(active=False)
        overview = build_overview()
        self.assertEqual(overview["summary"]["beds_total"], 8)

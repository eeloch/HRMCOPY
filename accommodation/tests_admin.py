from employees.admin_testing import AdminTestCase

from .models import Building, Room, RoomAssignment
from .services import AccommodationService


class AccommodationAdminTests(AdminTestCase):
    def setUp(self):
        super().setUp()
        self.ada = self.make_employee(middle="Grace", gender="female")
        self.bola = self.make_employee("000685", "Bola", "Adeyemi", gender="male")
        self.hostel = Building.objects.create(name="Main Hostel")
        self.women = Room.objects.create(building=self.hostel, name="Room 301", gender="female", capacity=2)
        self.men = Room.objects.create(building=self.hostel, name="Room 302", gender="male", capacity=1)
        self.ada_bed = AccommodationService.assign(self.ada, self.women, bed_number=1, actor=self.admin_user)
        self.bola_bed = AccommodationService.assign(self.bola, self.men, actor=self.admin_user)

    def test_changelists_load(self):
        for model in (Building, Room, RoomAssignment):
            self.get_list(model)
        for params in ({"occupancy": "full"}, {"occupancy": "empty"}, {"occupancy": "space"}, {"occupancy": "over"}, {"gender__exact": "female"}, {"active__exact": "1"}):
            self.get_list(Room, **params)

    def test_room_filters(self):
        found = lambda **p: {r.pk for r in self.get_list(Room, **p).context["cl"].result_list}  # noqa: E731
        self.assertEqual(found(occupancy="full"), {self.men.pk})
        self.assertEqual(found(occupancy="space"), {self.women.pk})

    def test_assignment_search_and_columns(self):
        self.assert_search(RoomAssignment, "000684", [self.ada_bed.pk])
        self.assert_search(RoomAssignment, "Grace", [self.ada_bed.pk])
        self.assert_search(RoomAssignment, "Adeyemi", [self.bola_bed.pk])
        self.assert_search(RoomAssignment, "Room 302", [self.bola_bed.pk])
        page = self.get_list(RoomAssignment).content.decode()
        for text in ("000684", "Ada Grace Okafor", "Operations", "Main Hostel"):
            self.assertIn(text, page)

    def test_no_n_plus_one(self):
        counter = iter(range(10, 200))

        def more():
            for _ in range(4):
                n = next(counter)
                room = Room.objects.create(building=self.hostel, name=f"Extra {n}", capacity=2)
                AccommodationService.assign(self.make_employee(f"0008{n}", "Extra", f"Person{n}"), room, actor=self.admin_user)

        self.assert_same_query_count(RoomAssignment, more)
        self.assert_same_query_count(Room, more)
        self.assert_same_query_count(Building, lambda: Building.objects.create(name=f"Lodge {next(counter)}"))

    def test_str(self):
        self.assertEqual(str(self.ada_bed), "000684 Ada Grace Okafor - Main Hostel Room 301, bed 1")

    def test_assignments_cannot_be_added_or_edited_directly(self):
        self.assertEqual(self.client.get(self.url(RoomAssignment, "add")).status_code, 403)
        self.assertEqual(self.client.post(self.url(RoomAssignment, "change", self.ada_bed.pk), {"room": self.men.pk}).status_code, 403)

    def test_room_page_lists_occupants(self):
        response = self.client.get(self.url(Room, "change", self.women.pk))
        self.assertContains(response, "000684")
        self.assertContains(response, "Ada Grace Okafor")

    def test_room_form_refuses_a_capacity_below_the_occupants(self):
        response = self.client.post(self.url(Room, "change", self.women.pk), {"building": self.hostel.pk, "name": "Room 301", "gender": "female", "capacity": "0", "notes": "", "active": "on",
                                                                                "assignments-TOTAL_FORMS": "0", "assignments-INITIAL_FORMS": "0"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "already in this room")

    def test_vacate_asks_first_then_uses_the_service_and_audits(self):
        self.run_action(RoomAssignment, "vacate_rooms", [self.ada_bed.pk], confirm=False)
        self.assertTrue(RoomAssignment.objects.filter(pk=self.ada_bed.pk).exists())
        self.run_action(RoomAssignment, "vacate_rooms", [self.ada_bed.pk])
        self.assertFalse(RoomAssignment.objects.filter(pk=self.ada_bed.pk).exists())
        self.ada.refresh_from_db()
        self.assertFalse(self.ada.lives_in_company_hostel)
        self.assertEqual(self.audit("accommodation.unassigned", employee=self.ada).count(), 1)

    def test_move_applies_the_gender_rule_per_person(self):
        empty = Room.objects.create(building=self.hostel, name="Room 303", gender="female", capacity=3)
        _, second = self.run_action(RoomAssignment, "move_to_room", [self.ada_bed.pk, self.bola_bed.pk], room=empty.pk)
        self.assertEqual(RoomAssignment.objects.get(employee=self.ada).room, empty)
        self.assertEqual(RoomAssignment.objects.get(employee=self.bola).room, self.men)  # a man cannot go to a women's room
        self.assertContains(second, "is a female room")
        self.assertEqual(self.audit("accommodation.assigned", employee=self.ada).count(), 2)
        self.ada.refresh_from_db()
        self.assertTrue(self.ada.lives_in_company_hostel)

from datetime import date, datetime
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from attendance.integrations.aiface_protocol import IDENTITY_SYSTEM
from attendance.models import BiometricDevice, DeviceCommand, EmployeeRosterDay, RosterDayStatus, Shift
from audit.admin_testing import admin_messages, run_admin_action, superuser
from audit.models import AuditEvent
from employees.models import BiometricIdentity, Department, Employee
from meals.authorizations import authorise
from meals.models import (
    EmployeeMealEntitlement,
    MealAbsencePenalty,
    MealCollection,
    MealDevice,
    MealEntitlementRule,
    MealExcessException,
    MealExcessStatus,
    MealExtraAuthorization,
    MealTerminalUserState,
    MealTicketRate,
    MealVendorPayment,
)
from meals.services import MealService
from payroll.models import PayrollPeriod

COLLECTIONS = "/admin/meals/mealcollection/"
EXCESS = "/admin/meals/mealexcessexception/"


class MealAdminActionTests(TestCase):
    def setUp(self):
        self.root = superuser()
        self.client.force_login(self.root)
        self.day = date(2026, 9, 7)
        dept = Department.objects.create(name="Melting")
        self.ada = Employee.objects.create(employee_id="000684", first_name="Ada", last_name="Okafor", department=dept)
        self.bayo = Employee.objects.create(employee_id="000700", first_name="Bayo", last_name="Eze", department=dept)
        MealTicketRate.objects.create(amount=Decimal("700.00"), effective_from=self.day)
        MealDevice.objects.create(name="Canteen", serial_number="MEAL001", active=True)
        shift = Shift.objects.create(name="Admin Day", start_time="07:00", end_time="19:00", is_overnight=False)
        for number, person in ((10, self.ada), (11, self.bayo)):
            EmployeeMealEntitlement.objects.create(employee=person, tickets_per_work_day=1, effective_from=self.day, reason="test")
            BiometricIdentity.objects.create(employee=person, system="device", source_identifier="MEAL001", external_user_id=str(number))
            EmployeeRosterDay.objects.create(employee=person, date=self.day, status=RosterDayStatus.WORK, shift=shift)
        PayrollPeriod.objects.create(year=2026, month=9)

    def scan(self, person, event_id, hour=12):
        collection, _ = MealService.ingest(
            system="device", source_identifier="MEAL001", device_serial_number="MEAL001", external_user_id={self.ada: "10", self.bayo: "11"}[person],
            external_event_id=event_id, timestamp=timezone.make_aware(datetime(2026, 9, 7, hour, 0)),
        )
        return collection

    def messages_contain(self, response, text):
        return any(text in message for message in admin_messages(response))

    # ---- lists
    def test_every_meals_list_loads_with_data(self):
        first, second = self.scan(self.ada, "a1"), self.scan(self.ada, "a2", 13)
        authorise(employee=self.bayo, quantity=1, pays="company", actor=self.root, work_date=self.day)
        MealTerminalUserState.objects.create(employee=self.ada, device_serial="MEAL001", enabled=False)
        MealAbsencePenalty.objects.create(employee=self.ada, penalty_type="single_absence")
        MealVendorPayment.objects.create(payroll_period=PayrollPeriod.objects.get(), amount=Decimal("1400.00"), payment_date=self.day)
        MealEntitlementRule.objects.create(tickets_per_work_day=1, description="Everyone")
        for name in ("mealdevice", "mealticketrate", "mealentitlementrule", "employeemealentitlement", "mealevent", "mealcollection", "mealexcessexception", "mealextraauthorization", "mealterminaluserstate", "mealabsencepenalty", "mealvendorpayment"):
            response = self.client.get(f"/admin/meals/{name}/")
            self.assertEqual(response.status_code, 200, name)
            self.assertNotContains(response, "delete_selected")
        response = self.client.get(COLLECTIONS)
        for text in ("000684", "Ada Okafor", "N 700.00", "2 of 1", "Excess"):
            self.assertContains(response, text)
        self.assertEqual(str(first), "000684 Ada Okafor - 2026-09-07 - ticket 1 of 1")
        self.assertContains(self.client.get(EXCESS), "N 700.00")

    def test_search_and_filters_on_collections(self):
        self.scan(self.ada, "a1"); self.scan(self.bayo, "b1")
        for term, expected in (("000684", 1), ("Okafor", 1), ("Ada Okafor", 1), ("Eze", 1)):
            self.assertEqual(len(self.client.get(COLLECTIONS, {"q": term}).context["cl"].result_list), expected, term)
        self.assertEqual(len(self.client.get(COLLECTIONS, {"voided": "yes"}).context["cl"].result_list), 0)
        self.assertEqual(len(self.client.get(COLLECTIONS, {"voided": "no"}).context["cl"].result_list), 2)

    def test_collections_are_read_only_and_undeletable(self):
        collection = self.scan(self.ada, "a1")
        detail = f"{COLLECTIONS}{collection.pk}/change/"
        self.assertEqual(self.client.get(detail).status_code, 200)
        self.assertEqual(self.client.post(detail, {"voided_at_0": "2026-09-07", "_save": "Save"}).status_code, 403)
        self.assertEqual(self.client.get(f"{COLLECTIONS}{collection.pk}/delete/").status_code, 403)
        self.assertEqual(self.client.get(COLLECTIONS + "add/").status_code, 403)

    # ---- void
    def test_void_needs_a_reason_then_uses_the_service(self):
        self.scan(self.ada, "a1")
        second = self.scan(self.ada, "a2", 13)
        exception = MealExcessException.objects.get()
        page, response = run_admin_action(self.client, COLLECTIONS, "void_collections", [second.pk], reason="")
        self.assertContains(page, "Reason for voiding")
        self.assertContains(response, "Reason for voiding is required.")
        second.refresh_from_db()
        self.assertIsNone(second.voided_at)

        _, response = run_admin_action(self.client, COLLECTIONS, "void_collections", [second.pk], reason="Accidental scan")
        second.refresh_from_db(); exception.refresh_from_db()
        self.assertEqual((second.voided_by, second.void_reason), (self.root, "Accidental scan"))
        self.assertEqual(exception.status, MealExcessStatus.CANCELLED)
        self.assertTrue(AuditEvent.objects.filter(event_type="meals.ticket_voided", actor=self.root).exists())
        summary = AuditEvent.objects.get(event_type="admin.meals.void_collections")
        self.assertEqual((summary.metadata["reason"], summary.metadata["done_count"], summary.metadata["via"]), ("Accidental scan", 1, "django-admin"))
        self.assertTrue(self.messages_contain(response, "Tickets voided: 1 of 1 selected."))

        _, response = run_admin_action(self.client, COLLECTIONS, "void_collections", [second.pk], reason="Again")
        self.assertTrue(self.messages_contain(response, "already been voided"))

    def test_void_is_refused_once_the_excess_is_deducted(self):
        self.scan(self.ada, "a1")
        second = self.scan(self.ada, "a2", 13)
        MealExcessException.objects.update(status=MealExcessStatus.DEDUCTED)
        _, response = run_admin_action(self.client, COLLECTIONS, "void_collections", [second.pk], reason="Oops")
        second.refresh_from_db()
        self.assertIsNone(second.voided_at)
        self.assertTrue(self.messages_contain(response, "already deducted"))

    # ---- excess decisions
    def excess(self, hour=13, event="a2"):
        self.scan(self.ada, f"first-{event}")
        self.scan(self.ada, event, hour)
        return MealExcessException.objects.filter(employee=self.ada).latest("id")

    def test_accept_excess_charges_through_the_service(self):
        exception = self.excess()
        _, response = run_admin_action(self.client, EXCESS, "accept_excess", [exception.pk], reason="Fine")
        exception.refresh_from_db()
        self.assertIn(exception.status, (MealExcessStatus.APPROVED, MealExcessStatus.DEDUCTED))
        self.assertEqual(exception.reviewer, self.root)
        self.assertTrue(AuditEvent.objects.filter(event_type="meals.excess_approved", actor=self.root).exists())
        self.assertTrue(AuditEvent.objects.filter(event_type="admin.meals.accept_excess").exists())
        self.assertTrue(self.messages_contain(response, "Excess accepted: 1 of 1 selected."))

    def test_waive_and_decline_need_a_reason_and_use_the_service(self):
        exception = self.excess()
        _, response = run_admin_action(self.client, EXCESS, "waive_excess", [exception.pk], reason="")
        self.assertContains(response, "Reason for waiving is required.")
        run_admin_action(self.client, EXCESS, "waive_excess", [exception.pk], reason="Supervisor asked")
        exception.refresh_from_db()
        self.assertEqual(exception.status, MealExcessStatus.CANCELLED)
        self.assertTrue(AuditEvent.objects.filter(event_type="meals.excess_cancelled", actor=self.root).exists())

        other = self.excess(hour=14, event="a3")
        run_admin_action(self.client, EXCESS, "decline_excess", [other.pk], reason="Not entitled")
        other.refresh_from_db()
        self.assertEqual(other.status, MealExcessStatus.DECLINED)
        self.assertEqual(MealCollection.objects.filter(excess_exception=other, voided_at__isnull=False).count(), 1)
        self.assertTrue(AuditEvent.objects.filter(event_type="meals.excess_declined", actor=self.root).exists())

    def test_a_decided_excess_is_skipped(self):
        exception = self.excess()
        MealExcessException.objects.filter(pk=exception.pk).update(status=MealExcessStatus.DECLINED)
        _, response = run_admin_action(self.client, EXCESS, "accept_excess", [exception.pk])
        self.assertTrue(self.messages_contain(response, "already been decided"))

    # ---- authorisations
    def test_withdraw_an_extra_ticket_authorisation(self):
        authorization = authorise(employee=self.ada, quantity=2, pays="employee", actor=self.root, work_date=self.day)
        run_admin_action(self.client, "/admin/meals/mealextraauthorization/", "withdraw_authorisations", [authorization.pk])
        authorization.refresh_from_db()
        self.assertIsNotNone(authorization.cancelled_at)
        self.assertTrue(AuditEvent.objects.filter(event_type="meals.extra_authorisation_cancelled", actor=self.root).exists())
        self.assertTrue(AuditEvent.objects.filter(event_type="admin.meals.withdraw_authorisations").exists())

    # ---- terminal access
    @override_settings(MEAL_GATING_EMPLOYEE_IDS=["000684"])
    def test_recheck_terminal_access_queues_switches_for_managed_people_only(self):
        BiometricDevice.objects.create(name="Canteen", serial_number="MEAL001", purpose="meal_ticket")
        for number, person in ((10, self.ada), (11, self.bayo)):
            BiometricIdentity.objects.update_or_create(employee=person, system=IDENTITY_SYSTEM, source_identifier="MEAL001", defaults={"external_user_id": str(number)})
        today = timezone.localdate()
        MealTerminalUserState.objects.create(employee=self.ada, device_serial="MEAL001", enabled=True)
        EmployeeMealEntitlement.objects.filter(employee=self.ada).update(effective_from=today, tickets_per_work_day=1)
        # no roster for today: no ticket left, so the terminal should switch Ada off
        ids = [c.pk for c in [self.scan(self.ada, "g1"), self.scan(self.bayo, "g2")]]
        response = self.client.post(COLLECTIONS, {"action": "recheck_terminal_access", "_selected_action": ids, "index": 0}, follow=True)
        self.assertTrue(self.messages_contain(response, "Re-checked 2 people (1 managed by terminal gating)"))
        commands = DeviceCommand.objects.filter(command_type="set_user_enabled")
        self.assertTrue(commands.exists())
        self.assertEqual({c.payload["employee_id"] for c in commands}, {self.ada.pk})
        event = AuditEvent.objects.get(event_type="admin.meals.recheck_terminal_access")
        self.assertEqual((event.metadata["people"], event.metadata["managed"]), (2, 1))

    def test_recheck_says_so_when_nobody_is_managed(self):
        collection = self.scan(self.ada, "g1")
        response = self.client.post(COLLECTIONS, {"action": "recheck_terminal_access", "_selected_action": [collection.pk], "index": 0}, follow=True)
        self.assertTrue(self.messages_contain(response, "none of them is managed by terminal gating"))
        self.assertTrue(AuditEvent.objects.filter(event_type="admin.meals.recheck_terminal_access").exists())


class MealConfigAdminTests(TestCase):
    def setUp(self):
        self.root = superuser()
        self.client.force_login(self.root)
        self.ada = Employee.objects.create(employee_id="000684", first_name="Ada", last_name="Okafor")
        self.period = PayrollPeriod.objects.create(year=2026, month=9)

    def test_a_new_entitlement_records_who_set_it_and_edits_are_audited(self):
        response = self.client.post("/admin/meals/employeemealentitlement/add/", {"employee": self.ada.pk, "tickets_per_work_day": 2, "effective_from": "2026-09-01", "reason": "Promoted", "_save": "Save"})
        self.assertEqual(response.status_code, 302)
        entitlement = EmployeeMealEntitlement.objects.get()
        self.assertEqual(entitlement.set_by, self.root)
        self.assertTrue(AuditEvent.objects.filter(event_type="admin.employeemealentitlement.added", employee=self.ada).exists())
        self.client.post(f"/admin/meals/employeemealentitlement/{entitlement.pk}/change/", {"employee": self.ada.pk, "tickets_per_work_day": 3, "effective_from": "2026-09-01", "reason": "Promoted", "_save": "Save"})
        event = AuditEvent.objects.get(event_type="admin.employeemealentitlement.changed")
        self.assertEqual(event.metadata["before"], {"tickets_per_work_day": 2})
        self.assertEqual(event.metadata["after"], {"tickets_per_work_day": 3})

    def test_a_vendor_payment_can_be_corrected_with_an_audit_trail(self):
        payment = MealVendorPayment.objects.create(payroll_period=self.period, amount=Decimal("15000.00"), payment_date=date(2026, 9, 30))
        self.assertContains(self.client.get("/admin/meals/mealvendorpayment/"), "N 15,000.00")
        self.client.post(f"/admin/meals/mealvendorpayment/{payment.pk}/change/", {"payroll_period": self.period.pk, "amount": "1500.00", "payment_date": "2026-09-30", "reference": "", "notes": "typo", "_save": "Save"})
        payment.refresh_from_db()
        self.assertEqual(payment.amount, Decimal("1500.00"))
        event = AuditEvent.objects.get(event_type="admin.mealvendorpayment.changed")
        self.assertEqual((event.metadata["before"]["amount"], event.metadata["after"]["amount"]), ("15000.00", "1500.00"))

    def test_ticket_rates_cannot_be_deleted(self):
        rate = MealTicketRate.objects.create(amount=Decimal("700.00"), effective_from=date(2026, 9, 1))
        self.assertEqual(self.client.get(f"/admin/meals/mealticketrate/{rate.pk}/delete/").status_code, 403)
        self.assertContains(self.client.get("/admin/meals/mealticketrate/"), "N 700.00")

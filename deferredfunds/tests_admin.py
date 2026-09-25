from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase

from audit.admin_testing import admin_messages, run_admin_action, superuser
from audit.models import AuditEvent
from employees.models import Department, Employee

from .models import DeferredFundAccount, DeferredFundEntry, DeferredFundWithdrawal, EntryType, WithdrawalStatus
from .services import DeferredFundService

ACCOUNTS = "/admin/deferredfunds/deferredfundaccount/"
ENTRIES = "/admin/deferredfunds/deferredfundentry/"
WITHDRAWALS = "/admin/deferredfunds/deferredfundwithdrawal/"


class DeferredFundAdminTests(TestCase):
    def setUp(self):
        self.root = superuser()
        self.client.force_login(self.root)
        self.hr = get_user_model().objects.create_user("hr", password="pw")
        dept = Department.objects.create(name="Melting")
        self.staff = Employee.objects.create(employee_id="000684", first_name="Ada", last_name="Okafor", employment_type="contract", department=dept, basic_salary=Decimal("200000.00"))
        self.account = DeferredFundService.enrol(employee=self.staff, percent=Decimal("10"), opening_balance=Decimal("50000"), saving_since=date(2020, 1, 1), actor=self.hr)
        self.withdrawal = DeferredFundService.request_withdrawal(self.account, kind="partial", amount=Decimal("5000"), reason="School", actor=self.hr)

    def test_lists_show_who_amounts_and_search(self):
        for url in (ACCOUNTS, ENTRIES, WITHDRAWALS):
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, "000684")
            self.assertContains(response, "Ada Okafor")
            for term in ("000684", "Okafor", "Ada Okafor"):
                self.assertEqual(len(self.client.get(url, {"q": term}).context["cl"].result_list), 1, (url, term))
        self.assertContains(self.client.get(ACCOUNTS), "N 50,000.00")
        self.assertContains(self.client.get(ENTRIES), "+N 50,000.00")
        self.assertContains(self.client.get(WITHDRAWALS), "N 5,000.00")
        self.assertEqual(str(self.account), "000684 Ada Okafor - deferred fund (10%)")

    def test_ledger_and_accounts_are_read_only_and_undeletable(self):
        entry = DeferredFundEntry.objects.get(entry_type=EntryType.OPENING)
        for base, pk in ((ACCOUNTS, self.account.pk), (ENTRIES, entry.pk), (WITHDRAWALS, self.withdrawal.pk)):
            self.assertEqual(self.client.get(f"{base}{pk}/change/").status_code, 200)
            self.assertEqual(self.client.post(f"{base}{pk}/change/", {"note": "x", "_save": "Save"}).status_code, 403)
            self.assertEqual(self.client.get(f"{base}{pk}/delete/").status_code, 403)
            self.assertEqual(self.client.get(base + "add/").status_code, 403)
            self.assertNotContains(self.client.get(base), "delete_selected")
        self.assertEqual(DeferredFundAccount.objects.get().balance, Decimal("50000.00"))

    def test_approve_within_policy_is_allowed_but_outside_policy_needs_a_reason(self):
        DeferredFundWithdrawal.objects.filter(pk=self.withdrawal.pk).update(rule_exceptions="Under 18 months")
        _, response = run_admin_action(self.client, WITHDRAWALS, "approve_withdrawals", [self.withdrawal.pk], reason="")
        self.withdrawal.refresh_from_db()
        self.assertEqual(self.withdrawal.status, WithdrawalStatus.REQUESTED)
        self.assertTrue(any("outside policy" in text for text in admin_messages(response)))
        run_admin_action(self.client, WITHDRAWALS, "approve_withdrawals", [self.withdrawal.pk], reason="Medical emergency")
        self.withdrawal.refresh_from_db()
        self.assertEqual((self.withdrawal.status, self.withdrawal.decided_by), (WithdrawalStatus.APPROVED, self.root))
        self.assertTrue(AuditEvent.objects.filter(event_type="deferred_fund.withdrawal_approved", actor=self.root).exists())
        self.assertTrue(AuditEvent.objects.filter(event_type="admin.deferred_funds.approve_withdrawals").exists())

    def test_decline_and_cancel(self):
        run_admin_action(self.client, WITHDRAWALS, "decline_withdrawals", [self.withdrawal.pk], reason="Not eligible")
        self.withdrawal.refresh_from_db()
        self.assertEqual(self.withdrawal.status, WithdrawalStatus.DECLINED)
        other = DeferredFundService.request_withdrawal(self.account, kind="partial", amount=Decimal("1000"), actor=self.hr)
        run_admin_action(self.client, WITHDRAWALS, "cancel_withdrawals", [other.pk])
        other.refresh_from_db()
        self.assertEqual(other.status, WithdrawalStatus.CANCELLED)
        self.assertTrue(AuditEvent.objects.filter(event_type="deferred_fund.withdrawal_cancelled").exists())

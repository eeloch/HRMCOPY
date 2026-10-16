import tempfile
from datetime import date, datetime
from decimal import Decimal
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import CommandError, call_command
from django.test import TestCase
from openpyxl import Workbook

from employees.models import Employee

from .models import DeferredFundAccount, DeferredFundEntry
from .services import DeferredFundService

HEADER = ["EMPLOYEE NUMBER", "NAME", "GENDER", "Current Salary", "Fund contribution", "Status", datetime(2025, 2, 25), datetime(2025, 3, 25), "January 26", "Total"]


def workbook(people, paid):
    book = Workbook()
    record = book.active
    record.title = "Deferred Fund Historical Record"
    record.append(HEADER)
    for row in people:
        record.append(list(row))
    disbursement = book.create_sheet("Disbursement Record")
    disbursement.append(["EMPLOYEE NUMBER", "NAME", "Amount Disbursed"])
    for row in paid:
        disbursement.append(list(row))
    handle = tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False)
    book.save(handle.name)
    return handle.name


class ImportDeferredFundHistoryTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_user(username="admin", password="pw")
        make = lambda number, first, last, **kw: Employee.objects.create(employee_id=number, first_name=first, last_name=last, employment_type="contract", status="active", **kw)
        self.saver = make("000001", "Obi", "Chidimma")        # opening = what the history nets
        self.partial = make("000014", "Mathew", "Faith")       # has been paid part of it
        self.paid = make("000023", "Bigland", "Princess")      # paid out in full
        self.differs = make("000015", "Ishoma", "Stanley")     # opening does not match the history
        self.two_a = make("000283", "Eguzouwa", "Godfirst")    # the staff number is shared in the sheet by two people
        self.two_b = make("000284", "Ezebialu", "Chidinma")
        self.over = make("000321", "Onwuka", "Nkiruka")        # paid out more than was saved
        for employee, opening in ((self.saver, 150), (self.partial, 100), (self.differs, 999)):
            DeferredFundService.enrol(employee=employee, percent=15, opening_balance=opening, actor=self.admin)

    PEOPLE = [
        (1, "Obi Chidimma", "Female", 400000, 0.15, None, 50, 50, 50, 150),
        (14, "Mathew Faith", "Male", 170000, 0.15, "Partially Paid", 40, 40, 40, 120),
        (23, "Bigland Princess", "Female", None, 0.1, "Paid", 10, 10, None, 20),
        (15, "Ishoma Stanley", "Male", 150000, 0.1, "Partially Paid", None, 14, 15, 29),
        (283, "Eguzouwa Godfirst", "Male", None, 0.1, "Paid", 5, 5, 5, 15),
        (283, "Ezebialu Chidinma", "Female", None, 0.15, None, 3, None, None, 3),
        (321, "Onwuka Nkiruka", "Female", None, 0.1, "Paid", 10, 10, 10, 30),
    ]
    PAID = [(14, "Mathew Faith", 20), (23, "Bigland Princess", 20), (283, "Eguzouwa Godfirst", 15), (321, "Onwuka Nkiruka", 45), (15, "Ishoma Stanley", 5)]

    def run_command(self, *flags, people=None, paid=None):
        out = StringIO()
        call_command("import_deferred_fund_history", workbook(people or self.PEOPLE, self.PAID if paid is None else paid), *flags, stdout=out)
        return out.getvalue()

    # ---- the dry run -----------------------------------------------------------------------
    def test_a_dry_run_changes_nothing_and_says_what_it_would_do(self):
        text = self.run_command()
        self.assertIn("DRY RUN", text)
        self.assertIn("To load: 2", text)
        self.assertEqual(self.saver.deferred_fund.entries.count(), 1)

    def test_it_will_not_save_disbursements_without_a_date(self):
        with self.assertRaises(CommandError):
            self.run_command("--apply")
        self.assertEqual(self.partial.deferred_fund.entries.count(), 1)

    # ---- applying --------------------------------------------------------------------------
    def test_the_opening_line_becomes_monthly_lines_and_the_balance_is_unchanged(self):
        self.run_command("--apply", "--disbursement-date", "2026-09-30")
        account = DeferredFundAccount.objects.get(employee=self.saver)  # not self.saver.deferred_fund: setUp cached the unchanged copy
        self.assertEqual(account.balance, Decimal("150.00"))
        entries = list(account.entries.all())
        self.assertEqual([e.entry_type for e in entries], ["contribution"] * 3)
        self.assertEqual([e.entry_date for e in entries], [date(2025, 2, 25), date(2025, 3, 25), date(2026, 1, 26)])  # "January 26" is the 26th
        self.assertEqual(entries[0].note, "Feb 2025 contribution (history)")
        self.assertEqual(account.saving_since, date(2025, 2, 25))

    def test_what_was_paid_out_is_a_withdrawal_and_still_counts_towards_what_was_saved(self):
        self.run_command("--apply", "--disbursement-date", "2026-09-30")
        account = self.partial.deferred_fund
        self.assertEqual((account.balance, account.partial_withdrawn, account.accumulated), (Decimal("100.00"), Decimal("20.00"), Decimal("120.00")))
        withdrawal = account.entries.get(entry_type="withdrawal")
        self.assertEqual((withdrawal.amount, withdrawal.entry_date), (Decimal("-20.00"), date(2026, 9, 30)))

    def test_nobody_whose_figures_differ_is_changed(self):
        text = self.run_command("--apply", "--disbursement-date", "2026-09-30")
        account = self.differs.deferred_fund
        self.assertEqual((account.balance, account.entries.count()), (Decimal("999.00"), 1))
        self.assertIn("Ishoma Stanley - the opening balance is 999.00 but the history nets 24.00", text)

    def test_two_people_sharing_a_staff_number_are_kept_apart(self):
        text = self.run_command("--apply", "--disbursement-date", "2026-09-30", "--create-missing")
        self.assertEqual(self.two_a.deferred_fund.balance, Decimal("0.00"))   # 15 saved, 15 paid out
        self.assertEqual(self.two_b.deferred_fund.balance, Decimal("3.00"))   # the 15 paid out is not theirs
        self.assertNotIn("no matching person", text)

    def test_someone_paid_out_more_than_was_saved_is_left_for_a_person(self):
        text = self.run_command("--apply", "--disbursement-date", "2026-09-30", "--create-missing")
        self.assertFalse(DeferredFundAccount.objects.filter(employee=self.over).exists())
        self.assertIn("15.00 more than the history holds", text)

    def test_people_with_no_account_are_only_recorded_when_asked(self):
        text = self.run_command("--apply", "--disbursement-date", "2026-09-30")
        self.assertFalse(DeferredFundAccount.objects.filter(employee=self.paid).exists())
        self.assertIn("Add --create-missing", text)
        self.run_command("--apply", "--disbursement-date", "2026-09-30", "--create-missing")
        account = self.paid.deferred_fund
        self.assertEqual((account.active, account.balance, account.percent), (False, Decimal("0.00"), Decimal("10.00")))
        self.assertEqual(account.entries.get(entry_type="release").amount, Decimal("-20.00"))  # paid out in full

    def test_running_it_twice_does_not_duplicate(self):
        self.run_command("--apply", "--disbursement-date", "2026-09-30")
        first = DeferredFundEntry.objects.count()
        text = self.run_command("--apply", "--disbursement-date", "2026-09-30")
        self.assertEqual(DeferredFundEntry.objects.count(), first)
        self.assertIn("already loaded", text)

    def test_a_disbursement_with_no_one_on_the_history_sheet_is_reported(self):
        text = self.run_command(paid=[(99, "Nobody Known", 10)])
        self.assertIn("no matching person on the history sheet", text)

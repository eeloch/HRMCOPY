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


class ImportDeferredFundHistoryEdgeCaseTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_user(username="admin", password="pw")
        make = lambda number, first, last: Employee.objects.create(employee_id=number, first_name=first, last_name=last, employment_type="contract", status="active")
        self.twice = make("000877", "Ebubechukwu", "Elosiuba")   # on two rows of the sheet, under two staff numbers
        self.extra = make("000705", "Shedrack", "Bismarck")      # holds one month more than the history shows
        DeferredFundService.enrol(employee=self.twice, percent=15, opening_balance=160, actor=self.admin)
        DeferredFundService.enrol(employee=self.extra, percent=15, opening_balance=180, actor=self.admin)

    def run_command(self, people, *flags, paid=()):
        out = StringIO()
        call_command("import_deferred_fund_history", workbook(people, paid), *flags, stdout=out)
        return out.getvalue()

    def test_one_person_on_two_rows_is_combined_month_by_month(self):
        people = [(162, "Ebubechukwu Elosiuba", "Male", 0, 0.15, None, 50, 50, None, 100), (877, "Ebubechukwu Elosiuba", "Male", 0, 0.15, None, None, 10, 50, 60)]
        text = self.run_command(people, "--apply")
        self.assertIn("are the same person", text)
        account = DeferredFundAccount.objects.get(employee=self.twice)
        self.assertEqual(account.balance, Decimal("160.00"))
        self.assertEqual([(e.entry_date, e.amount) for e in account.entries.all()], [(date(2025, 2, 25), Decimal("50.00")), (date(2025, 3, 25), Decimal("60.00")), (date(2026, 1, 26), Decimal("50.00"))])

    def test_a_difference_is_left_alone_unless_it_is_asked_for_as_an_adjustment(self):
        people = [(705, "Shedrack Bismarck", "Male", 0, 0.15, None, 60, 60, 42, 162)]
        text = self.run_command(people, "--apply")
        self.assertIn("Left for a person to decide, or add --adjust-differences", text)
        self.assertEqual(DeferredFundAccount.objects.get(employee=self.extra).entries.get().entry_type, "opening")
        self.run_command(people, "--apply", "--adjust-differences")
        account = DeferredFundAccount.objects.get(employee=self.extra)
        self.assertEqual(account.balance, Decimal("180.00"))  # the balance did not move
        adjustment = account.entries.get(entry_type="adjustment")
        self.assertEqual(adjustment.amount, Decimal("18.00"))
        self.assertTrue(adjustment.note.endswith("(history)"))

    def test_someone_nobody_matches_is_reported_with_what_the_system_holds(self):
        text = self.run_command([(877, "Someone Else", "Male", 0, 0.15, None, 5, None, None, 5)], paid=[(877, "Someone Else", 5)])
        self.assertIn("staff no. 000877 is Ebubechukwu Elosiuba (active)", text)
        self.assertIn("5.00 paid out", text)
        self.assertNotIn("no matching person on the history sheet", text)  # reported once, against the person


class ImportDeferredFundHistoryFormerStaffTests(TestCase):
    """People on the sheet who are not in the system at all."""

    def setUp(self):
        self.admin = get_user_model().objects.create_user(username="admin", password="pw")
        self.taken = Employee.objects.create(employee_id="000036", first_name="Garba", last_name="Ibrahim", employment_type="contract", status="active")

    PEOPLE = [
        (23, "Bigland Princess", "Female", None, 0.1, "Paid", 10, 10, None, 20),
        (76, "Maduka Victor Kosisochukwu", "Male", None, 0.15, "Paid", 40, 40, 40, 120),
        (36, "Okonkwo Hope Uche", "Female", None, 0.15, None, 5, None, None, 5),   # the number belongs to somebody else
        (283, "Eguzouwa Godfirst", "Male", None, 0.1, None, 5, None, None, 5),     # one number, two different people
        (283, "Ezebialu Chidinma", "Female", None, 0.15, None, 3, None, None, 3),
    ]
    PAID = [(23, "Bigland Princess", 20)]

    def run_command(self, *flags):
        out = StringIO()
        call_command("import_deferred_fund_history", workbook(self.PEOPLE, self.PAID), *flags, stdout=out)
        return out.getvalue()

    def test_without_the_option_they_are_only_reported(self):
        text = self.run_command("--apply", "--disbursement-date", "2026-09-30")
        self.assertIn("no unique match", text)
        self.assertFalse(Employee.objects.filter(employee_id="000023").exists())

    def test_a_dry_run_adds_nobody(self):
        text = self.run_command("--create-former-staff")
        self.assertIn("will be ADDED as staff no. 000023", text)
        self.assertEqual(Employee.objects.count(), 1)

    def test_they_are_added_as_inactive_contract_staff_with_their_history(self):
        self.run_command("--create-former-staff", "--disbursement-date", "2026-09-30", "--apply")
        person = Employee.objects.get(employee_id="000076")
        self.assertEqual((person.status, person.employment_type, person.gender), ("inactive", "contract", "male"))
        self.assertEqual((person.first_name, person.middle_name, person.last_name), ("Maduka", "Victor", "Kosisochukwu"))
        account = DeferredFundAccount.objects.get(employee=person)
        self.assertEqual((account.active, account.percent, account.balance, account.saving_since), (False, Decimal("15.00"), Decimal("120.00"), date(2025, 2, 25)))
        paid = DeferredFundAccount.objects.get(employee__employee_id="000023")
        self.assertEqual((paid.balance, paid.entries.get(entry_type="release").amount), (Decimal("0.00"), Decimal("-20.00")))

    def test_a_number_that_is_taken_or_shared_is_held_back_until_a_number_is_given(self):
        text = self.run_command("--create-former-staff", "--disbursement-date", "2026-09-30", "--apply")
        self.assertIn("staff no. 000036 already belongs to Garba Ibrahim", text)
        self.assertIn("is used by 2 different people on the sheet", text)
        self.assertEqual(Employee.objects.count(), 3)  # Garba Ibrahim + the two that could be added
        self.run_command("--create-former-staff", "--disbursement-date", "2026-09-30", "--apply", "--assign", "4=001300", "--assign", "5=001301", "--assign", "6=001302")
        added = {e.employee_id: e.full_name for e in Employee.objects.filter(employee_id__in=["001300", "001301", "001302"])}
        self.assertEqual(added, {"001300": "Okonkwo Hope Uche", "001301": "Eguzouwa Godfirst", "001302": "Ezebialu Chidinma"})
        self.assertEqual(DeferredFundAccount.objects.get(employee__employee_id="001302").balance, Decimal("3.00"))  # not the 5 of the other 283

    def test_running_it_twice_adds_nobody_again(self):
        self.run_command("--create-former-staff", "--disbursement-date", "2026-09-30", "--apply")
        count = Employee.objects.count()
        self.run_command("--create-former-staff", "--disbursement-date", "2026-09-30", "--apply")
        self.assertEqual(Employee.objects.count(), count)

    def test_a_badly_formed_assign_is_refused(self):
        with self.assertRaises(CommandError):
            self.run_command("--create-former-staff", "--assign", "twelve")

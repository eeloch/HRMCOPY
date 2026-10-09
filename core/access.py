"""Who may open what - one table, enforced on every API request.

Signing in is not permission to read everything. Each part of the system opens only for people holding one of the
permissions that work in it; a person who only issues PPE sees PPE and nothing else. This table adds to the checks
each view already makes (it can only refuse, never allow more) and also covers any endpoint added later, because it
matches on the URL.

The same table is what the menu follows (frontend/src/lib/access.ts keeps the matching list for the sidebar).
Superusers pass everything. `/api/employees/` is special: people who may not see the staff directory but do need to
choose an employee (issuing PPE, raising leave...) get a short name-and-number lookup instead, see EmployeeListAPI."""

import re

# the "app.codename" of each permission, grouped by the work it belongs to
DIRECTORY = ("employees.view_employee", "employees.add_employee", "employees.change_employee")
ACCOMMODATION = ("accommodation.view_accommodation", "accommodation.manage_accommodation")
EMPLOYEES_PAGE = DIRECTORY + ACCOMMODATION + ("employees.view_employee_statement",)
ATTENDANCE = DIRECTORY + (
    "attendance.review_attendanceexception", "attendance.manage_shifts", "attendance.manage_roster", "attendance.review_overtime",
    "attendance.view_biometrics_overview", "attendance.manage_devices", "attendance.enroll_biometric_users", "attendance.reverse_attendance_charge",
)
LEAVE = DIRECTORY + ("leave.raise_leave_request", "leave.approve_leave", "leave.manage_leave_policy", "payroll.view_payroll", "payroll.manage_payroll")
PPE = ("ppe.record_ppe_issue", "ppe.review_ppe_deduction", "payroll.manage_payroll")
MANAGEMENT = DIRECTORY + (
    "payroll.view_payroll", "payroll.manage_payroll", "attendance.review_attendanceexception", "attendance.manage_shifts", "attendance.manage_roster",
)
# anyone who has to pick an employee for their own work (they get the short lookup, not the directory)
PICK_AN_EMPLOYEE = EMPLOYEES_PAGE + (
    "ppe.record_ppe_issue", "ppe.review_ppe_deduction", "advances.record_salary_advance", "advances.approve_salary_advance",
    "leave.raise_leave_request", "offences.record_employee_offences", "offences.review_employee_offences", "bonuses.record_bonus",
    "deferredfunds.manage_deferred_funds", "meals.record_meal_operations", "meals.review_meal_excess", "meals.manage_meal_configuration",
    "meals.charge_back_meal_tickets", "attendance.manage_shifts", "attendance.manage_roster", "attendance.enroll_biometric_users",
    "documents.manage_documents", "payroll.view_payroll", "payroll.manage_payroll",
    "attendance.review_attendanceexception", "attendance.review_overtime", "attendance.manage_devices", "attendance.view_biometrics_overview",
    "attendance.reverse_attendance_charge", "leave.approve_leave", "leave.manage_leave_policy", "advances.pay_salary_advance",
    "deferredfunds.view_deferred_funds", "deferredfunds.approve_deferred_withdrawal", "deferredfunds.pay_deferred_withdrawal",
    "bonuses.view_bonuses", "bonuses.approve_bonus", "documents.view_documents", "meals.view_meal_vendor_payments", "meals.record_meal_vendor_payments",
    "offences.manage_offence_configuration",
)

READ = ("GET", "HEAD")
# (methods, URL pattern, permissions any one of which opens it)
RULES = [
    (READ, r"^/api/employees/$", PICK_AN_EMPLOYEE),
    (READ, r"^/api/employees/(summary|hires-exits)/$", EMPLOYEES_PAGE),
    (READ, r"^/api/employees/accommodation-report/$", ACCOMMODATION),
    (READ, r"^/api/employees/\d+/(profile/)?$", EMPLOYEES_PAGE),
    (("POST",), r"^/api/employees/import/(preview/|organization/)$", ("employees.add_employee", "employees.change_employee")),
    (READ, r"^/api/attendance/(dashboard|today|records|roster|shifts|shift-plans|shift-assignments|overtime|exceptions|biometric-events|devices)/", ATTENDANCE),
    (READ, r"^/api/leave/(balance|pending|policies|requests|types)/", LEAVE),
    (READ, r"^/api/(audit/activity|reports/weekly)/", MANAGEMENT),
    (READ, r"^/api/ppe/", PPE),
]
_COMPILED = [(methods, re.compile(pattern), perms) for methods, pattern, perms in RULES]


def holds_any(user, permissions):
    return bool(user and user.is_authenticated and (user.is_superuser or any(user.has_perm(p) for p in permissions)))


def required_permissions(method, path):
    """The permissions that open this request, or None when this table has nothing to say about it."""
    for methods, pattern, perms in _COMPILED:
        if method in methods and pattern.match(path):
            return perms
    return None


def can_see_staff_directory(user):
    return holds_any(user, DIRECTORY)


def install():
    """Apply the table to every API view (called once when the app starts)."""
    from rest_framework.views import APIView

    if getattr(APIView, "_module_access_installed", False):
        return
    original = APIView.check_permissions

    def check_permissions(self, request):
        original(self, request)
        needed = required_permissions(request.method, request.path)
        if needed is not None and not holds_any(request.user, needed):
            self.permission_denied(request, message="You do not have access to this part of the system.")

    APIView.check_permissions = check_permissions
    APIView._module_access_installed = True

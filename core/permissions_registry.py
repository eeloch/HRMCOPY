"""Single source of truth for the custom permissions a user account can be granted.

Every entry here corresponds to a real `Meta.permissions` codename checked
somewhere in the app (grep for `has_perm(` to verify). This list is what
powers the Settings > Users & Permissions screen - add an entry here
whenever a new gated capability is introduced, so it becomes assignable
without a code change anywhere else.
"""

MANAGED_PERMISSIONS = [
    {"codename": "view_salary", "app_label": "employees", "label": "View & set employee salary", "group": "Employees"},
    {"codename": "view_bank_details", "app_label": "employees", "label": "View & set employee bank details", "group": "Employees"},
    {"codename": "manage_devices", "app_label": "attendance", "label": "Manage biometric devices", "group": "Attendance"},
    {"codename": "manage_shifts", "app_label": "attendance", "label": "Assign & change shifts", "group": "Attendance"},
    {"codename": "manage_roster", "app_label": "attendance", "label": "Manage work rosters", "group": "Attendance"},
    {"codename": "review_attendanceexception", "app_label": "attendance", "label": "Review attendance exceptions", "group": "Attendance"},
    {"codename": "review_overtime", "app_label": "attendance", "label": "Review overtime records", "group": "Attendance"},
    {"codename": "approve_leave", "app_label": "leave", "label": "Approve leave requests", "group": "Leave"},
    {"codename": "view_payroll", "app_label": "payroll", "label": "View payroll", "group": "Payroll"},
    {"codename": "manage_payroll", "app_label": "payroll", "label": "Manage payroll periods & records", "group": "Payroll"},
    {"codename": "record_meal_operations", "app_label": "meals", "label": "Record meal operations", "group": "Meals"},
    {"codename": "review_meal_excess", "app_label": "meals", "label": "Review meal excess deductions", "group": "Meals"},
    {"codename": "manage_meal_configuration", "app_label": "meals", "label": "Manage meal configuration", "group": "Meals"},
    {"codename": "record_employee_offences", "app_label": "offences", "label": "Record employee offences", "group": "Offences"},
    {"codename": "review_employee_offences", "app_label": "offences", "label": "Review and approve employee offences", "group": "Offences"},
    {"codename": "manage_offence_configuration", "app_label": "offences", "label": "Manage offence types", "group": "Offences"},
    {"codename": "record_ppe_issue", "app_label": "ppe", "label": "Record PPE issues", "group": "PPE"},
    {"codename": "review_ppe_deduction", "app_label": "ppe", "label": "Review PPE deductions", "group": "PPE"},
    {"codename": "view_documents", "app_label": "documents", "label": "View employee documents", "group": "Documents"},
    {"codename": "manage_documents", "app_label": "documents", "label": "Upload & delete employee documents", "group": "Documents"},
]

MANAGED_PERMISSION_CODENAMES = {entry["codename"] for entry in MANAGED_PERMISSIONS}


def full_codename(codename):
    entry = next((item for item in MANAGED_PERMISSIONS if item["codename"] == codename), None)
    if entry is None:
        return None
    return f"{entry['app_label']}.{codename}"


def user_permission_map(user):
    """{codename: bool} for every managed permission, for this user."""
    return {
        entry["codename"]: user.has_perm(f"{entry['app_label']}.{entry['codename']}")
        for entry in MANAGED_PERMISSIONS
    }

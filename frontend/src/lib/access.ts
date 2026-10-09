import type { CurrentUser } from "@/lib/api";

/**
 * Which parts of the system open for whom - the menu, and the page a person lands on, follow this.
 * It mirrors the server's table (core/access.py), which is what actually enforces it: hiding a tab is only a courtesy,
 * the server refuses the data. A person who only issues PPE sees the PPE tab and nothing else.
 */
type Perms = Record<string, boolean>;

const DIRECTORY = ["view_employee", "add_employee", "change_employee"];
const MANAGEMENT = [...DIRECTORY, "view_payroll", "manage_payroll", "review_attendanceexception", "manage_shifts", "manage_roster"];

/** URL prefix -> permissions, any one of which opens it. "everyone" / "superuser" are the two special cases. */
export const ACCESS: Array<[string, string[] | "everyone" | "superuser"]> = [
  ["/dashboard", MANAGEMENT],
  ["/employees", [...DIRECTORY, "view_accommodation", "manage_accommodation", "view_employee_statement"]],
  ["/attendance/exceptions", ["review_attendanceexception", "reverse_attendance_charge"]],
  ["/attendance/shifts", [...DIRECTORY, "manage_shifts", "manage_roster"]],
  ["/attendance/roster", [...DIRECTORY, "manage_shifts", "manage_roster"]],
  ["/attendance/overtime", ["review_overtime"]],
  ["/attendance/biometric", ["view_biometrics_overview", "manage_devices", "enroll_biometric_users"]],
  ["/attendance", [...DIRECTORY, "review_attendanceexception", "manage_shifts", "manage_roster", "review_overtime", "view_biometrics_overview", "manage_devices", "enroll_biometric_users", "reverse_attendance_charge"]],
  ["/devices", ["manage_devices", "enroll_biometric_users", "view_biometrics_overview"]],
  ["/biometrics", ["manage_devices", "enroll_biometric_users", "view_biometrics_overview"]],
  ["/leave", [...DIRECTORY, "raise_leave_request", "approve_leave", "manage_leave_policy", "view_payroll", "manage_payroll"]],
  ["/payslips", ["view_payroll", "manage_payroll"]],
  ["/deferred-funds", ["view_deferred_funds", "manage_deferred_funds", "approve_deferred_withdrawal", "pay_deferred_withdrawal"]],
  ["/bonuses", ["view_bonuses", "record_bonus", "approve_bonus"]],
  ["/payroll", ["view_payroll", "manage_payroll"]],
  ["/ppe", ["record_ppe_issue", "review_ppe_deduction"]],
  ["/advances", ["record_salary_advance", "approve_salary_advance", "pay_salary_advance"]],
  ["/meals", ["record_meal_operations", "review_meal_excess", "manage_meal_configuration", "view_meal_vendor_payments", "record_meal_vendor_payments", "charge_back_meal_tickets"]],
  ["/offences", ["record_employee_offences", "review_employee_offences", "manage_offence_configuration"]],
  ["/accommodation", ["view_accommodation", "manage_accommodation"]],
  ["/reports", MANAGEMENT],
  ["/activity", MANAGEMENT],
  ["/settings", "superuser"],
  ["/notifications", "everyone"],
];

/** The order a person is sent to the first page they may open, when their usual landing page is not one of them. */
const LANDING_ORDER = ["/dashboard", "/employees", "/attendance", "/leave", "/payroll", "/ppe", "/advances", "/meals", "/offences", "/biometrics", "/deferred-funds", "/bonuses", "/accommodation", "/reports", "/notifications"];

export function canOpen(user: CurrentUser, href: string): boolean {
  if (user.is_superuser) return true;
  const rule = ACCESS.find(([prefix]) => href === prefix || href.startsWith(`${prefix}/`));
  if (!rule) return true; // a page with no rule (change password...) is open to a signed-in person
  const [, needed] = rule;
  if (needed === "everyone") return true;
  if (needed === "superuser") return false;
  const perms = user.permissions as unknown as Perms;
  return needed.some((codename) => perms[codename] === true);
}

export function homeFor(user: CurrentUser): string {
  return LANDING_ORDER.find((href) => canOpen(user, href)) ?? "/notifications";
}

let cached: Promise<CurrentUser> | null = null;
/** The signed-in user, fetched once per page load and shared (the menu and page guards all ask for it). */
export function loadUser(fetchUser: () => Promise<CurrentUser>): Promise<CurrentUser> {
  if (!cached) {
    cached = fetchUser().catch((error) => { cached = null; throw error; });
  }
  return cached;
}
export function forgetUser() { cached = null; }

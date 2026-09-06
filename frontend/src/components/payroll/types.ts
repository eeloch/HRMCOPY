export type PayrollPeriod = {
  id: number;
  year: number;
  month: number;
  display_name: string;
  status: string;
  notes: string;
  employee_count: number;
  total_basic_salary: string;
  total_gross_earnings: string;
  total_deductions: string;
  total_net_pay: string;
};

export type PayrollLineItem = {
  id: number;
  item_type: "earning" | "deduction";
  code: string;
  description: string;
  amount: string;
  source_type: string;
  source_reference: string;
  metadata: Record<string, string | number>;
  is_system_generated: boolean;
  created_at: string;
};

export type EmployeePayroll = {
  id: number;
  employee: number;
  employee_id: string;
  employee_name: string;
  department_name: string | null;
  basic_salary: string;
  gross_earnings: string;
  total_deductions: string;
  net_pay: string;
  status: string;
  line_items?: PayrollLineItem[];
};

export type PayrollAttendanceSummary = {
  created: number;
  existing: number;
  updated: number;
  skipped: number;
  pending: number;
  held: number;
  approved_absence: number;
  approved_lateness: number;
  approved_early_departure: number;
  approved_missing_punch: number;
  unpaid_leave: number;
  paid_leave: number;
  unsupported: number;
  roster_required: boolean;
  employees_processed: number;
  employees_skipped_incomplete_roster: number;
  absence_deductions_created: number;
  absence_deductions_existing: number;
  lateness_deductions_created: number;
  lateness_deductions_existing: number;
  early_departure_deductions_created: number;
  early_departure_deductions_existing: number;
  unpaid_leave_deductions_created: number;
  unpaid_leave_deductions_existing: number;
  paid_leave_days_ignored: string;
  rest_days_ignored: number;
  incomplete_roster_employees: { id: number; employee_id: string; name: string; missing_dates: string[] }[];
};

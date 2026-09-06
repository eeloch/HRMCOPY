export type LeaveType = {
  id: number;
  name: string;
  code: string;
  description: string;
  default_days: string;
  requires_approval: boolean;
  is_paid: boolean;
  color: string;
};

export type LeaveRequest = {
  id: number;
  request_number: string;
  employee: number;
  employee_id: string;
  employee_name: string;
  department_name?: string | null;
  leave_type: number;
  leave_type_name: string;
  start_date: string;
  end_date: string;
  return_date: string | null;
  duration_type: string;
  duration_type_display: string;
  total_days: string;
  reason: string;
  status: string;
  status_display: string;
  approved_by: number | null;
  approved_by_name: string | null;
  approved_at: string | null;
  approved_start_date: string | null;
  approved_end_date: string | null;
  approved_days: string | null;
  rejection_reason: string;
  created_at: string;
};

export type ListResponse<T> = {
  count: number;
  results: T[];
};

export type LeaveBalance = {
  leave_type: number;
  leave_type_name: string;
  allocated_days: string;
  used_days: string;
  remaining_days: string;
  matched_policy: number | null;
};

export type BalanceResponse = {
  employee_id: number;
  employee_number: string;
  count: number;
  results: LeaveBalance[];
};

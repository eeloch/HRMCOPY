export type Shift = {
  id: number;
  name: string;
  start_time: string;
  end_time: string;
  is_overnight: boolean;
  active: boolean;
};

export type ShiftAssignment = {
  id: number;
  employee: number;
  employee_name: string;
  employee_number: string;
  department_name: string | null;
  shift: number;
  shift_name: string;
  shift_start_time: string;
  shift_end_time: string;
  is_overnight: boolean;
  start_date: string;
  end_date: string | null;
  assigned_by: string;
  created_at: string;
  status: "active" | "upcoming" | "ended";
};

export type ShiftEmployee = {
  id: number;
  employee_id: string;
  full_name: string;
  department_name: string | null;
};

export type EmployeeRosterDay = {
  id: number;
  employee: number;
  employee_name: string;
  employee_number: string;
  date: string;
  status: "work" | "rest";
  shift: number | null;
  shift_name: string | null;
  source: "generated" | "manual" | "override";
  notes: string;
  updated_by_name: string | null;
};

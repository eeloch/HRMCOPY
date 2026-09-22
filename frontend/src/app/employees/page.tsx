"use client";

import type { FormEvent } from "react";
import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";

import Sidebar from "@/components/Sidebar";
import { AppCard, MetricCard, PageHeader, Section, StatusBadge } from "@/components/ui";
import { AccommodationTracker } from "@/components/accommodation/AccommodationTracker";
import { apiFetch, getAccessToken, getCurrentUser, type CurrentUser } from "@/lib/api";

type ShiftPlanInfo = { id: number; name: string; kind: string; group: string } | null;

type Employee = {
  id: number;
  employee_id: string;
  biometric_user_id: string | null;
  full_name: string;
  department_name: string | null;
  position_name: string | null;
  employment_type: string;
  gender: string;
  phone: string;
  // Absent (not just blank) when the current user lacks permission to view salary.
  basic_salary?: string;
  status: string;
  current_shift: {
    id: number;
    name: string;
    start_time: string;
    end_time: string;
  } | null;
  shift_plan: ShiftPlanInfo;
  attention_reasons: string[];
};

type Summary = {
  total: number;
  by_status: Record<string, number>;
  on_leave_today: number;
  new_hires_this_month: number;
  exits_this_month: number;
  gender: { male: number; female: number; unspecified: number };
  by_department: { id: number | null; name: string; count: number }[];
  by_employment_type: { value: string; label: string; count: number }[];
  by_shift_plan: { id: number; name: string; kind: string; count: number }[];
  not_assigned_shift_plan: number;
  missing_bank_details: number;
  missing_biometric: number;
  needs_attention: number;
};

type HireExitEntry = { id: number; employee_id: string; name: string; date: string; department: string };
type HiresExitsData = { week_start: string; week_end: string; hires: HireExitEntry[]; exits: HireExitEntry[] };
type AccommodationReport = { categories: string[]; housing_types: string[]; counts: Record<string, Record<string, number>>; totals: Record<string, number> };

const categoryLabels: Record<string, string> = { casual: "Casual", expatriate: "Expatriate", administrative: "Administrative", other: "Other" };
const housingLabels: Record<string, string> = { in_house: "In-House (Hostel)", external: "External (Rental)" };

const filterSelectClass = "rounded-xl border border-slate-300 bg-white px-3 py-2.5 text-sm text-slate-700 outline-none focus:ring-2 focus:ring-blue-500";

type SortKey = "full_name" | "department_name" | "employment_type" | "basic_salary" | "status";

function mondayOf(date: Date) {
  const day = date.getDay();
  const diff = (day === 0 ? -6 : 1) - day;
  const monday = new Date(date);
  monday.setDate(date.getDate() + diff);
  return monday.toISOString().slice(0, 10);
}

function initials(name: string) {
  const parts = name.trim().split(/\s+/);
  return ((parts[0]?.[0] || "") + (parts[1]?.[0] || "")).toUpperCase();
}

function csvCell(value: string | number) {
  const text = String(value ?? "");
  return /[",\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
}

function shiftPlanCell(employee: Employee): { label: string; sub: string; missing: boolean } {
  const plan = employee.shift_plan;
  if (!plan) return { label: "Not assigned", sub: "", missing: true };
  if (plan.kind === "rotation") {
    return { label: plan.name, sub: `Group ${plan.group}${employee.current_shift ? ` · ${employee.current_shift.name} today` : ""}`, missing: false };
  }
  return { label: plan.name, sub: employee.current_shift ? `${employee.current_shift.name}, ${employee.current_shift.start_time.slice(0, 5)}-${employee.current_shift.end_time.slice(0, 5)}` : "", missing: false };
}

export default function EmployeesPage() {
  const router = useRouter();
  const [tab, setTab] = useState<"directory" | "hires-exits" | "accommodation">("directory");
  const [employees, setEmployees] = useState<Employee[]>([]);
  const [summary, setSummary] = useState<Summary | null>(null);
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState("active");
  const [departmentFilter, setDepartmentFilter] = useState("");
  const [employmentTypeFilter, setEmploymentTypeFilter] = useState("");
  const [genderFilter, setGenderFilter] = useState("");
  const [shiftPlanFilter, setShiftPlanFilter] = useState("");
  const [needsAttentionOnly, setNeedsAttentionOnly] = useState(false);
  const [newHiresOnly, setNewHiresOnly] = useState(false);
  const [exitsOnly, setExitsOnly] = useState(false);
  const [sort, setSort] = useState<{ key: SortKey; dir: "asc" | "desc" }>({ key: "full_name", dir: "asc" });
  const [loading, setLoading] = useState(true);
  const [weekStart, setWeekStart] = useState(() => mondayOf(new Date()));
  const [hiresExits, setHiresExits] = useState<HiresExitsData | null>(null);
  const [loadingHiresExits, setLoadingHiresExits] = useState(false);
  const [accommodation, setAccommodation] = useState<AccommodationReport | null>(null);
  const [loadingAccommodation, setLoadingAccommodation] = useState(false);
  const [currentUser, setCurrentUser] = useState<CurrentUser | null>(null);

  useEffect(() => {
    if (!getAccessToken()) {
      router.push("/login");
      return;
    }

    void loadEmployees();
    void loadSummary();
    getCurrentUser().then(setCurrentUser).catch(() => {});
    const timer = window.setTimeout(() => {
      if (new URLSearchParams(window.location.search).get("tab") === "accommodation") setTab("accommodation");
    }, 0);
    return () => window.clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [router]);

  useEffect(() => {
    if (tab === "hires-exits") void loadHiresExits(weekStart);
    if (tab === "accommodation" && !accommodation) void loadAccommodation();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab, weekStart]);

  // Dropdown/toggle filters apply immediately; the search box still needs Enter/Search (it can hit many
  // fields at once and a full workforce list is large enough that typing shouldn't refetch every keystroke).
  useEffect(() => {
    void loadEmployees(search);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [statusFilter, departmentFilter, employmentTypeFilter, genderFilter, shiftPlanFilter, needsAttentionOnly, newHiresOnly, exitsOnly]);

  async function loadHiresExits(week: string) {
    setLoadingHiresExits(true);
    try {
      const response = await apiFetch(`/employees/hires-exits/?week_start=${week}`);
      if (!response.ok) throw new Error("Unable to load hires/exits.");
      setHiresExits(await response.json());
    } catch (error) {
      console.error(error);
    } finally {
      setLoadingHiresExits(false);
    }
  }

  async function loadAccommodation() {
    setLoadingAccommodation(true);
    try {
      const response = await apiFetch("/employees/accommodation-report/");
      if (!response.ok) throw new Error("Unable to load the accommodation report.");
      setAccommodation(await response.json());
    } catch (error) {
      console.error(error);
    } finally {
      setLoadingAccommodation(false);
    }
  }

  async function loadSummary() {
    try {
      const response = await apiFetch("/employees/summary/");
      if (!response.ok) throw new Error("Unable to load the workforce summary.");
      setSummary(await response.json());
    } catch (error) {
      console.error(error);
    }
  }

  function shiftWeek(days: number) {
    const next = new Date(`${weekStart}T00:00:00`);
    next.setDate(next.getDate() + days);
    setWeekStart(mondayOf(next));
  }

  async function loadEmployees(searchValue = "") {
    setLoading(true);

    try {
      const params = new URLSearchParams();
      if (searchValue) params.set("search", searchValue);
      if (statusFilter) params.set("status", statusFilter);
      if (departmentFilter) params.set("department", departmentFilter);
      if (employmentTypeFilter) params.set("employment_type", employmentTypeFilter);
      if (genderFilter) params.set("gender", genderFilter);
      if (shiftPlanFilter) params.set("shift_plan", shiftPlanFilter);
      if (needsAttentionOnly) params.set("needs_attention", "1");
      if (newHiresOnly) params.set("new_hires_this_month", "1");
      if (exitsOnly) params.set("exits_this_month", "1");

      const suffix = params.toString() ? `?${params.toString()}` : "";
      const response = await apiFetch(`/employees/${suffix}`);

      if (!response.ok) {
        throw new Error(`Unable to load employees (${response.status})`);
      }

      const data = await response.json();
      setEmployees(data.results || []);
    } catch (error) {
      console.error(error);
    } finally {
      setLoading(false);
    }
  }

  function submitSearch(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void loadEmployees(search);
  }

  function clearFilters() {
    setSearch("");
    setStatusFilter("");
    setDepartmentFilter("");
    setEmploymentTypeFilter("");
    setGenderFilter("");
    setShiftPlanFilter("");
    setNeedsAttentionOnly(false);
    setNewHiresOnly(false);
    setExitsOnly(false);
  }

  // The stat cards are quick, mutually-exclusive shortcuts: each one shows you exactly who makes up that
  // number, so clicking resets the others rather than combining with whatever was already selected.
  function showStatus(status: string) {
    setNewHiresOnly(false);
    setExitsOnly(false);
    setNeedsAttentionOnly(false);
    setStatusFilter(status);
  }

  function showNewHires() {
    setStatusFilter("");
    setExitsOnly(false);
    setNeedsAttentionOnly(false);
    setNewHiresOnly(true);
  }

  function showExits() {
    setStatusFilter("");
    setNewHiresOnly(false);
    setNeedsAttentionOnly(false);
    setExitsOnly(true);
  }

  function showNeedsAttention() {
    setStatusFilter("active");
    setNewHiresOnly(false);
    setExitsOnly(false);
    setNeedsAttentionOnly(true);
  }

  function toggleSort(key: SortKey) {
    setSort((previous) => (previous.key === key ? { key, dir: previous.dir === "asc" ? "desc" : "asc" } : { key, dir: "asc" }));
  }

  const sortedEmployees = useMemo(() => {
    const factor = sort.dir === "asc" ? 1 : -1;
    const value = (employee: Employee): string | number => {
      if (sort.key === "basic_salary") return Number(employee.basic_salary || 0);
      if (sort.key === "department_name") return employee.department_name || "";
      return (employee[sort.key] as string) || "";
    };
    return [...employees].sort((a, b) => {
      const av = value(a);
      const bv = value(b);
      if (av < bv) return -1 * factor;
      if (av > bv) return 1 * factor;
      return 0;
    });
  }, [employees, sort]);

  const activeFilterCount = [statusFilter && statusFilter !== "active", departmentFilter, employmentTypeFilter, genderFilter, shiftPlanFilter, needsAttentionOnly, newHiresOnly, exitsOnly].filter(Boolean).length;

  function exportCsv() {
    const headers = ["Staff Number", "Name", "Department", "Position", "Employment Type", "Gender", "Biometric ID", "Shift Plan", "Group", "Today's Shift", "Salary", "Status", "Needs Attention"];
    const rows = sortedEmployees.map((employee) => {
      const plan = shiftPlanCell(employee);
      return [
        employee.employee_id,
        employee.full_name,
        employee.department_name || "",
        employee.position_name || "",
        formatEmploymentType(employee.employment_type),
        employee.gender ? employee.gender.charAt(0).toUpperCase() + employee.gender.slice(1) : "",
        employee.biometric_user_id || "",
        plan.label,
        employee.shift_plan?.group || "",
        employee.current_shift?.name || "",
        employee.basic_salary === undefined ? "Restricted" : employee.basic_salary,
        employee.status,
        employee.attention_reasons.join("; "),
      ];
    });
    const csv = [headers, ...rows].map((row) => row.map(csvCell).join(",")).join("\n");
    const blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `Employee Directory - ${new Date().toISOString().slice(0, 10)}.csv`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
  }

  return (
    <div className="min-h-screen bg-slate-100">
      <Sidebar />

      <main className="ml-64 p-8">
        <PageHeader
          title="Employees"
          description="Employee records, biometric IDs and work assignments"
          actions={
            <>
              <button
                type="button"
                onClick={() => router.push("/employees/import")}
                className="rounded-xl border border-slate-300 bg-white px-4 py-3 font-medium text-slate-700 hover:bg-slate-50"
              >
                Bulk Import
              </button>
              {currentUser?.permissions.view_salary && (
                <button
                  type="button"
                  onClick={() => router.push("/employees/salary-import")}
                  className="rounded-xl border border-slate-300 bg-white px-4 py-3 font-medium text-slate-700 hover:bg-slate-50"
                >
                  Import Salaries
                </button>
              )}
              <button
                type="button"
                onClick={() => router.push("/employees/new")}
                className="rounded-xl bg-blue-600 px-5 py-3 font-semibold text-white hover:bg-blue-700"
              >
                + Add Employee
              </button>
            </>
          }
        />

        <div className="mb-6 flex gap-2 border-b border-slate-200">
          {([["directory", "Directory"], ["hires-exits", "New Hires & Exits"], ["accommodation", "Accommodation"]] as const).map(([value, label]) => (
            <button
              key={value}
              type="button"
              onClick={() => setTab(value)}
              className={`px-4 py-3 text-sm font-semibold ${tab === value ? "border-b-2 border-blue-600 text-blue-700" : "text-slate-500 hover:text-slate-700"}`}
            >
              {label}
            </button>
          ))}
        </div>

        {tab === "hires-exits" && (
          <Section title="New Hires & Exits" subtitle={hiresExits ? `Week of ${hiresExits.week_start} to ${hiresExits.week_end}` : "Loading..."}>
            <div className="flex items-center justify-between gap-3 border-b border-slate-200 p-4">
              <button type="button" onClick={() => shiftWeek(-7)} className="rounded-xl border border-slate-300 px-4 py-2 text-sm font-semibold text-slate-700">Previous Week</button>
              <button type="button" onClick={() => setWeekStart(mondayOf(new Date()))} className="text-sm font-semibold text-blue-700">This Week</button>
              <button type="button" onClick={() => shiftWeek(7)} className="rounded-xl border border-slate-300 px-4 py-2 text-sm font-semibold text-slate-700">Next Week</button>
            </div>
            {loadingHiresExits || !hiresExits ? (
              <div className="p-10">Loading...</div>
            ) : (
              <div className="grid gap-6 p-5 md:grid-cols-2">
                <div>
                  <h3 className="mb-3 font-bold text-slate-900">New Hires ({hiresExits.hires.length})</h3>
                  {hiresExits.hires.length ? (
                    <div className="divide-y divide-slate-100 rounded-xl border border-slate-200">
                      {hiresExits.hires.map((entry) => (
                        <div key={entry.id} className="cursor-pointer p-4 hover:bg-slate-50" onClick={() => router.push(`/employees/${entry.id}`)}>
                          <p className="font-semibold text-slate-900">{entry.name}</p>
                          <p className="text-sm text-slate-500">{entry.employee_id} · {entry.department || "No department"} · {entry.date}</p>
                        </div>
                      ))}
                    </div>
                  ) : <p className="p-6 text-center text-sm text-slate-500">No new hires this week.</p>}
                </div>
                <div>
                  <h3 className="mb-3 font-bold text-slate-900">Exits ({hiresExits.exits.length})</h3>
                  {hiresExits.exits.length ? (
                    <div className="divide-y divide-slate-100 rounded-xl border border-slate-200">
                      {hiresExits.exits.map((entry) => (
                        <div key={entry.id} className="cursor-pointer p-4 hover:bg-slate-50" onClick={() => router.push(`/employees/${entry.id}`)}>
                          <p className="font-semibold text-slate-900">{entry.name}</p>
                          <p className="text-sm text-slate-500">{entry.employee_id} · {entry.department || "No department"} · {entry.date}</p>
                        </div>
                      ))}
                    </div>
                  ) : <p className="p-6 text-center text-sm text-slate-500">No exits this week.</p>}
                </div>
              </div>
            )}
          </Section>
        )}

        {tab === "accommodation" && (
          <>
          <Section title="Accommodation Status Report" subtitle="Active employees, by housing type and category">
            {loadingAccommodation || !accommodation ? (
              <div className="p-10">Loading...</div>
            ) : (
              <div className="overflow-x-auto p-5">
                <table className="w-full min-w-[600px] text-left">
                  <thead className="bg-slate-50 text-xs font-semibold uppercase text-slate-500">
                    <tr>
                      <th className="px-5 py-3">Housing</th>
                      {accommodation.categories.map((category) => <th key={category} className="px-5 py-3">{categoryLabels[category] || category}</th>)}
                      <th className="px-5 py-3">Total</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-100">
                    {accommodation.housing_types.map((housing) => (
                      <tr key={housing}>
                        <td className="px-5 py-4 font-semibold text-slate-900">{housingLabels[housing] || housing}</td>
                        {accommodation.categories.map((category) => <td key={category} className="px-5 py-4">{accommodation.counts[housing]?.[category] ?? 0}</td>)}
                        <td className="px-5 py-4 font-semibold">{accommodation.totals[housing] ?? 0}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Section>
          <AccommodationTracker />
          </>
        )}

        {tab === "directory" && <>

        {summary && (
          <div className="mb-6 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <button type="button" onClick={() => showStatus("")} className="text-left">
              <MetricCard title="Total" value={summary.total} subtitle="All employees, any status" accentColor="#334155" />
            </button>
            <button type="button" onClick={() => showStatus("active")} className="text-left">
              <MetricCard title="Active" value={summary.by_status.active || 0} subtitle="Currently working" accentColor="#059669" />
            </button>
            <button type="button" onClick={() => showStatus("inactive")} className="text-left">
              <MetricCard title="Inactive" value={summary.by_status.inactive || 0} subtitle="No longer active" accentColor="#64748b" />
            </button>
            <MetricCard title="On Leave Today" value={summary.on_leave_today} subtitle="Approved leave covering today" accentColor="#2563eb" />
            <button type="button" onClick={() => showNewHires()} className="text-left">
              <MetricCard title="New Hires" value={summary.new_hires_this_month} subtitle="This month - click to see who" accentColor="#0891b2" />
            </button>
            <button type="button" onClick={() => showExits()} className="text-left">
              <MetricCard title="Exits" value={summary.exits_this_month} subtitle="This month - click to see who" accentColor="#dc2626" />
            </button>
            <MetricCard title="Gender (Active)" value={`${summary.gender.male} M · ${summary.gender.female} F`} subtitle={summary.gender.unspecified ? `${summary.gender.unspecified} not set` : "Company hostel is split by gender"} accentColor="#7c3aed" />
            <button type="button" onClick={() => showNeedsAttention()} className="text-left">
              <MetricCard title="Needs Attention" value={summary.needs_attention} subtitle="Click to see who, and why" accentColor="#d97706" />
            </button>
          </div>
        )}

        <AppCard className="mb-6">
          <form onSubmit={submitSearch} className="flex flex-wrap gap-3">
            <input
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder="Search name, staff number, biometric ID or phone..."
              className="min-w-[240px] flex-1 rounded-xl border border-slate-300 px-4 py-3 text-slate-900 outline-none focus:ring-2 focus:ring-blue-500"
            />
            <button type="submit" className="rounded-xl bg-slate-900 px-6 py-3 font-medium text-white">Search</button>
            <button
              type="button"
              onClick={() => { setSearch(""); void loadEmployees(); }}
              className="rounded-xl border border-slate-300 px-5 py-3"
            >
              Clear
            </button>
          </form>

          <div className="mt-4 flex flex-wrap items-center gap-3 border-t border-slate-100 pt-4">
            <select value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)} className={filterSelectClass}>
              <option value="">All statuses</option>
              <option value="active">Active</option>
              <option value="inactive">Inactive</option>
              <option value="suspended">Suspended</option>
              <option value="terminated">Terminated</option>
            </select>

            <select value={departmentFilter} onChange={(event) => setDepartmentFilter(event.target.value)} className={filterSelectClass}>
              <option value="">All departments</option>
              {summary?.by_department.map((department) => (
                <option key={department.id ?? "none"} value={department.id ?? ""}>{department.name} ({department.count})</option>
              ))}
            </select>

            <select value={employmentTypeFilter} onChange={(event) => setEmploymentTypeFilter(event.target.value)} className={filterSelectClass}>
              <option value="">All employment types</option>
              {summary?.by_employment_type.map((type) => (
                <option key={type.value} value={type.value}>{type.label} ({type.count})</option>
              ))}
            </select>

            <select value={genderFilter} onChange={(event) => setGenderFilter(event.target.value)} className={filterSelectClass}>
              <option value="">All genders</option>
              <option value="male">Male ({summary?.gender.male ?? 0})</option>
              <option value="female">Female ({summary?.gender.female ?? 0})</option>
            </select>

            <select value={shiftPlanFilter} onChange={(event) => setShiftPlanFilter(event.target.value)} className={filterSelectClass}>
              <option value="">All shift plans</option>
              <option value="not_assigned">Not assigned ({summary?.not_assigned_shift_plan ?? 0})</option>
              {summary?.by_shift_plan.map((plan) => (
                <option key={plan.id} value={plan.id}>{plan.name} ({plan.count})</option>
              ))}
            </select>

            <button
              type="button"
              onClick={() => setNeedsAttentionOnly((value) => !value)}
              className={`rounded-full border px-4 py-2 text-sm font-semibold ${needsAttentionOnly ? "border-amber-400 bg-amber-100 text-amber-800" : "border-slate-300 bg-white text-slate-700"}`}
            >
              Needs Attention{summary ? ` (${summary.needs_attention})` : ""}
            </button>

            {activeFilterCount > 0 && (
              <button type="button" onClick={clearFilters} className="text-sm font-semibold text-blue-700 hover:text-blue-800">
                Clear filters
              </button>
            )}

            <button type="button" onClick={exportCsv} disabled={!sortedEmployees.length} className="ml-auto rounded-xl border border-slate-300 bg-white px-4 py-2.5 text-sm font-semibold text-slate-700 disabled:opacity-50">
              Export CSV
            </button>
          </div>
        </AppCard>

        <Section
          title="Employee Directory"
          subtitle={`${employees.length} employee${employees.length === 1 ? "" : "s"}${activeFilterCount ? " matching the current filters" : ""}`}
        >
          {loading ? (
            <div className="p-10">Loading employees...</div>
          ) : employees.length === 0 ? (
            <div className="p-12 text-center text-slate-500">No employees found.</div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full">
                <thead className="bg-slate-50">
                  <tr className="text-left text-xs uppercase tracking-wide text-slate-500">
                    <SortableHeader label="Employee" sortKey="full_name" sort={sort} onSort={toggleSort} />
                    <SortableHeader label="Department" sortKey="department_name" sort={sort} onSort={toggleSort} />
                    <th className="px-5 py-4">Position</th>
                    <SortableHeader label="Employment Type" sortKey="employment_type" sort={sort} onSort={toggleSort} />
                    <th className="px-5 py-4">Biometric ID</th>
                    <th className="px-5 py-4">Shift Plan</th>
                    <SortableHeader label="Salary" sortKey="basic_salary" sort={sort} onSort={toggleSort} />
                    <SortableHeader label="Status" sortKey="status" sort={sort} onSort={toggleSort} />
                  </tr>
                </thead>
                <tbody>
                  {sortedEmployees.map((employee) => {
                    const plan = shiftPlanCell(employee);
                    return (
                      <tr
                        key={employee.id}
                        onClick={() => router.push(`/employees/${employee.id}`)}
                        className="cursor-pointer border-t border-slate-100 hover:bg-slate-50"
                      >
                        <td className="px-5 py-5">
                          <div className="flex items-center gap-3">
                            <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-blue-100 text-sm font-bold text-blue-700">
                              {initials(employee.full_name)}
                            </div>
                            <div>
                              <div className="font-semibold text-slate-900">{employee.full_name}</div>
                              <div className="text-sm text-slate-500">{employee.employee_id}</div>
                              {employee.attention_reasons.length > 0 && (
                                <div className="mt-1 flex flex-wrap gap-1">
                                  {employee.attention_reasons.map((reason) => (
                                    <span key={reason} className="rounded-full bg-amber-100 px-2 py-0.5 text-[11px] font-semibold text-amber-800">
                                      {reason}
                                    </span>
                                  ))}
                                </div>
                              )}
                            </div>
                          </div>
                        </td>
                        <td className="px-5 py-5 text-slate-700">
                          {employee.department_name || <StatusBadge status="incomplete" />}
                        </td>
                        <td className="px-5 py-5 text-slate-700">
                          {employee.position_name || "-"}
                        </td>
                        <td className="px-5 py-5">
                          <span className="inline-flex rounded-full bg-blue-50 px-3 py-1 text-xs font-semibold text-blue-700">
                            {formatEmploymentType(employee.employment_type)}
                          </span>
                        </td>
                        <td className="px-5 py-5 text-slate-700">
                          {employee.biometric_user_id || <span className="text-amber-700">Not linked</span>}
                        </td>
                        <td className="px-5 py-5">
                          <div className={`font-medium ${plan.missing && employee.status === "active" ? "text-amber-700" : "text-slate-900"}`}>{plan.label}</div>
                          {plan.sub && <div className="mt-0.5 text-sm text-slate-500">{plan.sub}</div>}
                        </td>
                        <td className="px-5 py-5 font-medium text-slate-900">
                          {employee.basic_salary === undefined ? <StatusBadge status="restricted" /> : `₦${Number(employee.basic_salary).toLocaleString()}`}
                        </td>
                        <td className="px-5 py-5">
                          <StatusBadge status={employee.status} />
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </Section>
        </>}
      </main>
    </div>
  );
}

function SortableHeader({ label, sortKey, sort, onSort }: { label: string; sortKey: SortKey; sort: { key: SortKey; dir: "asc" | "desc" }; onSort: (key: SortKey) => void }) {
  const active = sort.key === sortKey;
  return (
    <th className="px-5 py-4">
      <button type="button" onClick={() => onSort(sortKey)} className={`flex items-center gap-1 hover:text-slate-700 ${active ? "text-slate-900" : ""}`}>
        {label}
        <span className="text-[10px]">{active ? (sort.dir === "asc" ? "▲" : "▼") : ""}</span>
      </button>
    </th>
  );
}

function formatEmploymentType(value: string) {
  return value === "nysc"
    ? "NYSC"
    : value.charAt(0).toUpperCase() + value.slice(1);
}

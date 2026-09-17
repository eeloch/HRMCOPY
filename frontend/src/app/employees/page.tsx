"use client";

import type { FormEvent } from "react";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";

import Sidebar from "@/components/Sidebar";
import { AppCard, PageHeader, Section, StatusBadge } from "@/components/ui";
import { apiFetch, getAccessToken, getCurrentUser, type CurrentUser } from "@/lib/api";

type Employee = {
  id: number;
  employee_id: string;
  biometric_user_id: string | null;
  full_name: string;
  department_name: string | null;
  position_name: string | null;
  employment_type: string;
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
};

type HireExitEntry = { id: number; employee_id: string; name: string; date: string; department: string };
type HiresExitsData = { week_start: string; week_end: string; hires: HireExitEntry[]; exits: HireExitEntry[] };
type AccommodationReport = { categories: string[]; housing_types: string[]; counts: Record<string, Record<string, number>>; totals: Record<string, number> };

const categoryLabels: Record<string, string> = { casual: "Casual", expatriate: "Expatriate", administrative: "Administrative", other: "Other" };
const housingLabels: Record<string, string> = { in_house: "In-House (Hostel)", external: "External (Rental)" };

function mondayOf(date: Date) {
  const day = date.getDay();
  const diff = (day === 0 ? -6 : 1) - day;
  const monday = new Date(date);
  monday.setDate(date.getDate() + diff);
  return monday.toISOString().slice(0, 10);
}

export default function EmployeesPage() {
  const router = useRouter();
  const [tab, setTab] = useState<"directory" | "hires-exits" | "accommodation">("directory");
  const [employees, setEmployees] = useState<Employee[]>([]);
  const [search, setSearch] = useState("");
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
    getCurrentUser().then(setCurrentUser).catch(() => {});
  }, [router]);

  useEffect(() => {
    if (tab === "hires-exits") void loadHiresExits(weekStart);
    if (tab === "accommodation" && !accommodation) void loadAccommodation();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab, weekStart]);

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

  function shiftWeek(days: number) {
    const next = new Date(`${weekStart}T00:00:00`);
    next.setDate(next.getDate() + days);
    setWeekStart(mondayOf(next));
  }

  async function loadEmployees(searchValue = "") {
    setLoading(true);

    try {
      const params = new URLSearchParams();

      if (searchValue) {
        params.set("search", searchValue);
      }

      const suffix = params.toString() ? `?${params.toString()}` : "";
      const response = await apiFetch(`/employees/${suffix}`);

      if (!response.ok) {
        console.log("Status:", response.status);

        const body = await response.text();

        console.log("Response:", body);

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
        )}

        {tab === "directory" && <>
        <AppCard className="mb-6">
          <form onSubmit={submitSearch} className="flex gap-3">
            <input
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder="Search name, staff number, biometric ID or phone..."
              className="flex-1 rounded-xl border border-slate-300 px-4 py-3 text-slate-900 outline-none focus:ring-2 focus:ring-blue-500"
            />
            <button
              type="submit"
              className="rounded-xl bg-slate-900 px-6 py-3 font-medium text-white"
            >
              Search
            </button>
            <button
              type="button"
              onClick={() => {
                setSearch("");
                void loadEmployees();
              }}
              className="rounded-xl border border-slate-300 px-5 py-3"
            >
              Clear
            </button>
          </form>
        </AppCard>

        <Section
          title="Employee Directory"
          subtitle={`${employees.length} employee${employees.length === 1 ? "" : "s"}`}
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
                    <th className="px-5 py-4">Employee</th>
                    <th className="px-5 py-4">Department</th>
                    <th className="px-5 py-4">Position</th>
                    <th className="px-5 py-4">Employment Type</th>
                    <th className="px-5 py-4">Biometric ID</th>
                    <th className="px-5 py-4">Shift</th>
                    <th className="px-5 py-4">Salary</th>
                    <th className="px-5 py-4">Status</th>
                  </tr>
                </thead>
                <tbody>
                  {employees.map((employee) => (
                    <tr
                      key={employee.id}
                      onClick={() => router.push(`/employees/${employee.id}`)}
                      className="cursor-pointer border-t border-slate-100 hover:bg-slate-50"
                    >
                      <td className="px-5 py-5">
                        <div className="font-semibold text-slate-900">{employee.full_name}</div>
                        <div className="text-sm text-slate-500">{employee.employee_id}</div>
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
                        {employee.biometric_user_id || "Not linked"}
                      </td>
                      <td className="px-5 py-5 text-slate-700">
                        {employee.current_shift?.name || "Not assigned"}
                      </td>
                      <td className="px-5 py-5 font-medium text-slate-900">
                        {employee.basic_salary === undefined ? <StatusBadge status="restricted" /> : `₦${Number(employee.basic_salary).toLocaleString()}`}
                      </td>
                      <td className="px-5 py-5">
                        <StatusBadge status={employee.status} />
                      </td>
                    </tr>
                  ))}
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

function formatEmploymentType(value: string) {
  return value === "nysc"
    ? "NYSC"
    : value.charAt(0).toUpperCase() + value.slice(1);
}

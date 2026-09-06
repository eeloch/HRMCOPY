"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";

import Sidebar from "@/components/Sidebar";
import { apiFetch, getAccessToken } from "@/lib/api";

type DashboardSummary = {
  present: number;
  absent: number;
  late: number;
  on_leave: number;
  night_shift: number;
  overtime: number;
  conflicts: number;
};

type WorkforceEmployee = {
  employee_id: number;
  employee_number: string;
  employee_name: string;
  department: string | null;
  shift: string | null;
  hostel: boolean;
  room: string;
  status: string;
};

type DepartmentReadiness = {
  department: string;
  required: number;
  present: number;
  short: number;
  readiness: number;
};

type HostelAbsentee = {
  employee_id: number;
  employee_name: string;
  department: string | null;
  room: string;
};

type AttendanceDashboard = {
  summary: DashboardSummary;
  workforce_action_center: WorkforceEmployee[];
  department_readiness: DepartmentReadiness[];
  hostel_absentees: HostelAbsentee[];
};

const summaryCards: {
  key: keyof DashboardSummary;
  label: string;
  detail: string;
  accent: string;
}[] = [
  {
    key: "present",
    label: "Present",
    detail: "Clocked in today",
    accent: "bg-emerald-50 text-emerald-700 ring-emerald-100",
  },
  {
    key: "absent",
    label: "Absent",
    detail: "Needs attention",
    accent: "bg-red-50 text-red-700 ring-red-100",
  },
  {
    key: "late",
    label: "Late",
    detail: "Arrived after shift start",
    accent: "bg-amber-50 text-amber-700 ring-amber-100",
  },
  {
    key: "on_leave",
    label: "On Leave",
    detail: "Approved leave records",
    accent: "bg-sky-50 text-sky-700 ring-sky-100",
  },
  {
    key: "conflicts",
    label: "Conflicts",
    detail: "Leave with biometric punches",
    accent: "bg-rose-50 text-rose-700 ring-rose-100",
  },
  {
    key: "night_shift",
    label: "Night Shift",
    detail: "Overnight assignments",
    accent: "bg-indigo-50 text-indigo-700 ring-indigo-100",
  },
  {
    key: "overtime",
    label: "Overtime",
    detail: "Employees with extra time",
    accent: "bg-violet-50 text-violet-700 ring-violet-100",
  },
];

export default function AttendancePage() {
  const router = useRouter();
  const [dashboard, setDashboard] = useState<AttendanceDashboard | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!getAccessToken()) {
      router.push("/login");
      return;
    }

    void loadDashboard();
  }, [router]);

  async function loadDashboard() {
    setLoading(true);
    setError("");

    try {
      const response = await apiFetch("/attendance/dashboard/");

      if (!response.ok) {
        throw new Error("Unable to load the workforce dashboard.");
      }

      const data: AttendanceDashboard = await response.json();
      setDashboard(data);
    } catch (loadError) {
      console.error(loadError);
      setError(
        loadError instanceof Error
          ? loadError.message
          : "Unable to load the workforce dashboard."
      );
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="min-h-screen bg-slate-100">
      <Sidebar />

      <main className="ml-64 min-w-0 p-4 md:p-8">
        <div className="mb-8 flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
          <div>
            <p className="text-sm font-semibold uppercase tracking-wider text-blue-600">
              Attendance
            </p>
            <h1 className="mt-1 text-3xl font-bold text-slate-900">
              Workforce Operations
            </h1>
            <p className="mt-2 text-slate-500">
              Today&apos;s attendance status and workforce actions.
            </p>
          </div>

          {!loading && !error && (
            <button
              type="button"
              onClick={() => void loadDashboard()}
              className="rounded-xl border border-slate-300 bg-white px-4 py-2.5 text-sm font-medium text-slate-700 shadow-sm hover:bg-slate-50"
            >
              Refresh
            </button>
          )}
        </div>

        {error ? (
          <div className="rounded-2xl border border-red-200 bg-red-50 p-6 text-red-700">
            <p className="font-semibold">Unable to load dashboard</p>
            <p className="mt-1 text-sm">{error}</p>
            <button
              type="button"
              onClick={() => void loadDashboard()}
              className="mt-4 rounded-xl bg-red-600 px-4 py-2.5 text-sm font-semibold text-white hover:bg-red-700"
            >
              Try Again
            </button>
          </div>
        ) : (
          <>
            <section
              aria-label="Attendance summary"
              className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3"
            >
              {loading
                ? summaryCards.map((card) => <SummaryCardSkeleton key={card.key} />)
                : summaryCards.map((card) => (
                    <SummaryCard
                      key={card.key}
                      label={card.label}
                      detail={card.detail}
                      value={dashboard?.summary[card.key] || 0}
                      accent={card.accent}
                    />
                  ))}
            </section>

            <section className="mt-8 overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">
              <div className="flex flex-col gap-2 border-b border-slate-200 px-5 py-5 sm:flex-row sm:items-center sm:justify-between">
                <div>
                  <h2 className="text-lg font-bold text-slate-900">
                    Workforce Action Center
                  </h2>
                  <p className="mt-1 text-sm text-slate-500">
                    Employees absent today who may need follow-up.
                  </p>
                </div>

                {!loading && (
                  <span className="w-fit rounded-full bg-red-50 px-3 py-1 text-sm font-medium text-red-700">
                    {dashboard?.workforce_action_center.length || 0} absent
                  </span>
                )}
              </div>

              {loading ? (
                <TableSkeleton />
              ) : dashboard?.workforce_action_center.length ? (
                <div className="overflow-x-auto">
                  <table className="w-full min-w-[720px] text-left">
                    <thead className="bg-slate-50 text-xs font-semibold uppercase tracking-wider text-slate-500">
                      <tr>
                        <th className="px-5 py-3">Employee</th>
                        <th className="px-5 py-3">Department</th>
                        <th className="px-5 py-3">Shift</th>
                        <th className="px-5 py-3">Hostel</th>
                        <th className="px-5 py-3">Room</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-100">
                      {dashboard.workforce_action_center.map((employee) => (
                        <tr
                          key={employee.employee_id}
                          onClick={() => router.push(`/employees/${employee.employee_id}`)}
                          className="cursor-pointer transition hover:bg-blue-50/60"
                        >
                          <td className="px-5 py-4">
                            <div className="font-semibold text-slate-900">
                              {employee.employee_name}
                            </div>
                            <div className="mt-1 text-sm text-slate-500">
                              {employee.employee_number}
                            </div>
                          </td>
                          <td className="px-5 py-4 text-sm text-slate-600">
                            {employee.department || "Not assigned"}
                          </td>
                          <td className="px-5 py-4 text-sm text-slate-600">
                            {employee.shift || "No shift"}
                          </td>
                          <td className="px-5 py-4 text-sm text-slate-600">
                            {employee.hostel ? "Company hostel" : "Off-site"}
                          </td>
                          <td className="px-5 py-4 text-sm text-slate-600">
                            {employee.room || "-"}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <div className="p-10 text-center text-slate-500">
                  No workforce actions require attention today.
                </div>
              )}
            </section>

            <section className="mt-8 overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">
              <div className="border-b border-slate-200 px-5 py-5">
                <h2 className="text-lg font-bold text-slate-900">
                  Department Readiness
                </h2>
                <p className="mt-1 text-sm text-slate-500">
                  Staffing coverage against each department&apos;s required workforce.
                </p>
              </div>

              {loading ? (
                <TableSkeleton />
              ) : dashboard?.department_readiness.length ? (
                <div className="overflow-x-auto">
                  <table className="w-full min-w-[680px] text-left">
                    <thead className="bg-slate-50 text-xs font-semibold uppercase tracking-wider text-slate-500">
                      <tr>
                        <th className="px-5 py-3">Department</th>
                        <th className="px-5 py-3">Required</th>
                        <th className="px-5 py-3">Present</th>
                        <th className="px-5 py-3">Short</th>
                        <th className="px-5 py-3">Readiness %</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-100">
                      {dashboard.department_readiness.map((department) => (
                        <tr key={department.department} className="hover:bg-slate-50/80">
                          <td className="px-5 py-4 font-semibold text-slate-900">
                            {department.department}
                          </td>
                          <td className="px-5 py-4 text-sm text-slate-600">
                            {department.required}
                          </td>
                          <td className="px-5 py-4 text-sm text-slate-600">
                            {department.present}
                          </td>
                          <td className="px-5 py-4 text-sm text-slate-600">
                            {department.short}
                          </td>
                          <td className="px-5 py-4">
                            <ReadinessBadge value={department.readiness} />
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <div className="p-10 text-center text-slate-500">
                  No department readiness data.
                </div>
              )}
            </section>

            <section className="mt-8 overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">
              <div className="flex flex-col gap-2 border-b border-slate-200 px-5 py-5 sm:flex-row sm:items-center sm:justify-between">
                <div>
                  <h2 className="text-lg font-bold text-slate-900">
                    🏠 Hostel Residents Absent Today
                  </h2>
                  <p className="mt-1 text-sm text-slate-500">
                    Residents who are absent from their assigned shift today.
                  </p>
                </div>

                {!loading && (
                  <span className="w-fit rounded-full bg-red-50 px-3 py-1 text-sm font-medium text-red-700">
                    {dashboard?.hostel_absentees.length || 0} absent
                  </span>
                )}
              </div>

              {loading ? (
                <TableSkeleton />
              ) : dashboard?.hostel_absentees.length ? (
                <div className="overflow-x-auto">
                  <table className="w-full min-w-[600px] text-left">
                    <thead className="bg-slate-50 text-xs font-semibold uppercase tracking-wider text-slate-500">
                      <tr>
                        <th className="px-5 py-3">Employee</th>
                        <th className="px-5 py-3">Department</th>
                        <th className="px-5 py-3">Room</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-100">
                      {dashboard.hostel_absentees.map((employee) => (
                        <tr
                          key={employee.employee_id}
                          onClick={() => router.push(`/employees/${employee.employee_id}`)}
                          className="cursor-pointer transition hover:bg-blue-50/60"
                        >
                          <td className="px-5 py-4 font-semibold text-slate-900">
                            {employee.employee_name}
                          </td>
                          <td className="px-5 py-4 text-sm text-slate-600">
                            {employee.department || "Not assigned"}
                          </td>
                          <td className="px-5 py-4 text-sm text-slate-600">
                            {employee.room || "-"}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <div className="p-10 text-center text-slate-500">
                  No hostel residents are absent today.
                </div>
              )}
            </section>
          </>
        )}
      </main>
    </div>
  );
}

function SummaryCard({
  label,
  detail,
  value,
  accent,
}: {
  label: string;
  detail: string;
  value: number;
  accent: string;
}) {
  return (
    <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
      <div className={`inline-flex rounded-xl px-3 py-1 text-sm font-semibold ring-1 ${accent}`}>
        {label}
      </div>
      <div className="mt-5 text-3xl font-bold tabular-nums text-slate-900">
        {value}
      </div>
      <p className="mt-1 text-sm text-slate-500">{detail}</p>
    </div>
  );
}

function SummaryCardSkeleton() {
  return (
    <div className="animate-pulse rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
      <div className="h-7 w-24 rounded-xl bg-slate-100" />
      <div className="mt-5 h-9 w-16 rounded bg-slate-100" />
      <div className="mt-3 h-4 w-36 rounded bg-slate-100" />
    </div>
  );
}

function TableSkeleton() {
  return (
    <div className="animate-pulse space-y-4 p-5">
      {[0, 1, 2, 3].map((row) => (
        <div key={row} className="grid grid-cols-5 gap-4">
          <div className="col-span-2 h-10 rounded bg-slate-100" />
          <div className="h-10 rounded bg-slate-100" />
          <div className="h-10 rounded bg-slate-100" />
          <div className="h-10 rounded bg-slate-100" />
        </div>
      ))}
    </div>
  );
}

function ReadinessBadge({ value }: { value: number }) {
  const roundedValue = Math.round(value);
  const colorClass =
    value >= 95
      ? "bg-emerald-50 text-emerald-700 ring-emerald-100"
      : value >= 80
        ? "bg-amber-50 text-amber-700 ring-amber-100"
        : "bg-red-50 text-red-700 ring-red-100";
  const dotClass =
    value >= 95
      ? "bg-emerald-500"
      : value >= 80
        ? "bg-amber-500"
        : "bg-red-500";

  return (
    <span
      className={`inline-flex items-center gap-2 rounded-full px-3 py-1 text-sm font-semibold ring-1 ${colorClass}`}
    >
      <span className={`h-2 w-2 rounded-full ${dotClass}`} />
      {roundedValue}%
    </span>
  );
}

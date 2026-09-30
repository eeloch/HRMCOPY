"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";

import Sidebar from "@/components/Sidebar";
import { apiFetch, getAccessToken } from "@/lib/api";

type ShiftPeriod = "morning" | "night";

type PeriodSummary = {
  expected: number;
  not_yet_in: number;
  present: number;
  late: number;
  absent: number;
  conflicts: number;
  overtime: number;
  on_leave: number;
};

type RecentPunch = {
  employee_number: string;
  employee_name: string;
  department: string | null;
  device: string | null;
  timestamp: string;
};

type DeviceStatus = {
  name: string;
  purpose: string;
  online: boolean;
  last_sync_at: string | null;
};

type WorkforceEmployee = {
  shift_period: ShiftPeriod;
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

type NotYetInEmployee = {
  shift_period: ShiftPeriod;
  employee_id: number;
  employee_number: string;
  employee_name: string;
  department: string | null;
  hostel: boolean;
  room: string | null;
};

type LateEmployee = {
  shift_period: ShiftPeriod;
  employee_id: number;
  employee_number: string;
  employee_name: string;
  department: string | null;
  shift: string | null;
  actual_clock_in: string | null;
  late_minutes: number;
};

type AttendanceDashboard = {
  date: string;
  is_today: boolean;
  morning: PeriodSummary;
  night: PeriodSummary;
  not_yet_in_employees: NotYetInEmployee[];
  late_employees: LateEmployee[];
  workforce_action_center: WorkforceEmployee[];
  department_readiness: DepartmentReadiness[];
  hostel_absentees: HostelAbsentee[];
  recent_events: RecentPunch[];
  device_status: DeviceStatus[];
};

const periodCards: {
  key: keyof PeriodSummary;
  label: string;
  detail: string;
  accent: string;
  action?: string;
}[] = [
  {
    key: "expected",
    label: "Expected",
    detail: "Rostered and their shift has started",
    accent: "bg-slate-100 text-slate-700 ring-slate-200",
    action: "Click to print the not-yet-in roster",
  },
  {
    key: "present",
    label: "Present",
    detail: "Clocked in (on time or late)",
    accent: "bg-emerald-50 text-emerald-700 ring-emerald-100",
  },
  {
    key: "late",
    label: "Late",
    detail: "Arrived after shift start",
    accent: "bg-amber-50 text-amber-700 ring-amber-100",
    action: "Click to view the list",
  },
  {
    key: "absent",
    label: "Absent",
    detail: "Needs attention (once the shift is over)",
    accent: "bg-red-50 text-red-700 ring-red-100",
    action: "Click to jump to the list",
  },
  {
    key: "conflicts",
    label: "Conflicts",
    detail: "Leave with biometric punches",
    accent: "bg-rose-50 text-rose-700 ring-rose-100",
  },
  {
    key: "overtime",
    label: "Overtime",
    detail: "Employees with extra time",
    accent: "bg-violet-50 text-violet-700 ring-violet-100",
  },
  {
    key: "on_leave",
    label: "On Leave",
    detail: "Approved leave records",
    accent: "bg-sky-50 text-sky-700 ring-sky-100",
  },
];

function todayIsoFor(date: Date) {
  const offset = date.getTimezoneOffset();
  return new Date(date.getTime() - offset * 60000).toISOString().slice(0, 10);
}

function todayIso() {
  return todayIsoFor(new Date());
}

export default function AttendancePage() {
  const router = useRouter();
  const [dashboard, setDashboard] = useState<AttendanceDashboard | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [selectedDate, setSelectedDate] = useState(todayIso());
  const [showLate, setShowLate] = useState<ShiftPeriod | null>(null);
  const [showNotYetInReport, setShowNotYetInReport] = useState<ShiftPeriod | null>(null);
  const actionCenterRef = useRef<HTMLElement | null>(null);

  const isToday = selectedDate === todayIso();

  function handleCardClick(period: ShiftPeriod, key: keyof PeriodSummary) {
    if (key === "late") {
      setShowLate((current) => (current === period ? null : period));
      return;
    }
    if (key === "expected") {
      setShowNotYetInReport(period);
      return;
    }
    if (key === "absent") {
      actionCenterRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  }

  useEffect(() => {
    if (!getAccessToken()) {
      router.push("/login");
      return;
    }

    void loadDashboard(selectedDate);
  }, [router, selectedDate]);

  async function loadDashboard(date: string) {
    setLoading(true);
    setError("");

    try {
      const response = await apiFetch(`/attendance/dashboard/?date=${date}`);

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

  function shiftOffset(days: number) {
    const [year, month, day] = selectedDate.split("-").map(Number);
    const next = new Date(year, month - 1, day + days);
    setSelectedDate(todayIsoFor(next));
  }

  return (
    <div className="min-h-screen bg-slate-100">
      <Sidebar />

      <main className="ml-64 min-w-0 p-4 md:p-8 print:hidden">
        <div className="mb-8 flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
          <div>
            <p className="text-sm font-semibold uppercase tracking-wider text-blue-600">
              Attendance
            </p>
            <h1 className="mt-1 text-3xl font-bold text-slate-900">
              Workforce Operations
            </h1>
            <p className="mt-2 text-slate-500">
              {isToday ? "Today's" : "That day's"} attendance status and workforce actions.
            </p>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <div className="flex items-center gap-1 rounded-xl border border-slate-300 bg-white p-1 shadow-sm">
              <button
                type="button"
                onClick={() => shiftOffset(-1)}
                className="rounded-lg px-2.5 py-1.5 text-slate-500 hover:bg-slate-100"
                aria-label="Previous day"
              >
                ←
              </button>
              <input
                type="date"
                value={selectedDate}
                onChange={(event) => setSelectedDate(event.target.value)}
                className="rounded-lg px-2 py-1.5 text-sm font-medium text-slate-700 outline-none"
              />
              <button
                type="button"
                onClick={() => shiftOffset(1)}
                className="rounded-lg px-2.5 py-1.5 text-slate-500 hover:bg-slate-100"
                aria-label="Next day"
              >
                →
              </button>
            </div>
            {!isToday && (
              <button
                type="button"
                onClick={() => setSelectedDate(todayIso())}
                className="rounded-xl border border-blue-200 bg-blue-50 px-3 py-2 text-sm font-medium text-blue-700 hover:bg-blue-100"
              >
                Today
              </button>
            )}
            {!loading && !error && (
              <button
                type="button"
                onClick={() => void loadDashboard(selectedDate)}
                className="rounded-xl border border-slate-300 bg-white px-4 py-2.5 text-sm font-medium text-slate-700 shadow-sm hover:bg-slate-50"
              >
                Refresh
              </button>
            )}
          </div>
        </div>

        {error ? (
          <div className="rounded-2xl border border-red-200 bg-red-50 p-6 text-red-700">
            <p className="font-semibold">Unable to load dashboard</p>
            <p className="mt-1 text-sm">{error}</p>
            <button
              type="button"
              onClick={() => void loadDashboard(selectedDate)}
              className="mt-4 rounded-xl bg-red-600 px-4 py-2.5 text-sm font-semibold text-white hover:bg-red-700"
            >
              Try Again
            </button>
          </div>
        ) : (
          <>
            <section aria-label="Attendance summary" className="grid grid-cols-1 gap-6 xl:grid-cols-2">
              <ShiftColumn
                period="morning"
                title="Morning"
                subtitle="Day shifts"
                summary={dashboard?.morning}
                loading={loading}
                showLate={showLate === "morning"}
                onCardClick={(key) => handleCardClick("morning", key)}
              />
              <ShiftColumn
                period="night"
                title="Night"
                subtitle="Overnight shifts"
                summary={dashboard?.night}
                loading={loading}
                showLate={showLate === "night"}
                onCardClick={(key) => handleCardClick("night", key)}
              />
            </section>

            {showLate && (
              <section className="mt-6 overflow-hidden rounded-2xl border border-amber-200 bg-amber-50/40 shadow-sm">
                <div className="flex items-center justify-between border-b border-amber-200 px-5 py-4">
                  <div>
                    <h2 className="text-lg font-bold text-slate-900">
                      Late — {showLate === "morning" ? "Morning" : "Night"} (
                      {dashboard?.late_employees.filter((employee) => employee.shift_period === showLate).length ?? 0})
                    </h2>
                    <p className="mt-1 text-sm text-slate-500">Clocked in after their shift start.</p>
                  </div>
                  <button
                    type="button"
                    onClick={() => setShowLate(null)}
                    className="rounded-xl border border-slate-300 bg-white px-3 py-1.5 text-sm font-medium text-slate-600 hover:bg-slate-50"
                  >
                    Hide
                  </button>
                </div>
                {dashboard?.late_employees.filter((employee) => employee.shift_period === showLate).length ? (
                  <div className="overflow-x-auto">
                    <table className="w-full min-w-[640px] text-left">
                      <thead className="bg-white text-xs font-semibold uppercase tracking-wider text-slate-500">
                        <tr>
                          <th className="px-5 py-3">Employee</th>
                          <th className="px-5 py-3">Department</th>
                          <th className="px-5 py-3">Shift</th>
                          <th className="px-5 py-3">Clocked In</th>
                          <th className="px-5 py-3">Late By</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-amber-100 bg-white">
                        {dashboard.late_employees
                          .filter((employee) => employee.shift_period === showLate)
                          .map((employee) => (
                            <tr
                              key={employee.employee_id}
                              onClick={() => router.push(`/employees/${employee.employee_id}`)}
                              className="cursor-pointer hover:bg-amber-50"
                            >
                              <td className="px-5 py-3">
                                <div className="font-semibold text-slate-900">{employee.employee_name}</div>
                                <div className="text-sm text-slate-500">{employee.employee_number}</div>
                              </td>
                              <td className="px-5 py-3 text-sm text-slate-600">{employee.department || "Not assigned"}</td>
                              <td className="px-5 py-3 text-sm text-slate-600">{employee.shift || "No shift"}</td>
                              <td className="px-5 py-3 text-sm text-slate-600">
                                {employee.actual_clock_in
                                  ? new Date(employee.actual_clock_in).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" })
                                  : "-"}
                              </td>
                              <td className="px-5 py-3 text-sm font-semibold text-amber-700">{employee.late_minutes} min</td>
                            </tr>
                          ))}
                      </tbody>
                    </table>
                  </div>
                ) : (
                  <div className="p-8 text-center text-slate-500">No one is late.</div>
                )}
              </section>
            )}

            <section ref={actionCenterRef} className="mt-8 overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm scroll-mt-6">
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

            <section className="mt-8 overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">
              <div className="flex flex-col gap-2 border-b border-slate-200 px-5 py-5 sm:flex-row sm:items-center sm:justify-between">
                <div>
                  <h2 className="text-lg font-bold text-slate-900">Recent Punches</h2>
                  <p className="mt-1 text-sm text-slate-500">The latest scans from the attendance terminals, as they arrive. Attendance records update every few minutes.</p>
                </div>
                {!loading && (
                  <div className="flex flex-wrap gap-2">
                    {dashboard?.device_status.map((device) => (
                      <span key={device.name} className={`rounded-full px-3 py-1 text-xs font-semibold ${device.online ? "bg-emerald-50 text-emerald-700" : "bg-slate-100 text-slate-500"}`}>
                        {device.name}: {device.online ? "online" : "offline"}
                      </span>
                    ))}
                  </div>
                )}
              </div>
              {loading ? (
                <TableSkeleton />
              ) : dashboard?.recent_events.length ? (
                <div className="max-h-[420px] overflow-auto">
                  <table className="w-full min-w-[600px] text-left">
                    <thead className="sticky top-0 bg-slate-50 text-xs font-semibold uppercase tracking-wider text-slate-500">
                      <tr>
                        <th className="px-5 py-3">Time</th>
                        <th className="px-5 py-3">Employee</th>
                        <th className="px-5 py-3">Department</th>
                        <th className="px-5 py-3">Terminal</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-100">
                      {dashboard.recent_events.map((punch, index) => (
                        <tr key={`${punch.employee_number}-${punch.timestamp}-${index}`}>
                          <td className="px-5 py-3 text-sm tabular-nums text-slate-600">{new Date(punch.timestamp).toLocaleString("en-GB", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", second: "2-digit" })}</td>
                          <td className="px-5 py-3"><span className="font-semibold text-slate-900">{punch.employee_name}</span> <span className="text-xs text-slate-500">{punch.employee_number}</span></td>
                          <td className="px-5 py-3 text-sm text-slate-600">{punch.department || "-"}</td>
                          <td className="px-5 py-3 text-sm text-slate-600">{punch.device || "-"}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <div className="p-10 text-center text-slate-500">No punches yet.</div>
              )}
            </section>
          </>
        )}
      </main>

      {showNotYetInReport && dashboard && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/50 p-4 print:static print:block print:bg-white print:p-0">
          <div className="max-h-[90vh] w-full max-w-3xl overflow-auto rounded-2xl bg-white shadow-xl print:max-h-none print:w-full print:max-w-none print:overflow-visible print:rounded-none print:shadow-none">
            <div className="flex items-center justify-between border-b border-slate-200 px-6 py-4 print:hidden">
              <div>
                <h2 className="text-lg font-bold text-slate-900">
                  Not Yet In — {showNotYetInReport === "morning" ? "Morning" : "Night"} — Printable Roster
                </h2>
                <p className="mt-1 text-sm text-slate-500">
                  {dashboard.not_yet_in_employees.filter((employee) => employee.shift_period === showNotYetInReport).length} people
                  expected but not yet clocked in.
                </p>
              </div>
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={() => window.print()}
                  className="rounded-xl bg-blue-600 px-4 py-2 text-sm font-semibold text-white hover:bg-blue-700"
                >
                  Print
                </button>
                <button
                  type="button"
                  onClick={() => setShowNotYetInReport(null)}
                  className="rounded-xl border border-slate-300 px-4 py-2 text-sm font-semibold text-slate-700 hover:bg-slate-50"
                >
                  Close
                </button>
              </div>
            </div>

            <div className="p-6">
              <div className="mb-4 hidden print:block">
                <h2 className="text-xl font-bold text-slate-900">
                  Not Yet In — {showNotYetInReport === "morning" ? "Morning" : "Night"} — {dashboard.date}
                </h2>
                <p className="text-sm text-slate-600">
                  {dashboard.not_yet_in_employees.filter((employee) => employee.shift_period === showNotYetInReport).length} people
                  expected but not yet clocked in.
                </p>
              </div>

              {dashboard.not_yet_in_employees.filter((employee) => employee.shift_period === showNotYetInReport).length ? (
                <table className="w-full text-left text-sm">
                  <thead className="border-b-2 border-slate-300 text-xs font-semibold uppercase tracking-wider text-slate-500">
                    <tr>
                      <th className="py-2 pr-3">Department</th>
                      <th className="py-2 pr-3">Employee</th>
                      <th className="py-2 pr-3">Staff No.</th>
                      <th className="py-2 pr-3">Hostel</th>
                      <th className="py-2">Room</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-100">
                    {dashboard.not_yet_in_employees
                      .filter((employee) => employee.shift_period === showNotYetInReport)
                      .map((employee) => (
                        <tr key={employee.employee_id}>
                          <td className="py-2 pr-3 font-medium text-slate-900">{employee.department || "Not assigned"}</td>
                          <td className="py-2 pr-3 text-slate-700">{employee.employee_name}</td>
                          <td className="py-2 pr-3 text-slate-600">{employee.employee_number}</td>
                          <td className="py-2 pr-3 text-slate-600">{employee.hostel ? "Yes" : "No"}</td>
                          <td className="py-2 font-semibold text-slate-900">{employee.room || "-"}</td>
                        </tr>
                      ))}
                  </tbody>
                </table>
              ) : (
                <p className="py-6 text-center text-slate-500">Everyone expected has already clocked in.</p>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function ShiftColumn({
  period,
  title,
  subtitle,
  summary,
  loading,
  showLate,
  onCardClick,
}: {
  period: ShiftPeriod;
  title: string;
  subtitle: string;
  summary: PeriodSummary | undefined;
  loading: boolean;
  showLate: boolean;
  onCardClick: (key: keyof PeriodSummary) => void;
}) {
  return (
    <div className="rounded-2xl border border-slate-200 bg-slate-50/60 p-4">
      <div className="mb-4 flex items-center gap-2">
        <span
          className={`inline-flex h-9 w-9 items-center justify-center rounded-xl text-lg ${
            period === "morning" ? "bg-amber-100 text-amber-700" : "bg-indigo-100 text-indigo-700"
          }`}
        >
          {period === "morning" ? "☀️" : "🌙"}
        </span>
        <div>
          <h2 className="text-lg font-bold text-slate-900">{title}</h2>
          <p className="text-xs text-slate-500">{subtitle}</p>
        </div>
        {!loading && summary && summary.not_yet_in > 0 && (
          <span className="ml-auto rounded-full bg-orange-50 px-3 py-1 text-xs font-semibold text-orange-700">
            {summary.not_yet_in} not in yet
          </span>
        )}
      </div>

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-2 2xl:grid-cols-3">
        {loading
          ? periodCards.map((card) => <SummaryCardSkeleton key={card.key} />)
          : periodCards.map((card) => (
              <SummaryCard
                key={card.key}
                label={card.label}
                detail={card.detail}
                value={summary?.[card.key] || 0}
                accent={card.accent}
                action={card.action}
                active={card.key === "late" && showLate}
                onClick={card.action ? () => onCardClick(card.key) : undefined}
              />
            ))}
      </div>
    </div>
  );
}

function SummaryCard({
  label,
  detail,
  value,
  accent,
  action,
  active,
  onClick,
}: {
  label: string;
  detail: string;
  value: number;
  accent: string;
  action?: string;
  active?: boolean;
  onClick?: () => void;
}) {
  const clickable = Boolean(onClick);
  return (
    <div
      onClick={onClick}
      className={`rounded-2xl border bg-white p-5 shadow-sm transition ${
        clickable ? "cursor-pointer hover:-translate-y-0.5 hover:shadow-md" : ""
      } ${active ? "border-blue-400 ring-2 ring-blue-100" : "border-slate-200"}`}
    >
      <div className={`inline-flex rounded-xl px-3 py-1 text-sm font-semibold ring-1 ${accent}`}>
        {label}
      </div>
      <div className="mt-5 text-3xl font-bold tabular-nums text-slate-900">
        {value}
      </div>
      <p className="mt-1 text-sm text-slate-500">{detail}</p>
      {action && <p className="mt-2 text-xs font-semibold text-blue-600">{action} →</p>}
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

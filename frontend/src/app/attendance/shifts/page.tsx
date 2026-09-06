"use client";

import { FormEvent, useEffect, useState } from "react";
import { useRouter } from "next/navigation";

import Sidebar from "@/components/Sidebar";
import { ShiftAssignmentModal } from "@/components/shifts/ShiftAssignmentModal";
import type { Shift, ShiftAssignment, ShiftEmployee } from "@/components/shifts/types";
import { AppCard, PageHeader, Section } from "@/components/ui";
import { apiFetch, getAccessToken } from "@/lib/api";

function formatDate(value: string | null) {
  if (!value) return "Ongoing";
  return new Date(`${value}T00:00:00`).toLocaleDateString(undefined, {
    day: "numeric",
    month: "short",
    year: "numeric",
  });
}

export default function ShiftManagementPage() {
  const router = useRouter();
  const [assignments, setAssignments] = useState<ShiftAssignment[]>([]);
  const [employees, setEmployees] = useState<ShiftEmployee[]>([]);
  const [shifts, setShifts] = useState<Shift[]>([]);
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState("all");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [showModal, setShowModal] = useState(false);
  const [assignmentToChange, setAssignmentToChange] = useState<ShiftAssignment | null>(null);
  const [endingId, setEndingId] = useState<number | null>(null);
  const [rotationEmployee, setRotationEmployee] = useState("");
  const [rotationStart, setRotationStart] = useState("");
  const [rotationEnd, setRotationEnd] = useState("");
  const [rotationStartingShift, setRotationStartingShift] = useState<"day" | "night">("day");
  const [rotationDayShift, setRotationDayShift] = useState("");
  const [rotationNightShift, setRotationNightShift] = useState("");
  const [rotationFeedback, setRotationFeedback] = useState("");

  async function load() {
    setLoading(true);
    setError("");
    try {
      const [assignmentResponse, shiftResponse, employeeResponse] = await Promise.all([
        apiFetch("/attendance/shift-assignments/"),
        apiFetch("/attendance/shifts/"),
        apiFetch("/employees/"),
      ]);
      if (!assignmentResponse.ok || !shiftResponse.ok || !employeeResponse.ok) {
        throw new Error("Unable to load shift management data.");
      }
      const [assignmentData, shiftData, employeeData] = await Promise.all([
        assignmentResponse.json(),
        shiftResponse.json(),
        employeeResponse.json(),
      ]);
      setAssignments(assignmentData.results || []);
      setShifts(shiftData.results || []);
      setEmployees(employeeData.results || []);
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "Unable to load shift management data.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    if (!getAccessToken()) {
      router.push("/login");
      return;
    }
    const loadTimer = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(loadTimer);
  }, [router]);

  const visibleAssignments = assignments.filter((assignment) => {
    const term = search.trim().toLowerCase();
    const matchesSearch = !term || [
      assignment.employee_name,
      assignment.employee_number,
      assignment.department_name || "",
      assignment.shift_name,
    ].some((value) => value.toLowerCase().includes(term));
    return matchesSearch && (statusFilter === "all" || assignment.status === statusFilter);
  });

  async function endAssignment(assignment: ShiftAssignment) {
    const endDate = window.prompt(
      "Enter the final assignment date (YYYY-MM-DD):",
      new Date().toISOString().slice(0, 10),
    );
    if (!endDate) return;

    setEndingId(assignment.id);
    setError("");
    try {
      const response = await apiFetch(`/attendance/shift-assignments/${assignment.id}/`, {
        method: "PATCH",
        body: JSON.stringify({ end_date: endDate }),
      });
      if (!response.ok) throw new Error("Unable to end the shift assignment.");
      await load();
    } catch (endError) {
      setError(endError instanceof Error ? endError.message : "Unable to end the shift assignment.");
    } finally {
      setEndingId(null);
    }
  }

  async function generateRotation(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    setRotationFeedback("");
    try {
      const response = await apiFetch("/attendance/roster/rotation/generate/", {
        method: "POST",
        body: JSON.stringify({ employee: Number(rotationEmployee), start_date: rotationStart, end_date: rotationEnd, starting_shift: rotationStartingShift, day_shift: Number(rotationDayShift), night_shift: Number(rotationNightShift) }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.detail || "Unable to generate the rotation roster.");
      setRotationFeedback(`Rotation generated: ${data.summary.created} created, ${data.summary.skipped} preserved.`);
    } catch (rotationError) {
      setError(rotationError instanceof Error ? rotationError.message : "Unable to generate the rotation roster.");
    }
  }

  return (
    <div className="min-h-screen bg-slate-100">
      <Sidebar />
      <main className="ml-64 min-w-0 p-4 md:p-8">
        <PageHeader
          title="Shift Management"
          description="Assign and maintain employee work schedules without changing attendance records."
          actions={<button type="button" onClick={() => { setAssignmentToChange(null); setShowModal(true); }} className="rounded-xl bg-blue-600 px-5 py-3 font-semibold text-white hover:bg-blue-700">Assign Shift</button>}
        />

        <AppCard className="mb-6">
          <div className="flex flex-col gap-3 md:flex-row">
            <input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search employee, staff number, department or shift..." className="min-w-0 flex-1 rounded-xl border border-slate-300 px-4 py-3 outline-none focus:ring-2 focus:ring-blue-500" />
            <select value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)} className="rounded-xl border border-slate-300 bg-white px-4 py-3">
              <option value="all">All statuses</option>
              <option value="active">Active</option>
              <option value="upcoming">Upcoming</option>
              <option value="ended">Ended</option>
            </select>
          </div>
        </AppCard>

        <Section className="mb-6" title="Shift Definitions" subtitle="Active shift windows used by assignments and roster dates.">
          <div className="overflow-x-auto">
            <table className="w-full min-w-[560px] text-left">
              <thead className="bg-slate-50 text-xs font-semibold uppercase tracking-wide text-slate-500"><tr><th className="px-5 py-3">Shift</th><th className="px-5 py-3">Start</th><th className="px-5 py-3">End</th><th className="px-5 py-3">Schedule</th></tr></thead>
              <tbody className="divide-y divide-slate-100">{shifts.filter((shift) => shift.active).map((shift) => <tr key={shift.id}><td className="px-5 py-4 font-semibold text-slate-900">{shift.name}</td><td className="px-5 py-4 text-sm text-slate-600">{shift.start_time.slice(0, 5)}</td><td className="px-5 py-4 text-sm text-slate-600">{shift.end_time.slice(0, 5)}</td><td className="px-5 py-4 text-sm text-slate-600">{shift.is_overnight ? "Overnight" : "Daytime"}</td></tr>)}</tbody>
            </table>
          </div>
        </Section>

        <Section className="mb-6" title="Weekly Day / Night Rotation" subtitle="Generate dated roster records for a selected employee. Start dates must be Mondays.">
          <form onSubmit={generateRotation} className="grid gap-4 p-5 md:grid-cols-2">
            <label className="text-sm font-semibold">Employee<select required value={rotationEmployee} onChange={(event) => setRotationEmployee(event.target.value)} className="mt-2 w-full rounded-xl border border-slate-300 bg-white px-3 py-2.5 font-normal"><option value="">Select employee</option>{employees.map((employee) => <option key={employee.id} value={employee.id}>{employee.full_name} ({employee.employee_id})</option>)}</select></label>
            <label className="text-sm font-semibold">Starting shift<select required value={rotationStartingShift} onChange={(event) => setRotationStartingShift(event.target.value as "day" | "night")} className="mt-2 w-full rounded-xl border border-slate-300 bg-white px-3 py-2.5 font-normal"><option value="day">Day</option><option value="night">Night</option></select></label>
            <label className="text-sm font-semibold">Start date<input required type="date" value={rotationStart} onChange={(event) => setRotationStart(event.target.value)} className="mt-2 w-full rounded-xl border border-slate-300 px-3 py-2.5 font-normal" /></label>
            <label className="text-sm font-semibold">End date<input required type="date" value={rotationEnd} onChange={(event) => setRotationEnd(event.target.value)} className="mt-2 w-full rounded-xl border border-slate-300 px-3 py-2.5 font-normal" /></label>
            <label className="text-sm font-semibold">Day shift<select required value={rotationDayShift} onChange={(event) => setRotationDayShift(event.target.value)} className="mt-2 w-full rounded-xl border border-slate-300 bg-white px-3 py-2.5 font-normal"><option value="">Select Day shift</option>{shifts.filter((shift) => !shift.is_overnight).map((shift) => <option key={shift.id} value={shift.id}>{shift.name} ({shift.start_time.slice(0, 5)} - {shift.end_time.slice(0, 5)})</option>)}</select></label>
            <label className="text-sm font-semibold">Night shift<select required value={rotationNightShift} onChange={(event) => setRotationNightShift(event.target.value)} className="mt-2 w-full rounded-xl border border-slate-300 bg-white px-3 py-2.5 font-normal"><option value="">Select Night shift</option>{shifts.filter((shift) => shift.is_overnight).map((shift) => <option key={shift.id} value={shift.id}>{shift.name} ({shift.start_time.slice(0, 5)} - {shift.end_time.slice(0, 5)})</option>)}</select></label>
            <p className="text-sm text-slate-600 md:col-span-2">Day rotation works Monday-Saturday. When changing from Day to Night, Night rotation begins Sunday at 7:00 PM.</p>
            <button className="rounded-xl bg-blue-600 px-4 py-2.5 text-sm font-semibold text-white hover:bg-blue-700 md:col-span-2">Generate Rotation</button>
          </form>
          {rotationFeedback && <p className="border-t border-slate-200 px-5 py-4 text-sm text-emerald-700">{rotationFeedback}</p>}
        </Section>

        <Section title="Shift Assignments" subtitle={`${visibleAssignments.length} assignment${visibleAssignments.length === 1 ? "" : "s"}`}>
          {error ? (
            <div className="p-8 text-center text-red-700"><p>{error}</p><button type="button" onClick={() => void load()} className="mt-4 rounded-xl bg-red-600 px-4 py-2 text-sm font-semibold text-white">Try Again</button></div>
          ) : loading ? (
            <div className="space-y-3 p-6">{Array.from({ length: 6 }).map((_, index) => <div key={index} className="h-16 animate-pulse rounded-xl bg-slate-100" />)}</div>
          ) : visibleAssignments.length ? (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[920px] text-left">
                <thead className="bg-slate-50 text-xs font-semibold uppercase tracking-wide text-slate-500"><tr><th className="px-5 py-3">Employee</th><th className="px-5 py-3">Department</th><th className="px-5 py-3">Shift</th><th className="px-5 py-3">Start Date</th><th className="px-5 py-3">End Date</th><th className="px-5 py-3">Status</th><th className="px-5 py-3" /></tr></thead>
                <tbody className="divide-y divide-slate-100">
                  {visibleAssignments.map((assignment) => (
                    <tr key={assignment.id} className="hover:bg-slate-50">
                      <td className="px-5 py-4"><button type="button" onClick={() => router.push(`/employees/${assignment.employee}`)} className="text-left"><span className="block font-semibold text-slate-900 hover:text-blue-700">{assignment.employee_name}</span><span className="mt-1 block text-sm text-slate-500">{assignment.employee_number}</span></button></td>
                      <td className="px-5 py-4 text-sm text-slate-600">{assignment.department_name || "Not assigned"}</td>
                      <td className="px-5 py-4"><div className="font-semibold text-slate-900">{assignment.shift_name}</div><div className="mt-1 text-sm text-slate-500">{assignment.shift_start_time.slice(0, 5)} - {assignment.shift_end_time.slice(0, 5)}</div></td>
                      <td className="px-5 py-4 text-sm text-slate-600">{formatDate(assignment.start_date)}</td>
                      <td className="px-5 py-4 text-sm text-slate-600">{formatDate(assignment.end_date)}</td>
                      <td className="px-5 py-4"><AssignmentStatus status={assignment.status} /></td>
                      <td className="px-5 py-4 text-right whitespace-nowrap">
                        {assignment.end_date === null && <><button type="button" onClick={() => { setAssignmentToChange(assignment); setShowModal(true); }} className="mr-4 text-sm font-semibold text-blue-600 hover:text-blue-700">Change</button><button type="button" disabled={endingId === assignment.id} onClick={() => void endAssignment(assignment)} className="text-sm font-semibold text-red-600 hover:text-red-700 disabled:text-red-300">{endingId === assignment.id ? "Ending..." : "End"}</button></>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : <div className="p-12 text-center text-slate-500">No shift assignments match the current filters.</div>}
        </Section>
      </main>

      <ShiftAssignmentModal open={showModal} employees={employees} shifts={shifts} activeAssignment={assignmentToChange} onClose={() => setShowModal(false)} onSaved={() => void load()} />
    </div>
  );
}

function AssignmentStatus({ status }: { status: ShiftAssignment["status"] }) {
  const classes = { active: "bg-emerald-100 text-emerald-700", upcoming: "bg-blue-100 text-blue-700", ended: "bg-slate-100 text-slate-700" };
  return <span className={`rounded-full px-2.5 py-1 text-xs font-semibold capitalize ${classes[status]}`}>{status}</span>;
}

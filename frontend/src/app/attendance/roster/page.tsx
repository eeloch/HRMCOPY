"use client";

import { FormEvent, useCallback, useEffect, useEffectEvent, useState } from "react";
import { useRouter } from "next/navigation";

import Sidebar from "@/components/Sidebar";
import type { EmployeeRosterDay, Shift, ShiftEmployee } from "@/components/shifts/types";
import { AppCard, PageHeader, Section } from "@/components/ui";
import { apiFetch, getAccessToken } from "@/lib/api";

function monthRange(value: string) {
  const [year, month] = value.split("-").map(Number);
  const last = new Date(year, month, 0).getDate();
  return {
    start: `${value}-01`,
    end: `${value}-${String(last).padStart(2, "0")}`,
  };
}

export default function RosterPage() {
  const router = useRouter();
  const currentMonth = new Date().toISOString().slice(0, 7);
  const [employees, setEmployees] = useState<ShiftEmployee[]>([]);
  const [shifts, setShifts] = useState<Shift[]>([]);
  const [employee, setEmployee] = useState("");
  const [month, setMonth] = useState(currentMonth);
  const [rows, setRows] = useState<EmployeeRosterDay[]>([]);
  const [missing, setMissing] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [feedback, setFeedback] = useState("");
  const [shift, setShift] = useState("");
  const [workDays, setWorkDays] = useState("6");
  const [restDays, setRestDays] = useState("1");
  const [notes, setNotes] = useState("");
  const [overrideDate, setOverrideDate] = useState("");
  const [overrideStatus, setOverrideStatus] = useState<"work" | "rest">("work");
  const [overrideShift, setOverrideShift] = useState("");
  const [overrideNotes, setOverrideNotes] = useState("");

  const loadSetup = useEffectEvent(async () => {
    try {
      const [employeeResponse, shiftResponse] = await Promise.all([
        apiFetch("/employees/"),
        apiFetch("/attendance/shifts/"),
      ]);
      if (!employeeResponse.ok || !shiftResponse.ok) {
        throw new Error("Unable to load roster setup.");
      }
      const [employeeData, shiftData] = await Promise.all([
        employeeResponse.json(),
        shiftResponse.json(),
      ]);
      setEmployees(employeeData.results || []);
      setShifts(shiftData.results || []);
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "Unable to load roster setup.");
    }
  });

  const loadRoster = useCallback(async () => {
    if (!employee) return;
    setLoading(true);
    setError("");
    try {
      const range = monthRange(month);
      const response = await apiFetch(
        `/attendance/roster/?employee=${employee}&start_date=${range.start}&end_date=${range.end}`,
      );
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.detail || "Unable to load this roster.");
      setRows(data.results || []);
      setMissing(data.completeness?.missing_dates || []);
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "Unable to load this roster.");
    } finally {
      setLoading(false);
    }
  }, [employee, month]);

  useEffect(() => {
    if (!getAccessToken()) {
      router.push("/login");
      return;
    }
    const timer = window.setTimeout(() => void loadSetup(), 0);
    return () => window.clearTimeout(timer);
  }, [router]);

  useEffect(() => {
    if (!employee) return;
    const timer = window.setTimeout(() => void loadRoster(), 0);
    return () => window.clearTimeout(timer);
  }, [employee, loadRoster, month]);

  async function generate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!employee) return;
    setSaving(true);
    setError("");
    try {
      const range = monthRange(month);
      const response = await apiFetch("/attendance/roster/generate/", {
        method: "POST",
        body: JSON.stringify({
          employee: Number(employee),
          start_date: range.start,
          end_date: range.end,
          shift: Number(shift),
          work_days: Number(workDays),
          rest_days: Number(restDays),
          notes,
        }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.detail || "Unable to generate roster.");
      setFeedback(`Roster generated: ${data.summary.created} created, ${data.summary.updated} updated, ${data.summary.skipped} preserved.`);
      await loadRoster();
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : "Unable to generate roster.");
    } finally {
      setSaving(false);
    }
  }

  async function override(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!employee) return;
    setSaving(true);
    setError("");
    try {
      const response = await apiFetch("/attendance/roster/override/", {
        method: "POST",
        body: JSON.stringify({
          employee: Number(employee),
          date: overrideDate,
          status: overrideStatus,
          shift: overrideStatus === "work" ? Number(overrideShift) : null,
          notes: overrideNotes,
        }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.detail || "Unable to save roster override.");
      setFeedback(`Roster date ${data.date} was saved as ${data.status}.`);
      await loadRoster();
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : "Unable to save roster override.");
    } finally {
      setSaving(false);
    }
  }

  function shiftTime(row: EmployeeRosterDay) {
    const rosterShift = row.shift ? shifts.find((item) => item.id === row.shift) : null;
    return rosterShift ? `${rosterShift.start_time.slice(0, 5)}–${rosterShift.end_time.slice(0, 5)}` : "-";
  }

  return (
    <div className="min-h-screen bg-slate-100">
      <Sidebar />
      <main className="ml-64 min-w-0 p-4 md:p-8">
        <PageHeader
          title="Work Roster"
          description="Configure exact work and rest dates. Roster dates control attendance expectations."
          actions={
            <div className="flex flex-wrap gap-2">
              <button type="button" onClick={() => router.push("/attendance/shifts")} className="rounded-xl bg-blue-600 px-4 py-2.5 text-sm font-semibold text-white hover:bg-blue-700">Weekly Rotation</button>
              <button type="button" disabled={!employee || loading} onClick={() => void loadRoster()} className="rounded-xl border border-slate-300 bg-white px-4 py-2.5 text-sm font-semibold text-slate-700 disabled:opacity-50">Refresh</button>
            </div>
          }
        />

        <AppCard className="mb-6">
          <div className="grid gap-3 md:grid-cols-2">
            <label className="text-sm font-semibold text-slate-700">
              Employee
              <select value={employee} onChange={(event) => { setEmployee(event.target.value); setRows([]); setMissing([]); }} className="mt-2 w-full rounded-xl border border-slate-300 bg-white px-3 py-2.5 font-normal">
                <option value="">Select employee</option>
                {employees.map((item) => <option key={item.id} value={item.id}>{item.full_name} ({item.employee_id})</option>)}
              </select>
            </label>
            <label className="text-sm font-semibold text-slate-700">
              Month
              <input type="month" value={month} onChange={(event) => setMonth(event.target.value)} className="mt-2 w-full rounded-xl border border-slate-300 px-3 py-2.5 font-normal" />
            </label>
          </div>
        </AppCard>

        {feedback && <p className="mb-6 rounded-xl bg-emerald-50 p-4 text-sm text-emerald-800">{feedback}</p>}
        {error && <p className="mb-6 rounded-xl bg-red-50 p-4 text-sm text-red-700">{error}</p>}

        <div className="mb-6 rounded-2xl border border-blue-100 bg-blue-50 p-4 text-sm text-blue-900">
          <p className="font-semibold">Roster modes</p>
          <p className="mt-1">Use Fixed / Repeating Pattern for a standard schedule, or open Weekly Rotation for alternating Day and Night weeks.</p>
        </div>

        <div className="grid gap-6 xl:grid-cols-2">
          <Section title="Fixed / Repeating Pattern" subtitle="Choose the repeating work/rest pattern for this employee and range.">
            <form onSubmit={generate} className="grid gap-4 p-5 sm:grid-cols-2">
              <label className="text-sm font-semibold">Shift<select required value={shift} onChange={(event) => setShift(event.target.value)} className="mt-2 w-full rounded-xl border border-slate-300 bg-white px-3 py-2.5 font-normal"><option value="">Select shift</option>{shifts.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
              <label className="text-sm font-semibold">Work days<input required min="1" type="number" value={workDays} onChange={(event) => setWorkDays(event.target.value)} className="mt-2 w-full rounded-xl border border-slate-300 bg-white px-3 py-2.5 font-normal" /></label>
              <label className="text-sm font-semibold">Rest days<input required min="1" type="number" value={restDays} onChange={(event) => setRestDays(event.target.value)} className="mt-2 w-full rounded-xl border border-slate-300 bg-white px-3 py-2.5 font-normal" /></label>
              <label className="text-sm font-semibold">Notes<textarea value={notes} onChange={(event) => setNotes(event.target.value)} className="mt-2 min-h-11 w-full rounded-xl border border-slate-300 p-2.5 font-normal" /></label>
              <button disabled={!employee || saving} className="rounded-xl bg-blue-600 px-4 py-2.5 text-sm font-semibold text-white disabled:bg-blue-300 sm:col-span-2">{saving ? "Saving..." : "Generate Roster"}</button>
            </form>
          </Section>

          <Section title="Manual Override" subtitle="Overrides are preserved when the roster pattern is generated again.">
            <form onSubmit={override} className="grid gap-4 p-5 sm:grid-cols-2">
              <label className="text-sm font-semibold">Date<input required type="date" value={overrideDate} onChange={(event) => setOverrideDate(event.target.value)} className="mt-2 w-full rounded-xl border border-slate-300 bg-white px-3 py-2.5 font-normal" /></label>
              <label className="text-sm font-semibold">Schedule<select value={overrideStatus} onChange={(event) => setOverrideStatus(event.target.value as "work" | "rest")} className="mt-2 w-full rounded-xl border border-slate-300 bg-white px-3 py-2.5 font-normal"><option value="work">Work</option><option value="rest">Rest</option></select></label>
              {overrideStatus === "work" && <label className="text-sm font-semibold sm:col-span-2">Shift<select required value={overrideShift} onChange={(event) => setOverrideShift(event.target.value)} className="mt-2 w-full rounded-xl border border-slate-300 bg-white px-3 py-2.5 font-normal"><option value="">Select shift</option>{shifts.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>}
              <label className="text-sm font-semibold sm:col-span-2">Reason / notes<textarea value={overrideNotes} onChange={(event) => setOverrideNotes(event.target.value)} className="mt-2 min-h-11 w-full rounded-xl border border-slate-300 p-2.5 font-normal" /></label>
              <button disabled={!employee || saving} className="rounded-xl bg-slate-900 px-4 py-2.5 text-sm font-semibold text-white disabled:bg-slate-400 sm:col-span-2">Save Override</button>
            </form>
          </Section>
        </div>

        <Section className="mt-6" title="Roster Calendar" subtitle={missing.length ? `${missing.length} date(s) remain unconfigured.` : rows.length ? "Complete for the selected period." : "Select an employee and load a month."}>
          {loading ? (
            <div className="space-y-3 p-6">{Array.from({ length: 6 }).map((_, index) => <div key={index} className="h-14 animate-pulse rounded-xl bg-slate-100" />)}</div>
          ) : rows.length ? (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[760px] text-left">
                <thead className="bg-slate-50 text-xs font-semibold uppercase tracking-wide text-slate-500"><tr><th className="px-5 py-3">Date</th><th className="px-5 py-3">Status</th><th className="px-5 py-3">Shift</th><th className="px-5 py-3">Shift time</th><th className="px-5 py-3">Source</th><th className="px-5 py-3">Changed by</th><th className="px-5 py-3">Notes</th></tr></thead>
                <tbody className="divide-y divide-slate-100">{rows.map((row) => <tr key={row.id}><td className="px-5 py-3 text-sm text-slate-700">{row.date}</td><td className="px-5 py-3"><span className={`inline-flex rounded-full px-3 py-1 text-sm font-medium ring-1 ${row.status === "work" ? "bg-emerald-50 text-emerald-700 ring-emerald-100" : "bg-slate-100 text-slate-700 ring-slate-200"}`}>{row.status.toUpperCase()}</span></td><td className="px-5 py-3 text-sm font-semibold text-slate-700">{row.shift_name || "-"}</td><td className="px-5 py-3 text-sm text-slate-600">{shiftTime(row)}</td><td className="px-5 py-3 text-sm capitalize text-slate-700">{row.source}</td><td className="px-5 py-3 text-sm text-slate-500">{row.updated_by_name || "-"}</td><td className="px-5 py-3 text-sm text-slate-500">{row.notes || "-"}</td></tr>)}</tbody>
              </table>
            </div>
          ) : <p className="p-12 text-center text-slate-500">No roster dates are configured for this selection.</p>}
          {missing.length > 0 && <p className="border-t border-slate-200 p-4 text-sm text-amber-700">Missing: {missing.join(", ")}</p>}
        </Section>
      </main>
    </div>
  );
}

"use client";

import { useEffect, useEffectEvent, useState } from "react";

import { Section, StatusBadge } from "@/components/ui";
import { apiFetch } from "@/lib/api";

type AttendanceRecord = {
  id: number;
  date: string;
  shift_name: string | null;
  actual_clock_in: string | null;
  actual_clock_out: string | null;
  late_minutes: number;
  early_departure_minutes: number;
  worked_minutes: number;
  overtime_minutes: number;
  operational_status: string;
};

export function EmployeeAttendancePanel({ employeeId }: { employeeId: number }) {
  const [records, setRecords] = useState<AttendanceRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  async function loadRecords() {
    setLoading(true);
    setError("");
    try {
      const response = await apiFetch(`/attendance/records/?employee=${employeeId}`);
      if (!response.ok) throw new Error("Unable to load attendance history.");
      const data: { results?: AttendanceRecord[] } = await response.json();
      setRecords(data.results || []);
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "Unable to load attendance history.");
    } finally {
      setLoading(false);
    }
  }

  const loadOnMount = useEffectEvent(() => { void loadRecords(); });
  useEffect(() => { const timer = window.setTimeout(loadOnMount, 0); return () => window.clearTimeout(timer); }, [employeeId]);

  return <div className="mt-6">
    <Section title="Attendance History" subtitle="Processed attendance records. Raw device punches are shown separately under Biometric." actions={<button type="button" onClick={() => void loadRecords()} disabled={loading} className="rounded-xl border border-slate-300 px-4 py-2 text-sm font-semibold text-slate-700 hover:bg-slate-50 disabled:opacity-50">Refresh</button>}>
      {error ? <PanelError message={error} retry={loadRecords} /> : loading ? <PanelSkeleton /> : records.length ? <div className="overflow-x-auto"><table className="w-full min-w-[1100px] text-left"><thead className="bg-slate-50 text-xs font-semibold uppercase tracking-wide text-slate-500"><tr><th className="px-5 py-3">Date</th><th className="px-5 py-3">Shift</th><th className="px-5 py-3">Clock In</th><th className="px-5 py-3">Clock Out</th><th className="px-5 py-3">Late</th><th className="px-5 py-3">Early Departure</th><th className="px-5 py-3">Worked</th><th className="px-5 py-3">Overtime</th><th className="px-5 py-3">Status</th></tr></thead><tbody className="divide-y divide-slate-100">{records.map((record) => <tr key={record.id} className="hover:bg-slate-50"><td className="px-5 py-4 text-sm font-medium text-slate-800">{formatDate(record.date)}</td><td className="px-5 py-4 text-sm text-slate-600">{record.shift_name || "No shift"}</td><td className="px-5 py-4 text-sm text-slate-600">{formatDateTime(record.actual_clock_in)}</td><td className="px-5 py-4 text-sm text-slate-600">{formatDateTime(record.actual_clock_out)}</td><td className="px-5 py-4 text-sm text-slate-600">{formatMinutes(record.late_minutes)}</td><td className="px-5 py-4 text-sm text-slate-600">{formatMinutes(record.early_departure_minutes)}</td><td className="px-5 py-4 text-sm text-slate-600">{formatMinutes(record.worked_minutes)}</td><td className="px-5 py-4 text-sm text-slate-600">{formatMinutes(record.overtime_minutes)}</td><td className="px-5 py-4"><StatusBadge status={record.operational_status} /></td></tr>)}</tbody></table></div> : <Empty message="No processed attendance records found." />}
    </Section>
  </div>;
}

export function PanelSkeleton() { return <div className="space-y-3 p-6">{Array.from({ length: 5 }).map((_, index) => <div key={index} className="h-14 animate-pulse rounded-xl bg-slate-100" />)}</div>; }
export function PanelError({ message, retry }: { message: string; retry: () => void }) { return <div className="p-8 text-center text-red-700"><p>{message}</p><button type="button" onClick={() => void retry()} className="mt-4 rounded-xl bg-red-600 px-4 py-2.5 text-sm font-semibold text-white">Try Again</button></div>; }
export function Empty({ message }: { message: string }) { return <p className="p-12 text-center text-slate-500">{message}</p>; }
export function formatDate(value: string) { return new Intl.DateTimeFormat("en-GB", { day: "2-digit", month: "short", year: "numeric" }).format(new Date(`${value}T00:00:00`)); }
export function formatDateTime(value: string | null) { return value ? new Date(value).toLocaleString("en-NG", { dateStyle: "medium", timeStyle: "short" }) : "-"; }
export function formatMinutes(value: number) { return value ? `${value} min` : "-"; }

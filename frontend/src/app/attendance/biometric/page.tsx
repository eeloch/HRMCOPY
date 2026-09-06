"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";

import Sidebar from "@/components/Sidebar";
import { AppCard, PageHeader, Section, StatusBadge } from "@/components/ui";
import { apiFetch, getAccessToken } from "@/lib/api";

type BiometricEvent = {
  id: number;
  employee: number;
  employee_id: string;
  employee_name: string;
  department: string | null;
  device_name: string | null;
  device_serial_number: string | null;
  timestamp: string;
  verification_type: string;
  external_event_id: string | null;
};

export default function BiometricPunchesPage() {
  const router = useRouter();
  const [events, setEvents] = useState<BiometricEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [search, setSearch] = useState("");
  const [device, setDevice] = useState("");

  async function loadEvents() {
    setLoading(true);
    setError("");
    try {
      const response = await apiFetch("/attendance/biometric-events/");
      if (!response.ok) throw new Error("Unable to load biometric punches.");
      const data: { results?: BiometricEvent[] } = await response.json();
      setEvents(data.results || []);
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "Unable to load biometric punches.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    if (!getAccessToken()) {
      router.push("/login");
      return;
    }
    const timer = window.setTimeout(() => void loadEvents(), 0);
    return () => window.clearTimeout(timer);
  }, [router]);

  const devices = Array.from(new Set(events.map((event) => event.device_serial_number).filter((value): value is string => Boolean(value)))).sort();
  const term = search.trim().toLowerCase();
  const visible = events.filter((event) => {
    const matchesSearch = !term || [event.employee_id, event.employee_name, event.department || "", event.external_event_id || ""].some((value) => value.toLowerCase().includes(term));
    return matchesSearch && (!device || event.device_serial_number === device);
  });

  return <div className="min-h-screen bg-slate-100"><Sidebar /><main className="ml-64 min-w-0 p-4 md:p-8"><PageHeader title="Biometric Punches" description="Raw biometric punches received from attendance devices. Attendance status is calculated separately by the attendance processing engine." actions={<button type="button" onClick={() => void loadEvents()} disabled={loading} className="rounded-xl border border-slate-300 bg-white px-4 py-2.5 text-sm font-semibold text-slate-700 hover:bg-slate-50 disabled:opacity-50">Refresh</button>} /><AppCard className="mb-6 border-blue-200 bg-blue-50"><p className="text-sm text-blue-800">This is a read-only source feed. Punches are not attendance decisions, payroll entries, or overtime approvals.</p></AppCard>{error ? <AppCard><div className="p-8 text-center text-red-700"><p>{error}</p><button type="button" onClick={() => void loadEvents()} className="mt-4 rounded-xl bg-red-600 px-4 py-2.5 text-sm font-semibold text-white">Try Again</button></div></AppCard> : <Section title="Received Punches" subtitle={`${visible.length} raw punch${visible.length === 1 ? "" : "es"}`}><div className="border-b border-slate-200 p-5"><div className="grid gap-3 md:grid-cols-3"><input type="search" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search employee, department, or event ID..." className="rounded-xl border border-slate-300 px-4 py-3 outline-none focus:ring-2 focus:ring-blue-500 md:col-span-2" /><select value={device} onChange={(event) => setDevice(event.target.value)} className="rounded-xl border border-slate-300 bg-white px-4 py-3"><option value="">All devices</option>{devices.map((item) => <option key={item} value={item}>{item}</option>)}</select></div></div>{loading ? <div className="space-y-3 p-6">{Array.from({ length: 5 }).map((_, index) => <div key={index} className="h-16 animate-pulse rounded-xl bg-slate-100" />)}</div> : visible.length ? <div className="overflow-x-auto"><table className="w-full min-w-[1150px] text-left"><thead className="bg-slate-50 text-xs font-semibold uppercase tracking-wide text-slate-500"><tr><th className="px-5 py-3">Employee ID</th><th className="px-5 py-3">Employee</th><th className="px-5 py-3">Department</th><th className="px-5 py-3">Device</th><th className="px-5 py-3">Device Serial</th><th className="px-5 py-3">Punch Time</th><th className="px-5 py-3">Verification</th><th className="px-5 py-3">External Event ID</th></tr></thead><tbody className="divide-y divide-slate-100">{visible.map((event) => <tr key={event.id} className="hover:bg-slate-50"><td className="px-5 py-4 text-sm font-medium text-slate-700">{event.employee_id}</td><td className="px-5 py-4"><button type="button" onClick={() => router.push(`/employees/${event.employee}`)} className="font-semibold text-blue-700 hover:underline">{event.employee_name}</button></td><td className="px-5 py-4 text-sm text-slate-600">{event.department || "Not assigned"}</td><td className="px-5 py-4 text-sm text-slate-700">{event.device_name || "Unknown"}</td><td className="px-5 py-4 font-mono text-sm text-slate-700">{event.device_serial_number || "Unknown"}</td><td className="px-5 py-4 text-sm text-slate-700">{new Date(event.timestamp).toLocaleString()}</td><td className="px-5 py-4"><StatusBadge status={event.verification_type} /></td><td className="px-5 py-4 font-mono text-xs text-slate-600">{event.external_event_id || "-"}</td></tr>)}</tbody></table></div> : <p className="p-12 text-center text-slate-500">No biometric punches match the current filters.</p>}</Section>}</main></div>;
}

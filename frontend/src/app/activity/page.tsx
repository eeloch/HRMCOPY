"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";

import Sidebar from "@/components/Sidebar";
import { AppCard, PageHeader, Section, StatusBadge } from "@/components/ui";
import { apiFetch, getAccessToken } from "@/lib/api";

type AuditEvent = { id: number; event_type: string; module: string; employee_id: number | null; employee_number: string | null; employee_name: string | null; actor_name: string | null; severity: string; title: string; description: string; created_at: string };
type ActivityResponse = { count: number; page: number; total_pages: number; results: AuditEvent[] };

const modules = ["employees", "leave", "attendance", "documents", "payroll"];
const severities = ["info", "success", "warning", "error"];

export default function ActivityPage() {
  const router = useRouter();
  const [events, setEvents] = useState<AuditEvent[]>([]);
  const [count, setCount] = useState(0);
  const [page, setPage] = useState(1);
  const [totalPages, setTotalPages] = useState(1);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [search, setSearch] = useState("");
  const [employee, setEmployee] = useState("");
  const [module, setModule] = useState("");
  const [severity, setSeverity] = useState("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");

  useEffect(() => {
    if (!getAccessToken()) { router.push("/login"); return; }
    void loadActivity();
  }, [router, page]);

  async function loadActivity(resetPage = false) {
    const nextPage = resetPage ? 1 : page;
    if (resetPage) setPage(1);
    setLoading(true); setError("");
    const params = new URLSearchParams({ page: String(nextPage), page_size: "20" });
    if (search.trim()) params.set("search", search.trim());
    if (employee.trim()) params.set("employee", employee.trim());
    if (module) params.set("module", module);
    if (severity) params.set("severity", severity);
    if (dateFrom) params.set("date_from", dateFrom);
    if (dateTo) params.set("date_to", dateTo);
    try {
      const response = await apiFetch(`/audit/activity/?${params.toString()}`);
      if (!response.ok) throw new Error("Unable to load activity.");
      const data: ActivityResponse = await response.json();
      setEvents(data.results); setCount(data.count); setTotalPages(data.total_pages);
    } catch (loadError) { setError(loadError instanceof Error ? loadError.message : "Unable to load activity."); }
    finally { setLoading(false); }
  }

  function clearFilters() { setSearch(""); setEmployee(""); setModule(""); setSeverity(""); setDateFrom(""); setDateTo(""); setPage(1); setTimeout(() => void loadActivity(true), 0); }

  return <div className="min-h-screen bg-slate-100"><Sidebar /><main className="ml-64 min-w-0 p-4 md:p-8"><PageHeader title="Activity Center" description="A chronological record of completed workforce operations." actions={<button onClick={() => void loadActivity()} className="rounded-xl border border-slate-300 bg-white px-4 py-2.5 text-sm font-medium text-slate-700 hover:bg-slate-50">Refresh</button>} />{error ? <ErrorPanel message={error} retry={loadActivity} /> : <><AppCard className="mb-6" title="Filters"><div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4"><input value={search} onChange={(event) => setSearch(event.target.value)} onKeyDown={(event) => event.key === "Enter" && void loadActivity(true)} placeholder="Search activity..." className="rounded-xl border border-slate-300 px-4 py-3 outline-none focus:ring-2 focus:ring-blue-500 xl:col-span-2" /><input value={employee} onChange={(event) => setEmployee(event.target.value)} onKeyDown={(event) => event.key === "Enter" && void loadActivity(true)} placeholder="Employee ID" className="rounded-xl border border-slate-300 px-4 py-3 outline-none focus:ring-2 focus:ring-blue-500" /><select value={module} onChange={(event) => setModule(event.target.value)} className="rounded-xl border border-slate-300 bg-white px-4 py-3"><option value="">All modules</option>{modules.map((item) => <option key={item} value={item}>{capitalize(item)}</option>)}</select><select value={severity} onChange={(event) => setSeverity(event.target.value)} className="rounded-xl border border-slate-300 bg-white px-4 py-3"><option value="">All severities</option>{severities.map((item) => <option key={item} value={item}>{capitalize(item)}</option>)}</select><input type="date" value={dateFrom} onChange={(event) => setDateFrom(event.target.value)} className="rounded-xl border border-slate-300 px-4 py-3" /><input type="date" value={dateTo} onChange={(event) => setDateTo(event.target.value)} className="rounded-xl border border-slate-300 px-4 py-3" /><div className="flex gap-2"><button onClick={() => void loadActivity(true)} className="rounded-xl bg-blue-600 px-4 py-3 text-sm font-semibold text-white hover:bg-blue-700">Apply</button><button onClick={clearFilters} className="rounded-xl border border-slate-300 px-4 py-3 text-sm font-medium text-slate-700 hover:bg-slate-50">Clear</button></div></div></AppCard><Section title="Activity Timeline" subtitle={`${count} event${count === 1 ? "" : "s"} found.`}>{loading ? <TimelineSkeleton /> : events.length ? <div className="divide-y divide-slate-100">{events.map((event) => <article key={event.id} className="flex gap-4 px-5 py-5"><div className={`mt-1 h-3 w-3 shrink-0 rounded-full ${dotColor(event.severity)}`} /><div className="min-w-0 flex-1"><div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between"><div><p className="font-semibold text-slate-900">{event.title}</p><p className="mt-1 text-sm text-slate-600">{event.description || event.event_type}</p></div><StatusBadge status={event.severity} /></div><div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-sm text-slate-500"><span>{formatDateTime(event.created_at)}</span><span>{capitalize(event.module)}</span>{event.actor_name && <span>By {event.actor_name}</span>}{event.employee_id && <button onClick={() => router.push(`/employees/${event.employee_id}`)} className="font-medium text-blue-700 hover:underline">{event.employee_name || event.employee_number || "View employee"}</button>}</div></div></article>)}</div> : <p className="p-10 text-center text-slate-500">No activity found.</p>}<Pagination page={page} totalPages={totalPages} onChange={setPage} /></Section></>}</main></div>;
}

function Pagination({ page, totalPages, onChange }: { page: number; totalPages: number; onChange: (page: number) => void }) { return <div className="flex items-center justify-between border-t border-slate-200 px-5 py-4"><p className="text-sm text-slate-500">Page {page} of {totalPages}</p><div className="flex gap-2"><button disabled={page === 1} onClick={() => onChange(page - 1)} className="rounded-lg border border-slate-300 px-3 py-2 text-sm disabled:cursor-not-allowed disabled:opacity-50">Previous</button><button disabled={page >= totalPages} onClick={() => onChange(page + 1)} className="rounded-lg border border-slate-300 px-3 py-2 text-sm disabled:cursor-not-allowed disabled:opacity-50">Next</button></div></div>; }
function TimelineSkeleton() { return <div className="space-y-4 p-5">{[1, 2, 3, 4].map((item) => <div key={item} className="h-20 animate-pulse rounded-xl bg-slate-100" />)}</div>; }
function ErrorPanel({ message, retry }: { message: string; retry: () => void }) { return <div className="rounded-2xl border border-red-200 bg-red-50 p-6 text-red-700"><p className="font-semibold">Unable to load activity</p><p className="mt-1 text-sm">{message}</p><button onClick={() => void retry()} className="mt-4 rounded-xl bg-red-600 px-4 py-2.5 text-sm font-semibold text-white">Try Again</button></div>; }
function capitalize(value: string) { return value.charAt(0).toUpperCase() + value.slice(1); }
function dotColor(severity: string) { return { success: "bg-emerald-500", warning: "bg-amber-500", error: "bg-red-500", info: "bg-blue-500" }[severity] || "bg-slate-400"; }
function formatDateTime(value: string) { return new Intl.DateTimeFormat("en-GB", { day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" }).format(new Date(value)); }

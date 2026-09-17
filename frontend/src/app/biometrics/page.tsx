"use client";

import { useEffect, useEffectEvent, useState } from "react";
import { useRouter } from "next/navigation";

import Sidebar from "@/components/Sidebar";
import { MetricCard, PageHeader, Section } from "@/components/ui";
import { apiFetch, getAccessToken } from "@/lib/api";

type NotEnrolled = { id: number; employee_id: string; name: string; department: string; position: string; employment_type: string };
type Overview = { total_active: number; total_enrolled: number; total_not_enrolled: number; not_enrolled: NotEnrolled[] };

function apiMessage(data: unknown, fallback: string) {
  if (!data || typeof data !== "object") return fallback;
  const payload = data as Record<string, unknown>;
  return typeof payload.detail === "string" ? payload.detail : fallback;
}

export default function BiometricsOverviewPage() {
  const router = useRouter();
  const [overview, setOverview] = useState<Overview | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [search, setSearch] = useState("");

  async function load() {
    setLoading(true); setError("");
    try {
      const response = await apiFetch("/attendance/biometrics-overview/");
      if (!response.ok) {
        if (response.status === 403) throw new Error("Your account does not have permission to view this.");
        throw new Error(apiMessage(await response.json().catch(() => null), "Unable to load the biometrics overview."));
      }
      setOverview(await response.json());
    } catch (loadError) {
      const message = loadError instanceof Error ? loadError.message : "Unable to load the biometrics overview.";
      if (message === "Authentication required." || message === "Your session has expired.") { router.push("/login"); return; }
      setError(message);
    } finally { setLoading(false); }
  }

  const loadOnMount = useEffectEvent(() => { void load(); });
  useEffect(() => {
    if (!getAccessToken()) { router.push("/login"); return; }
    const timer = window.setTimeout(loadOnMount, 0);
    return () => window.clearTimeout(timer);
  }, [router]);

  const filtered = overview?.not_enrolled.filter((item) => {
    const term = search.trim().toLowerCase();
    if (!term) return true;
    return item.name.toLowerCase().includes(term) || item.employee_id.toLowerCase().includes(term) || item.department.toLowerCase().includes(term);
  }) ?? [];

  return <div className="min-h-screen bg-slate-100"><Sidebar /><main className="ml-64 min-w-0 p-4 md:p-8">
    <PageHeader
      title="Biometrics Overview"
      description="Active staff compared against who actually has a working biometric identity - so who still needs enrollment doesn't need to be tracked by memory."
      actions={<button type="button" onClick={() => void load()} disabled={loading} className="rounded-xl border border-slate-300 bg-white px-4 py-2.5 text-sm font-semibold text-slate-700 disabled:opacity-50">Refresh</button>}
    />
    {error && <p className="mb-6 rounded-xl bg-red-50 p-4 text-sm text-red-700">{error}</p>}
    {loading ? <div className="h-64 animate-pulse rounded-2xl bg-slate-200" /> : !overview ? null : <>
      <div className="mb-6 grid gap-4 sm:grid-cols-3">
        <MetricCard title="Active Staff" value={overview.total_active} subtitle="Currently active employees" accentColor="#2563eb" />
        <MetricCard title="Enrolled" value={overview.total_enrolled} subtitle="Has a working biometric identity" accentColor="#059669" />
        <MetricCard title="Not Enrolled" value={overview.total_not_enrolled} subtitle="Needs enrollment or linking" accentColor="#dc2626" />
      </div>

      <Section title="Active Staff Not Enrolled" subtitle={`${filtered.length} of ${overview.total_not_enrolled} shown`}>
        <div className="border-b border-slate-200 p-4">
          <input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search name, staff number, or department..." className="w-full max-w-md rounded-xl border border-slate-300 px-4 py-2.5 text-sm outline-none focus:ring-2 focus:ring-blue-500" />
        </div>
        {filtered.length ? <div className="overflow-x-auto"><table className="w-full min-w-[750px] text-left"><thead className="bg-slate-50 text-xs font-semibold uppercase text-slate-500"><tr><th className="px-5 py-3">Employee</th><th className="px-5 py-3">Department</th><th className="px-5 py-3">Position</th><th className="px-5 py-3">Employment Type</th></tr></thead><tbody className="divide-y divide-slate-100">{filtered.map((item) => <tr key={item.id} className="cursor-pointer hover:bg-slate-50" onClick={() => router.push(`/employees/${item.id}`)}><td className="px-5 py-4"><p className="font-semibold text-slate-900">{item.name}</p><p className="text-sm text-slate-500">{item.employee_id}</p></td><td className="px-5 py-4 text-slate-700">{item.department || "-"}</td><td className="px-5 py-4 text-slate-700">{item.position || "-"}</td><td className="px-5 py-4 text-slate-700">{item.employment_type}</td></tr>)}</tbody></table></div> : <p className="p-12 text-center text-slate-500">{overview.total_not_enrolled === 0 ? "Every active employee is enrolled." : "No results match this search."}</p>}
      </Section>
    </>}
  </main></div>;
}

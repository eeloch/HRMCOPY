"use client";

import { useEffect, useEffectEvent, useState } from "react";
import { useRouter } from "next/navigation";

import Sidebar from "@/components/Sidebar";
import { CreatePeriodModal } from "@/components/payroll/CreatePeriodModal";
import type { PayrollPeriod } from "@/components/payroll/types";
import { AppCard, MetricCard, PageHeader, Section, StatusBadge } from "@/components/ui";
import { apiFetch, getAccessToken } from "@/lib/api";

function money(value: string) { return `₦${Number(value || 0).toLocaleString(undefined, { maximumFractionDigits: 0 })}`; }

export default function PayrollPage() {
  const router = useRouter();
  const [periods, setPeriods] = useState<PayrollPeriod[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [showCreate, setShowCreate] = useState(false);

  async function load() {
    setLoading(true); setError("");
    try { const response = await apiFetch("/payroll/periods/"); if (!response.ok) throw new Error(response.status === 403 ? "You do not have permission to view payroll." : "Unable to load payroll periods."); const data = await response.json(); setPeriods(data.results || []); }
    catch (loadError) { setError(loadError instanceof Error ? loadError.message : "Unable to load payroll periods."); } finally { setLoading(false); }
  }
  const loadOnMount = useEffectEvent(() => { void load(); });

  useEffect(() => {
    if (!getAccessToken()) { router.push("/login"); return; }
    const timer = window.setTimeout(loadOnMount, 0);
    return () => window.clearTimeout(timer);
  }, [router]);
  const totalNet = periods.reduce((sum, period) => sum + Number(period.total_net_pay || 0), 0);
  const active = periods.filter((period) => !["approved", "paid", "closed"].includes(period.status)).length;

  return <div className="min-h-screen bg-slate-100"><Sidebar /><main className="ml-64 min-w-0 p-4 md:p-8"><PageHeader title="Payroll" description="Create, generate, and review monthly payroll records." actions={<><button type="button" onClick={() => void load()} className="rounded-xl border border-slate-300 bg-white px-4 py-2.5 text-sm font-semibold text-slate-700 hover:bg-slate-50">Refresh</button><button type="button" onClick={() => setShowCreate(true)} className="rounded-xl bg-blue-600 px-4 py-2.5 text-sm font-semibold text-white hover:bg-blue-700">Create Payroll Period</button></>} /><div className="mb-6 grid gap-4 sm:grid-cols-3"><MetricCard title="Payroll Periods" value={periods.length} subtitle="All monthly payroll runs" accentColor="#2563eb" /><MetricCard title="Open for Review" value={active} subtitle="Draft, processing, or review" accentColor="#d97706" /><MetricCard title="Total Net Pay" value={money(String(totalNet))} subtitle="Across listed periods" accentColor="#059669" /></div>{error ? <AppCard><div className="p-8 text-center text-red-700"><p>{error}</p><button type="button" onClick={() => void load()} className="mt-4 rounded-xl bg-red-600 px-4 py-2.5 text-sm font-semibold text-white">Try Again</button></div></AppCard> : <Section title="Payroll Periods" subtitle="Select a period to review employee payroll records.">{loading ? <div className="space-y-3 p-6">{Array.from({ length: 5 }).map((_, index) => <div key={index} className="h-20 animate-pulse rounded-xl bg-slate-100" />)}</div> : periods.length ? <div className="overflow-x-auto"><table className="w-full min-w-[850px] text-left"><thead className="bg-slate-50 text-xs font-semibold uppercase tracking-wide text-slate-500"><tr><th className="px-5 py-3">Period</th><th className="px-5 py-3">Status</th><th className="px-5 py-3">Employees</th><th className="px-5 py-3">Gross</th><th className="px-5 py-3">Deductions</th><th className="px-5 py-3">Net Pay</th><th className="px-5 py-3" /></tr></thead><tbody className="divide-y divide-slate-100">{periods.map((period) => <tr key={period.id} className="hover:bg-slate-50"><td className="px-5 py-4"><p className="font-semibold text-slate-900">{period.display_name}</p>{period.notes && <p className="mt-1 text-sm text-slate-500">{period.notes}</p>}</td><td className="px-5 py-4"><StatusBadge status={period.status} /></td><td className="px-5 py-4 font-medium text-slate-700">{period.employee_count}</td><td className="px-5 py-4 text-slate-700">{money(period.total_gross_earnings)}</td><td className="px-5 py-4 text-slate-700">{money(period.total_deductions)}</td><td className="px-5 py-4 font-semibold text-slate-900">{money(period.total_net_pay)}</td><td className="px-5 py-4 text-right"><button type="button" onClick={() => router.push(`/payroll/${period.id}`)} className="rounded-xl bg-slate-900 px-3 py-2 text-sm font-semibold text-white hover:bg-slate-700">Review</button></td></tr>)}</tbody></table></div> : <p className="p-12 text-center text-slate-500">No payroll periods have been created yet.</p>}</Section>}</main>{showCreate && <CreatePeriodModal onClose={() => setShowCreate(false)} onCreated={(period) => { setPeriods((current) => [period, ...current]); setShowCreate(false); }} />}</div>;
}

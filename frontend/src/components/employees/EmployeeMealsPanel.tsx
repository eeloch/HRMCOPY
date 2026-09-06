"use client";

import { useEffect, useEffectEvent, useState } from "react";

import { Section, StatusBadge } from "@/components/ui";
import { apiFetch } from "@/lib/api";

type Collection = { id: number; work_date: string; timestamp: string; device: string; sequence: number; entitlement: number; rate: string; status: string };
type Exception = { id: number; work_date: string; entitlement: number; collected_quantity: number; excess_quantity: number; proposed_deduction: string; status: string };
type Profile = { entitlement: { approved: number; effective_from: string | null; effective_to: string | null }; collections: Collection[]; exceptions: Exception[] };

const money = (value: string) => `₦${Number(value || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

export function EmployeeMealsPanel({ employeeId }: { employeeId: number }) {
  const [profile, setProfile] = useState<Profile | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  async function load() {
    setLoading(true); setError("");
    try { const response = await apiFetch(`/meals/employees/${employeeId}/profile/`); if (!response.ok) throw new Error("Unable to load Meals history."); setProfile(await response.json()); }
    catch (loadError) { setError(loadError instanceof Error ? loadError.message : "Unable to load Meals history."); }
    finally { setLoading(false); }
  }
  const loadOnMount = useEffectEvent(() => { void load(); });
  useEffect(() => { const timer = window.setTimeout(loadOnMount, 0); return () => window.clearTimeout(timer); }, [employeeId]);
  if (loading) return <div className="mt-6 rounded-2xl border border-slate-200 bg-white p-8 text-slate-500">Loading Meals history...</div>;
  if (error || !profile) return <div className="mt-6 rounded-2xl border border-red-200 bg-red-50 p-8 text-red-700">{error || "Meals history unavailable."}</div>;
  return <div className="mt-6 space-y-6"><Section title="Meal Entitlement" subtitle="Current approved daily entitlement for scheduled WORK days." actions={<button type="button" onClick={() => void load()} className="rounded-xl border border-slate-300 px-4 py-2 text-sm font-semibold text-slate-700">Refresh</button>}><div className="grid gap-4 p-5 sm:grid-cols-3"><div><p className="text-sm text-slate-500">Approved tickets</p><p className="mt-1 text-2xl font-bold text-slate-900">{profile.entitlement.approved}</p></div><div><p className="text-sm text-slate-500">Effective from</p><p className="mt-1 font-semibold text-slate-800">{profile.entitlement.effective_from || "-"}</p></div><div><p className="text-sm text-slate-500">Effective to</p><p className="mt-1 font-semibold text-slate-800">{profile.entitlement.effective_to || "Open ended"}</p></div></div></Section><Section title="Recent Meal Collections" subtitle="Immutable collection snapshots for this employee.">{profile.collections.length ? <div className="overflow-x-auto"><table className="w-full min-w-[850px] text-left"><thead className="bg-slate-50 text-xs font-semibold uppercase tracking-wide text-slate-500"><tr><th className="px-5 py-3">Work Date</th><th className="px-5 py-3">Sequence</th><th className="px-5 py-3">Entitlement</th><th className="px-5 py-3">Rate</th><th className="px-5 py-3">Device</th><th className="px-5 py-3">Status</th></tr></thead><tbody className="divide-y divide-slate-100">{profile.collections.map((item) => <tr key={item.id}><td className="px-5 py-4 text-sm text-slate-700">{item.work_date}</td><td className="px-5 py-4 text-sm text-slate-700">{item.sequence}</td><td className="px-5 py-4 text-sm text-slate-700">{item.entitlement}</td><td className="px-5 py-4 text-sm text-slate-700">{money(item.rate)}</td><td className="px-5 py-4 text-sm text-slate-600">{item.device}</td><td className="px-5 py-4"><StatusBadge status={item.status} /></td></tr>)}</tbody></table></div> : <p className="p-10 text-center text-slate-500">No meal collections found.</p>}</Section><Section title="Meal Excess History" subtitle="Review and payroll status for excess collections.">{profile.exceptions.length ? <div className="overflow-x-auto"><table className="w-full min-w-[850px] text-left"><thead className="bg-slate-50 text-xs font-semibold uppercase tracking-wide text-slate-500"><tr><th className="px-5 py-3">Work Date</th><th className="px-5 py-3">Collected</th><th className="px-5 py-3">Excess</th><th className="px-5 py-3">Deduction</th><th className="px-5 py-3">Status</th></tr></thead><tbody className="divide-y divide-slate-100">{profile.exceptions.map((item) => <tr key={item.id}><td className="px-5 py-4 text-sm text-slate-700">{item.work_date}</td><td className="px-5 py-4 text-sm text-slate-700">{item.collected_quantity}</td><td className="px-5 py-4 text-sm text-slate-700">{item.excess_quantity}</td><td className="px-5 py-4 text-sm text-slate-700">{money(item.proposed_deduction)}</td><td className="px-5 py-4"><StatusBadge status={item.status} /></td></tr>)}</tbody></table></div> : <p className="p-10 text-center text-slate-500">No meal excess history found.</p>}</Section></div>;
}

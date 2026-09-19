"use client";

import { useEffect, useEffectEvent, useState } from "react";

import { Section, StatusBadge } from "@/components/ui";
import { apiFetch, getCurrentUser } from "@/lib/api";

type Collection = { id: number; work_date: string; timestamp: string; device: string; sequence: number; entitlement: number; rate: string; status: string };
type Exception = { id: number; work_date: string; entitlement: number; collected_quantity: number; excess_quantity: number; proposed_deduction: string; status: string };
type Profile = { today: { work_date: string; roster_status: string; shift: string | null }; suggestion: { tickets_per_work_day: number; reason: string }; entitlement: { approved: number; effective_from: string | null; effective_to: string | null; reason: string }; collections: Collection[]; exceptions: Exception[] };

const money = (value: string) => `₦${Number(value || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

export function EmployeeMealsPanel({ employeeId }: { employeeId: number }) {
  const [profile, setProfile] = useState<Profile | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [canSet, setCanSet] = useState(false);
  const [tickets, setTickets] = useState("1");
  const [from, setFrom] = useState(new Date().toISOString().slice(0, 10));
  const [until, setUntil] = useState("");
  const [reason, setReason] = useState("");
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState("");
  const [saveError, setSaveError] = useState("");

  async function save() {
    setSaving(true); setSaveError(""); setSaved("");
    try {
      const response = await apiFetch("/meals/entitlements/", { method: "POST", body: JSON.stringify({ employee: employeeId, tickets_per_work_day: Number(tickets), effective_from: from, effective_to: until || null, reason: reason.trim() || "Set from the employee profile" }) });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : (Object.values(data).flat()[0] as string) || "Unable to save the meal allocation.");
      setSaved(`Saved: ${tickets} ticket${Number(tickets) === 1 ? "" : "s"} per work day from ${from}.`);
      await load();
    } catch (saveFailure) { setSaveError(saveFailure instanceof Error ? saveFailure.message : "Unable to save the meal allocation."); }
    finally { setSaving(false); }
  }

  async function load() {
    setLoading(true); setError("");
    try { const me = await getCurrentUser(); setCanSet(!!me.permissions.manage_meal_configuration); } catch { setCanSet(false); }
    try { const response = await apiFetch(`/meals/employees/${employeeId}/profile/`); if (!response.ok) throw new Error("Unable to load Meals history."); setProfile(await response.json()); }
    catch (loadError) { setError(loadError instanceof Error ? loadError.message : "Unable to load Meals history."); }
    finally { setLoading(false); }
  }
  const loadOnMount = useEffectEvent(() => { void load(); });
  useEffect(() => { const timer = window.setTimeout(loadOnMount, 0); return () => window.clearTimeout(timer); }, [employeeId]);
  if (loading) return <div className="mt-6 rounded-2xl border border-slate-200 bg-white p-8 text-slate-500">Loading Meals history...</div>;
  if (error || !profile) return <div className="mt-6 rounded-2xl border border-red-200 bg-red-50 p-8 text-red-700">{error || "Meals history unavailable."}</div>;
  return <div className="mt-6 space-y-6"><Section title="Meal Entitlement" subtitle="Current approved daily entitlement for scheduled WORK days." actions={<button type="button" onClick={() => void load()} className="rounded-xl border border-slate-300 px-4 py-2 text-sm font-semibold text-slate-700">Refresh</button>}><div className="grid gap-4 p-5 sm:grid-cols-3"><div><p className="text-sm text-slate-500">Approved tickets</p><p className="mt-1 text-2xl font-bold text-slate-900">{profile.entitlement.approved}</p></div><div><p className="text-sm text-slate-500">Effective from</p><p className="mt-1 font-semibold text-slate-800">{profile.entitlement.effective_from || "-"}</p></div><div><p className="text-sm text-slate-500">Effective to</p><p className="mt-1 font-semibold text-slate-800">{profile.entitlement.effective_to || "Open ended"}</p></div></div></Section><Section title="Meal Allocation" subtitle="How many free tickets this person gets on each work day. A scan only prints a ticket when they have an allocation AND today is a work day on their roster."><div className="space-y-4 p-5"><div className={`rounded-xl p-3 text-sm ${profile.entitlement.approved > 0 ? "bg-emerald-50 text-emerald-800" : "bg-amber-50 text-amber-900"}`}>{profile.entitlement.approved > 0 ? `Allocated ${profile.entitlement.approved} ticket(s) per work day.` : "No meal allocation yet - every scan counts as not entitled."}</div><div className={`rounded-xl p-3 text-sm ${profile.today.roster_status === "work" ? "bg-emerald-50 text-emerald-800" : "bg-amber-50 text-amber-900"}`}>Today ({profile.today.work_date}): {profile.today.roster_status === "work" ? `work day${profile.today.shift ? ` - ${profile.today.shift}` : ""}` : profile.today.roster_status === "rest" ? "REST day - a scan today counts as not entitled." : "not on the roster - a scan today counts as not entitled. Add them to a shift roster."}</div>{profile.suggestion.tickets_per_work_day > 0 && profile.entitlement.approved === 0 && <p className="text-sm text-slate-600">The meal rules suggest <b>{profile.suggestion.tickets_per_work_day}</b> ticket(s) a day for this person.</p>}{canSet ? <div className="grid gap-3 sm:grid-cols-4"><label className="text-sm font-semibold text-slate-700">Tickets per work day<input type="number" min="1" step="1" value={tickets} onChange={(event) => setTickets(event.target.value)} className="mt-1 w-full rounded-xl border border-slate-300 px-3 py-2.5 text-sm font-normal" /></label><label className="text-sm font-semibold text-slate-700">From<input type="date" value={from} onChange={(event) => setFrom(event.target.value)} className="mt-1 w-full rounded-xl border border-slate-300 px-3 py-2.5 text-sm font-normal" /></label><label className="text-sm font-semibold text-slate-700">Until (optional)<input type="date" value={until} onChange={(event) => setUntil(event.target.value)} className="mt-1 w-full rounded-xl border border-slate-300 px-3 py-2.5 text-sm font-normal" /></label><label className="text-sm font-semibold text-slate-700">Note (optional)<input value={reason} onChange={(event) => setReason(event.target.value)} className="mt-1 w-full rounded-xl border border-slate-300 px-3 py-2.5 text-sm font-normal" /></label><div className="sm:col-span-4"><button type="button" disabled={saving || !(Number(tickets) >= 1)} onClick={() => void save()} className="rounded-xl bg-blue-600 px-4 py-2.5 text-sm font-semibold text-white disabled:bg-blue-300">{saving ? "Saving..." : profile.entitlement.approved > 0 ? "Change Allocation" : "Set Allocation"}</button>{saved && <span className="ml-3 text-sm text-emerald-700">{saved}</span>}{saveError && <span className="ml-3 text-sm text-red-700">{saveError}</span>}</div></div> : <p className="text-xs text-slate-500">You can view this allocation but not change it.</p>}</div></Section><Section title="Recent Meal Collections" subtitle="Immutable collection snapshots for this employee.">{profile.collections.length ? <div className="overflow-x-auto"><table className="w-full min-w-[850px] text-left"><thead className="bg-slate-50 text-xs font-semibold uppercase tracking-wide text-slate-500"><tr><th className="px-5 py-3">Work Date</th><th className="px-5 py-3">Sequence</th><th className="px-5 py-3">Entitlement</th><th className="px-5 py-3">Rate</th><th className="px-5 py-3">Device</th><th className="px-5 py-3">Status</th></tr></thead><tbody className="divide-y divide-slate-100">{profile.collections.map((item) => <tr key={item.id}><td className="px-5 py-4 text-sm text-slate-700">{item.work_date}</td><td className="px-5 py-4 text-sm text-slate-700">{item.sequence}</td><td className="px-5 py-4 text-sm text-slate-700">{item.entitlement}</td><td className="px-5 py-4 text-sm text-slate-700">{money(item.rate)}</td><td className="px-5 py-4 text-sm text-slate-600">{item.device}</td><td className="px-5 py-4"><StatusBadge status={item.status} /></td></tr>)}</tbody></table></div> : <p className="p-10 text-center text-slate-500">No meal collections found.</p>}</Section><Section title="Meal Excess History" subtitle="Review and payroll status for excess collections.">{profile.exceptions.length ? <div className="overflow-x-auto"><table className="w-full min-w-[850px] text-left"><thead className="bg-slate-50 text-xs font-semibold uppercase tracking-wide text-slate-500"><tr><th className="px-5 py-3">Work Date</th><th className="px-5 py-3">Collected</th><th className="px-5 py-3">Excess</th><th className="px-5 py-3">Deduction</th><th className="px-5 py-3">Status</th></tr></thead><tbody className="divide-y divide-slate-100">{profile.exceptions.map((item) => <tr key={item.id}><td className="px-5 py-4 text-sm text-slate-700">{item.work_date}</td><td className="px-5 py-4 text-sm text-slate-700">{item.collected_quantity}</td><td className="px-5 py-4 text-sm text-slate-700">{item.excess_quantity}</td><td className="px-5 py-4 text-sm text-slate-700">{money(item.proposed_deduction)}</td><td className="px-5 py-4"><StatusBadge status={item.status} /></td></tr>)}</tbody></table></div> : <p className="p-10 text-center text-slate-500">No meal excess history found.</p>}</Section></div>;
}

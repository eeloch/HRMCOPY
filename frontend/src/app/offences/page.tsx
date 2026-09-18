"use client";

import type { FormEvent } from "react";
import { useEffect, useEffectEvent, useState } from "react";
import { useRouter } from "next/navigation";

import Sidebar from "@/components/Sidebar";
import { PageHeader, Section, StatusBadge } from "@/components/ui";
import { apiFetch, getAccessToken, getCurrentUser, type CurrentUser } from "@/lib/api";

type OffenceType = { id: number; name: string; default_amount: string; description: string; active: boolean };
type Offence = { id: number; employee: number; employee_name: string; employee_number: string; offence_type: number; offence_type_name: string; amount: string; incident_date: string; notes: string; status: string; recorded_by_name: string; reviewer_name: string; reviewed_at: string | null; comment: string; created_at: string };
type Employee = { id: number; employee_id: string; full_name: string };

const inputClass = "w-full rounded-xl border border-slate-300 bg-white px-3 py-2.5 text-sm outline-none focus:ring-2 focus:ring-blue-500";
const money = (value: string) => `₦${Number(value || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
const day = (value: string) => new Intl.DateTimeFormat("en-GB", { day: "2-digit", month: "short", year: "numeric" }).format(new Date(`${value}T00:00:00`));
const offenceFormInitial = () => ({ employee: "", offence_type: "", amount: "", incident_date: "", notes: "" });
const typeFormInitial = () => ({ name: "", default_amount: "", description: "" });

function apiMessage(data: unknown, fallback: string) {
  if (!data || typeof data !== "object") return fallback;
  const payload = data as Record<string, unknown>;
  if (typeof payload.detail === "string") return payload.detail;
  const messages = Object.entries(payload).flatMap(([field, value]) => {
    const message = Array.isArray(value) ? value.join(" ") : typeof value === "string" ? value : "";
    return message ? [`${field.replace(/_/g, " ")}: ${message}`] : [];
  });
  return messages.join(" ") || fallback;
}

export default function OffencesPage() {
  const router = useRouter();
  const [currentUser, setCurrentUser] = useState<CurrentUser | null>(null);
  const [offences, setOffences] = useState<Offence[]>([]);
  const [offenceTypes, setOffenceTypes] = useState<OffenceType[]>([]);
  const [employees, setEmployees] = useState<Employee[]>([]);
  const [loading, setLoading] = useState(true);
  const [acting, setActing] = useState(false);
  const [error, setError] = useState("");
  const [feedback, setFeedback] = useState("");
  const [offenceForm, setOffenceForm] = useState(offenceFormInitial);
  const [typeForm, setTypeForm] = useState(typeFormInitial);
  const [rejectId, setRejectId] = useState<number | null>(null);
  const [reason, setReason] = useState("");

  const canRecord = currentUser?.permissions.record_employee_offences === true;
  const canReview = currentUser?.permissions.review_employee_offences === true;
  const canConfigure = currentUser?.permissions.manage_offence_configuration === true;
  const canAccess = canRecord || canReview || canConfigure;

  async function load() {
    setLoading(true); setError("");
    try {
      const user = await getCurrentUser();
      setCurrentUser(user);
      const hasAccess = user.permissions.record_employee_offences || user.permissions.review_employee_offences || user.permissions.manage_offence_configuration;
      if (!hasAccess) return;
      const [offencesResponse, typesResponse] = await Promise.all([apiFetch("/offences/"), apiFetch("/offences/types/")]);
      if (!offencesResponse.ok) throw new Error(apiMessage(await offencesResponse.json().catch(() => null), "Unable to load offences."));
      if (!typesResponse.ok) throw new Error(apiMessage(await typesResponse.json().catch(() => null), "Unable to load offence types."));
      setOffences((await offencesResponse.json()).results || []);
      setOffenceTypes((await typesResponse.json()).results || []);
      if (user.permissions.record_employee_offences) {
        const employeeResponse = await apiFetch("/employees/");
        setEmployees(employeeResponse.ok ? (await employeeResponse.json()).results || [] : []);
      } else setEmployees([]);
    } catch (loadError) {
      const message = loadError instanceof Error ? loadError.message : "Unable to load Offences.";
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

  async function request(path: string, method: "POST" | "PATCH", body: unknown, success: string) {
    setActing(true); setError("");
    try {
      const response = await apiFetch(path, { method, body: JSON.stringify(body) });
      const data = await response.json().catch(() => null);
      if (!response.ok) throw new Error(apiMessage(data, "The request could not be completed."));
      setFeedback(success); await load(); return true;
    } catch (actionError) { setError(actionError instanceof Error ? actionError.message : "The request could not be completed."); return false; }
    finally { setActing(false); }
  }

  function selectOffenceType(typeId: string) {
    const type = offenceTypes.find((item) => String(item.id) === typeId);
    setOffenceForm({ ...offenceForm, offence_type: typeId, amount: type ? type.default_amount : offenceForm.amount });
  }

  async function createOffence(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const body = { employee: Number(offenceForm.employee), offence_type: Number(offenceForm.offence_type), amount: offenceForm.amount, incident_date: offenceForm.incident_date, notes: offenceForm.notes };
    if (await request("/offences/", "POST", body, "Offence logged and sent for review.")) setOffenceForm(offenceFormInitial());
  }
  async function approve(offence: Offence) {
    await request(`/offences/${offence.id}/approve/`, "POST", {}, "Offence approved - it is deducted from the salary in payroll (immediately if the record exists, otherwise when payroll is generated).");
  }
  async function reject() {
    if (rejectId === null || !reason.trim()) return;
    if (await request(`/offences/${rejectId}/reject/`, "POST", { reason: reason.trim() }, "Offence rejected.")) { setRejectId(null); setReason(""); }
  }
  async function createType(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (await request("/offences/types/", "POST", { ...typeForm, active: true }, "Offence type created.")) setTypeForm(typeFormInitial());
  }
  async function toggleType(type: OffenceType) {
    await request(`/offences/types/${type.id}/`, "PATCH", { active: !type.active }, "Offence type updated.");
  }

  const pending = offences.filter((item) => item.status === "pending");

  return <div className="min-h-screen bg-slate-100"><Sidebar /><main className="ml-64 min-w-0 p-4 md:p-8">
    <PageHeader title="Offences" description="Log staff offences as they happen; a senior reviewer approves before it becomes a payroll deduction." actions={<button type="button" onClick={() => void load()} disabled={loading} className="rounded-xl border border-slate-300 bg-white px-4 py-2.5 text-sm font-semibold text-slate-700 disabled:opacity-50">Refresh</button>} />
    {feedback && <p className="mb-6 rounded-xl bg-emerald-50 p-4 text-sm text-emerald-800">{feedback}</p>}{error && <p className="mb-6 rounded-xl bg-red-50 p-4 text-sm text-red-700">{error}</p>}
    {loading ? <div className="h-64 animate-pulse rounded-2xl bg-slate-200" /> : !canAccess ? <Section title="Offences Access" subtitle="Your account does not have an Offences capability."><p className="p-8 text-sm text-slate-600">Ask an administrator to grant an Offences permission if you need to log or review offences.</p></Section> : <>

      {canRecord && <Section title="Log Offence" subtitle="Record an incident the day it happens. It stays pending until approved.">
        <form onSubmit={createOffence} className="grid gap-3 p-5 md:grid-cols-3">
          <select required value={offenceForm.employee} onChange={(event) => setOffenceForm({ ...offenceForm, employee: event.target.value })} className={inputClass}><option value="">Select employee</option>{employees.map((employee) => <option key={employee.id} value={employee.id}>{employee.employee_id} - {employee.full_name}</option>)}</select>
          <select required value={offenceForm.offence_type} onChange={(event) => selectOffenceType(event.target.value)} className={inputClass}><option value="">Select offence type</option>{offenceTypes.filter((type) => type.active).map((type) => <option key={type.id} value={type.id}>{type.name} ({money(type.default_amount)})</option>)}</select>
          <input required type="date" value={offenceForm.incident_date} onChange={(event) => setOffenceForm({ ...offenceForm, incident_date: event.target.value })} className={inputClass} />
          <input required type="number" min="0.01" step="0.01" value={offenceForm.amount} onChange={(event) => setOffenceForm({ ...offenceForm, amount: event.target.value })} placeholder="Fine amount (NGN)" className={inputClass} />
          <textarea value={offenceForm.notes} onChange={(event) => setOffenceForm({ ...offenceForm, notes: event.target.value })} placeholder="Notes (optional)" className={`${inputClass} md:col-span-2 min-h-16`} />
          <div className="md:col-span-3 text-right"><button disabled={acting} className="rounded-xl bg-blue-600 px-4 py-2.5 text-sm font-semibold text-white disabled:opacity-50">Log Offence</button></div>
        </form>
      </Section>}

      {canReview && <Section className="mt-6" title="Pending Review" subtitle={`${pending.length} offence(s) awaiting a decision.`}>
        {pending.length ? <div className="overflow-x-auto"><table className="w-full min-w-[900px] text-left"><thead className="bg-slate-50 text-xs font-semibold uppercase text-slate-500"><tr><th className="px-5 py-3">Employee</th><th className="px-5 py-3">Offence</th><th className="px-5 py-3">Date</th><th className="px-5 py-3">Amount</th><th className="px-5 py-3">Recorded By</th><th className="px-5 py-3">Actions</th></tr></thead><tbody className="divide-y divide-slate-100">{pending.map((item) => <tr key={item.id}><td className="px-5 py-4"><p className="font-medium">{item.employee_name}</p><p className="text-xs text-slate-500">{item.employee_number}</p></td><td className="px-5 py-4">{item.offence_type_name}{item.notes && <p className="text-xs text-slate-500">{item.notes}</p>}</td><td className="px-5 py-4 text-sm text-slate-600">{day(item.incident_date)}</td><td className="px-5 py-4 font-semibold">{money(item.amount)}</td><td className="px-5 py-4 text-sm text-slate-600">{item.recorded_by_name || "-"}</td><td className="px-5 py-4"><div className="flex gap-2"><button type="button" disabled={acting} onClick={() => void approve(item)} className="rounded-lg bg-emerald-600 px-3 py-2 text-xs font-semibold text-white disabled:opacity-50">Approve</button><button type="button" disabled={acting} onClick={() => setRejectId(item.id)} className="rounded-lg border border-red-200 px-3 py-2 text-xs font-semibold text-red-700">Reject</button></div></td></tr>)}</tbody></table></div> : <p className="p-10 text-center text-sm text-slate-500">No offences pending review.</p>}
      </Section>}

      {canConfigure && <Section className="mt-6" title="Offence Types" subtitle="Editable catalog with a standard fine amount, overridable per incident.">
        <form onSubmit={createType} className="grid gap-3 border-b border-slate-200 p-5 md:grid-cols-4"><input required value={typeForm.name} onChange={(event) => setTypeForm({ ...typeForm, name: event.target.value })} placeholder="Offence type name" className={inputClass} /><input required type="number" min="0.01" step="0.01" value={typeForm.default_amount} onChange={(event) => setTypeForm({ ...typeForm, default_amount: event.target.value })} placeholder="Standard amount (NGN)" className={inputClass} /><input value={typeForm.description} onChange={(event) => setTypeForm({ ...typeForm, description: event.target.value })} placeholder="Description (optional)" className={inputClass} /><button disabled={acting} className="rounded-xl bg-blue-600 px-4 py-2.5 text-sm font-semibold text-white">Add Type</button></form>
        {offenceTypes.length ? <div className="overflow-x-auto"><table className="w-full min-w-[650px] text-left"><thead className="bg-slate-50 text-xs font-semibold uppercase text-slate-500"><tr><th className="px-5 py-3">Name</th><th className="px-5 py-3">Standard Amount</th><th className="px-5 py-3">Description</th><th className="px-5 py-3">Status</th><th className="px-5 py-3">Actions</th></tr></thead><tbody className="divide-y divide-slate-100">{offenceTypes.map((type) => <tr key={type.id}><td className="px-5 py-4 font-medium">{type.name}</td><td className="px-5 py-4">{money(type.default_amount)}</td><td className="px-5 py-4 text-sm text-slate-600">{type.description || "-"}</td><td className="px-5 py-4"><StatusBadge status={type.active ? "active" : "inactive"} /></td><td className="px-5 py-4"><button type="button" disabled={acting} onClick={() => void toggleType(type)} className="rounded-lg border border-slate-300 px-3 py-2 text-xs font-semibold">{type.active ? "Deactivate" : "Activate"}</button></td></tr>)}</tbody></table></div> : <p className="p-10 text-center text-sm text-slate-500">No offence types configured.</p>}
      </Section>}

      <Section className="mt-6" title="History" subtitle="All logged offences.">
        {offences.length ? <div className="overflow-x-auto"><table className="w-full min-w-[950px] text-left"><thead className="bg-slate-50 text-xs font-semibold uppercase text-slate-500"><tr><th className="px-5 py-3">Employee</th><th className="px-5 py-3">Offence</th><th className="px-5 py-3">Date</th><th className="px-5 py-3">Amount</th><th className="px-5 py-3">Status</th><th className="px-5 py-3">Reviewer</th><th className="px-5 py-3">Comment</th></tr></thead><tbody className="divide-y divide-slate-100">{offences.map((item) => <tr key={item.id}><td className="px-5 py-4"><p className="font-medium">{item.employee_name}</p><p className="text-xs text-slate-500">{item.employee_number}</p></td><td className="px-5 py-4">{item.offence_type_name}</td><td className="px-5 py-4 text-sm text-slate-600">{day(item.incident_date)}</td><td className="px-5 py-4">{money(item.amount)}</td><td className="px-5 py-4"><StatusBadge status={item.status} /></td><td className="px-5 py-4 text-sm text-slate-600">{item.reviewer_name || "-"}</td><td className="px-5 py-4 text-sm text-slate-600">{item.comment || "-"}</td></tr>)}</tbody></table></div> : <p className="p-10 text-center text-sm text-slate-500">No offences logged yet.</p>}
      </Section>
    </>}
  </main>{canReview && rejectId !== null && <div className="fixed inset-0 z-20 flex items-center justify-center bg-slate-950/40 p-4"><div className="w-full max-w-lg rounded-2xl bg-white p-6 shadow-xl"><h2 className="text-xl font-bold">Reject Offence</h2><p className="mt-2 text-sm text-slate-500">A reason is required and kept in the audit trail.</p><textarea autoFocus value={reason} onChange={(event) => setReason(event.target.value)} className="mt-5 min-h-32 w-full rounded-xl border border-slate-300 p-3 text-sm" placeholder="Enter rejection reason" /><div className="mt-5 flex justify-end gap-3"><button type="button" onClick={() => { setRejectId(null); setReason(""); }} className="rounded-xl border border-slate-300 px-4 py-2.5 text-sm font-semibold">Back</button><button type="button" disabled={acting || !reason.trim()} onClick={() => void reject()} className="rounded-xl bg-red-600 px-4 py-2.5 text-sm font-semibold text-white">Reject Offence</button></div></div></div>}</div>;
}

"use client";

import type { FormEvent } from "react";
import { Fragment, useEffect, useEffectEvent, useMemo, useState } from "react";
import { useRouter } from "next/navigation";

import Sidebar from "@/components/Sidebar";
import { PageHeader, Section, StatusBadge } from "@/components/ui";
import { apiFetch, getAccessToken, getCurrentUser, type CurrentUser } from "@/lib/api";

type OffenceType = {
  id: number; category: string; name: string; default_amount: string; description: string;
  penalty_first: string; penalty_second: string; penalty_third: string;
  amount_first: string | null; amount_second: string | null; amount_third: string | null; active: boolean;
};
type RewardType = { id: number; category: string; name: string; reward_first: string; reward_second: string; auto_rule: string; auto_rule_label: string; active: boolean };
type Offence = { id: number; employee: number; employee_name: string; employee_number: string; offence_type: number; offence_type_name: string; offence_category: string; amount: string; occurrence: number; penalty_text: string; incident_date: string; notes: string; status: string; recorded_by_name: string; reviewer_name: string; reviewed_at: string | null; comment: string; created_at: string };
type Employee = { id: number; employee_id: string; full_name: string };
type Preview = { occurrence: number; penalty_text: string; amount: string | null };

const POLICY_NOTE = "Suspension is unpaid and observed outside of the company premises. You must leave the hostel during your suspension. Any other offence not stated above is at the discretion of the disciplinary committee.";

const inputClass = "w-full rounded-xl border border-slate-300 bg-white px-3 py-2.5 text-sm outline-none focus:ring-2 focus:ring-blue-500";
const money = (value: string | number | null) => `₦${Number(value || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
const day = (value: string) => new Intl.DateTimeFormat("en-GB", { day: "2-digit", month: "short", year: "numeric" }).format(new Date(`${value}T00:00:00`));
const ordinal = (n: number) => (n === 1 ? "1st" : n === 2 ? "2nd" : n === 3 ? "3rd" : `${n}th`);
const offenceFormInitial = () => ({ employee: "", offence_type: "", amount: "", incident_date: "", notes: "" });
const typeFormInitial = () => ({ category: "", name: "", penalty_first: "", penalty_second: "", penalty_third: "", amount_first: "", amount_second: "", amount_third: "" });

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

function groupByCategory<T extends { category: string }>(items: T[]) {
  const groups: { category: string; items: T[] }[] = [];
  for (const item of items) {
    const name = item.category || "Other";
    const group = groups.find((entry) => entry.category === name);
    if (group) group.items.push(item); else groups.push({ category: name, items: [item] });
  }
  return groups;
}

const penaltyCell = (text: string) => (text && text.trim().toUpperCase() !== "NIL" ? text : <span className="text-slate-400">-</span>);

export default function OffencesAndRewardsPage() {
  const router = useRouter();
  const [currentUser, setCurrentUser] = useState<CurrentUser | null>(null);
  const [tab, setTab] = useState<"offences" | "rewards">("offences");
  const [offences, setOffences] = useState<Offence[]>([]);
  const [offenceTypes, setOffenceTypes] = useState<OffenceType[]>([]);
  const [rewardTypes, setRewardTypes] = useState<RewardType[]>([]);
  const [employees, setEmployees] = useState<Employee[]>([]);
  const [loading, setLoading] = useState(true);
  const [acting, setActing] = useState(false);
  const [error, setError] = useState("");
  const [feedback, setFeedback] = useState("");
  const [offenceForm, setOffenceForm] = useState(offenceFormInitial);
  const [typeForm, setTypeForm] = useState(typeFormInitial);
  const [rejectId, setRejectId] = useState<number | null>(null);
  const [reason, setReason] = useState("");
  const [preview, setPreview] = useState<Preview | null>(null);
  const [policySearch, setPolicySearch] = useState("");

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
      const [offencesResponse, typesResponse, rewardsResponse] = await Promise.all([apiFetch("/offences/"), apiFetch("/offences/types/"), apiFetch("/offences/reward-types/")]);
      if (!offencesResponse.ok) throw new Error(apiMessage(await offencesResponse.json().catch(() => null), "Unable to load offences."));
      if (!typesResponse.ok) throw new Error(apiMessage(await typesResponse.json().catch(() => null), "Unable to load offence types."));
      if (!rewardsResponse.ok) throw new Error(apiMessage(await rewardsResponse.json().catch(() => null), "Unable to load rewards."));
      setOffences((await offencesResponse.json()).results || []);
      setOffenceTypes((await typesResponse.json()).results || []);
      setRewardTypes((await rewardsResponse.json()).results || []);
      if (user.permissions.record_employee_offences) {
        const employeeResponse = await apiFetch("/employees/?status=active");
        setEmployees(employeeResponse.ok ? (await employeeResponse.json()).results || [] : []);
      } else setEmployees([]);
    } catch (loadError) {
      const message = loadError instanceof Error ? loadError.message : "Unable to load Offences and Rewards.";
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

  // What the policy says for this person doing this offence now (1st, 2nd, 3rd time...), before it is logged.
  const previewKey = `${offenceForm.employee}:${offenceForm.offence_type}`;
  const loadPreview = useEffectEvent(async (employee: string, offenceType: string) => {
    try {
      const response = await apiFetch(`/offences/penalty-preview/?employee=${employee}&offence_type=${offenceType}`);
      setPreview(response.ok ? await response.json() : null);
    } catch { setPreview(null); }
  });
  useEffect(() => {
    const [employee, offenceType] = previewKey.split(":");
    if (!employee || !offenceType) return;
    const timer = window.setTimeout(() => void loadPreview(employee, offenceType), 0);
    return () => window.clearTimeout(timer);
  }, [previewKey]);
  const shownPreview = offenceForm.employee && offenceForm.offence_type ? preview : null;

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

  async function createOffence(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const body = { employee: Number(offenceForm.employee), offence_type: Number(offenceForm.offence_type), ...(offenceForm.amount !== "" ? { amount: offenceForm.amount } : {}), incident_date: offenceForm.incident_date, notes: offenceForm.notes };
    if (await request("/offences/", "POST", body, "Offence logged and sent for review.")) { setOffenceForm(offenceFormInitial()); setPreview(null); }
  }
  async function approve(offence: Offence) {
    await request(`/offences/${offence.id}/approve/`, "POST", {}, Number(offence.amount) > 0 ? "Offence approved - it is deducted from the salary in payroll (immediately if the record exists, otherwise when payroll is generated)." : "Offence approved and recorded. The penalty is not money, so nothing is deducted.");
  }
  async function reject() {
    if (rejectId === null || !reason.trim()) return;
    if (await request(`/offences/${rejectId}/reject/`, "POST", { reason: reason.trim() }, "Offence rejected.")) { setRejectId(null); setReason(""); }
  }
  async function createType(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const amount = (value: string) => (value === "" ? null : value);
    const body = { ...typeForm, amount_first: amount(typeForm.amount_first), amount_second: amount(typeForm.amount_second), amount_third: amount(typeForm.amount_third), default_amount: typeForm.amount_first || "0", active: true };
    if (await request("/offences/types/", "POST", body, "Offence added to the policy.")) setTypeForm(typeFormInitial());
  }
  async function toggleType(type: OffenceType) {
    await request(`/offences/types/${type.id}/`, "PATCH", { active: !type.active }, "Offence updated.");
  }

  const pending = offences.filter((item) => item.status === "pending");
  const activeTypes = useMemo(() => offenceTypes.filter((type) => type.active), [offenceTypes]);
  const policyGroups = useMemo(() => {
    const term = policySearch.trim().toLowerCase();
    const shown = term ? offenceTypes.filter((type) => `${type.category} ${type.name}`.toLowerCase().includes(term)) : offenceTypes;
    return groupByCategory(shown);
  }, [offenceTypes, policySearch]);
  const rewardGroups = useMemo(() => groupByCategory(rewardTypes), [rewardTypes]);
  const penaltyLabel = (item: Offence) => (
    <>
      <span className="font-medium">{ordinal(item.occurrence)} time</span>
      <span className="block text-xs text-slate-500">{item.penalty_text || "As decided by management"}</span>
    </>
  );
  const amountLabel = (value: string) => (Number(value) > 0 ? <span className="font-semibold">{money(value)}</span> : <span className="text-xs text-slate-500">No deduction</span>);

  const tabButton = (key: "offences" | "rewards", label: string) => (
    <button type="button" onClick={() => setTab(key)} className={`rounded-xl px-4 py-2.5 text-sm font-semibold ${tab === key ? "bg-blue-600 text-white shadow-sm" : "bg-white text-slate-600 ring-1 ring-slate-200 hover:bg-slate-50"}`}>{label}</button>
  );

  return <div className="min-h-screen bg-slate-100"><Sidebar /><main className="ml-64 min-w-0 p-4 md:p-8">
    <PageHeader title="Offences and Rewards" description="The company Disciplinary Action Policy: log offences with the right penalty for a first, second or third time, and see the rewards staff can earn." actions={<button type="button" onClick={() => void load()} disabled={loading} className="rounded-xl border border-slate-300 bg-white px-4 py-2.5 text-sm font-semibold text-slate-700 disabled:opacity-50">Refresh</button>} />
    {feedback && <p className="mb-6 rounded-xl bg-emerald-50 p-4 text-sm text-emerald-800">{feedback}</p>}{error && <p className="mb-6 rounded-xl bg-red-50 p-4 text-sm text-red-700">{error}</p>}
    {loading ? <div className="h-64 animate-pulse rounded-2xl bg-slate-200" /> : !canAccess ? <Section title="Offences and Rewards Access" subtitle="Your account does not have an Offences capability."><p className="p-8 text-sm text-slate-600">Ask an administrator to grant an Offences permission if you need to log or review offences.</p></Section> : <>

      <div className="mb-6 flex gap-2">{tabButton("offences", "Offences")}{tabButton("rewards", "Rewards")}</div>

      {tab === "offences" && <>
        {canRecord && <Section title="Log Offence" subtitle="Record an incident the day it happens. The penalty follows the policy and how many times the person has done it before. It stays pending until approved.">
          <form onSubmit={createOffence} className="grid gap-3 p-5 md:grid-cols-3">
            <select required value={offenceForm.employee} onChange={(event) => setOffenceForm({ ...offenceForm, employee: event.target.value })} className={inputClass}><option value="">Select employee</option>{employees.map((employee) => <option key={employee.id} value={employee.id}>{employee.employee_id} - {employee.full_name}</option>)}</select>
            <select required value={offenceForm.offence_type} onChange={(event) => setOffenceForm({ ...offenceForm, offence_type: event.target.value })} className={inputClass}><option value="">Select offence</option>{groupByCategory(activeTypes).map((group) => <optgroup key={group.category} label={group.category}>{group.items.map((type) => <option key={type.id} value={type.id}>{type.name}</option>)}</optgroup>)}</select>
            <input required type="date" value={offenceForm.incident_date} onChange={(event) => setOffenceForm({ ...offenceForm, incident_date: event.target.value })} className={inputClass} />
            {shownPreview && <div className="rounded-xl border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900 md:col-span-3"><p><span className="font-semibold">This will be their {ordinal(shownPreview.occurrence)} time.</span> Policy penalty: <span className="font-semibold">{shownPreview.penalty_text || "as decided by management"}</span>.</p>{shownPreview.amount === null && <p className="mt-1 text-xs">No fixed amount in the policy. Leave the amount blank to record it as a non-money penalty, or enter an amount if money is to be deducted (for example a share of a repair cost or days of salary).</p>}</div>}
            <input type="number" min="0" step="0.01" value={offenceForm.amount} onChange={(event) => setOffenceForm({ ...offenceForm, amount: event.target.value })} placeholder={shownPreview?.amount ? `Amount (NGN) - policy: ${money(shownPreview.amount)}` : "Amount to deduct (NGN) - optional"} className={inputClass} />
            <textarea value={offenceForm.notes} onChange={(event) => setOffenceForm({ ...offenceForm, notes: event.target.value })} placeholder="Notes (optional)" className={`${inputClass} md:col-span-2 min-h-16`} />
            <div className="md:col-span-3 text-right"><button disabled={acting} className="rounded-xl bg-blue-600 px-4 py-2.5 text-sm font-semibold text-white disabled:opacity-50">Log Offence</button></div>
          </form>
        </Section>}

        {canReview && <Section className="mt-6" title="Pending Review" subtitle={`${pending.length} offence(s) awaiting a decision.`}>
          {pending.length ? <div className="overflow-x-auto"><table className="w-full min-w-[900px] text-left"><thead className="bg-slate-50 text-xs font-semibold uppercase text-slate-500"><tr><th className="px-5 py-3">Employee</th><th className="px-5 py-3">Offence</th><th className="px-5 py-3">Date</th><th className="px-5 py-3">Penalty</th><th className="px-5 py-3">Deduction</th><th className="px-5 py-3">Recorded By</th><th className="px-5 py-3">Actions</th></tr></thead><tbody className="divide-y divide-slate-100">{pending.map((item) => <tr key={item.id}><td className="px-5 py-4"><p className="font-medium">{item.employee_name}</p><p className="text-xs text-slate-500">{item.employee_number}</p></td><td className="px-5 py-4">{item.offence_type_name}<p className="text-xs text-slate-500">{item.offence_category}</p>{item.notes && <p className="text-xs text-slate-500">{item.notes}</p>}</td><td className="px-5 py-4 text-sm text-slate-600">{day(item.incident_date)}</td><td className="px-5 py-4 text-sm">{penaltyLabel(item)}</td><td className="px-5 py-4">{amountLabel(item.amount)}</td><td className="px-5 py-4 text-sm text-slate-600">{item.recorded_by_name || "-"}</td><td className="px-5 py-4"><div className="flex gap-2"><button type="button" disabled={acting} onClick={() => void approve(item)} className="rounded-lg bg-emerald-600 px-3 py-2 text-xs font-semibold text-white disabled:opacity-50">Approve</button><button type="button" disabled={acting} onClick={() => setRejectId(item.id)} className="rounded-lg border border-red-200 px-3 py-2 text-xs font-semibold text-red-700">Reject</button></div></td></tr>)}</tbody></table></div> : <p className="p-10 text-center text-sm text-slate-500">No offences pending review.</p>}
        </Section>}

        <Section className="mt-6" title="Disciplinary Action Policy" subtitle="What happens the first, second and third time. Amounts shown are fixed sums; the rest is decided case by case.">
          <div className="border-b border-slate-200 p-4"><input value={policySearch} onChange={(event) => setPolicySearch(event.target.value)} placeholder="Search offences..." className="w-full max-w-md rounded-xl border border-slate-300 px-4 py-2.5 text-sm outline-none focus:ring-2 focus:ring-blue-500" /></div>
          {policyGroups.length ? <div className="overflow-x-auto"><table className="w-full min-w-[950px] text-left"><thead className="bg-slate-50 text-xs font-semibold uppercase text-slate-500"><tr><th className="px-5 py-3">Offence</th><th className="px-5 py-3">1st time</th><th className="px-5 py-3">2nd time</th><th className="px-5 py-3">3rd time</th>{canConfigure && <th className="px-5 py-3">Status</th>}</tr></thead><tbody className="divide-y divide-slate-100">{policyGroups.map((group) => <Fragment key={group.category}><tr className="bg-slate-100"><td colSpan={canConfigure ? 5 : 4} className="px-5 py-2 text-xs font-bold uppercase tracking-wide text-slate-600">{group.category}</td></tr>{group.items.map((type) => <tr key={type.id} className={type.active ? "" : "bg-slate-50 text-slate-400"}><td className="px-5 py-3 font-medium">{type.name}</td><td className="px-5 py-3 text-sm">{penaltyCell(type.penalty_first)}</td><td className="px-5 py-3 text-sm">{penaltyCell(type.penalty_second)}</td><td className="px-5 py-3 text-sm">{penaltyCell(type.penalty_third)}</td>{canConfigure && <td className="px-5 py-3"><button type="button" disabled={acting} onClick={() => void toggleType(type)} className="rounded-lg border border-slate-300 px-3 py-1.5 text-xs font-semibold">{type.active ? "Deactivate" : "Activate"}</button></td>}</tr>)}</Fragment>)}</tbody></table></div> : <p className="p-10 text-center text-sm text-slate-500">{offenceTypes.length ? "No offences match this search." : "No offences are set up yet."}</p>}
          <p className="border-t border-slate-200 bg-slate-50 p-4 text-xs text-slate-600"><span className="font-semibold">Note: </span>{POLICY_NOTE}</p>
          {canConfigure && <form onSubmit={createType} className="grid gap-3 border-t border-slate-200 p-5 md:grid-cols-4"><p className="text-sm font-semibold text-slate-700 md:col-span-4">Add an offence to the policy</p><input value={typeForm.category} onChange={(event) => setTypeForm({ ...typeForm, category: event.target.value })} placeholder="Category (e.g. Safety)" className={inputClass} /><input required value={typeForm.name} onChange={(event) => setTypeForm({ ...typeForm, name: event.target.value })} placeholder="Offence" className={`${inputClass} md:col-span-3`} />{(["first", "second", "third"] as const).map((tier) => <Fragment key={tier}><input value={typeForm[`penalty_${tier}`]} onChange={(event) => setTypeForm({ ...typeForm, [`penalty_${tier}`]: event.target.value })} placeholder={`Penalty - ${tier} time`} className={inputClass} /><input type="number" min="0" step="0.01" value={typeForm[`amount_${tier}`]} onChange={(event) => setTypeForm({ ...typeForm, [`amount_${tier}`]: event.target.value })} placeholder="Fixed amount (optional)" className={inputClass} /></Fragment>)}<div className="md:col-span-4 text-right"><button disabled={acting} className="rounded-xl bg-blue-600 px-4 py-2.5 text-sm font-semibold text-white disabled:opacity-50">Add to Policy</button></div></form>}
        </Section>

        <Section className="mt-6" title="History" subtitle="All logged offences.">
          {offences.length ? <div className="overflow-x-auto"><table className="w-full min-w-[1000px] text-left"><thead className="bg-slate-50 text-xs font-semibold uppercase text-slate-500"><tr><th className="px-5 py-3">Employee</th><th className="px-5 py-3">Offence</th><th className="px-5 py-3">Date</th><th className="px-5 py-3">Penalty</th><th className="px-5 py-3">Deduction</th><th className="px-5 py-3">Status</th><th className="px-5 py-3">Reviewer</th><th className="px-5 py-3">Comment</th></tr></thead><tbody className="divide-y divide-slate-100">{offences.map((item) => <tr key={item.id}><td className="px-5 py-4"><p className="font-medium">{item.employee_name}</p><p className="text-xs text-slate-500">{item.employee_number}</p></td><td className="px-5 py-4">{item.offence_type_name}</td><td className="px-5 py-4 text-sm text-slate-600">{day(item.incident_date)}</td><td className="px-5 py-4 text-sm">{penaltyLabel(item)}</td><td className="px-5 py-4">{amountLabel(item.amount)}</td><td className="px-5 py-4"><StatusBadge status={item.status} /></td><td className="px-5 py-4 text-sm text-slate-600">{item.reviewer_name || "-"}</td><td className="px-5 py-4 text-sm text-slate-600">{item.comment || "-"}</td></tr>)}</tbody></table></div> : <p className="p-10 text-center text-sm text-slate-500">No offences logged yet.</p>}
        </Section>
      </>}

      {tab === "rewards" && <Section title="Rewards" subtitle="What staff can earn under the policy: the first time and the second time.">
        <div className="border-b border-slate-200 bg-blue-50 p-4 text-sm text-blue-900"><p><span className="font-semibold">Attendance and punctuality rewards are proposed automatically.</span> At the start of each month the app checks last month&apos;s attendance and proposes &ldquo;No absence&rdquo; and &ldquo;No lateness&rdquo; for everyone who earned them. The people who approve bonuses are notified, and each reward is approved in Payroll &gt; Bonuses before it is added to pay.</p><button type="button" onClick={() => router.push("/bonuses")} className="mt-3 rounded-xl bg-blue-600 px-4 py-2 text-sm font-semibold text-white hover:bg-blue-700">Open Bonuses</button></div>
        {rewardGroups.length ? <div className="overflow-x-auto"><table className="w-full min-w-[800px] text-left"><thead className="bg-slate-50 text-xs font-semibold uppercase text-slate-500"><tr><th className="px-5 py-3">Reward</th><th className="px-5 py-3">1st time</th><th className="px-5 py-3">2nd time</th><th className="px-5 py-3">How it is given</th></tr></thead><tbody className="divide-y divide-slate-100">{rewardGroups.map((group) => <Fragment key={group.category}><tr className="bg-slate-100"><td colSpan={4} className="px-5 py-2 text-xs font-bold uppercase tracking-wide text-slate-600">{group.category}</td></tr>{group.items.map((reward) => <tr key={reward.id}><td className="px-5 py-3 font-medium">{reward.name}</td><td className="px-5 py-3 text-sm">{penaltyCell(reward.reward_first)}</td><td className="px-5 py-3 text-sm">{penaltyCell(reward.reward_second)}</td><td className="px-5 py-3 text-sm">{reward.auto_rule ? <span className="inline-block rounded-full bg-emerald-100 px-3 py-1 text-xs font-semibold text-emerald-800">Proposed automatically each month</span> : <span className="text-slate-500">Recorded by hand</span>}</td></tr>)}</Fragment>)}</tbody></table></div> : <p className="p-10 text-center text-sm text-slate-500">No rewards are set up yet.</p>}
      </Section>}
    </>}
  </main>{canReview && rejectId !== null && <div className="fixed inset-0 z-20 flex items-center justify-center bg-slate-950/40 p-4"><div className="w-full max-w-lg rounded-2xl bg-white p-6 shadow-xl"><h2 className="text-xl font-bold">Reject Offence</h2><p className="mt-2 text-sm text-slate-500">A reason is required and kept in the audit trail.</p><textarea autoFocus value={reason} onChange={(event) => setReason(event.target.value)} className="mt-5 min-h-32 w-full rounded-xl border border-slate-300 p-3 text-sm" placeholder="Enter rejection reason" /><div className="mt-5 flex justify-end gap-3"><button type="button" onClick={() => { setRejectId(null); setReason(""); }} className="rounded-xl border border-slate-300 px-4 py-2.5 text-sm font-semibold">Back</button><button type="button" disabled={acting || !reason.trim()} onClick={() => void reject()} className="rounded-xl bg-red-600 px-4 py-2.5 text-sm font-semibold text-white">Reject Offence</button></div></div></div>}</div>;
}

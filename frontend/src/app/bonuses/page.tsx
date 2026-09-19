"use client";

import { useEffect, useEffectEvent, useMemo, useState } from "react";
import { useRouter } from "next/navigation";

import Sidebar from "@/components/Sidebar";
import { AppCard, PageHeader, Section } from "@/components/ui";
import { apiFetch, getAccessToken, getCurrentUser, type CurrentUser } from "@/lib/api";

type Bonus = { id: number; employee: number; employee_name: string; employee_number: string; department_name: string | null; kind: string; kind_label: string; amount: string; reason: string; performance_label: string; pay_year: number; pay_month: number; pay_label: string; status: string; status_label: string; recorded_by_name: string | null; created_at: string; decided_by_name: string | null; decision_comment: string; year_total: string };
type BonusSummary = { pending_count: number; pending_total: string; approved_count: number; approved_total: string; paid_total: string };
type Entry = { id: number; department: number; department_name: string; year: number; month: number; month_label: string; employee: number; employee_name: string; employee_number: string; reason: string; reward_amount: string | null; status: string; status_label: string; bonus_status: string | null; recorded_by_name: string | null; decided_by_name: string | null; decision_comment: string };
type DepartmentCard = { id: number; name: string; entry: Entry | null };
type Person = { id: number; employee_id: string; name: string; department: number | null; department_name: string | null };
type Dialog =
  | { kind: "record" }
  | { kind: "choose"; department: DepartmentCard }
  | { kind: "bonus"; bonus: Bonus; action: "approve" | "decline" | "cancel" }
  | { kind: "entry"; entry: Entry; action: "approve" | "decline" | "cancel" };

const MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
const money = (value: string | number) => `₦${Number(value || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
const inputClass = "mt-1 w-full rounded-xl border border-slate-300 bg-white px-3 py-2.5 text-sm outline-none focus:ring-2 focus:ring-blue-500";
const badge: Record<string, string> = { proposed: "bg-amber-100 text-amber-800", approved: "bg-blue-100 text-blue-800", paid: "bg-emerald-100 text-emerald-800", declined: "bg-red-100 text-red-700", cancelled: "bg-slate-200 text-slate-600" };

function apiError(data: unknown, fallback: string) {
  if (data && typeof data === "object") {
    const payload = data as Record<string, unknown>;
    if (typeof payload.detail === "string") return payload.detail;
    const first = Object.values(payload).flat()[0];
    if (typeof first === "string") return first;
  }
  return fallback;
}

export default function BonusesPage() {
  const router = useRouter();
  const now = new Date();
  const [user, setUser] = useState<CurrentUser | null>(null);
  const [year, setYear] = useState(now.getFullYear());
  const [month, setMonth] = useState(now.getMonth() + 1);
  const [tab, setTab] = useState<"bonuses" | "eotm">("bonuses");
  const [bonuses, setBonuses] = useState<Bonus[]>([]);
  const [summary, setSummary] = useState<BonusSummary | null>(null);
  const [departments, setDepartments] = useState<DepartmentCard[]>([]);
  const [recent, setRecent] = useState<Entry[]>([]);
  const [people, setPeople] = useState<Person[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [feedback, setFeedback] = useState("");
  const [dialog, setDialog] = useState<Dialog | null>(null);
  const [form, setForm] = useState({ q: "", employee: "", amount: "", reason: "", kind: "performance", payYear: "", payMonth: "", comment: "" });

  const perms = user?.permissions;
  const canRecord = !!perms?.record_bonus;
  const canApprove = !!perms?.approve_bonus;
  const allowed = !!(perms?.view_bonuses || canRecord || canApprove);

  async function load(silent = false) {
    if (!silent) setLoading(true);
    try {
      const me = await getCurrentUser();
      setUser(me);
      const p = me.permissions;
      if (!(p.view_bonuses || p.record_bonus || p.approve_bonus)) return;
      const [bonusResponse, eotmResponse] = await Promise.all([apiFetch(`/bonuses/?year=${year}&month=${month}`), apiFetch(`/bonuses/employee-of-the-month/?year=${year}&month=${month}`)]);
      if (!bonusResponse.ok) throw new Error(apiError(await bonusResponse.json().catch(() => null), "Unable to load bonuses."));
      const bonusData = await bonusResponse.json();
      setBonuses(bonusData.results || []);
      setSummary(bonusData.summary);
      if (eotmResponse.ok) { const eotm = await eotmResponse.json(); setDepartments(eotm.departments || []); setRecent(eotm.recent || []); }
      if ((p.record_bonus || p.approve_bonus) && !people.length) { const peopleResponse = await apiFetch("/bonuses/people/"); if (peopleResponse.ok) setPeople((await peopleResponse.json()).results || []); }
      setError("");
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "Unable to load bonuses.");
    } finally { setLoading(false); }
  }
  const loadNow = useEffectEvent((silent: boolean) => { void load(silent); });
  useEffect(() => {
    if (!getAccessToken()) { router.push("/login"); return; }
    const timer = window.setTimeout(() => loadNow(false), 0);
    return () => window.clearTimeout(timer);
  }, [router, year, month]);

  function shiftMonth(step: number) {
    const date = new Date(year, month - 1 + step, 1);
    setYear(date.getFullYear()); setMonth(date.getMonth() + 1);
  }

  function open(next: Dialog) {
    setError("");
    setForm({ q: "", employee: "", amount: "", reason: "", kind: "performance", payYear: String(year), payMonth: String(month), comment: "" });
    setDialog(next);
  }

  async function send(path: string, body: object | null, success: string) {
    setBusy(true); setError("");
    try {
      const response = await apiFetch(path, { method: "POST", ...(body ? { body: JSON.stringify(body) } : {}) });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(apiError(data, "That did not work."));
      setFeedback(success); setDialog(null);
      await load(true);
    } catch (actionError) {
      setError(actionError instanceof Error ? actionError.message : "That did not work.");
    } finally { setBusy(false); }
  }

  function confirm() {
    if (!dialog) return;
    if (dialog.kind === "record") return void send("/bonuses/", { employee: Number(form.employee), amount: form.amount, reason: form.reason, kind: form.kind, performance_year: year, performance_month: month, pay_year: Number(form.payYear), pay_month: Number(form.payMonth) }, "Bonus recorded. It now waits for management approval.");
    if (dialog.kind === "choose") return void send("/bonuses/employee-of-the-month/propose/", { department: dialog.department.id, employee: Number(form.employee), year, month, reason: form.reason, reward_amount: form.amount || null }, `${dialog.department.name}: proposed. It now waits for management approval.`);
    if (dialog.kind === "bonus") {
      const { bonus, action } = dialog;
      const body = action === "approve" ? { comment: form.comment, pay_year: Number(form.payYear), pay_month: Number(form.payMonth) } : { comment: form.comment };
      return void send(`/bonuses/${bonus.id}/${action}/`, body, { approve: "Approved. It goes into payroll for that month.", decline: "Declined.", cancel: "Cancelled." }[action]);
    }
    const { entry, action } = dialog;
    const body = action === "approve" ? { comment: form.comment, pay_year: Number(form.payYear), pay_month: Number(form.payMonth) } : { comment: form.comment };
    return void send(`/bonuses/employee-of-the-month/${entry.id}/${action}/`, body, { approve: "Approved.", decline: "Declined.", cancel: "Cancelled." }[action]);
  }

  const pickable = useMemo(() => {
    const department = dialog?.kind === "choose" ? dialog.department.id : null;
    return people.filter((p) => (department === null || p.department === department) && `${p.name} ${p.employee_id}`.toLowerCase().includes(form.q.trim().toLowerCase())).slice(0, 80);
  }, [people, dialog, form.q]);

  if (!loading && user && !allowed) {
    return <div className="min-h-screen bg-slate-100"><Sidebar /><main className="ml-64 p-8"><AppCard><p className="p-8 text-center text-slate-600">Your account does not have access to bonuses.</p></AppCard></main></div>;
  }

  const chosen = departments.filter((d) => d.entry?.status === "approved").length;
  const years = [now.getFullYear() - 1, now.getFullYear(), now.getFullYear() + 1];
  const payMonthPicker = (
    <div className="grid grid-cols-2 gap-3">
      <label className="text-sm font-semibold text-slate-700">Paid with the payroll of<select value={form.payMonth} onChange={(event) => setForm({ ...form, payMonth: event.target.value })} className={inputClass}>{MONTHS.map((name, index) => <option key={name} value={index + 1}>{name}</option>)}</select></label>
      <label className="text-sm font-semibold text-slate-700">&nbsp;<select value={form.payYear} onChange={(event) => setForm({ ...form, payYear: event.target.value })} className={inputClass}>{years.map((y) => <option key={y} value={y}>{y}</option>)}</select></label>
    </div>
  );
  const title = !dialog ? "" : dialog.kind === "record" ? `Record a bonus - ${MONTHS[month - 1]} ${year}` : dialog.kind === "choose" ? `${dialog.department.name} - Employee of the Month` : `${{ approve: "Approve", decline: "Decline", cancel: "Cancel" }[dialog.action]} ${dialog.kind === "bonus" ? "bonus" : "Employee of the Month"}`;

  return (
    <div className="min-h-screen bg-slate-100">
      <Sidebar />
      <main className="ml-64 min-w-0 p-4 md:p-8">
        <PageHeader title="Bonuses & Employee of the Month" description="Reward staff who went beyond the call of duty. Approved bonuses are added to that month's pay automatically." />
        {feedback && <p className="mb-6 rounded-xl bg-emerald-50 p-4 text-sm text-emerald-800">{feedback}</p>}
        {error && !dialog && <p className="mb-6 rounded-xl bg-red-50 p-4 text-sm text-red-700">{error}</p>}

        <div className="mb-6 flex items-center gap-3">
          <button type="button" onClick={() => shiftMonth(-1)} className="rounded-xl border border-slate-300 bg-white px-3 py-2 text-sm font-semibold text-slate-700">&larr;</button>
          <p className="min-w-[10rem] text-center text-lg font-bold text-slate-900">{MONTHS[month - 1]} {year}</p>
          <button type="button" onClick={() => shiftMonth(1)} className="rounded-xl border border-slate-300 bg-white px-3 py-2 text-sm font-semibold text-slate-700">&rarr;</button>
        </div>

        {summary && (
          <div className="mb-6 grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
            <div className="rounded-2xl border border-slate-200 bg-white p-5"><p className="text-sm font-semibold text-slate-500">Awaiting approval</p><p className="mt-1 text-3xl font-bold text-amber-700">{summary.pending_count}</p><p className="mt-1 text-xs text-slate-500">{money(summary.pending_total)}</p></div>
            <div className="rounded-2xl border border-slate-200 bg-white p-5"><p className="text-sm font-semibold text-slate-500">Approved this month</p><p className="mt-1 text-3xl font-bold text-blue-700">{summary.approved_count}</p><p className="mt-1 text-xs text-slate-500">{money(summary.approved_total)}</p></div>
            <div className="rounded-2xl border border-slate-200 bg-white p-5"><p className="text-sm font-semibold text-slate-500">Already in payroll</p><p className="mt-1 text-3xl font-bold text-emerald-700">{money(summary.paid_total)}</p></div>
            <div className="rounded-2xl border border-slate-200 bg-white p-5"><p className="text-sm font-semibold text-slate-500">Employees of the Month</p><p className="mt-1 text-3xl font-bold text-violet-700">{chosen}<span className="text-base font-semibold text-slate-500"> of {departments.length} departments</span></p></div>
          </div>
        )}

        <div className="mb-4 flex flex-wrap items-center gap-2">
          {([["bonuses", "Bonuses"], ["eotm", "Employee of the Month"]] as const).map(([key, label]) => <button key={key} type="button" onClick={() => setTab(key)} className={`rounded-full px-4 py-2 text-sm font-semibold ${tab === key ? "bg-slate-900 text-white" : "bg-white text-slate-700 hover:bg-slate-50"}`}>{label}</button>)}
          {tab === "bonuses" && canRecord && <button type="button" onClick={() => open({ kind: "record" })} className="ml-auto rounded-xl bg-blue-600 px-4 py-2 text-sm font-semibold text-white">+ Record a bonus</button>}
        </div>

        {loading && <div className="h-40 animate-pulse rounded-2xl bg-slate-200" />}

        {!loading && tab === "bonuses" && (
          <Section title="Bonuses for this month" subtitle={`${bonuses.length} recorded for work done in ${MONTHS[month - 1]} ${year}`}>
            {bonuses.length ? (
              <div className="overflow-x-auto">
                <table className="w-full min-w-[900px] text-left">
                  <thead className="bg-slate-50 text-xs font-semibold uppercase tracking-wide text-slate-500"><tr><th className="px-5 py-3">Employee</th><th className="px-5 py-3">Bonus</th><th className="px-5 py-3">Why</th><th className="px-5 py-3">Status</th><th className="px-5 py-3" /></tr></thead>
                  <tbody className="divide-y divide-slate-100 align-top">
                    {bonuses.map((b) => (
                      <tr key={b.id} className="hover:bg-slate-50">
                        <td className="px-5 py-4"><p className="font-semibold text-slate-900">{b.employee_name}</p><p className="text-sm text-slate-500">{b.employee_number}{b.department_name ? ` · ${b.department_name}` : ""}</p>{canApprove && b.status === "proposed" && Number(b.year_total) > 0 && <p className="mt-1 text-xs font-semibold text-amber-700">Already given {money(b.year_total)} in bonuses this year</p>}</td>
                        <td className="px-5 py-4"><p className="font-semibold text-slate-900">{money(b.amount)}</p><p className="text-sm text-slate-500">{b.kind_label}</p><p className="text-xs text-slate-500">Paid with {b.pay_label} payroll</p></td>
                        <td className="max-w-[300px] px-5 py-4 text-sm text-slate-700">{b.reason}<p className="mt-1 text-xs text-slate-500">Recorded by {b.recorded_by_name || "-"}{b.decided_by_name ? ` · decided by ${b.decided_by_name}${b.decision_comment ? `: ${b.decision_comment}` : ""}` : ""}</p></td>
                        <td className="px-5 py-4"><span className={`rounded-full px-3 py-1 text-xs font-semibold ${badge[b.status]}`}>{b.status_label}</span></td>
                        <td className="px-5 py-4"><div className="flex flex-wrap justify-end gap-2">
                          {b.status === "proposed" && canApprove && <button type="button" onClick={() => open({ kind: "bonus", bonus: b, action: "approve" })} className="rounded-xl bg-emerald-600 px-3 py-2 text-sm font-semibold text-white">Approve</button>}
                          {b.status === "proposed" && canApprove && <button type="button" onClick={() => open({ kind: "bonus", bonus: b, action: "decline" })} className="rounded-xl border border-red-300 px-3 py-2 text-sm font-semibold text-red-700">Decline</button>}
                          {["proposed", "approved"].includes(b.status) && (canRecord || canApprove) && <button type="button" onClick={() => open({ kind: "bonus", bonus: b, action: "cancel" })} className="rounded-xl border border-slate-300 px-3 py-2 text-sm font-semibold text-slate-700">Cancel</button>}
                        </div></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : <p className="p-12 text-center text-slate-500">No bonuses recorded for this month yet.</p>}
          </Section>
        )}

        {!loading && tab === "eotm" && (
          <>
            <div className="mb-6 grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
              {departments.map((d) => {
                const e = d.entry;
                return (
                  <div key={d.id} className={`rounded-2xl border p-5 ${e?.status === "approved" ? "border-violet-200 bg-violet-50" : "border-slate-200 bg-white"}`}>
                    <p className="text-sm font-semibold uppercase tracking-wide text-slate-500">{d.name}</p>
                    {e ? (
                      <>
                        <p className="mt-2 text-xl font-bold text-slate-900">{e.employee_name}</p>
                        <p className="text-xs text-slate-500">{e.employee_number}</p>
                        <p className="mt-2 text-sm text-slate-700">{e.reason}</p>
                        <div className="mt-3 flex flex-wrap items-center gap-2"><span className={`rounded-full px-3 py-1 text-xs font-semibold ${badge[e.status]}`}>{e.status_label}</span>{e.reward_amount && <span className="text-xs font-semibold text-slate-700">Reward {money(e.reward_amount)}{e.bonus_status === "paid" ? " · in payroll" : e.bonus_status === "approved" ? " · goes into payroll" : ""}</span>}</div>
                        <div className="mt-3 flex flex-wrap gap-2">
                          {e.status === "proposed" && canApprove && <button type="button" onClick={() => open({ kind: "entry", entry: e, action: "approve" })} className="rounded-xl bg-emerald-600 px-3 py-1.5 text-sm font-semibold text-white">Approve</button>}
                          {e.status === "proposed" && canApprove && <button type="button" onClick={() => open({ kind: "entry", entry: e, action: "decline" })} className="rounded-xl border border-red-300 px-3 py-1.5 text-sm font-semibold text-red-700">Decline</button>}
                          {(canRecord || canApprove) && <button type="button" onClick={() => open({ kind: "entry", entry: e, action: "cancel" })} className="rounded-xl border border-slate-300 px-3 py-1.5 text-sm font-semibold text-slate-700">Change</button>}
                        </div>
                      </>
                    ) : (
                      <>
                        <p className="mt-2 text-lg font-semibold text-slate-400">Not chosen yet</p>
                        {canRecord && <button type="button" onClick={() => open({ kind: "choose", department: d })} className="mt-3 rounded-xl bg-blue-600 px-4 py-2 text-sm font-semibold text-white">Choose</button>}
                      </>
                    )}
                  </div>
                );
              })}
            </div>
            <Section title="Recent winners" subtitle="Approved Employees of the Month, newest first">
              {recent.length ? (
                <div className="max-h-[420px] overflow-auto"><table className="w-full min-w-[640px] text-left"><thead className="sticky top-0 bg-slate-50 text-xs font-semibold uppercase tracking-wide text-slate-500"><tr><th className="px-5 py-3">Month</th><th className="px-5 py-3">Department</th><th className="px-5 py-3">Winner</th><th className="px-5 py-3">Reward</th></tr></thead>
                  <tbody className="divide-y divide-slate-100">{recent.map((e) => <tr key={e.id}><td className="px-5 py-3 text-sm text-slate-700">{e.month_label}</td><td className="px-5 py-3 text-sm text-slate-700">{e.department_name}</td><td className="px-5 py-3"><p className="font-semibold text-slate-900">{e.employee_name}</p><p className="text-xs text-slate-500">{e.reason}</p></td><td className="px-5 py-3 text-sm text-slate-700">{e.reward_amount ? money(e.reward_amount) : "Recognition only"}</td></tr>)}</tbody></table></div>
              ) : <p className="p-10 text-center text-slate-500">No winners yet.</p>}
            </Section>
          </>
        )}

        {dialog && (
          <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/40 p-4">
            <div className="flex max-h-[90vh] w-full max-w-lg flex-col rounded-2xl bg-white shadow-xl">
              <div className="border-b border-slate-200 p-5"><h2 className="text-lg font-bold text-slate-900">{title}</h2></div>
              <div className="overflow-y-auto p-5">
                {error && <p className="mb-3 rounded-xl bg-red-50 p-3 text-sm text-red-700">{error}</p>}
                {(dialog.kind === "record" || dialog.kind === "choose") && (
                  <div className="space-y-3">
                    <label className="block text-sm font-semibold text-slate-700">Employee{dialog.kind === "choose" ? ` (${dialog.department.name} only)` : ""}
                      <input value={form.q} onChange={(event) => setForm({ ...form, q: event.target.value })} placeholder="Search name or staff number..." className={inputClass} />
                      <select value={form.employee} onChange={(event) => setForm({ ...form, employee: event.target.value })} className={`${inputClass} mt-2`}><option value="">Select a person</option>{pickable.map((p) => <option key={p.id} value={p.id}>{p.name} ({p.employee_id}){p.department_name ? ` - ${p.department_name}` : ""}</option>)}</select>
                    </label>
                    <label className="block text-sm font-semibold text-slate-700">{dialog.kind === "choose" ? "Why they are the Employee of the Month" : "What they did that went beyond the call of duty"}<textarea value={form.reason} onChange={(event) => setForm({ ...form, reason: event.target.value })} rows={3} className={inputClass} /></label>
                    <label className="block text-sm font-semibold text-slate-700">{dialog.kind === "choose" ? "Reward (₦, optional - leave empty for recognition only)" : "Amount (₦)"}<input type="number" min="0" step="0.01" value={form.amount} onChange={(event) => setForm({ ...form, amount: event.target.value })} className={inputClass} /></label>
                    {dialog.kind === "record" && <label className="block text-sm font-semibold text-slate-700">Type<select value={form.kind} onChange={(event) => setForm({ ...form, kind: event.target.value })} className={inputClass}><option value="performance">Performance bonus</option><option value="other">Other bonus</option></select></label>}
                    {dialog.kind === "record" && payMonthPicker}
                  </div>
                )}
                {dialog.kind === "bonus" && (
                  <div className="space-y-3">
                    <p className="text-sm text-slate-700"><b>{dialog.bonus.employee_name}</b> · {money(dialog.bonus.amount)} · {dialog.bonus.kind_label}</p>
                    <p className="rounded-xl bg-slate-50 p-3 text-sm text-slate-700">{dialog.bonus.reason}</p>
                    {dialog.action === "approve" && <>{Number(dialog.bonus.year_total) > 0 && <p className="rounded-xl bg-amber-50 p-3 text-xs text-amber-900">Already given {money(dialog.bonus.year_total)} in bonuses this year.</p>}{payMonthPicker}</>}
                    {dialog.action !== "cancel" && <label className="block text-sm font-semibold text-slate-700">{dialog.action === "decline" ? "Reason (required)" : "Comment (optional)"}<input value={form.comment} onChange={(event) => setForm({ ...form, comment: event.target.value })} className={inputClass} /></label>}
                  </div>
                )}
                {dialog.kind === "entry" && (
                  <div className="space-y-3">
                    <p className="text-sm text-slate-700"><b>{dialog.entry.employee_name}</b> · {dialog.entry.department_name} · {dialog.entry.month_label}{dialog.entry.reward_amount ? ` · reward ${money(dialog.entry.reward_amount)}` : ""}</p>
                    <p className="rounded-xl bg-slate-50 p-3 text-sm text-slate-700">{dialog.entry.reason}</p>
                    {dialog.action === "approve" && dialog.entry.reward_amount && payMonthPicker}
                    {dialog.action === "cancel" && <p className="text-xs text-slate-500">This removes the winner so the department can be chosen again. A reward already in payroll cannot be undone here.</p>}
                    {dialog.action !== "cancel" && <label className="block text-sm font-semibold text-slate-700">{dialog.action === "decline" ? "Reason (required)" : "Comment (optional)"}<input value={form.comment} onChange={(event) => setForm({ ...form, comment: event.target.value })} className={inputClass} /></label>}
                  </div>
                )}
              </div>
              <div className="flex justify-end gap-3 border-t border-slate-200 p-4">
                <button type="button" onClick={() => setDialog(null)} className="rounded-xl border border-slate-300 px-4 py-2.5 text-sm font-semibold text-slate-700">Back</button>
                <button type="button" disabled={busy || ((dialog.kind === "record" || dialog.kind === "choose") && (!form.employee || !form.reason.trim() || (dialog.kind === "record" && !(Number(form.amount) > 0)))) || ((dialog.kind === "bonus" || dialog.kind === "entry") && dialog.action === "decline" && !form.comment.trim())} onClick={confirm} className="rounded-xl bg-slate-900 px-4 py-2.5 text-sm font-semibold text-white disabled:bg-slate-400">{busy ? "Saving..." : "Confirm"}</button>
              </div>
            </div>
          </div>
        )}
      </main>
    </div>
  );
}

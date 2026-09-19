"use client";

import { useEffect, useEffectEvent, useState } from "react";
import { useRouter } from "next/navigation";

import Sidebar from "@/components/Sidebar";
import { AdvancePaymentModal } from "@/components/advances/AdvancePaymentModal";
import { AppCard, MetricCard, PageHeader, Section } from "@/components/ui";
import { apiFetch, getAccessToken, getCurrentUser, type CurrentUser } from "@/lib/api";

type Account = { id: number; employee: number; employee_name: string; employee_number: string; department_name: string | null; position_name: string | null; employee_active: boolean; percent: string; active: boolean; balance: string; available: string; monthly_amount: string; saving_since: string; partial_eligible_from: string; partial_limit: string; partial_withdrawn: string };
type NotEnrolled = { id: number; employee_id: string; full_name: string; department_name: string | null; position_name: string | null };
type Policy = { min_months_for_partial: number; partial_limit_percent: string; min_notice_months: number };
type Overview = { policy: Policy; total_held: string; enrolled_count: number; accounts: Account[]; not_enrolled: NotEnrolled[] };
type Entry = { id: number; entry_type: string; entry_type_label: string; amount: string; entry_date: string; note: string; payroll_period_name: string | null };
type Withdrawal = { id: number; account: number; employee_name: string; employee_number: string; balance: string; kind: string; kind_label: string; amount: string | null; reason: string; notice_given_on: string | null; leaving_on: string | null; rule_exceptions: string; status: string; status_label: string; recorded_by_name: string | null; created_at: string; decided_by_name: string | null; decision_comment: string; paid_by_name: string | null; paid_on: string | null; payment_reference: string };
type Dialog =
  | { kind: "enrol"; person: NotEnrolled }
  | { kind: "percent"; account: Account }
  | { kind: "ledger"; account: Account; entries: Entry[] }
  | { kind: "withdraw"; account: Account }
  | { kind: "adjust"; account: Account }
  | { kind: "forfeit"; account: Account }
  | { kind: "decide"; withdrawal: Withdrawal; action: "approve" | "decline" | "pay" | "cancel" };

const PAY_PATHS = { preview: "/deferred-funds/withdrawals/bank-upload/preview/", download: "/deferred-funds/withdrawals/bank-upload/", markPaid: "/deferred-funds/withdrawals/mark-paid/" };
const money = (value: string | number) => `₦${Number(value || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
const inputClass = "mt-1 w-full rounded-xl border border-slate-300 bg-white px-3 py-2.5 text-sm outline-none focus:ring-2 focus:ring-blue-500";
const badge: Record<string, string> = { requested: "bg-amber-100 text-amber-800", approved: "bg-blue-100 text-blue-800", declined: "bg-red-100 text-red-700", paid: "bg-emerald-100 text-emerald-800", cancelled: "bg-slate-200 text-slate-600" };

function apiError(data: unknown, fallback: string) {
  if (data && typeof data === "object") {
    const payload = data as Record<string, unknown>;
    if (typeof payload.detail === "string") return payload.detail;
    const first = Object.values(payload).flat()[0];
    if (typeof first === "string") return first;
  }
  return fallback;
}

export default function DeferredFundsPage() {
  const router = useRouter();
  const [user, setUser] = useState<CurrentUser | null>(null);
  const [overview, setOverview] = useState<Overview | null>(null);
  const [withdrawals, setWithdrawals] = useState<Withdrawal[]>([]);
  const [tab, setTab] = useState<"funds" | "withdrawals">("funds");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [feedback, setFeedback] = useState("");
  const [dialog, setDialog] = useState<Dialog | null>(null);
  const [ticked, setTicked] = useState<number[]>([]);
  const [paying, setPaying] = useState(false);
  const [f, setF] = useState({ percent: "15", opening: "", amount: "", text: "", date: new Date().toISOString().slice(0, 10), full: false, since: "", notice: "", leaving: "" });

  const perms = user?.permissions;
  const canManage = !!perms?.manage_deferred_funds;
  const canApprove = !!perms?.approve_deferred_withdrawal;
  const canPay = !!perms?.pay_deferred_withdrawal;

  async function load() {
    try {
      const me = await getCurrentUser();
      setUser(me);
      const p = me.permissions;
      if (!(p.view_deferred_funds || p.manage_deferred_funds || p.approve_deferred_withdrawal || p.pay_deferred_withdrawal)) return;
      const [overviewResponse, withdrawalResponse] = await Promise.all([apiFetch("/deferred-funds/"), apiFetch("/deferred-funds/withdrawals/")]);
      if (!overviewResponse.ok) throw new Error(apiError(await overviewResponse.json().catch(() => null), "Unable to load deferred funds."));
      setOverview(await overviewResponse.json());
      if (withdrawalResponse.ok) setWithdrawals((await withdrawalResponse.json()).results || []);
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "Unable to load deferred funds.");
    } finally {
      setLoading(false);
    }
  }
  const loadOnMount = useEffectEvent(() => { void load(); });
  useEffect(() => {
    if (!getAccessToken()) { router.push("/login"); return; }
    const timer = window.setTimeout(loadOnMount, 0);
    return () => window.clearTimeout(timer);
  }, [router]);

  function open(next: Dialog) {
    setError("");
    setF((current) => ({ ...current, percent: next.kind === "percent" ? String(Number(next.account.percent)) : "15", opening: "", amount: "", text: "", full: false, since: "", notice: "", leaving: "" }));
    setDialog(next);
  }

  async function openLedger(account: Account) {
    const response = await apiFetch(`/deferred-funds/accounts/${account.id}/ledger/`);
    if (response.ok) open({ kind: "ledger", account, entries: (await response.json()).entries });
  }

  async function send(path: string, method: string, body: object, success: string) {
    setBusy(true);
    setError("");
    try {
      const response = await apiFetch(path, { method, body: JSON.stringify(body) });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(apiError(data, "That did not work."));
      setFeedback(success);
      setDialog(null);
      await load();
    } catch (actionError) {
      setError(actionError instanceof Error ? actionError.message : "That did not work.");
    } finally {
      setBusy(false);
    }
  }

  function confirm() {
    if (!dialog) return;
    if (dialog.kind === "enrol") return void send("/deferred-funds/accounts/", "POST", { employee: dialog.person.id, percent: f.percent, opening_balance: f.opening || "0", saving_since: f.since || null }, `${dialog.person.full_name} enrolled. The contribution comes off their pay when payroll is generated.`);
    if (dialog.kind === "percent") return void send(`/deferred-funds/accounts/${dialog.account.id}/`, "PATCH", { percent: f.percent }, "Percentage updated. It applies from the next payroll.");
    if (dialog.kind === "adjust") return void send(`/deferred-funds/accounts/${dialog.account.id}/adjust/`, "POST", { amount: f.amount, note: f.text }, "Balance adjusted.");
    if (dialog.kind === "forfeit") return void send(`/deferred-funds/accounts/${dialog.account.id}/forfeit/`, "POST", { reason: f.text }, "The fund was forfeited to the company.");
    if (dialog.kind === "withdraw") return void send("/deferred-funds/withdrawals/", "POST", f.full ? { account: dialog.account.id, kind: "final", reason: f.text, notice_given_on: f.notice || null, leaving_on: f.leaving || null } : { account: dialog.account.id, kind: "partial", amount: f.amount, reason: f.text }, "Withdrawal recorded. It now waits for management approval.");
    if (dialog.kind === "decide") {
      const { withdrawal, action } = dialog;
      const body = action === "pay" ? { paid_on: f.date, reference: f.text } : { comment: f.text };
      if (action === "approve" && withdrawal.rule_exceptions && !f.text.trim()) { setError("This request is outside policy. Give your reason for approving it anyway."); return; }
      const done = { approve: "Approved. Finance can now pay it out.", decline: "Declined.", pay: "Marked as paid and taken off the balance.", cancel: "Cancelled." }[action];
      return void send(`/deferred-funds/withdrawals/${withdrawal.id}/${action}/`, "POST", body, done);
    }
  }

  if (!loading && user && !overview) {
    return <div className="min-h-screen bg-slate-100"><Sidebar /><main className="ml-64 p-8"><AppCard><p className="p-8 text-center text-slate-600">{error || "Your account does not have access to deferred funds."}</p></AppCard></main></div>;
  }

  const payable = withdrawals.filter((w) => w.status === "approved");
  const chosen = ticked.filter((id) => payable.some((w) => w.id === id));
  const open_ = withdrawals.filter((w) => ["requested", "approved"].includes(w.status)).length;
  const title = dialog?.kind === "enrol" ? `Enrol ${dialog.person.full_name}` : dialog?.kind === "percent" ? `Change percentage - ${dialog.account.employee_name}` : dialog?.kind === "withdraw" ? `Withdrawal - ${dialog.account.employee_name}` : dialog?.kind === "adjust" ? `Adjust balance - ${dialog.account.employee_name}` : dialog?.kind === "forfeit" ? `Forfeit fund - ${dialog.account.employee_name}` : dialog?.kind === "decide" ? `${{ approve: "Approve", decline: "Decline", pay: "Pay out", cancel: "Cancel" }[dialog.action]} withdrawal - ${dialog.withdrawal.employee_name}` : "";

  return (
    <div className="min-h-screen bg-slate-100">
      <Sidebar />
      <main className="ml-64 min-w-0 p-4 md:p-8">
        <PageHeader title="Deferred Funds" description="The percentage of salary contract staff set aside with the company each month, and what the company currently holds for each person." />
        {feedback && <p className="mb-6 rounded-xl bg-emerald-50 p-4 text-sm text-emerald-800">{feedback}</p>}
        {error && !dialog && <p className="mb-6 rounded-xl bg-red-50 p-4 text-sm text-red-700">{error}</p>}

        <div className="mb-6 grid gap-4 sm:grid-cols-3">
          <MetricCard title="Total held by the company" value={money(overview?.total_held || 0)} accentColor="#7c3aed" />
          <MetricCard title="Contract staff contributing" value={String(overview?.enrolled_count ?? 0)} accentColor="#2563eb" />
          <MetricCard title="Withdrawals in progress" value={String(open_)} accentColor="#d97706" />
        </div>

        <div className="mb-4 flex gap-2">
          {([["funds", "Funds held"], ["withdrawals", `Withdrawals (${withdrawals.length})`]] as const).map(([key, label]) => (
            <button key={key} type="button" onClick={() => setTab(key)} className={`rounded-full px-4 py-2 text-sm font-semibold ${tab === key ? "bg-slate-900 text-white" : "bg-white text-slate-700 hover:bg-slate-50"}`}>{label}</button>
          ))}
        </div>

        {tab === "funds" && (
          <>
            {canManage && !!overview?.not_enrolled.length && (
              <AppCard className="mb-6" title="Contract staff not yet contributing">
                <div className="flex flex-wrap gap-2 p-1">
                  {overview.not_enrolled.map((person) => (
                    <button key={person.id} type="button" onClick={() => open({ kind: "enrol", person })} className="rounded-xl border border-slate-300 bg-white px-3 py-2 text-sm hover:bg-slate-50"><b>{person.full_name}</b> <span className="text-slate-500">{person.employee_id}{person.position_name ? ` · ${person.position_name}` : ""}</span> <span className="ml-1 font-semibold text-blue-700">Enrol</span></button>
                  ))}
                </div>
              </AppCard>
            )}
            <Section title="Contract staff funds" subtitle={`${overview?.accounts.length ?? 0} account${overview?.accounts.length === 1 ? "" : "s"}`}>
              {loading ? <div className="h-32 animate-pulse bg-slate-100" /> : overview?.accounts.length ? (
                <div className="overflow-x-auto">
                  <table className="w-full min-w-[900px] text-left">
                    <thead className="bg-slate-50 text-xs font-semibold uppercase tracking-wide text-slate-500"><tr><th className="px-5 py-3">Employee</th><th className="px-5 py-3">Role</th><th className="px-5 py-3">Rate</th><th className="px-5 py-3">Monthly</th><th className="px-5 py-3">Held by company</th><th className="px-5 py-3" /></tr></thead>
                    <tbody className="divide-y divide-slate-100">
                      {overview.accounts.map((account) => (
                        <tr key={account.id} className="hover:bg-slate-50">
                          <td className="px-5 py-4"><p className="font-semibold text-slate-900">{account.employee_name}</p><p className="text-sm text-slate-500">{account.employee_number}{!account.employee_active ? " · no longer active" : ""}{!account.active ? " · stopped" : ""}</p></td>
                          <td className="px-5 py-4 text-sm text-slate-600">{account.position_name || "-"}<br />{account.department_name || ""}</td>
                          <td className="px-5 py-4 font-semibold">{Number(account.percent)}%</td>
                          <td className="px-5 py-4 text-slate-700">{account.active ? money(account.monthly_amount) : "-"}</td>
                          <td className="px-5 py-4 text-lg font-bold text-slate-900">{money(account.balance)}</td>
                          <td className="px-5 py-4"><div className="flex flex-wrap justify-end gap-2">
                            <button type="button" onClick={() => void openLedger(account)} className="rounded-xl border border-slate-300 px-3 py-2 text-sm font-semibold text-slate-700">Statement</button>
                            {canManage && account.active && <button type="button" onClick={() => open({ kind: "percent", account })} className="rounded-xl border border-slate-300 px-3 py-2 text-sm font-semibold text-slate-700">Change %</button>}
                            {canManage && Number(account.balance) > 0 && <button type="button" onClick={() => open({ kind: "withdraw", account })} className="rounded-xl bg-violet-600 px-3 py-2 text-sm font-semibold text-white">Withdrawal</button>}
                            {canApprove && Number(account.balance) > 0 && <button type="button" onClick={() => open({ kind: "forfeit", account })} className="rounded-xl border border-red-300 px-3 py-2 text-sm font-semibold text-red-700">Forfeit</button>}
                            {canManage && <button type="button" onClick={() => open({ kind: "adjust", account })} className="rounded-xl border border-slate-300 px-3 py-2 text-sm font-semibold text-slate-500">Adjust</button>}
                          </div></td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : <p className="p-12 text-center text-slate-500">No one is contributing yet. Enrol a contract staff member above.</p>}
            </Section>
          </>
        )}

        {tab === "withdrawals" && (
          <>
          {canPay && payable.length > 0 && (
            <div className="mb-4 flex flex-wrap items-center gap-3 rounded-2xl border border-violet-200 bg-violet-50 p-4">
              <p className="text-sm text-violet-900"><b>{payable.length}</b> approved withdrawal{payable.length === 1 ? "" : "s"} waiting for payment. Tick the ones you are paying now.</p>
              <button type="button" onClick={() => setTicked(chosen.length === payable.length ? [] : payable.map((w) => w.id))} className="rounded-xl border border-violet-300 bg-white px-3 py-2 text-sm font-semibold text-violet-800">{chosen.length === payable.length ? "Untick all" : "Tick all"}</button>
              <button type="button" disabled={chosen.length === 0} onClick={() => setPaying(true)} className="rounded-xl bg-violet-600 px-4 py-2 text-sm font-semibold text-white disabled:bg-violet-300">Pay Selected ({chosen.length}) - Bank File</button>
            </div>
          )}
          <Section title="Withdrawals" subtitle="HR records, management approves, finance pays.">
            {withdrawals.length ? (
              <div className="overflow-x-auto"><table className="w-full min-w-[900px] text-left">
                <thead className="bg-slate-50 text-xs font-semibold uppercase tracking-wide text-slate-500"><tr>{canPay && <th className="w-10 px-3 py-3" />}<th className="px-5 py-3">Employee</th><th className="px-5 py-3">Request</th><th className="px-5 py-3">Status</th><th className="px-5 py-3">Trail</th><th className="px-5 py-3" /></tr></thead>
                <tbody className="divide-y divide-slate-100 align-top">
                  {withdrawals.map((w) => (
                    <tr key={w.id}>
                      {canPay && <td className="px-3 py-4">{w.status === "approved" && <input type="checkbox" checked={chosen.includes(w.id)} onChange={(event) => setTicked(event.target.checked ? [...chosen, w.id] : chosen.filter((id) => id !== w.id))} aria-label={`Select ${w.employee_name}`} />}</td>}
                      <td className="px-5 py-4"><p className="font-semibold">{w.employee_name}</p><p className="text-sm text-slate-500">{w.employee_number} · holds {money(w.balance)}</p></td>
                      <td className="px-5 py-4"><p className="font-semibold">{w.amount ? money(w.amount) : "Whole balance"}</p><p className="text-sm text-slate-500">{w.kind_label}</p>{w.reason && <p className="mt-1 max-w-[220px] text-sm text-slate-500">{w.reason}</p>}{w.leaving_on && <p className="mt-1 text-xs text-slate-500">Notice {w.notice_given_on ? new Date(w.notice_given_on).toLocaleDateString("en-GB") : "-"} · leaving {new Date(w.leaving_on).toLocaleDateString("en-GB")}</p>}{w.rule_exceptions && <p className="mt-2 max-w-[260px] whitespace-pre-line rounded-lg bg-amber-50 px-2 py-1 text-xs font-semibold text-amber-900">Outside policy: {w.rule_exceptions}</p>}</td>
                      <td className="px-5 py-4"><span className={`rounded-full px-3 py-1 text-xs font-semibold ${badge[w.status]}`}>{w.status_label}</span></td>
                      <td className="px-5 py-4 text-xs text-slate-500"><p>Recorded by {w.recorded_by_name || "-"} · {new Date(w.created_at).toLocaleDateString("en-GB")}</p>{w.decided_by_name && <p>{w.status === "declined" ? "Declined" : "Approved"} by {w.decided_by_name}{w.decision_comment ? `: ${w.decision_comment}` : ""}</p>}{w.paid_by_name && <p>Paid by {w.paid_by_name}{w.paid_on ? ` on ${new Date(w.paid_on).toLocaleDateString("en-GB")}` : ""}{w.payment_reference ? ` (ref ${w.payment_reference})` : ""}</p>}</td>
                      <td className="px-5 py-4"><div className="flex flex-wrap justify-end gap-2">
                        {w.status === "requested" && canApprove && <button type="button" onClick={() => open({ kind: "decide", withdrawal: w, action: "approve" })} className="rounded-xl bg-emerald-600 px-3 py-2 text-sm font-semibold text-white">Approve</button>}
                        {w.status === "requested" && canApprove && <button type="button" onClick={() => open({ kind: "decide", withdrawal: w, action: "decline" })} className="rounded-xl border border-red-300 px-3 py-2 text-sm font-semibold text-red-700">Decline</button>}
                        {w.status === "approved" && canPay && <button type="button" onClick={() => open({ kind: "decide", withdrawal: w, action: "pay" })} className="rounded-xl bg-violet-600 px-3 py-2 text-sm font-semibold text-white">Mark as Paid</button>}
                        {["requested", "approved"].includes(w.status) && (canManage || canApprove) && <button type="button" onClick={() => open({ kind: "decide", withdrawal: w, action: "cancel" })} className="rounded-xl border border-slate-300 px-3 py-2 text-sm font-semibold text-slate-700">Cancel</button>}
                      </div></td>
                    </tr>
                  ))}
                </tbody>
              </table></div>
            ) : <p className="p-12 text-center text-slate-500">No withdrawals yet.</p>}
          </Section>
          </>
        )}

        {paying && <AdvancePaymentModal ids={chosen} paths={PAY_PATHS} noun="deferred fund withdrawal" afterPaid="the money comes off the balance" onClose={() => setPaying(false)} onPaid={(count) => { setPaying(false); setTicked([]); setFeedback(`${count} withdrawal${count === 1 ? "" : "s"} marked as paid and taken off the balance.`); void load(); }} />}

        {dialog && (
          <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/40 p-4">
            <div className="flex max-h-[90vh] w-full max-w-lg flex-col rounded-2xl bg-white shadow-xl">
              <div className="border-b border-slate-200 p-5"><h2 className="text-lg font-bold text-slate-900">{dialog.kind === "ledger" ? `Statement - ${dialog.account.employee_name}` : title}</h2></div>
              <div className="overflow-y-auto p-5">
                {error && <p className="mb-4 rounded-xl bg-red-50 p-3 text-sm text-red-700">{error}</p>}

                {(dialog.kind === "enrol" || dialog.kind === "percent") && (
                  <>
                    <label className="text-sm font-semibold text-slate-700">Percentage of basic salary set aside each month</label>
                    <div className="mt-2 flex gap-2">{["5", "10", "15"].map((p) => <button key={p} type="button" onClick={() => setF({ ...f, percent: p })} className={`rounded-xl border px-4 py-2 text-sm font-semibold ${f.percent === p ? "border-blue-600 bg-blue-600 text-white" : "border-slate-300"}`}>{p}%</button>)}<input type="number" min="0.01" max="100" step="0.01" value={f.percent} onChange={(event) => setF({ ...f, percent: event.target.value })} className="w-24 rounded-xl border border-slate-300 px-3 py-2 text-sm" /></div>
                    {dialog.kind === "enrol" && <>
                      <label className="mt-4 block text-sm font-semibold text-slate-700">Amount already held for them (optional)<input type="number" min="0" step="0.01" value={f.opening} onChange={(event) => setF({ ...f, opening: event.target.value })} placeholder="Opening balance from before this system" className={inputClass} /></label>
                      <label className="mt-4 block text-sm font-semibold text-slate-700">Saving with the company since (optional)<input type="date" value={f.since} onChange={(event) => setF({ ...f, since: event.target.value })} className={inputClass} /></label>
                      <p className="mt-1 text-xs text-slate-500">Leave empty if they start saving today. It matters for the {overview?.policy.min_months_for_partial}-month rule on partial withdrawals.</p>
                    </>}
                    <p className="mt-3 text-xs text-slate-500">The contribution comes off their pay automatically each time payroll is generated.</p>
                  </>
                )}

                {dialog.kind === "withdraw" && (() => {
                  const policy = overview?.policy;
                  const today = new Date().toISOString().slice(0, 10);
                  const early = today < dialog.account.partial_eligible_from;
                  const over = !f.full && Number(f.amount) > Number(dialog.account.partial_limit);
                  const addMonths = (iso: string, months: number) => { const d = new Date(iso); d.setMonth(d.getMonth() + months); return d.toISOString().slice(0, 10); };
                  const shortNotice = f.full && f.notice && f.leaving && f.leaving < addMonths(f.notice, policy?.min_notice_months ?? 2);
                  return (<>
                    <p className="text-sm text-slate-600">Held: <b>{money(dialog.account.balance)}</b>. Saving since {new Date(dialog.account.saving_since).toLocaleDateString("en-GB")}.</p>
                    <label className="mt-4 flex items-center gap-2 text-sm font-semibold text-slate-700"><input type="checkbox" checked={f.full} onChange={(event) => setF({ ...f, full: event.target.checked })} /> Full release - the person is leaving</label>
                    {f.full ? (<>
                      <p className="mt-2 text-xs text-slate-500">Policy: the whole balance is paid on the last working day if {policy?.min_notice_months} months&apos; notice was given. Their monthly contribution then stops.</p>
                      <div className="mt-3 grid grid-cols-2 gap-3">
                        <label className="text-sm font-semibold text-slate-700">Notice given on<input type="date" value={f.notice} onChange={(event) => setF({ ...f, notice: event.target.value })} className={inputClass} /></label>
                        <label className="text-sm font-semibold text-slate-700">Last working day<input type="date" value={f.leaving} onChange={(event) => setF({ ...f, leaving: event.target.value })} className={inputClass} /></label>
                      </div>
                      {shortNotice && <p className="mt-3 rounded-xl bg-amber-50 p-3 text-xs text-amber-900">Less than {policy?.min_notice_months} months&apos; notice. This can be recorded, but management must approve it with a reason.</p>}
                    </>) : (<>
                      <p className="mt-2 text-xs text-slate-500">Policy: up to {Number(policy?.partial_limit_percent)}% of the accumulated fund, after {policy?.min_months_for_partial} months of service and saving. Allowed now: <b>{money(dialog.account.partial_limit)}</b> (eligible from {new Date(dialog.account.partial_eligible_from).toLocaleDateString("en-GB")}).</p>
                      <label className="mt-3 block text-sm font-semibold text-slate-700">Amount (₦)<input type="number" min="0.01" step="0.01" value={f.amount} onChange={(event) => setF({ ...f, amount: event.target.value })} className={inputClass} /></label>
                      {(early || over) && <p className="mt-3 rounded-xl bg-amber-50 p-3 text-xs text-amber-900">{early ? `Not yet ${policy?.min_months_for_partial} months. ` : ""}{over ? `Above the allowed ${money(dialog.account.partial_limit)}. ` : ""}This can be recorded, but management must approve it with a reason.</p>}
                    </>)}
                    <label className="mt-4 block text-sm font-semibold text-slate-700">Reason<input value={f.text} onChange={(event) => setF({ ...f, text: event.target.value })} placeholder="Optional" className={inputClass} /></label>
                  </>);
                })()}

                {dialog.kind === "forfeit" && (
                  <>
                    <p className="rounded-xl bg-red-50 p-3 text-sm text-red-800">The company keeps the whole <b>{money(dialog.account.balance)}</b>. Their contribution stops and any withdrawal in progress is cancelled. Use this for dismissal over theft or other serious misconduct.</p>
                    <label className="mt-4 block text-sm font-semibold text-slate-700">Reason (required)<input value={f.text} onChange={(event) => setF({ ...f, text: event.target.value })} className={inputClass} /></label>
                  </>
                )}

                {dialog.kind === "adjust" && (
                  <>
                    <p className="text-sm text-slate-600">Held: <b>{money(dialog.account.balance)}</b>. Use this to correct a mistake. Positive adds, negative removes.</p>
                    <label className="mt-4 block text-sm font-semibold text-slate-700">Amount (₦, can be negative)<input type="number" step="0.01" value={f.amount} onChange={(event) => setF({ ...f, amount: event.target.value })} className={inputClass} /></label>
                    <label className="mt-4 block text-sm font-semibold text-slate-700">Reason (required)<input value={f.text} onChange={(event) => setF({ ...f, text: event.target.value })} className={inputClass} /></label>
                  </>
                )}

                {dialog.kind === "decide" && (
                  <>
                    <p className="text-sm text-slate-600">{dialog.withdrawal.amount ? money(dialog.withdrawal.amount) : "Whole balance"} · currently held {money(dialog.withdrawal.balance)}</p>
                    {dialog.action === "pay" && <label className="mt-4 block text-sm font-semibold text-slate-700">Date paid<input type="date" value={f.date} onChange={(event) => setF({ ...f, date: event.target.value })} className={inputClass} /></label>}
                    {dialog.action === "approve" && dialog.withdrawal.rule_exceptions && <p className="mt-3 whitespace-pre-line rounded-xl bg-amber-50 p-3 text-xs text-amber-900"><b>Outside policy:</b>{"\n"}{dialog.withdrawal.rule_exceptions}</p>}
                    {dialog.action !== "cancel" && <label className="mt-4 block text-sm font-semibold text-slate-700">{dialog.action === "pay" ? "Payment reference (optional)" : dialog.action === "decline" || (dialog.action === "approve" && dialog.withdrawal.rule_exceptions) ? "Reason (required)" : "Comment (optional)"}<input value={f.text} onChange={(event) => setF({ ...f, text: event.target.value })} className={inputClass} /></label>}
                  </>
                )}

                {dialog.kind === "ledger" && (
                  <table className="w-full text-left text-sm">
                    <thead className="text-xs uppercase text-slate-500"><tr><th className="py-2">Date</th><th>What</th><th className="text-right">Amount</th></tr></thead>
                    <tbody className="divide-y divide-slate-100">
                      {dialog.entries.map((entry) => <tr key={entry.id}><td className="py-2 pr-3 text-slate-500">{new Date(entry.entry_date).toLocaleDateString("en-GB")}</td><td className="pr-3">{entry.entry_type_label}{entry.note ? <span className="block text-xs text-slate-500">{entry.note}</span> : null}</td><td className={`py-2 text-right font-semibold ${Number(entry.amount) < 0 ? "text-red-600" : "text-emerald-700"}`}>{Number(entry.amount) < 0 ? "-" : "+"}{money(Math.abs(Number(entry.amount)))}</td></tr>)}
                      {!dialog.entries.length && <tr><td colSpan={3} className="py-6 text-center text-slate-500">Nothing recorded yet.</td></tr>}
                    </tbody>
                    <tfoot><tr className="border-t border-slate-300"><td colSpan={2} className="py-3 font-bold">Held by company</td><td className="py-3 text-right font-bold">{money(dialog.account.balance)}</td></tr></tfoot>
                  </table>
                )}
              </div>
              <div className="flex justify-end gap-3 border-t border-slate-200 p-4">
                <button type="button" onClick={() => setDialog(null)} className="rounded-xl border border-slate-300 px-4 py-2.5 text-sm font-semibold text-slate-700">{dialog.kind === "ledger" ? "Close" : "Back"}</button>
                {dialog.kind !== "ledger" && <button type="button" disabled={busy || (dialog.kind === "decide" && dialog.action === "decline" && !f.text.trim()) || (dialog.kind === "adjust" && (!f.text.trim() || !f.amount)) || (dialog.kind === "forfeit" && !f.text.trim()) || (dialog.kind === "decide" && dialog.action === "approve" && !!dialog.withdrawal.rule_exceptions && !f.text.trim())} onClick={confirm} className="rounded-xl bg-slate-900 px-4 py-2.5 text-sm font-semibold text-white disabled:bg-slate-400">{busy ? "Saving..." : "Confirm"}</button>}
              </div>
            </div>
          </div>
        )}
      </main>
    </div>
  );
}

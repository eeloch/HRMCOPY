"use client";

import { FormEvent, useEffect, useEffectEvent, useState } from "react";
import { useRouter } from "next/navigation";

import Sidebar from "@/components/Sidebar";
import { AppCard, PageHeader, Section } from "@/components/ui";
import { apiFetch, getAccessToken, getCurrentUser, type CurrentUser } from "@/lib/api";

type Advance = {
  id: number;
  employee: number;
  employee_name: string;
  employee_number: string;
  department_name: string | null;
  amount: string;
  reason: string;
  repayment_months: number;
  instalment: string;
  deduct_from_label: string;
  status: string;
  status_label: string;
  amount_repaid: string;
  balance: string;
  recorded_by_name: string | null;
  created_at: string;
  decided_by_name: string | null;
  decision_comment: string;
  paid_by_name: string | null;
  paid_on: string | null;
  payment_reference: string;
  employee_basic_salary: string | null;
  other_outstanding: string;
};
type Employee = { id: number; employee_id: string; full_name: string };
type Action = { advance: Advance; kind: "decline" | "pay" | "cancel" | "approve" };

const badge: Record<string, string> = {
  requested: "bg-amber-100 text-amber-800",
  approved: "bg-blue-100 text-blue-800",
  declined: "bg-red-100 text-red-700",
  paid: "bg-violet-100 text-violet-800",
  repaid: "bg-emerald-100 text-emerald-800",
  cancelled: "bg-slate-200 text-slate-600",
};
const tabs = [
  { key: "requested", label: "Awaiting approval" },
  { key: "approved", label: "Awaiting payment" },
  { key: "paid", label: "Being repaid" },
  { key: "", label: "All" },
];
const monthNames = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
const money = (value: string | number) => `₦${Number(value || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
const inputClass = "w-full rounded-xl border border-slate-300 bg-white px-3 py-2.5 text-sm outline-none focus:ring-2 focus:ring-blue-500";

function apiError(data: unknown, fallback: string) {
  if (data && typeof data === "object") {
    const payload = data as Record<string, unknown>;
    if (typeof payload.detail === "string") return payload.detail;
    const first = Object.values(payload).flat()[0];
    if (typeof first === "string") return first;
  }
  return fallback;
}

function nextMonthDefault() {
  const now = new Date();
  const next = new Date(now.getFullYear(), now.getMonth() + 1, 1);
  return { year: String(next.getFullYear()), month: String(next.getMonth() + 1) };
}

export default function AdvancesPage() {
  const router = useRouter();
  const [user, setUser] = useState<CurrentUser | null>(null);
  const [advances, setAdvances] = useState<Advance[]>([]);
  const [employees, setEmployees] = useState<Employee[]>([]);
  const [tab, setTab] = useState("requested");
  const [loading, setLoading] = useState(true);
  const [acting, setActing] = useState(false);
  const [error, setError] = useState("");
  const [feedback, setFeedback] = useState("");
  const [query, setQuery] = useState("");
  const [form, setForm] = useState({ employee: "", amount: "", repayment_months: "1", reason: "", ...(() => { const d = nextMonthDefault(); return { deduct_from_year: d.year, deduct_from_month: d.month }; })() });
  const [action, setAction] = useState<Action | null>(null);
  const [actionText, setActionText] = useState("");
  const [actionDate, setActionDate] = useState(new Date().toISOString().slice(0, 10));

  const canRecord = !!user?.permissions.record_salary_advance;
  const canApprove = !!user?.permissions.approve_salary_advance;
  const canPay = !!user?.permissions.pay_salary_advance;

  async function load(silent = false) {
    if (!silent) setLoading(true);
    setError("");
    try {
      const me = await getCurrentUser();
      setUser(me);
      if (!(me.permissions.record_salary_advance || me.permissions.approve_salary_advance || me.permissions.pay_salary_advance)) return;
      const response = await apiFetch("/advances/");
      if (!response.ok) throw new Error(apiError(await response.json().catch(() => null), "Unable to load salary advances."));
      setAdvances((await response.json()).results || []);
      if (me.permissions.record_salary_advance && !employees.length) {
        const employeeResponse = await apiFetch("/employees/");
        if (employeeResponse.ok) setEmployees((await employeeResponse.json()).results || []);
      }
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "Unable to load salary advances.");
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

  async function send(path: string, body: object, success: string) {
    setActing(true);
    setError("");
    try {
      const response = await apiFetch(path, { method: "POST", body: JSON.stringify(body) });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(apiError(data, "That did not work."));
      setFeedback(success);
      await load(true);
      return true;
    } catch (actionError) {
      setError(actionError instanceof Error ? actionError.message : "That did not work.");
      return false;
    } finally {
      setActing(false);
    }
  }

  async function record(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const saved = await send("/advances/", {
      employee: Number(form.employee), amount: form.amount, repayment_months: Number(form.repayment_months), reason: form.reason,
      deduct_from_year: Number(form.deduct_from_year), deduct_from_month: Number(form.deduct_from_month),
    }, "Advance recorded. It now waits for management approval.");
    if (saved) setForm((current) => ({ ...current, employee: "", amount: "", reason: "", repayment_months: "1" }));
  }

  async function confirmAction() {
    if (!action) return;
    const { advance, kind } = action;
    const paths = { approve: "approve", decline: "decline", pay: "pay", cancel: "cancel" };
    const body = kind === "pay" ? { paid_on: actionDate, reference: actionText } : { comment: actionText };
    const messages = { approve: "Advance approved. Finance can now pay it out.", decline: "Advance declined.", pay: "Marked as paid. Repayment will come out of payroll automatically.", cancel: "Advance cancelled." };
    if (await send(`/advances/${advance.id}/${paths[kind]}/`, body, messages[kind])) { setAction(null); setActionText(""); }
  }

  const visible = advances.filter((item) => !tab || item.status === tab);
  const counts = (key: string) => advances.filter((item) => !key || item.status === key).length;
  const filteredEmployees = employees.filter((employee) => String(employee.id) === form.employee || `${employee.full_name} ${employee.employee_id}`.toLowerCase().includes(query.trim().toLowerCase()));
  const instalment = Number(form.amount) > 0 ? Number(form.amount) / Math.max(1, Number(form.repayment_months) || 1) : 0;
  const years = [new Date().getFullYear(), new Date().getFullYear() + 1];

  if (!loading && user && !canRecord && !canApprove && !canPay) {
    return <div className="min-h-screen bg-slate-100"><Sidebar /><main className="ml-64 p-8"><AppCard><p className="p-8 text-center text-slate-600">Your account does not have access to salary advances.</p></AppCard></main></div>;
  }

  return (
    <div className="min-h-screen bg-slate-100">
      <Sidebar />
      <main className="ml-64 min-w-0 p-4 md:p-8">
        <PageHeader title="Salary Advances" description="HR records the request, management approves it, finance pays it. Repayment then comes out of payroll automatically." />
        {feedback && <p className="mb-6 rounded-xl bg-emerald-50 p-4 text-sm text-emerald-800">{feedback}</p>}
        {error && <p className="mb-6 rounded-xl bg-red-50 p-4 text-sm text-red-700">{error}</p>}

        {canRecord && (
          <AppCard className="mb-6" title="Record an Advance Request">
            <form onSubmit={record} className="grid gap-4 md:grid-cols-3">
              <div>
                <label className="text-sm font-semibold text-slate-700">Employee</label>
                <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search by name or staff number..." className={`${inputClass} mt-1 mb-2`} />
                <select required value={form.employee} onChange={(event) => setForm({ ...form, employee: event.target.value })} className={inputClass}>
                  <option value="">Select an employee</option>
                  {filteredEmployees.map((employee) => <option key={employee.id} value={employee.id}>{employee.full_name} ({employee.employee_id})</option>)}
                </select>
              </div>
              <div>
                <label className="text-sm font-semibold text-slate-700">Amount (₦)</label>
                <input required type="number" min="1" step="0.01" value={form.amount} onChange={(event) => setForm({ ...form, amount: event.target.value })} className={`${inputClass} mt-1`} />
                <label className="mt-3 block text-sm font-semibold text-slate-700">Repay over how many months?</label>
                <input required type="number" min="1" max="24" step="1" value={form.repayment_months} onChange={(event) => setForm({ ...form, repayment_months: event.target.value })} className={`${inputClass} mt-1`} />
                {instalment > 0 && <p className="mt-1 text-xs text-slate-500">{money(instalment)} a month</p>}
              </div>
              <div>
                <label className="text-sm font-semibold text-slate-700">First deduction from payroll of</label>
                <div className="mt-1 grid grid-cols-2 gap-2">
                  <select value={form.deduct_from_month} onChange={(event) => setForm({ ...form, deduct_from_month: event.target.value })} className={inputClass}>{monthNames.map((name, index) => <option key={name} value={index + 1}>{name}</option>)}</select>
                  <select value={form.deduct_from_year} onChange={(event) => setForm({ ...form, deduct_from_year: event.target.value })} className={inputClass}>{years.map((year) => <option key={year} value={year}>{year}</option>)}</select>
                </div>
                <label className="mt-3 block text-sm font-semibold text-slate-700">Reason</label>
                <input value={form.reason} onChange={(event) => setForm({ ...form, reason: event.target.value })} placeholder="Optional" className={`${inputClass} mt-1`} />
              </div>
              <div className="md:col-span-3 text-right">
                <button disabled={acting} className="rounded-xl bg-blue-600 px-5 py-2.5 text-sm font-semibold text-white disabled:bg-blue-300">{acting ? "Saving..." : "Record Request"}</button>
              </div>
            </form>
          </AppCard>
        )}

        <div className="mb-4 flex flex-wrap gap-2">
          {tabs.map((item) => (
            <button key={item.key} type="button" onClick={() => setTab(item.key)} className={`rounded-full px-4 py-2 text-sm font-semibold ${tab === item.key ? "bg-slate-900 text-white" : "bg-white text-slate-700 hover:bg-slate-50"}`}>
              {item.label} <span className="ml-1 opacity-70">{counts(item.key)}</span>
            </button>
          ))}
        </div>

        <Section title="Advances" subtitle={`${visible.length} shown`}>
          {loading ? <div className="h-40 animate-pulse bg-slate-100" /> : visible.length ? (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[980px] text-left">
                <thead className="bg-slate-50 text-xs font-semibold uppercase tracking-wide text-slate-500">
                  <tr><th className="px-5 py-3">Employee</th><th className="px-5 py-3">Advance</th><th className="px-5 py-3">Repayment</th><th className="px-5 py-3">Status</th><th className="px-5 py-3">Trail</th><th className="px-5 py-3" /></tr>
                </thead>
                <tbody className="divide-y divide-slate-100 align-top">
                  {visible.map((item) => (
                    <tr key={item.id} className="hover:bg-slate-50">
                      <td className="px-5 py-4">
                        <p className="font-semibold text-slate-900">{item.employee_name}</p>
                        <p className="text-sm text-slate-500">{item.employee_number}{item.department_name ? ` · ${item.department_name}` : ""}</p>
                        {(canApprove || canPay) && item.status === "requested" && (
                          <p className="mt-1 text-xs text-slate-500">
                            {item.employee_basic_salary ? `Salary ${money(item.employee_basic_salary)}. ` : ""}
                            {Number(item.other_outstanding) > 0 ? <span className="font-semibold text-amber-700">Still owes {money(item.other_outstanding)} from other advances.</span> : "No other advance owed."}
                          </p>
                        )}
                      </td>
                      <td className="px-5 py-4"><p className="font-semibold text-slate-900">{money(item.amount)}</p>{item.reason && <p className="mt-1 max-w-[220px] text-sm text-slate-500">{item.reason}</p>}</td>
                      <td className="px-5 py-4 text-sm text-slate-700">
                        <p>{money(item.instalment)} × {item.repayment_months} month{item.repayment_months === 1 ? "" : "s"}</p>
                        <p className="text-slate-500">from {item.deduct_from_label}</p>
                        {["paid", "repaid"].includes(item.status) && <p className="mt-1 font-semibold">Repaid {money(item.amount_repaid)} · owes {money(item.balance)}</p>}
                      </td>
                      <td className="px-5 py-4"><span className={`rounded-full px-3 py-1 text-xs font-semibold ${badge[item.status] || "bg-slate-100"}`}>{item.status_label}</span></td>
                      <td className="px-5 py-4 text-xs text-slate-500">
                        <p>Recorded by {item.recorded_by_name || "-"} · {new Date(item.created_at).toLocaleDateString("en-GB")}</p>
                        {item.decided_by_name && <p>{item.status === "declined" ? "Declined" : "Approved"} by {item.decided_by_name}{item.decision_comment ? `: ${item.decision_comment}` : ""}</p>}
                        {item.paid_by_name && <p>Paid by {item.paid_by_name}{item.paid_on ? ` on ${new Date(item.paid_on).toLocaleDateString("en-GB")}` : ""}{item.payment_reference ? ` (ref ${item.payment_reference})` : ""}</p>}
                      </td>
                      <td className="px-5 py-4 text-right">
                        <div className="flex flex-wrap justify-end gap-2">
                          {item.status === "requested" && canApprove && <button type="button" disabled={acting} onClick={() => setAction({ advance: item, kind: "approve" })} className="rounded-xl bg-emerald-600 px-3 py-2 text-sm font-semibold text-white disabled:opacity-50">Approve</button>}
                          {item.status === "requested" && canApprove && <button type="button" disabled={acting} onClick={() => setAction({ advance: item, kind: "decline" })} className="rounded-xl border border-red-300 px-3 py-2 text-sm font-semibold text-red-700 disabled:opacity-50">Decline</button>}
                          {item.status === "approved" && canPay && <button type="button" disabled={acting} onClick={() => setAction({ advance: item, kind: "pay" })} className="rounded-xl bg-violet-600 px-3 py-2 text-sm font-semibold text-white disabled:opacity-50">Mark as Paid</button>}
                          {["requested", "approved"].includes(item.status) && (canRecord || canApprove) && <button type="button" disabled={acting} onClick={() => setAction({ advance: item, kind: "cancel" })} className="rounded-xl border border-slate-300 px-3 py-2 text-sm font-semibold text-slate-700 disabled:opacity-50">Cancel</button>}
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : <p className="p-12 text-center text-slate-500">Nothing here.</p>}
        </Section>

        {action && (
          <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/50 p-4">
            <div className="w-full max-w-md rounded-2xl bg-white p-6 shadow-xl">
              <h2 className="text-lg font-bold text-slate-900">
                {{ approve: "Approve advance", decline: "Decline advance", pay: "Mark advance as paid", cancel: "Cancel advance" }[action.kind]}
              </h2>
              <p className="mt-1 text-sm text-slate-600">{action.advance.employee_name} · {money(action.advance.amount)}</p>
              {action.kind === "pay" && <>
                <label className="mt-4 block text-sm font-semibold text-slate-700">Date paid</label>
                <input type="date" value={actionDate} onChange={(event) => setActionDate(event.target.value)} className={`${inputClass} mt-1`} />
              </>}
              <label className="mt-4 block text-sm font-semibold text-slate-700">{action.kind === "pay" ? "Payment reference (optional)" : action.kind === "decline" ? "Reason (required)" : "Comment (optional)"}</label>
              <input value={actionText} onChange={(event) => setActionText(event.target.value)} className={`${inputClass} mt-1`} />
              <div className="mt-6 flex justify-end gap-3">
                <button type="button" onClick={() => { setAction(null); setActionText(""); }} className="rounded-xl border border-slate-300 px-4 py-2.5 text-sm font-semibold text-slate-700">Back</button>
                <button type="button" disabled={acting || (action.kind === "decline" && !actionText.trim())} onClick={() => void confirmAction()} className="rounded-xl bg-slate-900 px-4 py-2.5 text-sm font-semibold text-white disabled:bg-slate-400">Confirm</button>
              </div>
            </div>
          </div>
        )}
      </main>
    </div>
  );
}

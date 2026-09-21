"use client";

import { useEffect, useEffectEvent, useMemo, useState } from "react";

import { Section } from "@/components/ui";
import { apiFetch } from "@/lib/api";

type Row = { id: number; employee_name: string; employee_number: string; work_date: string; quantity: number; used: number; pays: string; pays_label: string; reason: string; status: string; authorised_by: string | null };
type Person = { id: number; employee_id: string; full_name: string; department_name: string | null };

const statusStyle: Record<string, string> = { waiting: "bg-amber-100 text-amber-800", collected: "bg-emerald-100 text-emerald-800", "not used": "bg-slate-200 text-slate-600", cancelled: "bg-slate-200 text-slate-600" };
const inputClass = "mt-1 w-full rounded-xl border border-slate-300 bg-white px-3 py-2.5 text-sm outline-none focus:ring-2 focus:ring-blue-500";

function apiError(data: unknown, fallback: string) {
  if (data && typeof data === "object" && typeof (data as { detail?: unknown }).detail === "string") return (data as { detail: string }).detail;
  return fallback;
}

/** Let someone collect extra meal ticket(s) today, deciding in advance who pays. */
export function ExtraTicketAuthorizations({ canReview }: { canReview: boolean }) {
  const [rows, setRows] = useState<Row[]>([]);
  const [people, setPeople] = useState<Person[]>([]);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [feedback, setFeedback] = useState("");
  const [form, setForm] = useState({ q: "", employee: "", quantity: "1", pays: "employee", reason: "" });

  async function load() {
    const response = await apiFetch("/meals/extra-authorizations/");
    if (response.ok) setRows((await response.json()).results || []);
  }
  const loadOnMount = useEffectEvent(() => { void load(); });
  useEffect(() => { const timer = window.setTimeout(loadOnMount, 0); return () => window.clearTimeout(timer); }, []);

  async function openForm() {
    setError(""); setForm({ q: "", employee: "", quantity: "1", pays: "employee", reason: "" }); setOpen(true);
    if (!people.length) { const response = await apiFetch("/employees/"); if (response.ok) setPeople((await response.json()).results || []); }
  }

  async function send(path: string, body: object | null, success: string) {
    setBusy(true); setError("");
    try {
      const response = await apiFetch(path, { method: "POST", ...(body ? { body: JSON.stringify(body) } : {}) });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(apiError(data, "That did not work."));
      setFeedback(success); setOpen(false);
      await load();
    } catch (actionError) { setError(actionError instanceof Error ? actionError.message : "That did not work."); }
    finally { setBusy(false); }
  }

  const matches = useMemo(() => people.filter((p) => String(p.id) === form.employee || `${p.full_name} ${p.employee_id}`.toLowerCase().includes(form.q.trim().toLowerCase())).slice(0, 60), [people, form.q, form.employee]);

  return (
    <>
      <Section className="mb-6" title="Extra ticket authorisations" subtitle="Let someone collect an extra ticket today and say who pays. They are switched on at the terminal for it, and the ticket is decided automatically when they scan." actions={canReview ? <button type="button" onClick={() => void openForm()} className="rounded-xl bg-blue-600 px-4 py-2.5 text-sm font-semibold text-white">+ Authorise an extra ticket</button> : undefined}>
        {feedback && <p className="m-4 rounded-xl bg-emerald-50 p-3 text-sm text-emerald-800">{feedback}</p>}
        {rows.length ? (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[820px] text-left">
              <thead className="bg-slate-50 text-xs font-semibold uppercase tracking-wide text-slate-500"><tr><th className="px-5 py-3">Employee</th><th className="px-5 py-3">Day</th><th className="px-5 py-3">Tickets</th><th className="px-5 py-3">Who pays</th><th className="px-5 py-3">Status</th><th className="px-5 py-3" /></tr></thead>
              <tbody className="divide-y divide-slate-100">
                {rows.map((r) => (
                  <tr key={r.id}>
                    <td className="px-5 py-3"><p className="font-semibold text-slate-900">{r.employee_name}</p><p className="text-xs text-slate-500">{r.employee_number}{r.authorised_by ? ` · authorised by ${r.authorised_by}` : ""}{r.reason ? ` · ${r.reason}` : ""}</p></td>
                    <td className="px-5 py-3 text-sm text-slate-700">{new Date(r.work_date).toLocaleDateString("en-GB")}</td>
                    <td className="px-5 py-3 text-sm text-slate-700">{r.used} of {r.quantity} collected</td>
                    <td className="px-5 py-3 text-sm text-slate-700">{r.pays_label}</td>
                    <td className="px-5 py-3"><span className={`rounded-full px-3 py-1 text-xs font-semibold ${statusStyle[r.status] || "bg-slate-100"}`}>{r.status === "waiting" ? "Waiting to be collected" : r.status === "collected" ? "Collected" : r.status === "not used" ? "Not used" : "Withdrawn"}</span></td>
                    <td className="px-5 py-3 text-right">{canReview && r.status === "waiting" && <button type="button" disabled={busy} onClick={() => void send(`/meals/extra-authorizations/${r.id}/cancel/`, null, "Authorisation withdrawn.")} className="rounded-xl border border-slate-300 px-3 py-2 text-sm font-semibold text-slate-700">Withdraw</button>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : <p className="p-8 text-center text-sm text-slate-500">No extra tickets authorised in the last week.</p>}
      </Section>

      {open && (
        <div className="fixed inset-0 z-30 flex items-center justify-center bg-slate-950/40 p-4">
          <div className="w-full max-w-lg rounded-2xl bg-white p-6 shadow-xl">
            <h2 className="text-lg font-bold text-slate-900">Authorise an extra ticket - today</h2>
            {error && <p className="mt-3 rounded-xl bg-red-50 p-3 text-sm text-red-700">{error}</p>}
            <label className="mt-4 block text-sm font-semibold text-slate-700">Employee
              <input value={form.q} onChange={(event) => setForm({ ...form, q: event.target.value })} placeholder="Search name or staff number..." className={inputClass} />
              <select value={form.employee} onChange={(event) => setForm({ ...form, employee: event.target.value })} className={`${inputClass} mt-2`}><option value="">Select a person</option>{matches.map((p) => <option key={p.id} value={p.id}>{p.full_name} ({p.employee_id}){p.department_name ? ` - ${p.department_name}` : ""}</option>)}</select>
            </label>
            <label className="mt-3 block text-sm font-semibold text-slate-700">How many extra tickets<input type="number" min="1" max="5" value={form.quantity} onChange={(event) => setForm({ ...form, quantity: event.target.value })} className={inputClass} /></label>
            <fieldset className="mt-3 text-sm font-semibold text-slate-700">Who pays for it
              <label className="mt-2 flex items-center gap-2 font-normal"><input type="radio" checked={form.pays === "employee"} onChange={() => setForm({ ...form, pays: "employee" })} /> The employee - deducted from their pay</label>
              <label className="mt-1 flex items-center gap-2 font-normal"><input type="radio" checked={form.pays === "company"} onChange={() => setForm({ ...form, pays: "company" })} /> The company - no deduction (the vendor is still paid)</label>
            </fieldset>
            <label className="mt-3 block text-sm font-semibold text-slate-700">Reason (optional)<input value={form.reason} onChange={(event) => setForm({ ...form, reason: event.target.value })} placeholder="e.g. worked a double shift" className={inputClass} /></label>
            <div className="mt-6 flex justify-end gap-3">
              <button type="button" onClick={() => setOpen(false)} className="rounded-xl border border-slate-300 px-4 py-2.5 text-sm font-semibold text-slate-700">Cancel</button>
              <button type="button" disabled={busy || !form.employee || !(Number(form.quantity) >= 1)} onClick={() => void send("/meals/extra-authorizations/", { employee: Number(form.employee), quantity: Number(form.quantity), pays: form.pays, reason: form.reason }, "Extra ticket authorised. They can scan now.")} className="rounded-xl bg-blue-600 px-4 py-2.5 text-sm font-semibold text-white disabled:bg-blue-300">{busy ? "Saving..." : "Authorise"}</button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}

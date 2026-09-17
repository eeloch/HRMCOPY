"use client";

import { FormEvent, useState } from "react";

import { AppCard, StatusBadge } from "@/components/ui";
import { apiFetch } from "@/lib/api";
import type { EmployeePayroll, PayrollLineItem } from "./types";

function money(value: string) { return `₦${Number(value || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`; }

function LineItemRow({ item, editable, submitting, onRemove }: { item: PayrollLineItem; editable: boolean; submitting: boolean; onRemove: (id: number) => void }) {
  return <div className="flex items-center justify-between gap-4 p-4"><div><p className="font-semibold text-slate-900">{item.description}</p><p className="text-sm text-slate-500">{item.code} · {item.item_type}</p>{item.is_system_generated && <><p className="mt-1 text-xs text-blue-700">System source: {item.source_type} #{item.source_reference}</p>{item.metadata?.minutes_affected !== undefined && <p className="mt-1 text-xs text-slate-600">{item.metadata.minutes_affected} minute(s) · {item.metadata.policy_band === undefined ? "" : String(item.metadata.policy_band).replace(/_/g, " ")}</p>}</>}</div><div className="flex items-center gap-4"><span className={item.item_type === "deduction" ? "font-semibold text-red-700" : "font-semibold text-emerald-700"}>{money(item.amount)}</span>{editable && !item.is_system_generated && <button type="button" disabled={submitting} onClick={() => onRemove(item.id)} className="text-sm font-semibold text-red-600 disabled:text-red-300">Delete</button>}</div></div>;
}

const lineItemFormInitial = () => ({ code: "", description: "", amount: "" });

function LineItemForm({ itemType, buttonLabel, submitting, onSubmit }: { itemType: "earning" | "deduction"; buttonLabel: string; submitting: boolean; onSubmit: (values: { item_type: "earning" | "deduction"; code: string; description: string; amount: string }) => Promise<boolean> }) {
  const [values, setValues] = useState(lineItemFormInitial);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (await onSubmit({ item_type: itemType, ...values })) setValues(lineItemFormInitial());
  }

  return <form onSubmit={handleSubmit} className="mt-3 grid gap-3 rounded-xl bg-slate-50 p-4 md:grid-cols-4"><input required value={values.code} onChange={(event) => setValues({ ...values, code: event.target.value })} placeholder="Code" className="rounded-xl border border-slate-300 bg-white px-3 py-2.5" /><input required value={values.description} onChange={(event) => setValues({ ...values, description: event.target.value })} placeholder="Description" className="rounded-xl border border-slate-300 bg-white px-3 py-2.5" /><input required min="0" step="0.01" type="number" value={values.amount} onChange={(event) => setValues({ ...values, amount: event.target.value })} placeholder="Amount" className="rounded-xl border border-slate-300 bg-white px-3 py-2.5" /><button disabled={submitting} className="rounded-xl bg-blue-600 px-4 py-2.5 text-sm font-semibold text-white disabled:bg-blue-300">{submitting ? "Saving..." : buttonLabel}</button></form>;
}

export function PayrollRecordModal({ payroll, editable, onClose, onChanged }: { payroll: EmployeePayroll; editable: boolean; onClose: () => void; onChanged: () => void }) {
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  async function addItem(values: { item_type: "earning" | "deduction"; code: string; description: string; amount: string }) {
    setSubmitting(true); setError("");
    try {
      const response = await apiFetch(`/payroll/employee-payrolls/${payroll.id}/line-items/`, { method: "POST", body: JSON.stringify(values) });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.detail || data.amount?.[0] || "Unable to add the line item.");
      onChanged();
      return true;
    } catch (actionError) { setError(actionError instanceof Error ? actionError.message : "Unable to add the line item."); return false; } finally { setSubmitting(false); }
  }

  async function removeItem(id: number) {
    setSubmitting(true); setError("");
    try { const response = await apiFetch(`/payroll/line-items/${id}/`, { method: "DELETE" }); if (!response.ok) { const data = await response.json().catch(() => ({})); throw new Error(data.detail || "Unable to remove the line item."); } onChanged(); }
    catch (actionError) { setError(actionError instanceof Error ? actionError.message : "Unable to remove the line item."); } finally { setSubmitting(false); }
  }

  const earnings = payroll.line_items?.filter((item) => item.item_type === "earning") ?? [];
  const offences = payroll.line_items?.filter((item) => item.item_type === "deduction" && item.source_type === "employee_offence") ?? [];
  const deductions = payroll.line_items?.filter((item) => item.item_type === "deduction" && item.source_type !== "employee_offence") ?? [];

  return <div className="fixed inset-0 z-50 overflow-y-auto bg-slate-950/40 p-4"><div className="mx-auto my-8 w-full max-w-3xl rounded-2xl bg-white p-6 shadow-2xl"><div className="flex items-start justify-between gap-4"><div><h2 className="text-xl font-bold text-slate-900">{payroll.employee_name}</h2><p className="mt-1 text-sm text-slate-500">{payroll.employee_id} · {payroll.department_name || "No department"}</p></div><StatusBadge status={payroll.status} /></div><div className="mt-5 grid gap-3 sm:grid-cols-4">{[["Basic", payroll.basic_salary], ["Gross", payroll.gross_earnings], ["Deductions", payroll.total_deductions], ["Net", payroll.net_pay]].map(([label, value]) => <AppCard key={label} className="bg-slate-50 p-3"><p className="text-xs font-semibold uppercase text-slate-500">{label}</p><p className="mt-1 font-bold text-slate-900">{money(value)}</p></AppCard>)}</div>
    {!editable && <p className="mt-5 rounded-xl bg-amber-50 p-4 text-sm text-amber-800">This payroll period has been approved, paid, or closed and can no longer be edited - manual earnings can only be added while the period is in Draft, Processing, or Review.</p>}
    <div className="mt-6"><h3 className="font-bold text-slate-900">Earnings</h3><div className="mt-3 divide-y divide-slate-100 rounded-xl border border-slate-200">{earnings.length ? earnings.map((item) => <LineItemRow key={item.id} item={item} editable={editable} submitting={submitting} onRemove={(id) => void removeItem(id)} />) : <p className="p-6 text-center text-sm text-slate-500">No earnings line items.</p>}</div>{editable && <LineItemForm itemType="earning" buttonLabel="Add Earning" submitting={submitting} onSubmit={addItem} />}</div>
    <div className="mt-6"><h3 className="font-bold text-slate-900">Deductions</h3><div className="mt-3 divide-y divide-slate-100 rounded-xl border border-slate-200">{deductions.length ? deductions.map((item) => <LineItemRow key={item.id} item={item} editable={editable} submitting={submitting} onRemove={(id) => void removeItem(id)} />) : <p className="p-6 text-center text-sm text-slate-500">No deductions.</p>}</div></div>
    <div className="mt-6"><h3 className="font-bold text-slate-900">Offences</h3><p className="mt-1 text-xs text-slate-500">Approved employee offence fines. Record and approve new offences from the Offences page.</p><div className="mt-3 divide-y divide-slate-100 rounded-xl border border-slate-200">{offences.length ? offences.map((item) => <LineItemRow key={item.id} item={item} editable={editable} submitting={submitting} onRemove={(id) => void removeItem(id)} />) : <p className="p-6 text-center text-sm text-slate-500">No offence deductions for this period.</p>}</div></div>
    {error && <p className="mt-4 rounded-xl bg-red-50 p-3 text-sm text-red-700">{error}</p>}<div className="mt-6 text-right"><button type="button" disabled={submitting} onClick={onClose} className="rounded-xl border border-slate-300 px-4 py-2.5 text-sm font-semibold text-slate-700">Close</button></div></div></div>;
}

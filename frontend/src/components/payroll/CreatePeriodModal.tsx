"use client";

import { FormEvent, useState } from "react";

import { apiFetch } from "@/lib/api";
import type { PayrollPeriod } from "./types";

export function CreatePeriodModal({ onClose, onCreated }: { onClose: () => void; onCreated: (period: PayrollPeriod) => void }) {
  const now = new Date();
  const [year, setYear] = useState(String(now.getFullYear()));
  const [month, setMonth] = useState(String(now.getMonth() + 1));
  const [notes, setNotes] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitting(true);
    setError("");
    try {
      const response = await apiFetch("/payroll/periods/", { method: "POST", body: JSON.stringify({ year: Number(year), month: Number(month), notes }) });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.detail || "Unable to create the payroll period.");
      onCreated(data as PayrollPeriod);
    } catch (submitError) {
      setError(submitError instanceof Error ? submitError.message : "Unable to create the payroll period.");
    } finally {
      setSubmitting(false);
    }
  }

  return <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/40 p-4"><form onSubmit={submit} className="w-full max-w-md rounded-2xl bg-white p-6 shadow-2xl"><h2 className="text-xl font-bold text-slate-900">Create Payroll Period</h2><p className="mt-1 text-sm text-slate-500">Creates a new draft only. Payroll records are generated separately.</p><div className="mt-5 grid gap-4 sm:grid-cols-2"><label className="text-sm font-semibold text-slate-700">Year<input required min="2000" type="number" value={year} onChange={(event) => setYear(event.target.value)} className="mt-2 w-full rounded-xl border border-slate-300 px-3 py-2.5 font-normal" /></label><label className="text-sm font-semibold text-slate-700">Month<select value={month} onChange={(event) => setMonth(event.target.value)} className="mt-2 w-full rounded-xl border border-slate-300 bg-white px-3 py-2.5 font-normal">{Array.from({ length: 12 }, (_, index) => <option key={index + 1} value={index + 1}>{new Date(2020, index).toLocaleString(undefined, { month: "long" })}</option>)}</select></label></div><label className="mt-4 block text-sm font-semibold text-slate-700">Notes (optional)<textarea value={notes} onChange={(event) => setNotes(event.target.value)} className="mt-2 min-h-24 w-full rounded-xl border border-slate-300 p-3 font-normal" /></label>{error && <p className="mt-4 rounded-xl bg-red-50 p-3 text-sm text-red-700">{error}</p>}<div className="mt-6 flex justify-end gap-3"><button type="button" disabled={submitting} onClick={onClose} className="rounded-xl border border-slate-300 px-4 py-2.5 text-sm font-semibold text-slate-700">Cancel</button><button disabled={submitting} className="rounded-xl bg-blue-600 px-4 py-2.5 text-sm font-semibold text-white disabled:bg-blue-300">{submitting ? "Creating..." : "Create Draft"}</button></div></form></div>;
}

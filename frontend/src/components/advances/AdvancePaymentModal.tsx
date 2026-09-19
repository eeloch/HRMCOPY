"use client";

import { useEffect, useState } from "react";

import { apiFetch } from "@/lib/api";

type Issue = { employee_id: string; employee_name: string; net_pay: string; reason: string; fixable: boolean };
type Preview = { narration: string; ready_count: number; ready_total: string; padded: { employee_id: string; employee_name: string; account_number: string }[]; issues: Issue[] };

const money = (value: string) => `₦${Number(value || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

function message(data: unknown, fallback: string) {
  return data && typeof data === "object" && typeof (data as { detail?: unknown }).detail === "string" ? (data as { detail: string }).detail : fallback;
}

/** Bank file for the ticked advances, then (once the bank has them) mark them all paid in one go. */
export function AdvancePaymentModal({ ids: selection, onClose, onPaid }: { ids: number[]; onClose: () => void; onPaid: (count: number) => void }) {
  const [ids] = useState(selection); // the selection is fixed once the window opens
  const [preview, setPreview] = useState<Preview | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [downloaded, setDownloaded] = useState(false);
  const [paidOn, setPaidOn] = useState(new Date().toISOString().slice(0, 10));
  const [reference, setReference] = useState("");

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const response = await apiFetch("/advances/bank-upload/preview/", { method: "POST", body: JSON.stringify({ ids }) });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(message(data, "Unable to prepare the bank file."));
        if (!cancelled) setPreview(data);
      } catch (loadError) {
        if (!cancelled) setError(loadError instanceof Error ? loadError.message : "Unable to prepare the bank file.");
      }
    })();
    return () => { cancelled = true; };
  }, [ids]);

  async function download() {
    setBusy(true);
    setError("");
    try {
      const response = await apiFetch("/advances/bank-upload/", { method: "POST", body: JSON.stringify({ ids }) });
      if (!response.ok) throw new Error(message(await response.json().catch(() => ({})), "Unable to create the bank file."));
      const disposition = response.headers.get("Content-Disposition") || "";
      const name = /filename="([^"]+)"/.exec(disposition)?.[1] || "Salary Advances - Bank Upload.xlsx";
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement("a");
      link.href = url;
      link.download = name;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
      setDownloaded(true);
    } catch (downloadError) {
      setError(downloadError instanceof Error ? downloadError.message : "Unable to create the bank file.");
    } finally {
      setBusy(false);
    }
  }

  async function markPaid() {
    setBusy(true);
    setError("");
    try {
      const response = await apiFetch("/advances/mark-paid/", { method: "POST", body: JSON.stringify({ ids, paid_on: paidOn, reference }) });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(message(data, "Unable to mark these as paid."));
      onPaid(data.paid);
    } catch (payError) {
      setError(payError instanceof Error ? payError.message : "Unable to mark these as paid.");
    } finally {
      setBusy(false);
    }
  }

  const fix = preview?.issues.filter((issue) => issue.fixable) || [];
  const inputClass = "mt-1 w-full rounded-xl border border-slate-300 px-3 py-2.5 text-sm outline-none focus:ring-2 focus:ring-blue-500";

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/40 p-4">
      <div className="flex max-h-[90vh] w-full max-w-2xl flex-col rounded-2xl bg-white shadow-xl">
        <div className="border-b border-slate-200 p-6">
          <h2 className="text-xl font-bold text-slate-900">Pay {ids.length} salary advance{ids.length === 1 ? "" : "s"}</h2>
          <p className="mt-1 text-sm text-slate-500">Step 1: download the bank file for accounting to upload. Step 2: once the bank has them, mark them paid so repayment starts.</p>
        </div>
        <div className="overflow-y-auto p-6">
          {error && <p className="mb-4 rounded-xl bg-red-50 p-4 text-sm text-red-700">{error}</p>}
          {!preview && !error && <div className="h-24 animate-pulse rounded-xl bg-slate-100" />}
          {preview && (
            <>
              <div className="grid gap-3 sm:grid-cols-3">
                <div className="rounded-xl border border-slate-200 p-4"><p className="text-xs font-semibold uppercase text-slate-500">Payments in the file</p><p className="mt-1 text-2xl font-bold">{preview.ready_count}</p></div>
                <div className="rounded-xl border border-slate-200 p-4"><p className="text-xs font-semibold uppercase text-slate-500">Total</p><p className="mt-1 text-2xl font-bold">{money(preview.ready_total)}</p></div>
                <div className="rounded-xl border border-slate-200 p-4"><p className="text-xs font-semibold uppercase text-slate-500">Narration</p><p className="mt-1 text-2xl font-bold">{preview.narration}</p></div>
              </div>
              {fix.length > 0 && (
                <div className="mt-4 rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm">
                  <p className="font-semibold text-amber-900">{fix.length} {fix.length === 1 ? "person is" : "people are"} NOT in the file - bank details need fixing</p>
                  <ul className="mt-2 space-y-1 text-xs text-amber-800">{fix.map((issue) => <li key={issue.employee_id}>{issue.employee_name} ({issue.employee_id}) - {money(issue.net_pay)} - {issue.reason}</li>)}</ul>
                  <p className="mt-2 text-xs text-amber-800">Fix their account details on the employee profile and pay them separately. They stay in &ldquo;Awaiting payment&rdquo; and are not touched by step 2.</p>
                </div>
              )}
              {preview.padded.length > 0 && <p className="mt-4 rounded-xl bg-slate-50 p-3 text-xs text-slate-600">{preview.padded.length} short account number{preview.padded.length === 1 ? "" : "s"} padded with leading zeros - worth a quick check: {preview.padded.map((item) => `${item.employee_name} (${item.account_number})`).join(", ")}.</p>}

              <div className="mt-6 rounded-xl border border-slate-200 p-4">
                <p className="text-sm font-semibold text-slate-800">1. Bank file</p>
                <button type="button" disabled={busy || preview.ready_count === 0} onClick={() => void download()} className="mt-3 rounded-xl bg-emerald-600 px-4 py-2.5 text-sm font-semibold text-white disabled:bg-emerald-300">{busy && !downloaded ? "Creating..." : downloaded ? "Download again" : "Download Excel file"}</button>
              </div>

              <div className="mt-4 rounded-xl border border-slate-200 p-4">
                <p className="text-sm font-semibold text-slate-800">2. After the bank has paid: mark as paid</p>
                <p className="mt-1 text-xs text-slate-500">This marks the {preview.ready_count} advance{preview.ready_count === 1 ? "" : "s"} in the file paid and starts their repayment through payroll.{fix.length > 0 ? " People not in the file are skipped." : ""}</p>
                <div className="mt-3 grid gap-3 sm:grid-cols-2">
                  <label className="text-sm font-semibold text-slate-700">Date paid<input type="date" value={paidOn} onChange={(event) => setPaidOn(event.target.value)} className={inputClass} /></label>
                  <label className="text-sm font-semibold text-slate-700">Bank reference (optional)<input value={reference} onChange={(event) => setReference(event.target.value)} className={inputClass} /></label>
                </div>
                <button type="button" disabled={busy || !paidOn} onClick={() => void markPaid()} className="mt-3 rounded-xl bg-violet-600 px-4 py-2.5 text-sm font-semibold text-white disabled:bg-violet-300">Mark as paid</button>
              </div>
            </>
          )}
        </div>
        <div className="flex justify-end border-t border-slate-200 p-4"><button type="button" onClick={onClose} className="rounded-xl border border-slate-300 px-4 py-2.5 text-sm font-semibold text-slate-700">Close</button></div>
      </div>
    </div>
  );
}

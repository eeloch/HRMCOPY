"use client";

import { useEffect, useState } from "react";

import { apiFetch } from "@/lib/api";

type Issue = { employee_id: string; employee_name: string; net_pay: string; bank_name: string; account_number: string; bank_code: string; reason: string; fixable: boolean };
type Preview = {
  period: string;
  narration: string;
  ready_count: number;
  ready_total: string;
  padded: { employee_id: string; employee_name: string; account_number: string }[];
  issues: Issue[];
};

const money = (value: string) => `₦${Number(value || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

export function BankUploadModal({ periodId, onClose }: { periodId: string; onClose: () => void }) {
  const [preview, setPreview] = useState<Preview | null>(null);
  const [error, setError] = useState("");
  const [batchSize, setBatchSize] = useState("");
  const [downloading, setDownloading] = useState(false);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const response = await apiFetch(`/payroll/periods/${periodId}/bank-upload/preview/`);
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Unable to prepare the bank upload file.");
        if (!cancelled) setPreview(data);
      } catch (loadError) {
        if (!cancelled) setError(loadError instanceof Error ? loadError.message : "Unable to prepare the bank upload file.");
      }
    })();
    return () => { cancelled = true; };
  }, [periodId]);

  const size = Number(batchSize);
  const splitting = Boolean(preview && size > 0 && preview.ready_count > size);

  async function download() {
    if (!preview) return;
    setDownloading(true);
    setError("");
    try {
      const query = size > 0 ? `?batch_size=${size}` : "";
      const response = await apiFetch(`/payroll/periods/${periodId}/bank-upload/${query}`);
      if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        throw new Error(typeof data.detail === "string" ? data.detail : "Unable to create the bank upload file.");
      }
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement("a");
      link.href = url;
      link.download = `${preview.period} - Bank Upload.${splitting ? "zip" : "xlsx"}`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
    } catch (downloadError) {
      setError(downloadError instanceof Error ? downloadError.message : "Unable to create the bank upload file.");
    } finally {
      setDownloading(false);
    }
  }

  const fix = preview?.issues.filter((issue) => issue.fixable) || [];
  const nothingToPay = preview?.issues.filter((issue) => !issue.fixable) || [];

  return (
    <div className="fixed inset-0 z-30 flex items-center justify-center bg-slate-950/40 p-4">
      <div className="flex max-h-[90vh] w-full max-w-3xl flex-col rounded-2xl bg-white shadow-xl">
        <div className="border-b border-slate-200 p-6">
          <h2 className="text-xl font-bold text-slate-900">Bank Upload File{preview ? ` - ${preview.period}` : ""}</h2>
          <p className="mt-1 text-sm text-slate-500">An Excel file in the layout you upload to the bank: account number, amount, bank code and the narration on every row.</p>
        </div>

        <div className="overflow-y-auto p-6">
          {error && <p className="mb-4 rounded-xl bg-red-50 p-4 text-sm text-red-700">{error}</p>}
          {!preview && !error && <div className="h-32 animate-pulse rounded-xl bg-slate-100" />}
          {preview && (
            <>
              <div className="grid gap-4 sm:grid-cols-3">
                <div className="rounded-xl border border-slate-200 p-4"><p className="text-xs font-semibold uppercase text-slate-500">Payments in the file</p><p className="mt-1 text-2xl font-bold text-slate-900">{preview.ready_count}</p></div>
                <div className="rounded-xl border border-slate-200 p-4"><p className="text-xs font-semibold uppercase text-slate-500">Total</p><p className="mt-1 text-2xl font-bold text-slate-900">{money(preview.ready_total)}</p></div>
                <div className="rounded-xl border border-slate-200 p-4"><p className="text-xs font-semibold uppercase text-slate-500">Narration</p><p className="mt-1 text-2xl font-bold text-slate-900">{preview.narration}</p></div>
              </div>

              {fix.length > 0 && (
                <div className="mt-6 rounded-xl border border-amber-200 bg-amber-50 p-4">
                  <p className="text-sm font-semibold text-amber-900">{fix.length} {fix.length === 1 ? "person is" : "people are"} NOT in the file - their bank details need fixing first</p>
                  <div className="mt-3 max-h-56 overflow-y-auto rounded-lg bg-white">
                    <table className="w-full text-left text-xs">
                      <thead className="bg-slate-50 text-slate-500"><tr><th className="px-3 py-2">Staff no.</th><th className="px-3 py-2">Name</th><th className="px-3 py-2">Net pay</th><th className="px-3 py-2">Problem</th></tr></thead>
                      <tbody className="divide-y divide-slate-100">
                        {fix.map((issue) => (
                          <tr key={issue.employee_id}><td className="px-3 py-2 font-mono">{issue.employee_id}</td><td className="px-3 py-2">{issue.employee_name}</td><td className="px-3 py-2">{money(issue.net_pay)}</td><td className="px-3 py-2 text-amber-800">{issue.reason}</td></tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  <p className="mt-2 text-xs text-amber-800">Correct their bank name, account number and bank code on their employee profile, then open this window again.</p>
                </div>
              )}

              {preview.padded.length > 0 && (
                <div className="mt-4 rounded-xl border border-slate-200 bg-slate-50 p-4">
                  <p className="text-sm font-semibold text-slate-800">{preview.padded.length} account number{preview.padded.length === 1 ? " was" : "s were"} short and padded with leading zeros to 10 digits</p>
                  <p className="mt-1 text-xs text-slate-600">This is how your upload sheets have always shown them, but a mistyped account can look the same - worth a quick check: {preview.padded.slice(0, 8).map((item) => `${item.employee_name} (${item.account_number})`).join(", ")}{preview.padded.length > 8 ? `, and ${preview.padded.length - 8} more` : ""}.</p>
                </div>
              )}

              {nothingToPay.length > 0 && <p className="mt-4 text-xs text-slate-500">{nothingToPay.length} {nothingToPay.length === 1 ? "person has" : "people have"} no net pay this month (nothing to pay) and {nothingToPay.length === 1 ? "is" : "are"} left out.</p>}

              <label className="mt-6 block text-sm font-semibold text-slate-700">
                Split into smaller files? <span className="font-normal text-slate-500">(optional - most people per file)</span>
                <input type="number" min={1} max={5000} value={batchSize} onChange={(event) => setBatchSize(event.target.value)} placeholder="Leave empty for one file" className="mt-2 w-full max-w-xs rounded-xl border border-slate-300 px-3 py-2.5 text-sm font-normal outline-none focus:ring-2 focus:ring-blue-500" />
              </label>
              {splitting && <p className="mt-2 text-xs text-slate-500">You will get one download (a .zip) containing Batch 1.xlsx, Batch 2.xlsx, and so on.</p>}
            </>
          )}
        </div>

        <div className="flex justify-end gap-3 border-t border-slate-200 p-4">
          <button type="button" onClick={onClose} className="rounded-xl border border-slate-300 px-4 py-2.5 text-sm font-semibold text-slate-700">Close</button>
          <button type="button" disabled={!preview || preview.ready_count === 0 || downloading} onClick={() => void download()} className="rounded-xl bg-blue-600 px-5 py-2.5 text-sm font-semibold text-white disabled:bg-blue-300">
            {downloading ? "Creating..." : splitting ? "Download batches (.zip)" : "Download Excel file"}
          </button>
        </div>
      </div>
    </div>
  );
}

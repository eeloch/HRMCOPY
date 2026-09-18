"use client";

import { useEffect, useEffectEvent, useState } from "react";
import { useParams, useRouter } from "next/navigation";

import Sidebar from "@/components/Sidebar";
import { PayslipDocument, type PayslipData } from "@/components/payroll/PayslipDocument";
import { apiFetch, getAccessToken } from "@/lib/api";

export default function PrintAllPayslipsPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const [period, setPeriod] = useState("");
  const [payslips, setPayslips] = useState<PayslipData[]>([]);
  const [includeZero, setIncludeZero] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  async function load(withZero: boolean) {
    setLoading(true);
    setError("");
    try {
      const response = await apiFetch(`/payroll/periods/${params.id}/payslips/${withZero ? "?include_zero=1" : ""}`);
      if (!response.ok) throw new Error(response.status === 403 ? "You don't have permission to view payslips." : "Unable to load payslips.");
      const data = await response.json();
      setPeriod(data.period);
      setPayslips(data.results || []);
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "Unable to load payslips.");
    } finally {
      setLoading(false);
    }
  }

  const loadFor = useEffectEvent((withZero: boolean) => { void load(withZero); });
  useEffect(() => {
    if (!getAccessToken()) { router.push("/login"); return; }
    const timer = window.setTimeout(() => loadFor(includeZero), 0);
    return () => window.clearTimeout(timer);
  }, [router, includeZero]);

  return (
    <div className="min-h-screen bg-slate-100">
      <Sidebar />
      <main className="ml-64 min-w-0 p-4 md:p-8 print:ml-0 print:p-0">
        <div className="mb-6 flex flex-wrap items-center justify-between gap-4 print:hidden">
          <div>
            <h1 className="text-2xl font-bold text-slate-900">Print All Payslips{period ? ` - ${period}` : ""}</h1>
            <p className="mt-1 text-sm text-slate-500">{loading ? "Loading..." : `${payslips.length} payslip${payslips.length === 1 ? "" : "s"}, one per page, grouped by department. In the print window choose "Save as PDF" or your printer.`}</p>
          </div>
          <div className="flex flex-wrap items-center gap-3">
            <label className="flex items-center gap-2 text-sm text-slate-700">
              <input type="checkbox" checked={includeZero} onChange={(event) => setIncludeZero(event.target.checked)} />
              Include people with no pay
            </label>
            <button type="button" onClick={() => router.push(`/payroll/${params.id}`)} className="rounded-xl border border-slate-300 bg-white px-4 py-2.5 text-sm font-semibold text-slate-700">Back</button>
            <button type="button" disabled={loading || !payslips.length} onClick={() => window.print()} className="rounded-xl bg-slate-900 px-4 py-2.5 text-sm font-semibold text-white disabled:bg-slate-400">Print All</button>
          </div>
        </div>

        {error && <p className="mb-6 rounded-xl bg-red-50 p-4 text-sm text-red-700 print:hidden">{error}</p>}
        {loading ? <div className="h-96 animate-pulse rounded-2xl bg-slate-200 print:hidden" /> : (
          <div className="space-y-8 print:space-y-0">
            {payslips.map((payslip) => <PayslipDocument key={payslip.id} payslip={payslip} />)}
          </div>
        )}
      </main>
    </div>
  );
}

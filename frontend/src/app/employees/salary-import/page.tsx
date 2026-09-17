"use client";

import type { FormEvent } from "react";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";

import Sidebar from "@/components/Sidebar";
import { PageHeader, Section } from "@/components/ui";
import { apiFetch, getAccessToken, getCurrentUser, type CurrentUser } from "@/lib/api";

type Matched = { id: number; employee_id: string; name: string; old_salary: string; new_salary: string; changed: boolean };
type Unmatched = { row: number; employee_id: string };
type Invalid = { row: number; employee_id: string; reason: string };
type Preview = { matched: Matched[]; unmatched: Unmatched[]; invalid: Invalid[] };
type ApplyResult = { updated: number; updated_employees: { employee_id: string; name: string; old_salary: string; new_salary: string }[]; unmatched: Unmatched[]; invalid: Invalid[] };

const money = (value: string) => `₦${Number(value || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

function apiMessage(data: unknown, fallback: string) {
  if (!data || typeof data !== "object") return fallback;
  const payload = data as Record<string, unknown>;
  return typeof payload.detail === "string" ? payload.detail : fallback;
}

export default function SalaryImportPage() {
  const router = useRouter();
  const [currentUser, setCurrentUser] = useState<CurrentUser | null>(null);
  const [loading, setLoading] = useState(true);
  const [file, setFile] = useState<File | null>(null);
  const [previewing, setPreviewing] = useState(false);
  const [applying, setApplying] = useState(false);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [applyResult, setApplyResult] = useState<ApplyResult | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!getAccessToken()) { router.push("/login"); return; }
    (async () => {
      try { setCurrentUser(await getCurrentUser()); }
      catch { router.push("/login"); return; }
      finally { setLoading(false); }
    })();
  }, [router]);

  async function runPreview(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!file) return;
    setPreviewing(true); setError(""); setPreview(null); setApplyResult(null);
    try {
      const formData = new FormData();
      formData.append("file", file);
      const response = await apiFetch("/employees/salary-import/preview/", { method: "POST", body: formData });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(apiMessage(data, "Unable to preview this file."));
      setPreview(data as Preview);
    } catch (previewError) {
      setError(previewError instanceof Error ? previewError.message : "Unable to preview this file.");
    } finally { setPreviewing(false); }
  }

  async function applyChanges() {
    if (!file) return;
    setApplying(true); setError("");
    try {
      const formData = new FormData();
      formData.append("file", file);
      const response = await apiFetch("/employees/salary-import/", { method: "POST", body: formData });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(apiMessage(data, "Unable to apply these salary changes."));
      setApplyResult(data as ApplyResult);
      setPreview(null);
      setFile(null);
    } catch (applyError) {
      setError(applyError instanceof Error ? applyError.message : "Unable to apply these salary changes.");
    } finally { setApplying(false); }
  }

  const changedCount = preview?.matched.filter((item) => item.changed).length ?? 0;

  return <div className="min-h-screen bg-slate-100"><Sidebar /><main className="ml-64 min-w-0 p-4 md:p-8">
    <PageHeader
      title="Bulk Salary Import"
      description="Upload a spreadsheet with Employee ID and Basic Salary columns. Nothing is saved until you review the preview and confirm."
      actions={<button type="button" onClick={() => router.push("/employees")} className="rounded-xl border border-slate-300 bg-white px-4 py-2.5 text-sm font-semibold text-slate-700">Back to Employees</button>}
    />
    {error && <p className="mb-6 rounded-xl bg-red-50 p-4 text-sm text-red-700">{error}</p>}
    {loading ? <div className="h-64 animate-pulse rounded-2xl bg-slate-200" /> : !currentUser?.permissions.view_salary ? (
      <Section title="Not Available"><p className="p-8 text-sm text-slate-600">You don&apos;t have permission to view or set employee salaries, so this tool isn&apos;t available to your account.</p></Section>
    ) : <>
      <Section title="Upload">
        <form onSubmit={runPreview} className="grid gap-4 p-5 md:grid-cols-3">
          <label className="text-sm font-semibold md:col-span-2">
            Salary Spreadsheet (.csv or .xlsx)
            <input required type="file" accept=".csv,.xlsx" onChange={(event) => { setFile(event.target.files?.[0] ?? null); setPreview(null); setApplyResult(null); }} className="mt-2 w-full rounded-xl border border-slate-300 bg-white px-3 py-2.5 font-normal" />
          </label>
          <button disabled={previewing || !file} className="self-end rounded-xl bg-blue-600 px-4 py-2.5 text-sm font-semibold text-white hover:bg-blue-700 disabled:opacity-50">{previewing ? "Reading..." : "Preview"}</button>
        </form>
        <p className="border-t border-slate-200 px-5 py-3 text-xs text-slate-500">Columns recognized: Employee ID (or Staff Number/Staff No.) and Basic Salary (or Salary/Amount). Any other columns are ignored.</p>
      </Section>

      {preview && (
        <Section className="mt-6" title="Preview" subtitle={`${changedCount} of ${preview.matched.length} matched row(s) will change · ${preview.unmatched.length} unmatched · ${preview.invalid.length} invalid`}>
          <div className="flex items-center justify-between gap-4 border-b border-slate-200 p-5">
            <p className="text-sm text-slate-600">Nothing has been saved yet. Review below, then confirm.</p>
            <button type="button" disabled={applying || changedCount === 0} onClick={() => void applyChanges()} className="rounded-xl bg-emerald-600 px-4 py-2.5 text-sm font-semibold text-white disabled:opacity-50">{applying ? "Applying..." : `Confirm & Apply ${changedCount} Change(s)`}</button>
          </div>

          {preview.matched.length > 0 && (
            <div className="overflow-x-auto"><table className="w-full min-w-[700px] text-left"><thead className="bg-slate-50 text-xs font-semibold uppercase text-slate-500"><tr><th className="px-5 py-3">Employee</th><th className="px-5 py-3">Current Salary</th><th className="px-5 py-3">New Salary</th><th className="px-5 py-3">Change</th></tr></thead><tbody className="divide-y divide-slate-100">{preview.matched.map((item) => <tr key={item.id} className={item.changed ? "" : "opacity-50"}><td className="px-5 py-4"><p className="font-medium">{item.name}</p><p className="text-xs text-slate-500">{item.employee_id}</p></td><td className="px-5 py-4">{money(item.old_salary)}</td><td className="px-5 py-4 font-semibold">{money(item.new_salary)}</td><td className="px-5 py-4 text-sm">{item.changed ? "Will update" : "No change"}</td></tr>)}</tbody></table></div>
          )}

          {preview.unmatched.length > 0 && (
            <div className="border-t border-slate-200 p-5">
              <h3 className="font-bold text-slate-900">Unmatched ({preview.unmatched.length})</h3>
              <p className="mt-1 text-xs text-slate-500">No employee found with this staff number - check for typos.</p>
              <div className="mt-3 max-h-48 overflow-y-auto rounded-xl border border-slate-200"><table className="w-full text-left text-sm"><thead className="bg-slate-50 text-xs font-semibold uppercase text-slate-500"><tr><th className="px-4 py-2">Row</th><th className="px-4 py-2">Employee ID</th></tr></thead><tbody className="divide-y divide-slate-100">{preview.unmatched.map((item, index) => <tr key={index}><td className="px-4 py-2">{item.row}</td><td className="px-4 py-2">{item.employee_id || "(blank)"}</td></tr>)}</tbody></table></div>
            </div>
          )}

          {preview.invalid.length > 0 && (
            <div className="border-t border-slate-200 p-5">
              <h3 className="font-bold text-slate-900">Invalid ({preview.invalid.length})</h3>
              <p className="mt-1 text-xs text-slate-500">These rows have a problem and will be skipped.</p>
              <div className="mt-3 max-h-48 overflow-y-auto rounded-xl border border-slate-200"><table className="w-full text-left text-sm"><thead className="bg-slate-50 text-xs font-semibold uppercase text-slate-500"><tr><th className="px-4 py-2">Row</th><th className="px-4 py-2">Employee ID</th><th className="px-4 py-2">Reason</th></tr></thead><tbody className="divide-y divide-slate-100">{preview.invalid.map((item, index) => <tr key={index}><td className="px-4 py-2">{item.row}</td><td className="px-4 py-2">{item.employee_id || "(blank)"}</td><td className="px-4 py-2 text-slate-600">{item.reason}</td></tr>)}</tbody></table></div>
            </div>
          )}
        </Section>
      )}

      {applyResult && (
        <Section className="mt-6" title="Applied" subtitle={`${applyResult.updated} employee(s) updated`}>
          {applyResult.updated_employees.length ? <div className="overflow-x-auto"><table className="w-full min-w-[650px] text-left"><thead className="bg-slate-50 text-xs font-semibold uppercase text-slate-500"><tr><th className="px-5 py-3">Employee</th><th className="px-5 py-3">Old Salary</th><th className="px-5 py-3">New Salary</th></tr></thead><tbody className="divide-y divide-slate-100">{applyResult.updated_employees.map((item) => <tr key={item.employee_id}><td className="px-5 py-4"><p className="font-medium">{item.name}</p><p className="text-xs text-slate-500">{item.employee_id}</p></td><td className="px-5 py-4">{money(item.old_salary)}</td><td className="px-5 py-4 font-semibold">{money(item.new_salary)}</td></tr>)}</tbody></table></div> : <p className="p-8 text-center text-sm text-slate-500">No employees were updated.</p>}
        </Section>
      )}
    </>}
  </main></div>;
}

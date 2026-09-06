"use client";

import { useEffect, useEffectEvent, useState } from "react";

import { Section, StatusBadge } from "@/components/ui";
import { apiFetch } from "@/lib/api";
import { Empty, PanelError, PanelSkeleton, formatDate } from "./EmployeeAttendancePanel";

type PPEIssue = { id: number; ppe_type_name: string; quantity: number; issue_date: string; total_cost: string; deduction_status: string; target_payroll_period_name: string | null; deducted_payroll_id: number | null; review_comment: string; };
const money = (value: string) => `₦${Number(value || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

export function EmployeePPEPanel({ employeeId }: { employeeId: number }) {
  const [issues, setIssues] = useState<PPEIssue[]>([]); const [loading, setLoading] = useState(true); const [error, setError] = useState("");
  async function loadIssues() { setLoading(true); setError(""); try { const response = await apiFetch(`/ppe/issues/?employee=${employeeId}`); if (!response.ok) throw new Error("Unable to load PPE history."); const data: { results?: PPEIssue[] } = await response.json(); setIssues(data.results || []); } catch (loadError) { setError(loadError instanceof Error ? loadError.message : "Unable to load PPE history."); } finally { setLoading(false); } }
  const loadOnMount = useEffectEvent(() => { void loadIssues(); });
  useEffect(() => { const timer = window.setTimeout(loadOnMount, 0); return () => window.clearTimeout(timer); }, [employeeId]);
  return <div className="mt-6"><Section title="PPE History" subtitle="Issued protective equipment and its payroll deduction status." actions={<button type="button" onClick={() => void loadIssues()} className="rounded-xl border border-slate-300 px-4 py-2 text-sm font-semibold text-slate-700 hover:bg-slate-50">Refresh</button>}>{error ? <PanelError message={error} retry={loadIssues} /> : loading ? <PanelSkeleton /> : issues.length ? <div className="overflow-x-auto"><table className="w-full min-w-[860px] text-left"><thead className="bg-slate-50 text-xs font-semibold uppercase tracking-wide text-slate-500"><tr><th className="px-5 py-3">PPE Item</th><th className="px-5 py-3">Issue Date</th><th className="px-5 py-3">Quantity</th><th className="px-5 py-3">Cost</th><th className="px-5 py-3">Payroll</th><th className="px-5 py-3">Status</th></tr></thead><tbody className="divide-y divide-slate-100">{issues.map((issue) => <tr key={issue.id} className="hover:bg-slate-50"><td className="px-5 py-4 font-medium text-slate-800">{issue.ppe_type_name}</td><td className="px-5 py-4 text-sm text-slate-700">{formatDate(issue.issue_date)}</td><td className="px-5 py-4 text-sm text-slate-700">{issue.quantity}</td><td className="px-5 py-4 text-sm text-slate-700">{money(issue.total_cost)}</td><td className="px-5 py-4 text-sm text-slate-600">{issue.target_payroll_period_name || "Not targeted"}</td><td className="px-5 py-4"><StatusBadge status={issue.deduction_status} /></td></tr>)}</tbody></table></div> : <Empty message="No PPE issues found for this employee." />}</Section></div>;
}

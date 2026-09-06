"use client";

import { useEffect, useEffectEvent, useState } from "react";
import { useRouter } from "next/navigation";

import { Section, StatusBadge } from "@/components/ui";
import { apiFetch } from "@/lib/api";
import { Empty, PanelError, PanelSkeleton, formatDate } from "./EmployeeAttendancePanel";

type PayrollRecord = { id: number; payroll_period_name: string; basic_salary: string; gross_earnings: string; total_deductions: string; net_pay: string; status: string; generated_at: string; };
function money(value: string) { return `₦${Number(value || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`; }

export function EmployeePayrollPanel({ employeeId }: { employeeId: number }) {
  const router = useRouter(); const [records, setRecords] = useState<PayrollRecord[]>([]); const [loading, setLoading] = useState(true); const [error, setError] = useState("");
  async function loadRecords() { setLoading(true); setError(""); try { const response = await apiFetch(`/payroll/employee-payrolls/?employee=${employeeId}`); if (response.status === 403) throw new Error("You do not have permission to view payroll records."); if (!response.ok) throw new Error("Unable to load payroll history."); const data: { results?: PayrollRecord[] } = await response.json(); setRecords(data.results || []); } catch (loadError) { setError(loadError instanceof Error ? loadError.message : "Unable to load payroll history."); } finally { setLoading(false); } }
  const loadOnMount = useEffectEvent(() => { void loadRecords(); });
  useEffect(() => { const timer = window.setTimeout(loadOnMount, 0); return () => window.clearTimeout(timer); }, [employeeId]);
  return <div className="mt-6"><Section title="Payroll History" subtitle="Payroll snapshots are calculated and maintained by the payroll module.">{error ? <PanelError message={error} retry={loadRecords} /> : loading ? <PanelSkeleton /> : records.length ? <div className="overflow-x-auto"><table className="w-full min-w-[900px] text-left"><thead className="bg-slate-50 text-xs font-semibold uppercase tracking-wide text-slate-500"><tr><th className="px-5 py-3">Period</th><th className="px-5 py-3">Basic Salary</th><th className="px-5 py-3">Earnings</th><th className="px-5 py-3">Deductions</th><th className="px-5 py-3">Net Pay</th><th className="px-5 py-3">Status</th><th className="px-5 py-3" /></tr></thead><tbody className="divide-y divide-slate-100">{records.map((record) => <tr key={record.id} className="hover:bg-slate-50"><td className="px-5 py-4"><p className="font-medium text-slate-800">{record.payroll_period_name}</p><p className="mt-1 text-xs text-slate-500">Generated {formatDate(record.generated_at.slice(0, 10))}</p></td><td className="px-5 py-4 text-sm text-slate-700">{money(record.basic_salary)}</td><td className="px-5 py-4 text-sm text-slate-700">{money(record.gross_earnings)}</td><td className="px-5 py-4 text-sm text-slate-700">{money(record.total_deductions)}</td><td className="px-5 py-4 font-semibold text-slate-900">{money(record.net_pay)}</td><td className="px-5 py-4"><StatusBadge status={record.status} /></td><td className="px-5 py-4 text-right"><button type="button" onClick={() => router.push(`/payroll/${record.id}`)} className="rounded-xl border border-slate-300 px-3 py-2 text-sm font-semibold text-slate-700 hover:bg-slate-50">View Payslip</button></td></tr>)}</tbody></table></div> : <Empty message="No payroll records found for this employee." />}</Section></div>;
}

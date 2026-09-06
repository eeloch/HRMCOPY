"use client";

import { useEffect, useEffectEvent, useState } from "react";
import { useRouter } from "next/navigation";

import { Section, StatusBadge } from "@/components/ui";
import type { LeaveRequest } from "@/components/leave/types";
import { apiFetch } from "@/lib/api";
import { Empty, PanelError, PanelSkeleton, formatDate } from "./EmployeeAttendancePanel";

export function EmployeeLeavePanel({ employeeId }: { employeeId: number }) {
  const router = useRouter(); const [requests, setRequests] = useState<LeaveRequest[]>([]); const [loading, setLoading] = useState(true); const [error, setError] = useState("");
  async function loadRequests() { setLoading(true); setError(""); try { const response = await apiFetch(`/leave/requests/?employee_id=${employeeId}`); if (!response.ok) throw new Error("Unable to load leave history."); const data: { results?: LeaveRequest[] } = await response.json(); setRequests(data.results || []); } catch (loadError) { setError(loadError instanceof Error ? loadError.message : "Unable to load leave history."); } finally { setLoading(false); } }
  const loadOnMount = useEffectEvent(() => { void loadRequests(); });
  useEffect(() => { const timer = window.setTimeout(loadOnMount, 0); return () => window.clearTimeout(timer); }, [employeeId]);
  return <div className="mt-6"><Section title="Leave History" subtitle="Employee leave requests and their review status." actions={<button type="button" onClick={() => router.push("/leave/requests")} className="rounded-xl border border-slate-300 px-4 py-2 text-sm font-semibold text-slate-700 hover:bg-slate-50">View All Leave</button>}>{error ? <PanelError message={error} retry={loadRequests} /> : loading ? <PanelSkeleton /> : requests.length ? <div className="overflow-x-auto"><table className="w-full min-w-[980px] text-left"><thead className="bg-slate-50 text-xs font-semibold uppercase tracking-wide text-slate-500"><tr><th className="px-5 py-3">Request</th><th className="px-5 py-3">Leave Type</th><th className="px-5 py-3">Dates</th><th className="px-5 py-3">Days</th><th className="px-5 py-3">Reason</th><th className="px-5 py-3">Reviewed By</th><th className="px-5 py-3">Status</th></tr></thead><tbody className="divide-y divide-slate-100">{requests.map((request) => <tr key={request.id} className="hover:bg-slate-50"><td className="px-5 py-4 font-mono text-sm font-semibold text-slate-800">{request.request_number}</td><td className="px-5 py-4 text-sm text-slate-700">{request.leave_type_name}</td><td className="px-5 py-4 text-sm text-slate-700">{formatDate(request.start_date)} - {formatDate(request.end_date)}</td><td className="px-5 py-4 text-sm text-slate-700">{request.total_days}</td><td className="max-w-xs px-5 py-4 text-sm text-slate-600">{request.reason || "-"}</td><td className="px-5 py-4 text-sm text-slate-600">{request.approved_by_name || "-"}</td><td className="px-5 py-4"><StatusBadge status={request.status} /></td></tr>)}</tbody></table></div> : <Empty message="No leave records found for this employee." />}</Section></div>;
}

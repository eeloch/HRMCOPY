"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";

import { LeaveShell } from "@/components/leave/LeaveShell";
import type { BalanceResponse, LeaveBalance, LeaveRequest, ListResponse } from "@/components/leave/types";
import { AppCard, MetricCard, PageHeader, Section, StatusBadge } from "@/components/ui";
import { apiFetch, getAccessToken } from "@/lib/api";
import { ErrorPanel, formatDate, TableSkeleton } from "./requests/page";

export default function LeaveDashboardPage() {
  const router = useRouter();
  const [requests, setRequests] = useState<LeaveRequest[]>([]);
  const [balances, setBalances] = useState<LeaveBalance[]>([]);
  const [pendingCount, setPendingCount] = useState(0);
  const [employeeId, setEmployeeId] = useState("");
  const [loading, setLoading] = useState(true);
  const [balanceLoading, setBalanceLoading] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!getAccessToken()) { router.push("/login"); return; }
    void loadDashboard();
  }, [router]);

  async function loadDashboard() {
    setLoading(true); setError("");
    try {
      const [requestsResponse, pendingResponse] = await Promise.all([apiFetch("/leave/requests/"), apiFetch("/leave/pending/")]);
      if (!requestsResponse.ok || !pendingResponse.ok) throw new Error("Unable to load leave dashboard.");
      const requestsData: ListResponse<LeaveRequest> = await requestsResponse.json();
      const pendingData: ListResponse<LeaveRequest> = await pendingResponse.json();
      setRequests(requestsData.results);
      setPendingCount(pendingData.count);
      const firstEmployee = requestsData.results[0]?.employee;
      if (firstEmployee) { setEmployeeId(String(firstEmployee)); void loadBalances(String(firstEmployee)); }
    } catch (loadError) { setError(loadError instanceof Error ? loadError.message : "Unable to load leave dashboard."); }
    finally { setLoading(false); }
  }

  async function loadBalances(value = employeeId) {
    if (!value) { setBalances([]); return; }
    setBalanceLoading(true);
    try {
      const response = await apiFetch(`/leave/balance/${value}/`);
      if (!response.ok) throw new Error("Unable to load leave balance.");
      const data: BalanceResponse = await response.json();
      setBalances(data.results);
    } catch (loadError) { setError(loadError instanceof Error ? loadError.message : "Unable to load leave balance."); }
    finally { setBalanceLoading(false); }
  }

  const employees = Array.from(new Map(requests.map((request) => [request.employee, request])).values());
  const selectedEmployee = employees.find((request) => String(request.employee) === employeeId);
  const selectedBalance = balances[0];
  const upcoming = requests.filter((request) => new Date(`${request.start_date}T00:00:00`) >= new Date()).slice(0, 5);

  return <LeaveShell>
    <PageHeader title="Leave Dashboard" description="Leave balances and upcoming requests." actions={<button onClick={() => void loadDashboard()} className="rounded-xl border border-slate-300 bg-white px-4 py-2.5 text-sm font-medium text-slate-700 hover:bg-slate-50">Refresh</button>} />
    {error ? <ErrorPanel message={error} retry={loadDashboard} /> : <>
      <AppCard className="mb-6" title="Employee Balance" actions={<select value={employeeId} onChange={(event) => { setEmployeeId(event.target.value); void loadBalances(event.target.value); }} className="rounded-xl border border-slate-300 bg-white px-3 py-2 text-sm"><option value="">Select employee</option>{employees.map((employee) => <option key={employee.employee} value={employee.employee}>{employee.employee_name} ({employee.employee_id})</option>)}</select>}>
        <p className="text-sm text-slate-500">{selectedEmployee ? `Showing policy-calculated balances for ${selectedEmployee.employee_name}.` : "Choose an employee with a leave request to view backend-calculated balances."}</p>
      </AppCard>
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {loading || balanceLoading ? ["Allocated", "Used", "Remaining", "Pending"].map((title) => <div key={title} className="h-36 animate-pulse rounded-2xl bg-slate-200" />) : <>
          <MetricCard title="Allocated" value={selectedBalance?.allocated_days ?? "-"} subtitle={selectedBalance?.leave_type_name || "Select an employee"} icon="◷" accentColor="#2563eb" />
          <MetricCard title="Used" value={selectedBalance?.used_days ?? "-"} subtitle="Approved leave" icon="✓" accentColor="#d97706" />
          <MetricCard title="Remaining" value={selectedBalance?.remaining_days ?? "-"} subtitle="Available balance" icon="◌" accentColor="#059669" />
          <MetricCard title="Pending" value={pendingCount} subtitle="Requests awaiting review" icon="!" accentColor="#7c3aed" />
        </>}
      </div>
      <Section className="mt-8" title="Upcoming Leave" subtitle="Requests with an upcoming start date." actions={<button onClick={() => router.push("/leave/requests")} className="text-sm font-semibold text-blue-700 hover:text-blue-800">View all requests</button>}>
        {loading ? <TableSkeleton /> : upcoming.length ? <div className="divide-y divide-slate-100">{upcoming.map((request) => <div key={request.id} className="flex flex-col gap-3 px-5 py-4 sm:flex-row sm:items-center sm:justify-between"><div><p className="font-semibold text-slate-900">{request.employee_name}</p><p className="mt-1 text-sm text-slate-500">{request.leave_type_name} · {formatDate(request.start_date)} to {formatDate(request.end_date)}</p></div><StatusBadge status={request.status} /></div>)}</div> : <p className="p-10 text-center text-slate-500">No upcoming leave requests.</p>}
      </Section>
    </>}
  </LeaveShell>;
}

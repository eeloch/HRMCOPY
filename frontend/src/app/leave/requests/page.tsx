"use client";

import { useEffect, useState } from "react";

import { LeaveShell } from "@/components/leave/LeaveShell";
import type { LeaveRequest, ListResponse } from "@/components/leave/types";
import { PageHeader, Section, StatusBadge } from "@/components/ui";
import { apiFetch, getAccessToken } from "@/lib/api";
import { useRouter } from "next/navigation";

export default function LeaveRequestsPage() {
  const router = useRouter();
  const [requests, setRequests] = useState<LeaveRequest[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState("");

  useEffect(() => {
    if (!getAccessToken()) {
      router.push("/login");
      return;
    }
    void loadRequests();
  }, [router]);

  async function loadRequests() {
    setLoading(true);
    setError("");
    try {
      const response = await apiFetch("/leave/requests/");
      if (!response.ok) throw new Error("Unable to load leave requests.");
      const data: ListResponse<LeaveRequest> = await response.json();
      setRequests(data.results);
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "Unable to load leave requests.");
    } finally {
      setLoading(false);
    }
  }

  const searchTerm = search.trim().toLowerCase();
  const statuses = Array.from(new Set(requests.map((request) => request.status))).sort();
  const filteredRequests = requests.filter((request) => {
    const matchesSearch = !searchTerm || [request.request_number, request.employee_name, request.employee_id, request.leave_type_name]
      .some((value) => value.toLowerCase().includes(searchTerm));
    return matchesSearch && (!status || request.status === status);
  });

  return (
    <LeaveShell>
      <PageHeader
        title="My Leave Requests"
        description="Track submitted leave requests and their current status."
        actions={<button onClick={() => router.push("/leave/new")} className="rounded-xl bg-blue-600 px-4 py-2.5 text-sm font-semibold text-white hover:bg-blue-700">Request Leave</button>}
      />
      {error ? <ErrorPanel message={error} retry={loadRequests} /> : (
        <Section title="Requests" subtitle="Search and filter your leave request history." actions={<button onClick={() => void loadRequests()} className="rounded-xl border border-slate-300 px-4 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50">Refresh</button>}>
          <div className="grid gap-3 border-b border-slate-200 p-5 sm:grid-cols-[1fr_220px]">
            <input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search request number, employee, or leave type..." className="rounded-xl border border-slate-300 px-4 py-3 outline-none focus:ring-2 focus:ring-blue-500" />
            <select value={status} onChange={(event) => setStatus(event.target.value)} className="rounded-xl border border-slate-300 bg-white px-4 py-3 outline-none focus:ring-2 focus:ring-blue-500">
              <option value="">All statuses</option>
              {statuses.map((item) => <option key={item} value={item}>{item.replace(/_/g, " ")}</option>)}
            </select>
          </div>
          {loading ? <TableSkeleton /> : filteredRequests.length ? (
            <div className="overflow-x-auto"><table className="w-full min-w-[880px] text-left"><thead className="bg-slate-50 text-xs font-semibold uppercase tracking-wider text-slate-500"><tr><th className="px-5 py-3">Request Number</th><th className="px-5 py-3">Employee</th><th className="px-5 py-3">Leave Type</th><th className="px-5 py-3">Dates</th><th className="px-5 py-3">Days</th><th className="px-5 py-3">Status</th></tr></thead><tbody className="divide-y divide-slate-100">{filteredRequests.map((request) => <tr key={request.id} className="hover:bg-slate-50"><td className="px-5 py-4 font-mono text-sm font-semibold text-slate-800">{request.request_number}</td><td className="px-5 py-4"><p className="font-semibold text-slate-900">{request.employee_name}</p><p className="text-sm text-slate-500">{request.employee_id}</p></td><td className="px-5 py-4 text-sm text-slate-700">{request.leave_type_name}</td><td className="px-5 py-4 text-sm text-slate-700">{formatDate(request.start_date)} - {formatDate(request.end_date)}</td><td className="px-5 py-4 text-sm text-slate-700">{request.total_days}</td><td className="px-5 py-4"><StatusBadge status={request.status} /></td></tr>)}</tbody></table></div>
          ) : <p className="p-10 text-center text-slate-500">No leave requests found.</p>}
        </Section>
      )}
    </LeaveShell>
  );
}

export function formatDate(value: string) { return new Intl.DateTimeFormat("en-GB", { day: "2-digit", month: "short", year: "numeric" }).format(new Date(`${value}T00:00:00`)); }
export function TableSkeleton() { return <div className="space-y-3 p-5">{[1, 2, 3, 4].map((item) => <div key={item} className="h-14 animate-pulse rounded-xl bg-slate-100" />)}</div>; }
export function ErrorPanel({ message, retry }: { message: string; retry: () => void }) { return <div className="rounded-2xl border border-red-200 bg-red-50 p-6 text-red-700"><p className="font-semibold">Unable to load leave data</p><p className="mt-1 text-sm">{message}</p><button onClick={() => void retry()} className="mt-4 rounded-xl bg-red-600 px-4 py-2.5 text-sm font-semibold text-white hover:bg-red-700">Try Again</button></div>; }

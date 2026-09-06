"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";

import Sidebar from "@/components/Sidebar";
import {
  ExceptionReviewModal,
  type AttendanceExceptionRecord,
} from "@/components/attendance/ExceptionReviewModal";
import { AppCard, PageHeader, Section, StatusBadge } from "@/components/ui";
import { apiFetch, getAccessToken } from "@/lib/api";

type ViewMode = "pending" | "history";
type Decision = "approved" | "waived" | "held";

const exceptionTypes = ["late", "early_departure", "absence", "missing_clock_in", "missing_clock_out", "hostel_violation"];

function label(value: string) {
  return value.replace(/_/g, " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function formatDate(value: string | null) {
  if (!value) return "-";
  return new Date(value).toLocaleString(undefined, { day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" });
}

async function errorMessage(response: Response) {
  const data: unknown = await response.json().catch(() => null);
  if (data && typeof data === "object") {
    const detail = Object.values(data as Record<string, unknown>)
      .flatMap((value) => (Array.isArray(value) ? value : [value]))
      .map(String)
      .join(" ");
    if (detail) return detail;
  }
  if (response.status === 403) return "You do not have permission to review attendance exceptions.";
  return "Unable to process the attendance exception.";
}

export default function ExceptionPage() {
  const router = useRouter();
  const [records, setRecords] = useState<AttendanceExceptionRecord[]>([]);
  const [viewMode, setViewMode] = useState<ViewMode>("pending");
  const [search, setSearch] = useState("");
  const [typeFilter, setTypeFilter] = useState("all");
  const [statusFilter, setStatusFilter] = useState("all");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [feedback, setFeedback] = useState("");
  const [selectedRecord, setSelectedRecord] = useState<AttendanceExceptionRecord | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [modalError, setModalError] = useState("");

  const loadExceptions = useCallback(async (mode = viewMode) => {
    setLoading(true);
    setError("");
    try {
      const endpoint = mode === "pending" ? "/attendance/exceptions/pending/" : "/attendance/exceptions/";
      const response = await apiFetch(endpoint);
      if (!response.ok) throw new Error(await errorMessage(response));
      const data = await response.json();
      setRecords(data.results || []);
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "Unable to load attendance exceptions.");
    } finally {
      setLoading(false);
    }
  }, [viewMode]);

  useEffect(() => {
    if (!getAccessToken()) {
      router.push("/login");
      return;
    }
    const loadTimer = window.setTimeout(() => void loadExceptions(), 0);
    return () => window.clearTimeout(loadTimer);
  }, [router, loadExceptions]);

  function switchView(mode: ViewMode) {
    setViewMode(mode);
    setStatusFilter("all");
    setFeedback("");
  }

  const visibleRecords = records.filter((record) => {
    const term = search.trim().toLowerCase();
    const matchesSearch = !term || [record.employee_name, record.employee_id, record.department || "", record.exception_type].some((value) => value.toLowerCase().includes(term));
    const matchesType = typeFilter === "all" || record.exception_type === typeFilter;
    const matchesStatus = statusFilter === "all" || record.status === statusFilter;
    const isHistory = viewMode === "history" ? record.status !== "pending" : record.status === "pending";
    return matchesSearch && matchesType && matchesStatus && isHistory;
  });

  async function submitDecision(decision: Decision, comment: string) {
    if (!selectedRecord) return;
    setSubmitting(true);
    setModalError("");
    try {
      const response = await apiFetch(`/attendance/exceptions/${selectedRecord.id}/decision/`, {
        method: "POST",
        body: JSON.stringify({ decision, comment }),
      });
      if (!response.ok) throw new Error(await errorMessage(response));
      setSelectedRecord(null);
      setFeedback(`${label(selectedRecord.exception_type)} was ${decision}.`);
      await loadExceptions("pending");
    } catch (decisionError) {
      setModalError(decisionError instanceof Error ? decisionError.message : "Unable to process the attendance exception.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="min-h-screen bg-slate-100">
      <Sidebar />
      <main className="ml-64 min-w-0 p-4 md:p-8">
        <PageHeader
          title="Attendance Exceptions"
          description="Review attendance issues without changing biometric or attendance facts."
          actions={<button type="button" onClick={() => void loadExceptions()} className="rounded-xl border border-slate-300 bg-white px-4 py-2.5 text-sm font-semibold text-slate-700 hover:bg-slate-50">Refresh</button>}
        />

        <AppCard className="mb-6 border-blue-200 bg-blue-50">
          <p className="text-sm text-blue-800">Biometric punches and calculated attendance facts remain read-only. Decisions record the review outcome only.</p>
        </AppCard>

        <div className="mb-6 flex gap-2 border-b border-slate-200">
          <ViewTab active={viewMode === "pending"} onClick={() => switchView("pending")}>Pending Review</ViewTab>
          <ViewTab active={viewMode === "history"} onClick={() => switchView("history")}>Review History</ViewTab>
        </div>

        <AppCard className="mb-6">
          <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
            <input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search employee, staff number, department or issue..." className="rounded-xl border border-slate-300 px-4 py-3 outline-none focus:ring-2 focus:ring-blue-500 md:col-span-1" />
            <select value={typeFilter} onChange={(event) => setTypeFilter(event.target.value)} className="rounded-xl border border-slate-300 bg-white px-4 py-3"><option value="all">All exception types</option>{exceptionTypes.map((type) => <option key={type} value={type}>{label(type)}</option>)}</select>
            <select value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)} className="rounded-xl border border-slate-300 bg-white px-4 py-3"><option value="all">All statuses</option>{viewMode === "pending" ? <option value="pending">Pending</option> : <><option value="approved">Approved</option><option value="waived">Waived</option><option value="held">Held</option></>}</select>
          </div>
        </AppCard>

        {feedback && <p className="mb-6 rounded-xl bg-emerald-50 px-4 py-3 text-sm text-emerald-700">{feedback}</p>}

        <Section title={viewMode === "pending" ? "Pending Review" : "Review History"} subtitle={`${visibleRecords.length} case${visibleRecords.length === 1 ? "" : "s"}`}>
          {error ? <div className="p-10 text-center text-red-700"><p>{error}</p><button type="button" onClick={() => void loadExceptions()} className="mt-4 rounded-xl bg-red-600 px-4 py-2.5 text-sm font-semibold text-white">Try Again</button></div> : loading ? <div className="space-y-3 p-6">{Array.from({ length: 5 }).map((_, index) => <div key={index} className="h-16 animate-pulse rounded-xl bg-slate-100" />)}</div> : visibleRecords.length ? <div className="overflow-x-auto"><table className="w-full min-w-[940px] text-left"><thead className="bg-slate-50 text-xs font-semibold uppercase tracking-wide text-slate-500"><tr><th className="px-5 py-3">Employee</th><th className="px-5 py-3">Date / Shift</th><th className="px-5 py-3">Exception</th><th className="px-5 py-3">Affected</th><th className="px-5 py-3">Status</th>{viewMode === "history" && <><th className="px-5 py-3">Reviewer</th><th className="px-5 py-3">Comment</th></>}<th className="px-5 py-3" /></tr></thead><tbody className="divide-y divide-slate-100">{visibleRecords.map((record) => <tr key={record.id} className="hover:bg-slate-50"><td className="px-5 py-4"><div className="font-semibold text-slate-900">{record.employee_name}</div><div className="mt-1 text-sm text-slate-500">{record.employee_id} · {record.department || "No department"}</div></td><td className="px-5 py-4 text-sm text-slate-600">{record.attendance_date}<div className="mt-1 text-xs text-slate-500">{record.shift_name || "No shift"}</div></td><td className="px-5 py-4"><div className="font-semibold text-slate-900">{label(record.exception_type)}</div><div className="mt-1 text-xs text-amber-700">₦{Number(record.proposed_deduction).toLocaleString()} proposed</div></td><td className="px-5 py-4 text-sm text-slate-600">{record.minutes_affected} min</td><td className="px-5 py-4"><StatusBadge status={record.status} /></td>{viewMode === "history" && <><td className="px-5 py-4 text-sm text-slate-600">{record.reviewed_by || "-"}<div className="mt-1 text-xs text-slate-500">{formatDate(record.reviewed_at)}</div></td><td className="max-w-xs px-5 py-4 text-sm text-slate-600">{record.admin_comment || "-"}</td></>}<td className="px-5 py-4 text-right">{viewMode === "pending" && <button type="button" onClick={() => { setModalError(""); setSelectedRecord(record); }} className="rounded-xl bg-blue-600 px-3 py-2 text-sm font-semibold text-white hover:bg-blue-700">Review</button>}</td></tr>)}</tbody></table></div> : <div className="p-12 text-center text-slate-500">{viewMode === "pending" ? "No attendance exceptions waiting for review." : "No reviewed attendance exceptions match the current filters."}</div>}
        </Section>
      </main>

      {selectedRecord && <ExceptionReviewModal record={selectedRecord} submitting={submitting} error={modalError} onClose={() => !submitting && setSelectedRecord(null)} onSubmit={(decision, comment) => void submitDecision(decision, comment)} />}
    </div>
  );
}

function ViewTab({ active, onClick, children }: { active: boolean; onClick: () => void; children: React.ReactNode }) {
  return <button type="button" onClick={onClick} className={`border-b-2 px-4 py-3 text-sm font-semibold ${active ? "border-blue-600 text-blue-700" : "border-transparent text-slate-500 hover:text-slate-800"}`}>{children}</button>;
}

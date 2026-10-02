"use client";

import { Suspense, useCallback, useEffect, useEffectEvent, useMemo, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";

import Sidebar from "@/components/Sidebar";
import {
  ExceptionReviewModal,
  type AttendanceExceptionRecord,
} from "@/components/attendance/ExceptionReviewModal";
import { AppCard, PageHeader, Section, StatusBadge } from "@/components/ui";
import { apiFetch, getAccessToken, getCurrentUser } from "@/lib/api";

type ViewMode = "pending" | "history";
type Decision = "approved" | "waived" | "held";
type Filters = { search: string; type: string; status: string; date_from: string; date_to: string; department: string; min_minutes: string; max_minutes: string };
type Rule = { key: string; label: string; description: string; decision: Decision; filters: Record<string, string>; comment: string; matching: number };
type Department = { id: number; name: string };
type Queue = { count: number; page: number; page_size: number; total_proposed_deduction: string; by_type: Record<string, number>; results: AttendanceExceptionRecord[] };

const PAGE_SIZE = 50;
const BULK_CHUNK = 100;
const exceptionTypes = ["late", "early_departure", "absence", "missing_clock_in", "missing_clock_out", "hostel_violation"];
const emptyFilters: Filters = { search: "", type: "", status: "", date_from: "", date_to: "", department: "", min_minutes: "", max_minutes: "" };
const inputClass = "rounded-xl border border-slate-300 bg-white px-3 py-2.5 text-sm outline-none focus:ring-2 focus:ring-blue-500";
const decisionText: Record<Decision, string> = { approved: "approve", waived: "waive", held: "hold" };
const decisionTone: Record<Decision, string> = { approved: "bg-emerald-600", waived: "bg-slate-700", held: "bg-amber-600" };

function label(value: string) {
  return value.replace(/_/g, " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

const money = (value: string) => `₦${Number(value || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
const day = (value: string) => new Intl.DateTimeFormat("en-GB", { day: "2-digit", month: "short", year: "numeric" }).format(new Date(`${value}T00:00:00`));

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

function queryString(filters: Record<string, string>, extra: Record<string, string> = {}) {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries({ ...filters, ...extra })) if (value) params.set(key, value);
  return params.toString();
}

export default function ExceptionPage() {
  return (
    <Suspense fallback={null}>
      <ExceptionPageInner />
    </Suspense>
  );
}

function ExceptionPageInner() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const deepLinkId = searchParams.get("id");
  const [viewMode, setViewMode] = useState<ViewMode>("pending");
  const [filters, setFilters] = useState<Filters>(emptyFilters);
  const [searchText, setSearchText] = useState("");
  const [page, setPage] = useState(1);
  const [queue, setQueue] = useState<Queue | null>(null);
  const [departments, setDepartments] = useState<Department[]>([]);
  const [rules, setRules] = useState<Rule[]>([]);
  const [canReview, setCanReview] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [feedback, setFeedback] = useState("");
  const [selected, setSelected] = useState<number[]>([]);
  const [allMatching, setAllMatching] = useState(false);
  const [bulk, setBulk] = useState<{ decision: Decision; comment: string; title: string; ids: number[] | "selection"; lockComment?: boolean; filterNote?: string } | null>(null);
  const [progress, setProgress] = useState<{ done: number; total: number } | null>(null);
  const [selectedRecord, setSelectedRecord] = useState<AttendanceExceptionRecord | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [modalError, setModalError] = useState("");
  const [deepLinkNotice, setDeepLinkNotice] = useState("");
  const [showRules, setShowRules] = useState(true);

  const pendingQuery = useMemo(() => queryString({ ...filters, status: viewMode === "pending" ? "pending" : filters.status || "reviewed" }, { page: String(page), page_size: String(PAGE_SIZE) }), [filters, viewMode, page]);

  const load = useCallback(async () => {
    setLoading(true); setError("");
    try {
      const response = await apiFetch(`/attendance/exceptions/queue/?${pendingQuery}`);
      if (!response.ok) throw new Error(await errorMessage(response));
      setQueue(await response.json());
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "Unable to load attendance exceptions.");
    } finally { setLoading(false); }
  }, [pendingQuery]);

  const loadRules = useCallback(async () => {
    const response = await apiFetch("/attendance/exceptions/rules/");
    if (response.ok) setRules((await response.json()).results || []);
  }, []);

  const loadSetup = useEffectEvent(async () => {
    try {
      const user = await getCurrentUser();
      const allowed = user.is_superuser || user.permissions.review_attendanceexception;
      setCanReview(allowed);
      if (allowed) void loadRules();
      const response = await apiFetch("/employees/departments/");
      if (response.ok) { const data = await response.json(); setDepartments(Array.isArray(data) ? data : data.results || []); }
    } catch { /* the queue itself reports problems */ }
  });
  useEffect(() => {
    if (!getAccessToken()) { router.push("/login"); return; }
    const timer = window.setTimeout(() => void loadSetup(), 0);
    return () => window.clearTimeout(timer);
  }, [router]);

  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(timer);
  }, [load]);

  // Typing in the search box waits a moment before it asks the server.
  useEffect(() => {
    const timer = window.setTimeout(() => { setFilters((current) => (current.search === searchText ? current : { ...current, search: searchText })); setPage(1); }, 350);
    return () => window.clearTimeout(timer);
  }, [searchText]);

  // Arrived from a clicked Activity Center card: jump straight to the record that prompted it.
  useEffect(() => {
    if (!deepLinkId || !getAccessToken()) return;
    (async () => {
      try {
        const response = await apiFetch(`/attendance/exceptions/?id=${deepLinkId}`);
        if (!response.ok) throw new Error(await errorMessage(response));
        const record: AttendanceExceptionRecord | undefined = (await response.json()).results?.[0];
        if (!record) { setDeepLinkNotice("That attendance exception could not be found."); return; }
        if (record.status === "pending") { setModalError(""); setSelectedRecord(record); }
        else {
          setViewMode("history");
          setFilters((current) => ({ ...current, status: record.status, search: record.employee_name }));
          setSearchText(record.employee_name);
          setDeepLinkNotice(`This ${label(record.exception_type).toLowerCase()} was already ${record.status} by ${record.reviewed_by || "a reviewer"}.`);
        }
      } catch { setDeepLinkNotice("That attendance exception could not be found."); }
    })();
  }, [deepLinkId]);

  function changeFilter(next: Partial<Filters>) {
    setFilters((current) => ({ ...current, ...next }));
    setPage(1); setSelected([]); setAllMatching(false);
  }
  function resetFilters() { setFilters(emptyFilters); setSearchText(""); setPage(1); setSelected([]); setAllMatching(false); }
  function switchView(mode: ViewMode) { setViewMode(mode); resetFilters(); setFeedback(""); }
  function showRuleCases(rule: Rule) { setViewMode("pending"); setFilters({ ...emptyFilters, ...rule.filters }); setSearchText(""); setPage(1); setSelected([]); setAllMatching(false); }

  const rows = queue?.results ?? [];
  const pageIds = rows.map((record) => record.id);
  const pageAllSelected = pageIds.length > 0 && pageIds.every((id) => selected.includes(id));
  const filtersActive = Object.entries(filters).some(([key, value]) => value && !(key === "status"));
  const pageCount = queue ? Math.max(1, Math.ceil(queue.count / PAGE_SIZE)) : 1;

  async function selectEverythingMatching() {
    setError("");
    try {
      const response = await apiFetch(`/attendance/exceptions/queue/ids/?${queryString({ ...filters, status: "pending" })}`);
      if (!response.ok) throw new Error(await errorMessage(response));
      const data = await response.json();
      setSelected(data.ids); setAllMatching(true);
      if (data.truncated) setFeedback(`Only the first ${data.ids.length} matching cases were selected.`);
    } catch (selectError) { setError(selectError instanceof Error ? selectError.message : "Unable to select every matching case."); }
  }

  async function runBulk(ids: number[], decision: Decision, comment: string) {
    setError(""); setProgress({ done: 0, total: ids.length });
    let decided = 0; let skipped = 0;
    try {
      for (let start = 0; start < ids.length; start += BULK_CHUNK) {
        const chunk = ids.slice(start, start + BULK_CHUNK);
        const response = await apiFetch("/attendance/exceptions/bulk-decision/", { method: "POST", body: JSON.stringify({ ids: chunk, decision, comment }) });
        if (!response.ok) throw new Error(await errorMessage(response));
        const data = await response.json();
        decided += data.decided; skipped += data.skipped;
        setProgress({ done: Math.min(start + BULK_CHUNK, ids.length), total: ids.length });
      }
      setFeedback(`${decided} case${decided === 1 ? "" : "s"} ${decision === "held" ? "held" : decision}.${skipped ? ` ${skipped} had already been decided and were left as they were.` : ""}`);
    } catch (bulkError) {
      setError(`${bulkError instanceof Error ? bulkError.message : "The decision could not be completed."} ${decided} case${decided === 1 ? " was" : "s were"} decided before it stopped; run it again to carry on.`);
    } finally {
      setProgress(null); setBulk(null); setSelected([]); setAllMatching(false);
      await Promise.all([load(), loadRules()]);
    }
  }

  async function confirmBulk() {
    if (!bulk) return;
    const reason = bulk.comment.trim();
    if (bulk.decision !== "approved" && !reason) return;
    if (bulk.ids === "selection") { await runBulk(selected, bulk.decision, reason); return; }
    await runBulk(bulk.ids, bulk.decision, reason);
  }

  async function startRule(rule: Rule) {
    setError("");
    const response = await apiFetch(`/attendance/exceptions/queue/ids/?${queryString({ ...rule.filters, status: "pending" })}`);
    if (!response.ok) { setError(await errorMessage(response)); return; }
    const data = await response.json();
    if (!data.ids.length) { setFeedback("Nothing is waiting for that rule right now."); await loadRules(); return; }
    setBulk({ decision: rule.decision, comment: rule.comment, title: rule.label, ids: data.ids, lockComment: true, filterNote: rule.description });
  }

  async function submitDecision(decision: Decision, comment: string) {
    if (!selectedRecord) return;
    setSubmitting(true); setModalError("");
    try {
      const response = await apiFetch(`/attendance/exceptions/${selectedRecord.id}/decision/`, { method: "POST", body: JSON.stringify({ decision, comment }) });
      if (!response.ok) throw new Error(await errorMessage(response));
      setSelectedRecord(null);
      setFeedback(`${label(selectedRecord.exception_type)} was ${decision}.`);
      await Promise.all([load(), loadRules()]);
    } catch (decisionError) {
      setModalError(decisionError instanceof Error ? decisionError.message : "Unable to process the attendance exception.");
    } finally { setSubmitting(false); }
  }

  const bulkCount = bulk ? (bulk.ids === "selection" ? selected.length : bulk.ids.length) : 0;

  return (
    <div className="min-h-screen bg-slate-100">
      <Sidebar />
      <main className="ml-64 min-w-0 p-4 md:p-8">
        <PageHeader
          title="Attendance Exceptions"
          description="Review attendance issues without changing biometric or attendance facts."
          actions={<button type="button" onClick={() => void Promise.all([load(), loadRules()])} className="rounded-xl border border-slate-300 bg-white px-4 py-2.5 text-sm font-semibold text-slate-700 hover:bg-slate-50">Refresh</button>}
        />

        <AppCard className="mb-6 border-blue-200 bg-blue-50">
          <p className="text-sm text-blue-800">Biometric punches and calculated attendance facts remain read-only. Decisions record the review outcome only.</p>
        </AppCard>

        {deepLinkNotice && <AppCard className="mb-6 border-amber-200 bg-amber-50"><p className="text-sm text-amber-800">{deepLinkNotice}</p></AppCard>}

        <div className="mb-6 flex gap-2 border-b border-slate-200">
          <ViewTab active={viewMode === "pending"} onClick={() => switchView("pending")}>Pending Review{viewMode === "pending" && queue ? ` (${queue.count})` : ""}</ViewTab>
          <ViewTab active={viewMode === "history"} onClick={() => switchView("history")}>Review History</ViewTab>
        </div>

        {canReview && viewMode === "pending" && rules.length > 0 && (
          <Section className="mb-6" title="Quick rules" subtitle="Ready-made decisions for common cases. Each applies only to cases still waiting, and records the reason shown."
            actions={<button type="button" onClick={() => setShowRules((open) => !open)} className="rounded-lg border border-slate-300 px-3 py-1.5 text-xs font-semibold text-slate-700">{showRules ? "Hide" : "Show"}</button>}>
            {showRules && <div className="grid gap-3 p-5 md:grid-cols-2 xl:grid-cols-3">
              {rules.map((rule) => (
                <div key={rule.key} className="flex flex-col rounded-2xl border border-slate-200 p-4">
                  <div className="flex items-start justify-between gap-3">
                    <p className="font-semibold text-slate-900">{rule.label}</p>
                    <span className={`shrink-0 rounded-full px-2.5 py-1 text-xs font-bold text-white ${decisionTone[rule.decision]}`}>{decisionText[rule.decision]}</span>
                  </div>
                  <p className="mt-1 flex-1 text-sm text-slate-600">{rule.description}</p>
                  <p className="mt-3 text-sm"><span className="text-2xl font-bold text-slate-900">{rule.matching}</span> <span className="text-slate-500">case{rule.matching === 1 ? "" : "s"} waiting</span></p>
                  <div className="mt-3 flex gap-2">
                    <button type="button" disabled={!rule.matching || progress !== null} onClick={() => void startRule(rule)} className="rounded-xl bg-blue-600 px-3 py-2 text-xs font-semibold text-white disabled:bg-slate-300">Apply to {rule.matching}</button>
                    <button type="button" disabled={!rule.matching} onClick={() => showRuleCases(rule)} className="rounded-xl border border-slate-300 px-3 py-2 text-xs font-semibold text-slate-700 disabled:opacity-50">Show the cases</button>
                  </div>
                </div>
              ))}
            </div>}
          </Section>
        )}

        <AppCard className="mb-6">
          <div className="grid grid-cols-1 gap-3 md:grid-cols-4">
            <input value={searchText} onChange={(event) => setSearchText(event.target.value)} placeholder="Search name, staff number or department..." className={`${inputClass} md:col-span-2`} />
            <select value={filters.department} onChange={(event) => changeFilter({ department: event.target.value })} className={inputClass}><option value="">All departments</option>{departments.map((department) => <option key={department.id} value={department.id}>{department.name}</option>)}</select>
            {viewMode === "history" ? <select value={filters.status} onChange={(event) => changeFilter({ status: event.target.value })} className={inputClass}><option value="">All decisions</option><option value="approved">Approved</option><option value="waived">Waived</option><option value="held">Held</option></select> : <div />}
            <label className="text-xs font-semibold uppercase text-slate-500">From<input type="date" value={filters.date_from} onChange={(event) => changeFilter({ date_from: event.target.value })} className={`${inputClass} mt-1 w-full`} /></label>
            <label className="text-xs font-semibold uppercase text-slate-500">To<input type="date" value={filters.date_to} onChange={(event) => changeFilter({ date_to: event.target.value })} className={`${inputClass} mt-1 w-full`} /></label>
            <label className="text-xs font-semibold uppercase text-slate-500">Minutes affected - at least<input type="number" min="0" value={filters.min_minutes} onChange={(event) => changeFilter({ min_minutes: event.target.value })} className={`${inputClass} mt-1 w-full`} /></label>
            <label className="text-xs font-semibold uppercase text-slate-500">Minutes affected - at most<input type="number" min="0" value={filters.max_minutes} onChange={(event) => changeFilter({ max_minutes: event.target.value })} className={`${inputClass} mt-1 w-full`} /></label>
          </div>
          <div className="mt-4 flex flex-wrap items-center gap-2">
            <button type="button" onClick={() => changeFilter({ type: "" })} className={`rounded-full px-3 py-1.5 text-xs font-semibold ${filters.type === "" ? "bg-blue-600 text-white" : "bg-slate-100 text-slate-700 hover:bg-slate-200"}`}>All types{queue ? ` (${Object.values(queue.by_type).reduce((a, b) => a + b, 0)})` : ""}</button>
            {exceptionTypes.map((type) => <button key={type} type="button" onClick={() => changeFilter({ type })} className={`rounded-full px-3 py-1.5 text-xs font-semibold ${filters.type === type ? "bg-blue-600 text-white" : "bg-slate-100 text-slate-700 hover:bg-slate-200"}`}>{label(type)}{queue?.by_type[type] ? ` (${queue.by_type[type]})` : ""}</button>)}
            {filtersActive && <button type="button" onClick={resetFilters} className="ml-auto rounded-lg border border-slate-300 px-3 py-1.5 text-xs font-semibold text-slate-700">Clear filters</button>}
          </div>
        </AppCard>

        {feedback && <p className="mb-6 rounded-xl bg-emerald-50 px-4 py-3 text-sm text-emerald-700">{feedback}</p>}
        {error && !loading && <p className="mb-6 rounded-xl bg-red-50 px-4 py-3 text-sm text-red-700">{error}</p>}
        {progress && <div className="mb-6 rounded-xl border border-blue-200 bg-blue-50 p-4"><p className="text-sm font-semibold text-blue-900">Deciding... {progress.done} of {progress.total}</p><div className="mt-2 h-2 overflow-hidden rounded-full bg-blue-100"><div className="h-full bg-blue-600 transition-all" style={{ width: `${Math.round((progress.done / progress.total) * 100)}%` }} /></div></div>}

        {canReview && viewMode === "pending" && selected.length > 0 && (
          <div className="sticky top-2 z-10 mb-4 flex flex-wrap items-center gap-3 rounded-2xl border border-blue-200 bg-white p-4 shadow-lg">
            <p className="text-sm font-semibold text-slate-900">{selected.length} selected{allMatching ? " (everything that matches)" : ""}</p>
            <button type="button" onClick={() => setBulk({ decision: "approved", comment: "", title: "Approve", ids: "selection" })} className="rounded-xl bg-emerald-600 px-4 py-2 text-sm font-semibold text-white">Approve</button>
            <button type="button" onClick={() => setBulk({ decision: "waived", comment: "", title: "Waive", ids: "selection" })} className="rounded-xl bg-slate-700 px-4 py-2 text-sm font-semibold text-white">Waive</button>
            <button type="button" onClick={() => setBulk({ decision: "held", comment: "", title: "Hold", ids: "selection" })} className="rounded-xl bg-amber-600 px-4 py-2 text-sm font-semibold text-white">Hold</button>
            <button type="button" onClick={() => { setSelected([]); setAllMatching(false); }} className="ml-auto rounded-xl border border-slate-300 px-3 py-2 text-sm font-semibold text-slate-700">Clear selection</button>
          </div>
        )}

        <Section title={viewMode === "pending" ? "Pending Review" : "Review History"} subtitle={queue ? `${queue.count} case${queue.count === 1 ? "" : "s"}${viewMode === "pending" && Number(queue.total_proposed_deduction) > 0 ? ` - ${money(queue.total_proposed_deduction)} proposed in deductions` : ""}` : "Loading..."}>
          {loading && !queue ? <div className="h-48 animate-pulse bg-slate-100" /> : !rows.length ? <p className="p-12 text-center text-slate-500">{filtersActive ? "No cases match these filters." : viewMode === "pending" ? "Nothing is waiting for review." : "No reviewed cases yet."}</p> : <>
            {canReview && viewMode === "pending" && pageAllSelected && queue && queue.count > rows.length && !allMatching && (
              <div className="border-b border-blue-100 bg-blue-50 px-5 py-3 text-sm text-blue-900">All {rows.length} on this page are selected. <button type="button" onClick={() => void selectEverythingMatching()} className="font-bold underline">Select all {queue.count} matching cases</button></div>
            )}
            <div className="overflow-x-auto">
              <table className="w-full min-w-[900px] text-left">
                <thead className="bg-slate-50 text-xs font-semibold uppercase text-slate-500"><tr>
                  {canReview && viewMode === "pending" && <th className="w-10 px-4 py-3"><input type="checkbox" aria-label="Select this page" checked={pageAllSelected} onChange={(event) => { setAllMatching(false); setSelected(event.target.checked ? Array.from(new Set([...selected, ...pageIds])) : selected.filter((id) => !pageIds.includes(id))); }} /></th>}
                  <th className="px-4 py-3">Employee</th><th className="px-4 py-3">Date / Shift</th><th className="px-4 py-3">Exception</th><th className="px-4 py-3">Affected</th><th className="px-4 py-3">Status</th><th className="px-4 py-3" />
                </tr></thead>
                <tbody className="divide-y divide-slate-100">
                  {rows.map((record) => (
                    <tr key={record.id} className={selected.includes(record.id) ? "bg-blue-50/60" : ""}>
                      {canReview && viewMode === "pending" && <td className="px-4 py-3"><input type="checkbox" aria-label={`Select ${record.employee_name}`} checked={selected.includes(record.id)} onChange={(event) => { setAllMatching(false); setSelected(event.target.checked ? [...selected, record.id] : selected.filter((id) => id !== record.id)); }} /></td>}
                      <td className="px-4 py-3"><p className="font-semibold text-slate-900">{record.employee_name}</p><p className="text-sm text-slate-500">{record.employee_id}{record.department ? ` · ${record.department}` : ""}</p></td>
                      <td className="px-4 py-3 text-sm text-slate-700">{day(record.attendance_date)}<p className="text-xs text-slate-500">{record.shift_name || "-"}</p></td>
                      <td className="px-4 py-3"><p className="font-semibold text-slate-900">{label(record.exception_type)}</p><p className="text-xs text-orange-700">{Number(record.proposed_deduction) > 0 ? `${money(record.proposed_deduction)} proposed` : "No deduction proposed"}</p></td>
                      <td className="px-4 py-3 text-sm">{record.minutes_affected} min</td>
                      <td className="px-4 py-3"><StatusBadge status={record.status} />{record.status !== "pending" && record.admin_comment && <p className="mt-1 max-w-[16rem] text-xs text-slate-500">{record.admin_comment}</p>}</td>
                      <td className="px-4 py-3 text-right">{record.status === "pending" && canReview ? <button type="button" onClick={() => { setModalError(""); setSelectedRecord(record); }} className="rounded-xl bg-blue-600 px-4 py-2 text-sm font-semibold text-white">Review</button> : <span className="text-xs text-slate-500">{record.reviewed_by || ""}</span>}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="flex items-center justify-between border-t border-slate-200 px-5 py-3 text-sm text-slate-600">
              <span>Page {page} of {pageCount}</span>
              <div className="flex gap-2">
                <button type="button" disabled={page <= 1} onClick={() => setPage(page - 1)} className="rounded-lg border border-slate-300 px-3 py-1.5 font-semibold disabled:opacity-40">Previous</button>
                <button type="button" disabled={page >= pageCount} onClick={() => setPage(page + 1)} className="rounded-lg border border-slate-300 px-3 py-1.5 font-semibold disabled:opacity-40">Next</button>
              </div>
            </div>
          </>}
        </Section>
      </main>

      {bulk && (
        <div className="fixed inset-0 z-30 flex items-center justify-center bg-slate-950/40 p-4">
          <div className="w-full max-w-lg rounded-2xl bg-white p-6 shadow-xl">
            <h2 className="text-xl font-bold text-slate-900">{bulk.title}: {bulkCount} case{bulkCount === 1 ? "" : "s"}</h2>
            <p className="mt-2 text-sm text-slate-600">{bulk.filterNote ? `${bulk.filterNote} ` : ""}Each case is <b>{bulk.decision === "held" ? "held" : decisionText[bulk.decision] + (bulk.decision === "approved" ? "d" : "d")}</b>{bulk.decision === "approved" ? " and its proposed deduction goes into payroll" : ""}. This is recorded against your name for every case.</p>
            <label className="mt-4 block text-sm font-semibold text-slate-700">Reason{bulk.decision === "approved" ? " (optional)" : ""}
              <textarea value={bulk.comment} readOnly={bulk.lockComment && !!bulk.comment} onChange={(event) => setBulk({ ...bulk, comment: event.target.value })} className={`mt-2 min-h-24 w-full rounded-xl border border-slate-300 p-3 text-sm ${bulk.lockComment ? "bg-slate-50" : ""}`} placeholder="Why?" />
            </label>
            <div className="mt-5 flex justify-end gap-3">
              <button type="button" disabled={progress !== null} onClick={() => setBulk(null)} className="rounded-xl border border-slate-300 px-4 py-2.5 text-sm font-semibold">Cancel</button>
              <button type="button" disabled={progress !== null || (bulk.decision !== "approved" && !bulk.comment.trim())} onClick={() => void confirmBulk()} className={`rounded-xl px-4 py-2.5 text-sm font-semibold text-white disabled:opacity-50 ${decisionTone[bulk.decision]}`}>{progress ? "Working..." : `${label(decisionText[bulk.decision])} ${bulkCount}`}</button>
            </div>
          </div>
        </div>
      )}

      {selectedRecord && <ExceptionReviewModal record={selectedRecord} submitting={submitting} error={modalError} onClose={() => !submitting && setSelectedRecord(null)} onSubmit={(decision, comment) => void submitDecision(decision, comment)} />}
    </div>
  );
}

function ViewTab({ active, onClick, children }: { active: boolean; onClick: () => void; children: React.ReactNode }) {
  return <button type="button" onClick={onClick} className={`border-b-2 px-4 py-3 text-sm font-semibold ${active ? "border-blue-600 text-blue-700" : "border-transparent text-slate-500 hover:text-slate-800"}`}>{children}</button>;
}

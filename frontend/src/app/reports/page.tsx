"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";

import Sidebar from "@/components/Sidebar";
import { MetricCard, PageHeader, Section } from "@/components/ui";
import { apiFetch, getAccessToken } from "@/lib/api";

type Summary = {
  week_start: string;
  week_end: string;
  week_number: number;
  workforce: {
    total_employees: number;
    active_employees: number;
    inactive_employees: number;
    by_department: { name: string; count: number }[];
    by_employment_type: { name: string; count: number }[];
    new_hires: number;
    exits: number;
  };
  attendance: { present: number; late: number; absent: number; on_leave: number; night_shift: number; overtime: number };
  leave: { submitted: number; approved: number; pending: number; rejected: number; cancelled: number; days_approved: string };
  meals: { total: number; within_entitlement: number; excess: number };
};

function mondayOf(date: Date) {
  const day = date.getDay();
  const diff = (day === 0 ? -6 : 1) - day;
  const monday = new Date(date);
  monday.setDate(date.getDate() + diff);
  return monday.toISOString().slice(0, 10);
}

function formatDate(value: string) {
  return new Date(`${value}T00:00:00`).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
}

export default function ReportsPage() {
  const router = useRouter();
  const [weekStart, setWeekStart] = useState(() => mondayOf(new Date()));
  const [summary, setSummary] = useState<Summary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [downloading, setDownloading] = useState(false);

  useEffect(() => {
    if (!getAccessToken()) { router.push("/login"); return; }
    void loadSummary();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [router, weekStart]);

  async function loadSummary() {
    setLoading(true);
    setError("");
    try {
      const response = await apiFetch(`/reports/weekly/?week_start=${weekStart}`);
      if (!response.ok) throw new Error("Unable to load the weekly report.");
      setSummary(await response.json());
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "Unable to load the weekly report.");
    } finally {
      setLoading(false);
    }
  }

  function shiftWeek(days: number) {
    const next = new Date(`${weekStart}T00:00:00`);
    next.setDate(next.getDate() + days);
    setWeekStart(mondayOf(next));
  }

  async function downloadPresentation() {
    setDownloading(true);
    setError("");
    try {
      const response = await apiFetch(`/reports/weekly/presentation/?week_start=${weekStart}`);
      if (!response.ok) throw new Error("Unable to generate the presentation.");
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = `Week ${summary?.week_number ?? ""} HR Report.pptx`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
    } catch (downloadError) {
      setError(downloadError instanceof Error ? downloadError.message : "Unable to generate the presentation.");
    } finally {
      setDownloading(false);
    }
  }

  return (
    <div className="min-h-screen bg-slate-100">
      <Sidebar />
      <main className="ml-64 min-w-0 p-4 md:p-8">
        <PageHeader
          title="Weekly HR Report"
          description="A snapshot of workforce, attendance, leave, and meals for one week, downloadable as a slide deck."
          actions={
            <button
              type="button"
              disabled={downloading || loading}
              onClick={() => void downloadPresentation()}
              className="rounded-xl bg-blue-600 px-5 py-3 font-semibold text-white hover:bg-blue-700 disabled:bg-blue-300"
            >
              {downloading ? "Generating..." : "Download Presentation (.pptx)"}
            </button>
          }
        />

        <div className="mb-6 flex flex-col gap-3 rounded-2xl border border-slate-200 bg-white p-4 sm:flex-row sm:items-center sm:justify-between">
          <div className="flex items-center gap-3">
            <button type="button" onClick={() => shiftWeek(-7)} className="rounded-xl border border-slate-300 px-4 py-2 text-sm font-semibold text-slate-700 hover:bg-slate-50">Previous Week</button>
            <button type="button" onClick={() => setWeekStart(mondayOf(new Date()))} className="text-sm font-semibold text-blue-700 hover:text-blue-800">This Week</button>
            <button type="button" onClick={() => shiftWeek(7)} className="rounded-xl border border-slate-300 px-4 py-2 text-sm font-semibold text-slate-700 hover:bg-slate-50">Next Week</button>
          </div>
          {summary && <p className="text-sm font-semibold text-slate-600">Week {summary.week_number} &middot; {formatDate(summary.week_start)} - {formatDate(summary.week_end)}</p>}
        </div>

        {error && <p className="mb-6 rounded-xl bg-red-50 p-4 text-sm text-red-700">{error}</p>}

        {loading || !summary ? (
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            {Array.from({ length: 8 }).map((_, index) => <div key={index} className="h-28 animate-pulse rounded-2xl bg-slate-200" />)}
          </div>
        ) : (
          <>
            <Section className="mb-6" title="Workforce" subtitle="Headcount as of the end of the selected week.">
              <div className="grid gap-4 p-5 sm:grid-cols-2 lg:grid-cols-5">
                <MetricCard title="Total" value={summary.workforce.total_employees} subtitle="All employees" accentColor="#1E2761" />
                <MetricCard title="Active" value={summary.workforce.active_employees} subtitle="Currently working" accentColor="#059669" />
                <MetricCard title="Inactive" value={summary.workforce.inactive_employees} subtitle="No longer active" accentColor="#64748b" />
                <MetricCard title="New Hires" value={summary.workforce.new_hires} subtitle="This week" accentColor="#0891b2" />
                <MetricCard title="Exits" value={summary.workforce.exits} subtitle="This week" accentColor="#dc2626" />
              </div>
            </Section>

            <Section className="mb-6" title="Attendance" subtitle="Monday to Saturday of the selected week.">
              <div className="grid gap-4 p-5 sm:grid-cols-2 lg:grid-cols-6">
                <MetricCard title="Present" value={summary.attendance.present} accentColor="#059669" />
                <MetricCard title="Late" value={summary.attendance.late} accentColor="#d97706" />
                <MetricCard title="Absent" value={summary.attendance.absent} accentColor="#dc2626" />
                <MetricCard title="On Leave" value={summary.attendance.on_leave} accentColor="#1E2761" />
                <MetricCard title="Night Shift" value={summary.attendance.night_shift} accentColor="#64748b" />
                <MetricCard title="Overtime" value={summary.attendance.overtime} accentColor="#7c3aed" />
              </div>
            </Section>

            <Section className="mb-6" title="Leave" subtitle="Requests submitted or decided during the selected week.">
              <div className="grid gap-4 p-5 sm:grid-cols-2 lg:grid-cols-5">
                <MetricCard title="Submitted" value={summary.leave.submitted} accentColor="#1E2761" />
                <MetricCard title="Approved" value={summary.leave.approved} accentColor="#059669" />
                <MetricCard title="Pending" value={summary.leave.pending} accentColor="#d97706" />
                <MetricCard title="Rejected" value={summary.leave.rejected} accentColor="#dc2626" />
                <MetricCard title="Days Approved" value={summary.leave.days_approved} accentColor="#64748b" />
              </div>
            </Section>

            <Section title="Meals" subtitle="Tickets collected during the selected week.">
              <div className="grid gap-4 p-5 sm:grid-cols-3">
                <MetricCard title="Total Tickets" value={summary.meals.total} accentColor="#1E2761" />
                <MetricCard title="Within Entitlement" value={summary.meals.within_entitlement} accentColor="#059669" />
                <MetricCard title="Excess" value={summary.meals.excess} accentColor="#d97706" />
              </div>
            </Section>
          </>
        )}
      </main>
    </div>
  );
}

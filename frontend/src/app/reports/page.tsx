"use client";

import { useCallback, useEffect, useState } from "react";
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
  meals: { total: number; within_entitlement: number; excess: number; total_cost: string; previous_total_cost: string; cost_per_ticket: string };
  rates: { retention_rate: number; hire_rate: number; attrition_rate: number; net_movement: number };
  department_needs: {
    approved_headcount_total: number;
    pending_hires_total: number;
    surplus_employees_total: number;
    departments: { name: string; current: number; approved: number; diff: number; status: string }[];
  };
  gender: { male: number; female: number; unspecified: number };
  accommodation: {
    company: { capacity: number; occupied: number };
    external: { capacity: number; occupied: number };
  };
  offences: { count: number; total_amount: string };
};

function formatNaira(value: string | number) {
  return `₦${Number(value).toLocaleString("en-NG", { maximumFractionDigits: 0 })}`;
}

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

  const loadSummary = useCallback(async () => {
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
  }, [weekStart]);

  useEffect(() => {
    if (!getAccessToken()) { router.push("/login"); return; }
    // eslint-disable-next-line react-hooks/set-state-in-effect -- fetch-on-mount/param-change; the loader only sets its loading/error flags, no derived state
    void loadSummary();
  }, [router, loadSummary]);

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

            <Section className="mb-6" title="Meals" subtitle="Tickets and cost collected during the selected week.">
              <div className="grid gap-4 p-5 sm:grid-cols-3 lg:grid-cols-5">
                <MetricCard title="Total Tickets" value={summary.meals.total} accentColor="#1E2761" />
                <MetricCard title="Within Entitlement" value={summary.meals.within_entitlement} accentColor="#059669" />
                <MetricCard title="Excess" value={summary.meals.excess} accentColor="#d97706" />
                <MetricCard title="Total Cost" value={formatNaira(summary.meals.total_cost)} accentColor="#1E2761" />
                <MetricCard title="Cost per Ticket" value={formatNaira(summary.meals.cost_per_ticket)} accentColor="#64748b" />
              </div>
            </Section>

            <Section className="mb-6" title="Rates" subtitle="Retention, hiring and attrition, computed from this week's movement.">
              <div className="grid gap-4 p-5 sm:grid-cols-2 lg:grid-cols-4">
                <MetricCard title="Retention Rate" value={`${summary.rates.retention_rate}%`} accentColor="#059669" />
                <MetricCard title="Hire Rate" value={`${summary.rates.hire_rate}%`} accentColor="#0891b2" />
                <MetricCard title="Attrition Rate" value={`${summary.rates.attrition_rate}%`} accentColor="#dc2626" />
                <MetricCard title="Net Movement" value={summary.rates.net_movement} accentColor="#1E2761" />
              </div>
            </Section>

            <Section className="mb-6" title="Department Needs" subtitle="Current headcount against each department's approved target.">
              <div className="grid gap-4 p-5 sm:grid-cols-3">
                <MetricCard title="Approved Headcount" value={summary.department_needs.approved_headcount_total} accentColor="#64748b" />
                <MetricCard title="Pending Hires" value={summary.department_needs.pending_hires_total} accentColor="#d97706" />
                <MetricCard title="Surplus Employees" value={summary.department_needs.surplus_employees_total} accentColor="#1E2761" />
              </div>
            </Section>

            <Section className="mb-6" title="Gender" subtitle="Active workforce snapshot.">
              <div className="grid gap-4 p-5 sm:grid-cols-3">
                <MetricCard title="Male" value={summary.gender.male} accentColor="#1E2761" />
                <MetricCard title="Female" value={summary.gender.female} accentColor="#7c3aed" />
                <MetricCard title="Unspecified" value={summary.gender.unspecified} accentColor="#64748b" />
              </div>
            </Section>

            <Section className="mb-6" title="Accommodation" subtitle="Live occupancy snapshot, company vs external.">
              <div className="grid gap-4 p-5 sm:grid-cols-2">
                <MetricCard title="Company" value={`${summary.accommodation.company.occupied} / ${summary.accommodation.company.capacity}`} accentColor="#1E2761" />
                <MetricCard title="External" value={`${summary.accommodation.external.occupied} / ${summary.accommodation.external.capacity}`} accentColor="#64748b" />
              </div>
            </Section>

            <Section title="Disciplinary" subtitle="From the Offences module, this week.">
              <div className="grid gap-4 p-5 sm:grid-cols-2">
                <MetricCard title="Cases" value={summary.offences.count} accentColor="#d97706" />
                <MetricCard title="Total Amount" value={formatNaira(summary.offences.total_amount)} accentColor="#1E2761" />
              </div>
            </Section>
          </>
        )}
      </main>
    </div>
  );
}

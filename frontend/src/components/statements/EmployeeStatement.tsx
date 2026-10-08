"use client";

import type { FormEvent } from "react";
import { useEffect, useEffectEvent, useMemo, useState } from "react";
import { Section } from "@/components/ui";
import { apiFetch, getCurrentUser } from "@/lib/api";

type Employee = { id: number; employee_id: string; full_name: string; department_name?: string | null };
type Day = { date: string; weekday: string; status: string; status_label: string; shift: string | null; clock_in: string | null; clock_out: string | null; late_minutes: number; hours_worked: number };
type Charge = { date: string | null; kind: string; description: string; amount: string; status: string; status_label: string };
type Case = { id: number; date: string; kind: string; minutes: number | null; shift: string | null; scheduled: string | null; clock_in: string | null; clock_out: string | null; status: string; status_label: string; decided_by: string | null; decided_on: string | null; comment: string | null; can_change: boolean };
type Reward = { description: string; amount: string; status_label: string };
type Statement = {
  employee: { id: number; employee_id: string; name: string; department: string | null; position: string | null };
  period: { year: number; month: number; label: string; provisional: boolean; shown_until: string };
  attendance_summary: { days_worked: number; present: number; late: number; absent: number; leave: number; incomplete: number; rest_days: number; scheduled_work_days: number; hours_worked: number; late_minutes: number };
  days: Day[];
  attendance_cases: Case[];
  can_change_cases: boolean;
  attendance_deduction_note: string | null;
  charges: Charge[];
  charges_total: string;
  charges_pending_total: string;
  rewards: Reward[];
  rewards_total: string;
};

const inputClass = "w-full rounded-xl border border-slate-300 bg-white px-4 py-3 text-slate-900 outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-500";
const money = (value: string) => `₦${Number(value || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
const dayLabel = (value: string) => new Intl.DateTimeFormat("en-GB", { day: "2-digit", month: "short" }).format(new Date(`${value}T00:00:00`));
const fullDate = (value: string) => new Intl.DateTimeFormat("en-GB", { day: "2-digit", month: "short", year: "numeric" }).format(new Date(`${value}T00:00:00`));
const currentMonth = () => { const now = new Date(); return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`; };

const statusTone: Record<string, string> = {
  present: "text-emerald-700", late: "text-amber-700", absent: "text-red-700 font-semibold", leave: "text-blue-700", incomplete: "text-amber-700", rest: "text-slate-400", none: "text-slate-300",
};

function Tile({ label, value, hint }: { label: string; value: string | number; hint?: string }) {
  return <div className="rounded-xl border border-slate-200 p-3 text-center"><p className="text-xs font-semibold uppercase tracking-wide text-slate-500">{label}</p><p className="mt-1 text-2xl font-bold text-slate-900">{value}</p>{hint && <p className="text-xs text-slate-500">{hint}</p>}</div>;
}

export function EmployeeStatement() {
  const [allowed, setAllowed] = useState<boolean | null>(null);
  const [employees, setEmployees] = useState<Employee[]>([]);
  const [filter, setFilter] = useState("");
  const [employeeId, setEmployeeId] = useState("");
  const [month, setMonth] = useState(currentMonth);
  const [statement, setStatement] = useState<Statement | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [changing, setChanging] = useState<number | null>(null);

  const loadAccess = useEffectEvent(async () => {
    try {
      const user = await getCurrentUser();
      const ok = user.is_superuser || user.permissions.view_employee_statement;
      setAllowed(ok);
      if (!ok) return;
      const response = await apiFetch("/employees/?status=active");
      if (response.ok) setEmployees((await response.json()).results || []);
    } catch { setAllowed(false); }
  });
  useEffect(() => {
    const timer = window.setTimeout(() => void loadAccess(), 0);
    return () => window.clearTimeout(timer);
  }, []);

  const shown = useMemo(() => {
    const term = filter.trim().toLowerCase();
    return term ? employees.filter((employee) => `${employee.full_name} ${employee.employee_id}`.toLowerCase().includes(term)) : employees;
  }, [employees, filter]);

  async function changeCase(item: Case, decision: "waived" | "approved") {
    const question = decision === "waived" ? `Reverse this ${item.kind.toLowerCase()} charge (${fullDate(item.date)})? Say what you checked:` : `Charge this ${item.kind.toLowerCase()} (${fullDate(item.date)})? Say what you checked:`;
    const reason = window.prompt(question);
    if (reason === null) return;
    setChanging(item.id); setError("");
    try {
      const response = await apiFetch(`/attendance/exceptions/${item.id}/change-decision/`, { method: "POST", body: JSON.stringify({ decision, reason }) });
      const data = await response.json().catch(() => null);
      if (!response.ok) throw new Error(data?.detail || "The charge could not be changed.");
      await load(false);
    } catch (changeError) {
      setError(changeError instanceof Error ? changeError.message : "The charge could not be changed.");
    } finally { setChanging(null); }
  }

  async function generate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    await load(true);
  }

  async function load(reset: boolean) {
    setLoading(true); setError(""); if (reset) setStatement(null);
    try {
      const [year, monthNumber] = month.split("-");
      const response = await apiFetch(`/reports/employee-statement/?employee=${employeeId}&year=${year}&month=${Number(monthNumber)}`);
      const data = await response.json().catch(() => null);
      if (!response.ok) throw new Error(data?.detail || "Unable to generate the statement.");
      setStatement(data);
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "Unable to generate the statement.");
    } finally { setLoading(false); }
  }

  const summary = statement?.attendance_summary;

  return <div>
    <style>{`@media print { body * { visibility: hidden !important; } #employee-statement, #employee-statement * { visibility: visible !important; } #employee-statement { position: absolute; left: 0; top: 0; width: 100%; margin: 0; } }`}</style>
    <div className="print:hidden">
      <p className="mb-4 text-sm text-slate-500">Attendance and charges for one employee and month, to show or print when they ask. It does not show salary.</p>
      {allowed === false && <Section title="No access" subtitle="Your account cannot generate employee statements."><p className="p-8 text-sm text-slate-600">Ask an administrator to grant &ldquo;Generate an employee&apos;s monthly statement&rdquo; in Settings.</p></Section>}
      {allowed && <Section title="Choose employee and month">
        <form onSubmit={generate} className="grid gap-4 p-5 md:grid-cols-4">
          <label className="block md:col-span-2"><span className="mb-2 block text-sm font-semibold text-slate-700">Employee</span>
            <input value={filter} onChange={(event) => setFilter(event.target.value)} placeholder="Type to narrow the list by name or staff number..." className={`${inputClass} mb-2`} />
            <select required value={employeeId} onChange={(event) => setEmployeeId(event.target.value)} className={inputClass}>
              <option value="">{shown.length ? `Select employee (${shown.length})` : "No matching employee"}</option>
              {shown.map((employee) => <option key={employee.id} value={employee.id}>{employee.full_name} - {employee.employee_id}{employee.department_name ? ` - ${employee.department_name}` : ""}</option>)}
            </select>
          </label>
          <label className="block"><span className="mb-2 block text-sm font-semibold text-slate-700">Month</span><input required type="month" max={currentMonth()} value={month} onChange={(event) => setMonth(event.target.value)} className={inputClass} /></label>
          <div className="flex items-end"><button disabled={loading || !employeeId} className="w-full rounded-xl bg-blue-600 px-5 py-3 text-sm font-semibold text-white disabled:cursor-not-allowed disabled:bg-blue-300">{loading ? "Generating..." : "Generate statement"}</button></div>
        </form>
      </Section>}
      {error && <p className="mt-6 rounded-xl bg-red-50 p-4 text-sm text-red-700">{error}</p>}
    </div>

      {statement && summary && <article id="employee-statement" className="mt-6 rounded-2xl border border-slate-200 bg-white p-6 shadow-sm print:mt-0 print:rounded-none print:border-0 print:p-0 print:shadow-none">
        <div className="flex items-start justify-between gap-4 border-b border-slate-200 pb-4">
          <div>
            <p className="text-xs font-bold uppercase tracking-widest text-blue-700">Rotic - Monthly Statement</p>
            <h2 className="mt-1 text-2xl font-bold text-slate-900">{statement.employee.name}</h2>
            <p className="text-sm text-slate-600">Staff no. {statement.employee.employee_id}{statement.employee.department ? ` - ${statement.employee.department}` : ""}{statement.employee.position ? ` - ${statement.employee.position}` : ""}</p>
          </div>
          <div className="text-right">
            <p className="text-lg font-bold text-slate-900">{statement.period.label}</p>
            <p className="text-xs text-slate-500">Printed {fullDate(new Date().toISOString().slice(0, 10))}</p>
            <button type="button" onClick={() => window.print()} className="mt-2 rounded-xl bg-slate-900 px-4 py-2 text-sm font-semibold text-white print:hidden">Print</button>
          </div>
        </div>
        {statement.period.provisional && <p className="mt-4 rounded-xl bg-amber-50 p-3 text-sm text-amber-900">This month is not finished. Figures are up to {fullDate(statement.period.shown_until)} and may change.</p>}

        <h3 className="mt-6 text-sm font-bold uppercase tracking-wide text-slate-700">Attendance</h3>
        <div className="mt-3 grid grid-cols-3 gap-3 md:grid-cols-6">
          <Tile label="Days worked" value={summary.days_worked} hint={summary.scheduled_work_days ? `of ${summary.scheduled_work_days} scheduled` : undefined} />
          <Tile label="Absent" value={summary.absent} />
          <Tile label="Late" value={summary.late} hint={summary.late_minutes ? `${summary.late_minutes} min in total` : undefined} />
          <Tile label="On leave" value={summary.leave} />
          <Tile label="Rest days" value={summary.rest_days} />
          <Tile label="Hours worked" value={summary.hours_worked} />
        </div>
        <div className="mt-4 overflow-x-auto"><table className="w-full text-left text-sm"><thead className="bg-slate-50 text-xs font-semibold uppercase text-slate-500"><tr><th className="px-3 py-2">Date</th><th className="px-3 py-2">Day</th><th className="px-3 py-2">Status</th><th className="px-3 py-2">Shift</th><th className="px-3 py-2">In</th><th className="px-3 py-2">Out</th><th className="px-3 py-2 text-right">Late (min)</th><th className="px-3 py-2 text-right">Hours</th></tr></thead>
          <tbody className="divide-y divide-slate-100">{statement.days.map((day) => <tr key={day.date}><td className="px-3 py-1.5">{dayLabel(day.date)}</td><td className="px-3 py-1.5 text-slate-500">{day.weekday}</td><td className={`px-3 py-1.5 ${statusTone[day.status] || ""}`}>{day.status_label}</td><td className="px-3 py-1.5 text-slate-600">{day.shift || "-"}</td><td className="px-3 py-1.5">{day.clock_in || "-"}</td><td className="px-3 py-1.5">{day.clock_out || "-"}</td><td className="px-3 py-1.5 text-right">{day.late_minutes || "-"}</td><td className="px-3 py-1.5 text-right">{day.hours_worked || "-"}</td></tr>)}</tbody></table></div>
        {statement.attendance_deduction_note && <p className="mt-3 rounded-xl bg-slate-50 p-3 text-xs text-slate-600">{statement.attendance_deduction_note}</p>}

        {statement.attendance_cases.length > 0 && <>
          <h3 className="mt-8 text-sm font-bold uppercase tracking-wide text-slate-700">Lateness, early departures and absences</h3>
          <p className="mt-1 text-xs text-slate-500">Each case with the times it was judged on. If the employee disputes one, check the punches here{statement.can_change_cases ? " and reverse the charge if it is wrong." : "; someone with permission can then reverse it."}</p>
          <div className="mt-3 overflow-x-auto"><table className="w-full text-left text-sm"><thead className="bg-slate-50 text-xs font-semibold uppercase text-slate-500"><tr><th className="px-3 py-2">Date</th><th className="px-3 py-2">Case</th><th className="px-3 py-2">Shift</th><th className="px-3 py-2">Clocked in</th><th className="px-3 py-2">Clocked out</th><th className="px-3 py-2">Decision</th>{statement.can_change_cases && <th className="px-3 py-2 print:hidden" />}</tr></thead>
            <tbody className="divide-y divide-slate-100">{statement.attendance_cases.map((item) => <tr key={item.id}><td className="px-3 py-1.5">{dayLabel(item.date)}</td><td className="px-3 py-1.5">{item.kind}{item.minutes ? ` - ${item.minutes} min` : ""}</td><td className="px-3 py-1.5 text-slate-600">{item.scheduled || item.shift || "-"}</td><td className="px-3 py-1.5">{item.clock_in || "-"}</td><td className="px-3 py-1.5">{item.clock_out || "-"}</td><td className="px-3 py-1.5"><span className={item.status === "waived" ? "text-emerald-700" : item.status === "approved" ? "font-semibold text-red-700" : "text-amber-700"}>{item.status_label}</span>{item.decided_by && item.status !== "pending" && <span className="block text-xs text-slate-500">{item.decided_by}{item.decided_on ? `, ${fullDate(item.decided_on)}` : ""}</span>}{item.comment && <span className="block max-w-[22rem] text-xs text-slate-500">{item.comment}</span>}</td>{statement.can_change_cases && <td className="whitespace-nowrap px-3 py-1.5 text-right print:hidden">{item.can_change ? <>{item.status !== "waived" && <button type="button" disabled={changing === item.id} onClick={() => void changeCase(item, "waived")} className="rounded-lg border border-emerald-300 px-2.5 py-1 text-xs font-semibold text-emerald-700 hover:bg-emerald-50 disabled:opacity-50">Reverse charge</button>}{item.status !== "approved" && <button type="button" disabled={changing === item.id} onClick={() => void changeCase(item, "approved")} className="ml-2 rounded-lg border border-red-300 px-2.5 py-1 text-xs font-semibold text-red-700 hover:bg-red-50 disabled:opacity-50">Charge it</button>}</> : <span className="text-xs text-slate-400">Awaiting a decision</span>}</td>}</tr>)}</tbody></table></div>
        </>}

        <h3 className="mt-8 text-sm font-bold uppercase tracking-wide text-slate-700">Charges to come off pay</h3>
        {statement.charges.length ? <div className="mt-3 overflow-x-auto"><table className="w-full text-left text-sm"><thead className="bg-slate-50 text-xs font-semibold uppercase text-slate-500"><tr><th className="px-3 py-2">Date</th><th className="px-3 py-2">Type</th><th className="px-3 py-2">Details</th><th className="px-3 py-2">Status</th><th className="px-3 py-2 text-right">Amount</th></tr></thead>
          <tbody className="divide-y divide-slate-100">{statement.charges.map((charge, index) => <tr key={index}><td className="px-3 py-1.5">{charge.date ? dayLabel(charge.date) : "-"}</td><td className="px-3 py-1.5">{charge.kind}</td><td className="px-3 py-1.5">{charge.description}</td><td className="px-3 py-1.5 text-slate-600">{charge.status_label}</td><td className="px-3 py-1.5 text-right font-semibold">{Number(charge.amount) > 0 ? money(charge.amount) : "No deduction"}</td></tr>)}</tbody></table>
          <div className="mt-3 flex flex-col items-end gap-1 text-sm"><p>Charges confirmed or expected: <span className="font-bold">{money(statement.charges_total)}</span></p>{Number(statement.charges_pending_total) > 0 && <p className="text-slate-600">Awaiting approval (may be added): <span className="font-semibold">{money(statement.charges_pending_total)}</span></p>}</div></div> : <p className="mt-3 rounded-xl bg-emerald-50 p-4 text-sm text-emerald-800">No charges for this month.</p>}

        {statement.rewards.length > 0 && <>
          <h3 className="mt-8 text-sm font-bold uppercase tracking-wide text-slate-700">Rewards</h3>
          <div className="mt-3 overflow-x-auto"><table className="w-full text-left text-sm"><tbody className="divide-y divide-slate-100">{statement.rewards.map((reward, index) => <tr key={index}><td className="px-3 py-1.5">{reward.description}</td><td className="px-3 py-1.5 text-slate-600">{reward.status_label}</td><td className="px-3 py-1.5 text-right font-semibold">{money(reward.amount)}</td></tr>)}</tbody></table></div>
        </>}

        <p className="mt-8 border-t border-slate-200 pt-3 text-xs text-slate-500">This statement shows attendance and charges only; it does not show salary. Amounts marked &ldquo;awaiting approval&rdquo; are not yet confirmed. If anything looks wrong, please tell HR.</p>
      </article>}
  </div>;
}

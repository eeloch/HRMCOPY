"use client";

import { useEffect, useEffectEvent, useState } from "react";

import { Section } from "@/components/ui";
import { apiFetch } from "@/lib/api";

type Plan = { id: number; name: string; kind: string; description: string; members: number; group_a: number; group_b: number; shift: string | null; this_week?: { monday: string; day_group: string; next_monday: string; next_day_group: string } };
type Overview = { results: Plan[]; active_employees: number; on_a_plan: number };
type Department = { id: number; name: string };
type UploadReport = { dry_run: boolean; rows_in_file: number; changes: number; unchanged: number; by_plan: Record<string, number>; issues: { row: number; employee_id: string; reason: string }[] };

const inputClass = "mt-1 w-full rounded-xl border border-slate-300 bg-white px-3 py-2.5 text-sm outline-none focus:ring-2 focus:ring-blue-500";

function apiError(data: unknown, fallback: string) {
  if (data && typeof data === "object" && typeof (data as { detail?: unknown }).detail === "string") return (data as { detail: string }).detail;
  return fallback;
}

function nextMondayISO() {
  const date = new Date();
  const add = ((8 - date.getDay()) % 7) || 7;
  date.setDate(date.getDate() + add);
  return date.toISOString().slice(0, 10);
}

/** Shift plans: assign whole departments to a rotation or a permanent shift once; rosters then look after themselves. */
export function ShiftPlans({ onChanged }: { onChanged?: () => void }) {
  const [overview, setOverview] = useState<Overview | null>(null);
  const [departments, setDepartments] = useState<Department[]>([]);
  const [error, setError] = useState("");
  const [feedback, setFeedback] = useState("");
  const [busy, setBusy] = useState(false);
  const [form, setForm] = useState({ plan: "", group: "split", who: "departments", departments: [] as number[], currentPlan: "", start: nextMondayISO() });
  const [preview, setPreview] = useState<{ people: number; split: { A: number; B: number } | null; sample: string[] } | null>(null);
  const [downloading, setDownloading] = useState(false);
  const [uploadFile, setUploadFile] = useState<File | null>(null);
  const [uploadReport, setUploadReport] = useState<UploadReport | null>(null);
  const [uploading, setUploading] = useState(false);

  async function load() {
    const [plans, deps] = await Promise.all([apiFetch("/attendance/shift-plans/"), apiFetch("/employees/departments/")]);
    if (plans.ok) setOverview(await plans.json());
    if (deps.ok) { const data = await deps.json(); setDepartments(Array.isArray(data) ? data : data.results || []); }
  }
  const loadOnMount = useEffectEvent(() => { void load(); });
  useEffect(() => { const timer = window.setTimeout(loadOnMount, 0); return () => window.clearTimeout(timer); }, []);

  const selected = overview?.results.find((plan) => String(plan.id) === form.plan);
  const rotation = selected?.kind === "rotation";

  function body(dryRun: boolean) {
    return {
      plan: Number(form.plan), group: rotation ? form.group : "", start_date: form.start, dry_run: dryRun,
      ...(form.who === "everyone" ? { everyone: true } : form.who === "plan" ? { current_plan: Number(form.currentPlan) } : { department_ids: form.departments }),
    };
  }

  async function run(dryRun: boolean) {
    setBusy(true); setError(""); setFeedback("");
    try {
      const response = await apiFetch("/attendance/shift-plans/assign/", { method: "POST", body: JSON.stringify(body(dryRun)) });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(apiError(data, "That did not work."));
      if (dryRun) setPreview(data);
      else { setPreview(null); setFeedback(`${data.people} people assigned from ${new Date(data.start_date).toLocaleDateString("en-GB")}. Their rosters are written for the next 17 weeks and extend by themselves.`); await load(); onChanged?.(); }
    } catch (runError) { setError(runError instanceof Error ? runError.message : "That did not work."); }
    finally { setBusy(false); }
  }

  async function flip(plan: Plan) {
    if (!window.confirm(`Swap which group is on Day in "${plan.name}"? Group A and Group B trade places from today onward.`)) return;
    setBusy(true); setError("");
    try {
      const response = await apiFetch(`/attendance/shift-plans/${plan.id}/flip-week/`, { method: "POST" });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(apiError(data, "That did not work."));
      setFeedback("Groups swapped. Rosters from today onward have been rewritten."); await load(); onChanged?.();
    } catch (flipError) { setError(flipError instanceof Error ? flipError.message : "That did not work."); }
    finally { setBusy(false); }
  }

  async function downloadTemplate() {
    setDownloading(true); setError("");
    try {
      const response = await apiFetch("/attendance/shift-roster/template/");
      if (!response.ok) throw new Error("Unable to create the roster template.");
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement("a");
      link.href = url;
      link.download = "Roster Upload Template.xlsx";
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
    } catch (downloadError) {
      setError(downloadError instanceof Error ? downloadError.message : "Unable to create the roster template.");
    } finally { setDownloading(false); }
  }

  async function runUpload(dryRun: boolean) {
    if (!uploadFile) return;
    setUploading(true); setError("");
    try {
      const form = new FormData();
      form.append("file", uploadFile);
      form.append("dry_run", dryRun ? "true" : "false");
      const response = await apiFetch("/attendance/shift-roster/upload/", { method: "POST", body: form });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(apiError(data, "Unable to read that file."));
      setUploadReport(data);
      if (!dryRun) {
        setFeedback(`${data.changes} people assigned from the spreadsheet. Their rosters are written for the next 17 weeks and extend by themselves.`);
        setUploadFile(null); setUploadReport(null);
        await load(); onChanged?.();
      }
    } catch (uploadError) {
      setError(uploadError instanceof Error ? uploadError.message : "Unable to read that file.");
    } finally { setUploading(false); }
  }

  const ready = form.plan && (!rotation || form.group) && (form.who !== "departments" || form.departments.length > 0) && (form.who !== "plan" || form.currentPlan);

  return (
    <Section className="mb-6" title="Shift Plans" subtitle="Put people on a plan once. Rotating groups swap Day and Night every week by themselves, and rosters are written 17 weeks ahead and topped up daily - nobody has to move 400 people each week.">
      <div className="space-y-6 p-5">
        {feedback && <p className="rounded-xl bg-emerald-50 p-3 text-sm text-emerald-800">{feedback}</p>}
        {error && <p className="rounded-xl bg-red-50 p-3 text-sm text-red-700">{error}</p>}
        {overview && (
          <>
            <p className="text-sm text-slate-600"><b>{overview.on_a_plan}</b> of {overview.active_employees} active employees are on a plan.{overview.on_a_plan < overview.active_employees ? " The rest have no automatic roster." : ""}</p>
            <div className="grid gap-4 md:grid-cols-2">
              {overview.results.map((plan) => (
                <div key={plan.id} className="rounded-2xl border border-slate-200 p-4">
                  <div className="flex items-start justify-between gap-3"><p className="font-bold text-slate-900">{plan.name}</p><span className="rounded-full bg-slate-100 px-3 py-1 text-xs font-semibold text-slate-600">{plan.members} people</span></div>
                  <p className="mt-1 text-sm text-slate-600">{plan.description}</p>
                  {plan.kind === "rotation" && plan.this_week && (
                    <div className="mt-3 rounded-xl bg-slate-50 p-3 text-sm">
                      <p><b>Group A:</b> {plan.group_a} · <b>Group B:</b> {plan.group_b}</p>
                      <p className="mt-1 text-slate-600">This week: <b>Group {plan.this_week.day_group}</b> on Day, Group {plan.this_week.day_group === "A" ? "B" : "A"} on Night. Next week (from {new Date(plan.this_week.next_monday).toLocaleDateString("en-GB", { day: "numeric", month: "short" })}) they swap.</p>
                      <button type="button" disabled={busy} onClick={() => void flip(plan)} className="mt-2 rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-xs font-semibold text-slate-700">Swap the groups</button>
                    </div>
                  )}
                </div>
              ))}
            </div>

            <div className="rounded-2xl border border-slate-200 p-4">
              <p className="font-semibold text-slate-900">Assign people to a plan</p>
              <div className="mt-3 grid gap-3 md:grid-cols-3">
                <label className="text-sm font-semibold text-slate-700">Plan<select value={form.plan} onChange={(event) => { setForm({ ...form, plan: event.target.value }); setPreview(null); }} className={inputClass}><option value="">Choose a plan</option>{overview.results.map((plan) => <option key={plan.id} value={plan.id}>{plan.name}</option>)}</select></label>
                {rotation && <label className="text-sm font-semibold text-slate-700">Group<select value={form.group} onChange={(event) => { setForm({ ...form, group: event.target.value }); setPreview(null); }} className={inputClass}><option value="split">Split evenly between A and B</option><option value="A">All in Group A</option><option value="B">All in Group B</option></select></label>}
                <label className="text-sm font-semibold text-slate-700">Starting from<input type="date" value={form.start} onChange={(event) => setForm({ ...form, start: event.target.value })} className={inputClass} /></label>
              </div>
              <div className="mt-3 text-sm font-semibold text-slate-700">Who
                <div className="mt-1 flex flex-wrap gap-4 font-normal">
                  {([["departments", "Chosen departments"], ["plan", "Everyone now on another plan"], ["everyone", "All active employees"]] as const).map(([key, label]) => <label key={key} className="flex items-center gap-2"><input type="radio" checked={form.who === key} onChange={() => { setForm({ ...form, who: key }); setPreview(null); }} />{label}</label>)}
                </div>
              </div>
              {form.who === "departments" && <div className="mt-3 flex flex-wrap gap-2">{departments.map((d) => <label key={d.id} className={`cursor-pointer rounded-full border px-3 py-1.5 text-sm ${form.departments.includes(d.id) ? "border-blue-600 bg-blue-600 text-white" : "border-slate-300 bg-white text-slate-700"}`}><input type="checkbox" className="hidden" checked={form.departments.includes(d.id)} onChange={(event) => { setForm({ ...form, departments: event.target.checked ? [...form.departments, d.id] : form.departments.filter((id) => id !== d.id) }); setPreview(null); }} />{d.name}</label>)}</div>}
              {form.who === "plan" && <label className="mt-3 block max-w-sm text-sm font-semibold text-slate-700">Currently on<select value={form.currentPlan} onChange={(event) => { setForm({ ...form, currentPlan: event.target.value }); setPreview(null); }} className={inputClass}><option value="">Choose a plan</option>{overview.results.map((plan) => <option key={plan.id} value={plan.id}>{plan.name} ({plan.members})</option>)}</select></label>}
              {preview && <p className="mt-3 rounded-xl bg-blue-50 p-3 text-sm text-blue-900"><b>{preview.people}</b> people will be assigned{preview.split ? ` (${preview.split.A} in Group A, ${preview.split.B} in Group B)` : ""}. For example: {preview.sample.join(", ")}.</p>}
              <div className="mt-4 flex gap-3">
                <button type="button" disabled={!ready || busy} onClick={() => void run(true)} className="rounded-xl border border-slate-300 bg-white px-4 py-2.5 text-sm font-semibold text-slate-700 disabled:opacity-50">Preview</button>
                <button type="button" disabled={!ready || busy || !preview} onClick={() => void run(false)} className="rounded-xl bg-blue-600 px-4 py-2.5 text-sm font-semibold text-white disabled:bg-blue-300">{busy ? "Working..." : "Assign"}</button>
              </div>
              <p className="mt-2 text-xs text-slate-500">Manual roster changes you have made for individual days are never overwritten. A rotation should start on a Monday.</p>
            </div>

            <div className="rounded-2xl border border-slate-200 p-4">
              <p className="font-semibold text-slate-900">Assign by spreadsheet</p>
              <p className="mt-1 text-sm text-slate-600">Download the template - it already lists every active employee with their staff number, name, department and current plan. Type the Plan Name (and Group, for a rotation plan) only for the people who should change, leave the rest blank, and upload it back.</p>
              <div className="mt-3 flex flex-wrap items-center gap-3">
                <button type="button" disabled={downloading} onClick={() => void downloadTemplate()} className="rounded-xl border border-slate-300 bg-white px-4 py-2.5 text-sm font-semibold text-slate-700 disabled:opacity-50">{downloading ? "Preparing..." : "Download template"}</button>
                <input type="file" accept=".xlsx" onChange={(event) => { setUploadFile(event.target.files?.[0] || null); setUploadReport(null); }} className="text-sm text-slate-600" />
              </div>
              {uploadReport && (
                <div className="mt-3 rounded-xl bg-blue-50 p-3 text-sm text-blue-900">
                  <p><b>{uploadReport.changes}</b> {uploadReport.changes === 1 ? "person" : "people"} will be assigned, {uploadReport.unchanged} left unchanged.{uploadReport.issues.length ? ` ${uploadReport.issues.length} row(s) have a problem.` : ""}</p>
                  {Object.keys(uploadReport.by_plan).length > 0 && (
                    <ul className="mt-2 list-disc pl-5">
                      {Object.entries(uploadReport.by_plan).map(([label, count]) => <li key={label}>{label}: {count}</li>)}
                    </ul>
                  )}
                  {uploadReport.issues.length > 0 && (
                    <ul className="mt-2 list-disc space-y-1 pl-5 text-red-700">
                      {uploadReport.issues.map((issue) => <li key={issue.row}>Row {issue.row}{issue.employee_id ? ` (${issue.employee_id})` : ""}: {issue.reason}</li>)}
                    </ul>
                  )}
                </div>
              )}
              <div className="mt-4 flex gap-3">
                <button type="button" disabled={!uploadFile || uploading} onClick={() => void runUpload(true)} className="rounded-xl border border-slate-300 bg-white px-4 py-2.5 text-sm font-semibold text-slate-700 disabled:opacity-50">Preview</button>
                <button type="button" disabled={!uploadFile || uploading || !uploadReport || uploadReport.changes === 0} onClick={() => void runUpload(false)} className="rounded-xl bg-blue-600 px-4 py-2.5 text-sm font-semibold text-white disabled:bg-blue-300">{uploading ? "Working..." : "Assign from file"}</button>
              </div>
            </div>
          </>
        )}
      </div>
    </Section>
  );
}

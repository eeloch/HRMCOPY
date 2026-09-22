"use client";

import { useEffect, useState } from "react";

import { apiFetch } from "@/lib/api";
import type { ShiftEmployee } from "./types";

type Props = {
  employee: ShiftEmployee;
  onAssignmentChanged: () => void;
};

type PlanSummary = { id: number; name: string; kind: string; group: string; start_date: string } | null;
type TodaySummary = { date: string; status: "work" | "rest"; shift: { name: string; start_time: string; end_time: string } | null } | null;
type Plan = { id: number; name: string; kind: string };

const inputClass = "mt-2 w-full rounded-xl border border-slate-300 bg-white px-3 py-2.5 text-sm outline-none focus:ring-2 focus:ring-blue-500";

function formatDate(value: string) {
  return new Date(`${value}T00:00:00`).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
}

function formatTime(value: string) {
  return value.slice(0, 5);
}

function apiError(data: unknown, fallback: string) {
  if (data && typeof data === "object" && typeof (data as { detail?: unknown }).detail === "string") return (data as { detail: string }).detail;
  return fallback;
}

/** The employee's real, live shift: their current plan (and group, for a rotation) plus what today's roster
 * actually says - not a static shift that never updates when a rotation flips Day/Night week to week. */
export function EmployeeShiftPanel({ employee, onAssignmentChanged }: Props) {
  const [plan, setPlan] = useState<PlanSummary>(null);
  const [today, setToday] = useState<TodaySummary>(null);
  const [plans, setPlans] = useState<Plan[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [showModal, setShowModal] = useState(false);
  const [form, setForm] = useState({ plan: "", group: "", start: new Date().toISOString().slice(0, 10) });
  const [saving, setSaving] = useState(false);

  async function load() {
    setLoading(true);
    setError("");
    try {
      const [summaryResponse, plansResponse] = await Promise.all([
        apiFetch(`/attendance/shift-plans/for-employee/${employee.id}/`),
        apiFetch("/attendance/shift-plans/"),
      ]);
      if (!summaryResponse.ok || !plansResponse.ok) throw new Error("Unable to load this employee's shift.");
      const summary = await summaryResponse.json();
      const plansData = await plansResponse.json();
      setPlan(summary.plan);
      setToday(summary.today);
      setPlans((plansData.results || []).map((p: { id: number; name: string; kind: string }) => ({ id: p.id, name: p.name, kind: p.kind })));
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "Unable to load this employee's shift.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    const loadTimer = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(loadTimer);
  }, [employee.id]);

  const selectedPlan = plans.find((p) => String(p.id) === form.plan);
  const needsGroup = selectedPlan?.kind === "rotation";

  function openModal() {
    setForm({ plan: plan ? String(plan.id) : "", group: plan?.group || "", start: new Date().toISOString().slice(0, 10) });
    setShowModal(true);
  }

  async function save() {
    setSaving(true);
    setError("");
    try {
      const response = await apiFetch("/attendance/shift-plans/assign/", {
        method: "POST",
        body: JSON.stringify({
          plan: Number(form.plan),
          group: needsGroup ? form.group : "",
          start_date: form.start,
          employee_ids: [employee.id],
        }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(apiError(data, "Unable to change this employee's shift."));
      setShowModal(false);
      await load();
      onAssignmentChanged();
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : "Unable to change this employee's shift.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="mt-6 rounded-2xl border border-slate-200 bg-white shadow-sm">
      <div className="flex flex-col gap-3 border-b border-slate-200 px-6 py-5 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h2 className="font-bold text-slate-900">Shift Plan</h2>
          <p className="mt-1 text-sm text-slate-500">Their current plan and what today's roster actually has them on.</p>
        </div>
        <button type="button" onClick={openModal} className="rounded-xl bg-blue-600 px-4 py-2.5 text-sm font-semibold text-white hover:bg-blue-700">
          {plan ? "Change Plan" : "Assign a Plan"}
        </button>
      </div>

      {error && <p className="mx-6 mt-5 rounded-xl bg-red-50 px-3 py-2 text-sm text-red-700">{error}</p>}

      {loading ? (
        <div className="space-y-3 p-6">
          <div className="h-16 animate-pulse rounded-xl bg-slate-100" />
          <div className="h-16 animate-pulse rounded-xl bg-slate-100" />
        </div>
      ) : (
        <div className="grid gap-4 p-6 sm:grid-cols-2">
          <div className="rounded-xl border border-slate-200 p-4">
            <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">Current Plan</p>
            {plan ? (
              <>
                <p className="mt-1 text-lg font-bold text-slate-900">{plan.name}</p>
                {plan.group && <p className="mt-1 text-sm text-slate-600">Group {plan.group}</p>}
                <p className="mt-1 text-sm text-slate-500">Effective from {formatDate(plan.start_date)}</p>
              </>
            ) : (
              <p className="mt-1 text-slate-500">No plan assigned yet.</p>
            )}
          </div>
          <div className="rounded-xl border border-slate-200 p-4">
            <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">Today ({today ? formatDate(today.date) : "-"})</p>
            {today && today.status === "work" && today.shift ? (
              <>
                <p className="mt-1 text-lg font-bold text-slate-900">{today.shift.name}</p>
                <p className="mt-1 text-sm text-slate-600">{formatTime(today.shift.start_time)} - {formatTime(today.shift.end_time)}</p>
              </>
            ) : today && today.status === "rest" ? (
              <p className="mt-1 text-lg font-bold text-slate-900">Resting today</p>
            ) : (
              <p className="mt-1 text-slate-500">No roster day generated yet.</p>
            )}
          </div>
        </div>
      )}

      {showModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
          <div className="w-full max-w-md rounded-2xl bg-white p-6 shadow-xl">
            <h3 className="text-lg font-bold text-slate-900">{plan ? "Change Plan" : "Assign a Plan"}</h3>
            <p className="mt-1 text-sm text-slate-500">This only changes {employee.full_name}'s own schedule.</p>
            <label className="mt-4 block text-sm font-semibold text-slate-700">
              Plan
              <select value={form.plan} onChange={(event) => setForm({ ...form, plan: event.target.value, group: "" })} className={inputClass}>
                <option value="">Choose a plan</option>
                {plans.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
              </select>
            </label>
            {needsGroup && (
              <label className="mt-4 block text-sm font-semibold text-slate-700">
                Group
                <select value={form.group} onChange={(event) => setForm({ ...form, group: event.target.value })} className={inputClass}>
                  <option value="">Choose a group</option>
                  <option value="A">Group A</option>
                  <option value="B">Group B</option>
                </select>
              </label>
            )}
            <label className="mt-4 block text-sm font-semibold text-slate-700">
              Starting from
              <input type="date" value={form.start} onChange={(event) => setForm({ ...form, start: event.target.value })} className={inputClass} />
            </label>
            <div className="mt-6 flex justify-end gap-3">
              <button type="button" onClick={() => setShowModal(false)} className="rounded-xl border border-slate-300 px-4 py-2.5 text-sm font-semibold text-slate-700">Cancel</button>
              <button type="button" disabled={!form.plan || (needsGroup && !form.group) || saving} onClick={() => void save()} className="rounded-xl bg-blue-600 px-4 py-2.5 text-sm font-semibold text-white disabled:bg-blue-300">
                {saving ? "Saving..." : "Save"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

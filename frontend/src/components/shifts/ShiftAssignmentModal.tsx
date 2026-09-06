"use client";

import { useState } from "react";

import { apiFetch } from "@/lib/api";
import type { Shift, ShiftAssignment, ShiftEmployee } from "./types";

type Props = {
  open: boolean;
  employees: ShiftEmployee[];
  shifts: Shift[];
  employeeId?: number;
  activeAssignment?: ShiftAssignment | null;
  onClose: () => void;
  onSaved: () => void;
};

function today() {
  return new Date().toISOString().slice(0, 10);
}

function nextDay() {
  const date = new Date();
  date.setDate(date.getDate() + 1);
  return date.toISOString().slice(0, 10);
}

async function responseError(response: Response) {
  const body: unknown = await response.json().catch(() => null);
  if (body && typeof body === "object") {
    const message = Object.values(body as Record<string, unknown>)
      .flatMap((value) => (Array.isArray(value) ? value : [value]))
      .map(String)
      .join(" ");
    if (message) return message;
  }
  return "Unable to save the shift assignment.";
}

export function ShiftAssignmentModal({
  open,
  employeeId,
  activeAssignment,
  ...props
}: Props) {
  if (!open) return null;

  return (
    <ShiftAssignmentForm
      key={`${employeeId || "new"}-${activeAssignment?.id || "assignment"}`}
      employeeId={employeeId}
      activeAssignment={activeAssignment}
      {...props}
    />
  );
}

function ShiftAssignmentForm({
  employees,
  shifts,
  employeeId,
  activeAssignment,
  onClose,
  onSaved,
}: Omit<Props, "open">) {
  const [selectedEmployee, setSelectedEmployee] = useState(String(employeeId || ""));
  const [selectedShift, setSelectedShift] = useState("");
  const [startDate, setStartDate] = useState(activeAssignment?.end_date === null ? nextDay() : today());
  const [endDate, setEndDate] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  const isChangingOpenAssignment = activeAssignment?.end_date === null;

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    setSaving(true);

    try {
      const payload = {
        employee: Number(selectedEmployee),
        shift: Number(selectedShift),
        start_date: startDate,
        ...(!isChangingOpenAssignment && endDate ? { end_date: endDate } : {}),
      };
      const endpoint = isChangingOpenAssignment
        ? "/attendance/shift-assignments/change/"
        : "/attendance/shift-assignments/";
      const response = await apiFetch(endpoint, {
        method: "POST",
        body: JSON.stringify(payload),
      });

      if (!response.ok) throw new Error(await responseError(response));

      onSaved();
      onClose();
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : "Unable to save the shift assignment.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/40 p-4">
      <form onSubmit={submit} className="w-full max-w-lg rounded-2xl bg-white p-6 shadow-xl">
        <div className="flex items-start justify-between gap-4">
          <div>
            <h2 className="text-xl font-bold text-slate-900">
              {isChangingOpenAssignment ? "Change Shift" : "Assign Shift"}
            </h2>
            <p className="mt-1 text-sm text-slate-500">
              {isChangingOpenAssignment
                ? "The current open-ended assignment will end the day before this one starts."
                : "Create a dated shift assignment for this employee."}
            </p>
          </div>
          <button type="button" onClick={onClose} className="text-slate-400 hover:text-slate-700" aria-label="Close">
            Close
          </button>
        </div>

        <div className="mt-6 space-y-4">
          <label className="block text-sm font-medium text-slate-700">
            Employee
            <select
              required
              value={selectedEmployee}
              disabled={Boolean(employeeId)}
              onChange={(event) => setSelectedEmployee(event.target.value)}
              className="mt-1.5 w-full rounded-xl border border-slate-300 bg-white px-3 py-2.5 disabled:bg-slate-100"
            >
              <option value="">Select employee</option>
              {employees.map((employee) => (
                <option key={employee.id} value={employee.id}>
                  {employee.full_name} ({employee.employee_id})
                </option>
              ))}
            </select>
          </label>

          <label className="block text-sm font-medium text-slate-700">
            Shift
            <select required value={selectedShift} onChange={(event) => setSelectedShift(event.target.value)} className="mt-1.5 w-full rounded-xl border border-slate-300 bg-white px-3 py-2.5">
              <option value="">Select shift</option>
              {shifts.map((shift) => (
                <option key={shift.id} value={shift.id}>
                  {shift.name} ({shift.start_time.slice(0, 5)} - {shift.end_time.slice(0, 5)})
                </option>
              ))}
            </select>
          </label>

          <div className={`grid grid-cols-1 gap-4 ${isChangingOpenAssignment ? "" : "sm:grid-cols-2"}`}>
            <label className="block text-sm font-medium text-slate-700">
              Start Date
              <input required type="date" value={startDate} onChange={(event) => setStartDate(event.target.value)} className="mt-1.5 w-full rounded-xl border border-slate-300 px-3 py-2.5" />
            </label>
            {!isChangingOpenAssignment && (
              <label className="block text-sm font-medium text-slate-700">
                End Date <span className="font-normal text-slate-400">(optional)</span>
                <input type="date" value={endDate} onChange={(event) => setEndDate(event.target.value)} className="mt-1.5 w-full rounded-xl border border-slate-300 px-3 py-2.5" />
              </label>
            )}
          </div>
        </div>

        {error && <p className="mt-4 rounded-xl bg-red-50 px-3 py-2 text-sm text-red-700">{error}</p>}

        <div className="mt-6 flex justify-end gap-3">
          <button type="button" onClick={onClose} disabled={saving} className="rounded-xl border border-slate-300 px-4 py-2.5 text-sm font-semibold text-slate-700">
            Cancel
          </button>
          <button type="submit" disabled={saving} className="rounded-xl bg-blue-600 px-4 py-2.5 text-sm font-semibold text-white disabled:bg-blue-300">
            {saving ? "Saving..." : isChangingOpenAssignment ? "Change Shift" : "Assign Shift"}
          </button>
        </div>
      </form>
    </div>
  );
}

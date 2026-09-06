"use client";

import { useEffect, useState } from "react";

import { apiFetch } from "@/lib/api";
import { ShiftAssignmentModal } from "./ShiftAssignmentModal";
import type { Shift, ShiftAssignment, ShiftEmployee } from "./types";

type Props = {
  employee: ShiftEmployee;
  onAssignmentChanged: () => void;
};

function formatDate(value: string | null) {
  if (!value) return "Ongoing";
  return new Date(`${value}T00:00:00`).toLocaleDateString(undefined, {
    day: "numeric",
    month: "short",
    year: "numeric",
  });
}

function formatTime(value: string) {
  return value.slice(0, 5);
}

export function EmployeeShiftPanel({ employee, onAssignmentChanged }: Props) {
  const [assignments, setAssignments] = useState<ShiftAssignment[]>([]);
  const [shifts, setShifts] = useState<Shift[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [showModal, setShowModal] = useState(false);
  const [endingId, setEndingId] = useState<number | null>(null);

  const activeAssignment = assignments.find((assignment) => assignment.end_date === null) || null;

  async function load() {
    setLoading(true);
    setError("");
    try {
      const [assignmentsResponse, shiftsResponse] = await Promise.all([
        apiFetch(`/attendance/shift-assignments/?employee=${employee.id}`),
        apiFetch("/attendance/shifts/"),
      ]);
      if (!assignmentsResponse.ok || !shiftsResponse.ok) {
        throw new Error("Unable to load shift assignments.");
      }
      const [assignmentsData, shiftsData] = await Promise.all([
        assignmentsResponse.json(),
        shiftsResponse.json(),
      ]);
      setAssignments(assignmentsData.results || []);
      setShifts(shiftsData.results || []);
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "Unable to load shift assignments.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    const loadTimer = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(loadTimer);
  }, [employee.id]);

  async function endAssignment(assignment: ShiftAssignment) {
    const defaultDate = new Date().toISOString().slice(0, 10);
    const endDate = window.prompt("Enter the final assignment date (YYYY-MM-DD):", defaultDate);
    if (!endDate) return;

    setEndingId(assignment.id);
    setError("");
    try {
      const response = await apiFetch(`/attendance/shift-assignments/${assignment.id}/`, {
        method: "PATCH",
        body: JSON.stringify({ end_date: endDate }),
      });
      if (!response.ok) throw new Error("Unable to end the shift assignment.");
      await load();
      onAssignmentChanged();
    } catch (endError) {
      setError(endError instanceof Error ? endError.message : "Unable to end the shift assignment.");
    } finally {
      setEndingId(null);
    }
  }

  return (
    <div className="mt-6 rounded-2xl border border-slate-200 bg-white shadow-sm">
      <div className="flex flex-col gap-3 border-b border-slate-200 px-6 py-5 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h2 className="font-bold text-slate-900">Shift Assignment</h2>
          <p className="mt-1 text-sm text-slate-500">Current schedule and assignment history.</p>
        </div>
        <button type="button" onClick={() => setShowModal(true)} className="rounded-xl bg-blue-600 px-4 py-2.5 text-sm font-semibold text-white hover:bg-blue-700">
          {activeAssignment ? "Change Shift" : "Assign Shift"}
        </button>
      </div>

      {error && <p className="mx-6 mt-5 rounded-xl bg-red-50 px-3 py-2 text-sm text-red-700">{error}</p>}

      {loading ? (
        <div className="space-y-3 p-6">
          <div className="h-16 animate-pulse rounded-xl bg-slate-100" />
          <div className="h-16 animate-pulse rounded-xl bg-slate-100" />
        </div>
      ) : assignments.length ? (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[650px] text-left">
            <thead className="bg-slate-50 text-xs font-semibold uppercase tracking-wide text-slate-500">
              <tr>
                <th className="px-6 py-3">Shift</th>
                <th className="px-6 py-3">Schedule</th>
                <th className="px-6 py-3">Start</th>
                <th className="px-6 py-3">End</th>
                <th className="px-6 py-3">Status</th>
                <th className="px-6 py-3" />
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {assignments.map((assignment) => (
                <tr key={assignment.id} className="hover:bg-slate-50">
                  <td className="px-6 py-4 font-semibold text-slate-900">{assignment.shift_name}</td>
                  <td className="px-6 py-4 text-sm text-slate-600">{formatTime(assignment.shift_start_time)} - {formatTime(assignment.shift_end_time)}</td>
                  <td className="px-6 py-4 text-sm text-slate-600">{formatDate(assignment.start_date)}</td>
                  <td className="px-6 py-4 text-sm text-slate-600">{formatDate(assignment.end_date)}</td>
                  <td className="px-6 py-4"><AssignmentStatus status={assignment.status} /></td>
                  <td className="px-6 py-4 text-right">
                    {assignment.end_date === null && (
                      <button type="button" disabled={endingId === assignment.id} onClick={() => void endAssignment(assignment)} className="text-sm font-semibold text-red-600 hover:text-red-700 disabled:text-red-300">
                        {endingId === assignment.id ? "Ending..." : "End"}
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="p-10 text-center text-slate-500">No shift assignments yet.</div>
      )}

      <ShiftAssignmentModal
        open={showModal}
        employees={[employee]}
        shifts={shifts}
        employeeId={employee.id}
        activeAssignment={activeAssignment}
        onClose={() => setShowModal(false)}
        onSaved={() => {
          void load();
          onAssignmentChanged();
        }}
      />
    </div>
  );
}

function AssignmentStatus({ status }: { status: ShiftAssignment["status"] }) {
  const classes = {
    active: "bg-emerald-100 text-emerald-700",
    upcoming: "bg-blue-100 text-blue-700",
    ended: "bg-slate-100 text-slate-700",
  };
  return <span className={`rounded-full px-2.5 py-1 text-xs font-semibold capitalize ${classes[status]}`}>{status}</span>;
}

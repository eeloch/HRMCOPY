"use client";

import { useState } from "react";

export type AttendanceExceptionRecord = {
  id: number;
  employee_id: string;
  employee_name: string;
  department: string | null;
  attendance_date: string;
  shift_name: string | null;
  scheduled_start: string | null;
  scheduled_end: string | null;
  actual_clock_in: string | null;
  actual_clock_out: string | null;
  exception_type: string;
  minutes_affected: number;
  proposed_deduction: string;
  status: string;
  admin_comment: string;
  reviewed_by: string;
  reviewed_at: string | null;
};

type Decision = "approved" | "waived" | "held";

type Props = {
  record: AttendanceExceptionRecord;
  submitting: boolean;
  error: string;
  onClose: () => void;
  onSubmit: (decision: Decision, comment: string) => void;
};

function formatDateTime(value: string | null) {
  if (!value) return "Not recorded";
  return new Date(value).toLocaleString(undefined, {
    day: "numeric",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function title(value: string) {
  return value.replace(/_/g, " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

export function ExceptionReviewModal({ record, submitting, error, onClose, onSubmit }: Props) {
  const [decision, setDecision] = useState<Decision>("approved");
  const [comment, setComment] = useState("");
  const [validationError, setValidationError] = useState("");

  function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if ((decision === "waived" || decision === "held") && !comment.trim()) {
      setValidationError(`A reason is required to ${decision === "waived" ? "waive" : "hold"} this exception.`);
      return;
    }
    setValidationError("");
    onSubmit(decision, comment.trim());
  }

  return (
    <div className="fixed inset-0 z-50 overflow-y-auto bg-slate-950/45 p-4">
      <form onSubmit={submit} className="mx-auto my-8 w-full max-w-3xl rounded-2xl bg-white shadow-xl">
        <div className="flex items-start justify-between gap-4 border-b border-slate-200 px-6 py-5">
          <div>
            <p className="text-sm font-semibold uppercase tracking-wide text-blue-600">Exception Review</p>
            <h2 className="mt-1 text-xl font-bold text-slate-900">{title(record.exception_type)}</h2>
            <p className="mt-1 text-sm text-slate-500">Attendance facts are read-only and will not be changed.</p>
          </div>
          <button type="button" onClick={onClose} disabled={submitting} className="text-sm font-semibold text-slate-500 hover:text-slate-800">Close</button>
        </div>

        <div className="grid grid-cols-1 gap-5 p-6 md:grid-cols-2">
          <Fact label="Employee" value={`${record.employee_name} (${record.employee_id})`} />
          <Fact label="Department" value={record.department || "Not assigned"} />
          <Fact label="Attendance Date" value={record.attendance_date} />
          <Fact label="Shift" value={record.shift_name || "Not assigned"} />
          <Fact label="Scheduled Start" value={formatDateTime(record.scheduled_start)} />
          <Fact label="Scheduled End" value={formatDateTime(record.scheduled_end)} />
          <Fact label="Actual Clock In" value={formatDateTime(record.actual_clock_in)} />
          <Fact label="Actual Clock Out" value={formatDateTime(record.actual_clock_out)} />
          <Fact label="Affected Minutes" value={`${record.minutes_affected} min`} />
          <Fact label="Proposed Deduction" value={`₦${Number(record.proposed_deduction).toLocaleString()}`} />
        </div>

        <div className="border-t border-slate-200 px-6 py-5">
          <p className="text-sm font-semibold text-slate-800">Decision</p>
          <div className="mt-3 grid grid-cols-1 gap-2 sm:grid-cols-3">
            <DecisionOption decision="approved" active={decision === "approved"} onSelect={setDecision} label="Approve" detail="Approve the proposed deduction." />
            <DecisionOption decision="waived" active={decision === "waived"} onSelect={setDecision} label="Waive" detail="Waive with a recorded reason." />
            <DecisionOption decision="held" active={decision === "held"} onSelect={setDecision} label="Hold" detail="Hold for further investigation." />
          </div>

          <label className="mt-5 block text-sm font-medium text-slate-700">
            Admin Comment {decision !== "approved" && <span className="text-red-600">(required)</span>}
            <textarea value={comment} onChange={(event) => setComment(event.target.value)} rows={3} placeholder={decision === "approved" ? "Optional review note..." : "Explain this decision..."} className="mt-1.5 w-full rounded-xl border border-slate-300 px-3 py-2.5 outline-none focus:ring-2 focus:ring-blue-500" />
          </label>
          {(validationError || error) && <p className="mt-3 rounded-xl bg-red-50 px-3 py-2 text-sm text-red-700">{validationError || error}</p>}
        </div>

        <div className="flex justify-end gap-3 border-t border-slate-200 px-6 py-5">
          <button type="button" onClick={onClose} disabled={submitting} className="rounded-xl border border-slate-300 px-4 py-2.5 text-sm font-semibold text-slate-700">Cancel</button>
          <button type="submit" disabled={submitting} className="rounded-xl bg-blue-600 px-4 py-2.5 text-sm font-semibold text-white disabled:bg-blue-300">{submitting ? "Saving..." : `Confirm ${title(decision)}`}</button>
        </div>
      </form>
    </div>
  );
}

function Fact({ label, value }: { label: string; value: string }) {
  return <div><p className="text-xs font-semibold uppercase tracking-wide text-slate-400">{label}</p><p className="mt-1 text-sm font-medium text-slate-800">{value}</p></div>;
}

function DecisionOption({ decision, active, onSelect, label, detail }: { decision: Decision; active: boolean; onSelect: (decision: Decision) => void; label: string; detail: string }) {
  return <button type="button" onClick={() => onSelect(decision)} className={`rounded-xl border p-3 text-left transition ${active ? "border-blue-500 bg-blue-50 ring-1 ring-blue-200" : "border-slate-200 hover:bg-slate-50"}`}><span className="block text-sm font-semibold text-slate-900">{label}</span><span className="mt-1 block text-xs text-slate-500">{detail}</span></button>;
}

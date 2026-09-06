const statusStyles: Record<string, string> = {
  present: "bg-emerald-50 text-emerald-700 ring-emerald-100",
  active: "bg-emerald-50 text-emerald-700 ring-emerald-100",
  approved: "bg-emerald-50 text-emerald-700 ring-emerald-100",
  processing: "bg-blue-50 text-blue-700 ring-blue-100",
  review: "bg-violet-50 text-violet-700 ring-violet-100",
  paid: "bg-emerald-50 text-emerald-700 ring-emerald-100",
  closed: "bg-slate-100 text-slate-700 ring-slate-200",
  draft: "bg-slate-100 text-slate-700 ring-slate-200",
  success: "bg-emerald-50 text-emerald-700 ring-emerald-100",
  late: "bg-amber-50 text-amber-700 ring-amber-100",
  pending: "bg-amber-50 text-amber-700 ring-amber-100",
  warning: "bg-amber-50 text-amber-700 ring-amber-100",
  absent: "bg-red-50 text-red-700 ring-red-100",
  rejected: "bg-red-50 text-red-700 ring-red-100",
  error: "bg-red-50 text-red-700 ring-red-100",
  leave: "bg-blue-50 text-blue-700 ring-blue-100",
  leave_punch_conflict: "bg-rose-50 text-rose-700 ring-rose-100",
  info: "bg-blue-50 text-blue-700 ring-blue-100",
  inactive: "bg-slate-100 text-slate-700 ring-slate-200",
  held: "bg-slate-100 text-slate-700 ring-slate-200",
  waived: "bg-blue-50 text-blue-700 ring-blue-100",
};

export function StatusBadge({ status }: { status: string }) {
  const normalizedStatus = status.toLowerCase();
  const label = normalizedStatus === "leave_punch_conflict"
    ? "Leave / Punch Conflict"
    : normalizedStatus
    .replace(/_/g, " ")
    .replace(/\b\w/g, (character) => character.toUpperCase());

  return (
    <span
      className={`inline-flex rounded-full px-3 py-1 text-sm font-medium ring-1 ${
        statusStyles[normalizedStatus] || "bg-slate-100 text-slate-700 ring-slate-200"
      }`}
    >
      {label}
    </span>
  );
}

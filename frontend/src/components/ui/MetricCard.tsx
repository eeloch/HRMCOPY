import type { ReactNode } from "react";

type MetricCardProps = {
  title: string;
  value: ReactNode;
  subtitle?: string;
  icon?: ReactNode;
  accentColor?: string;
};

export function MetricCard({
  title,
  value,
  subtitle,
  icon,
  accentColor = "#2563eb",
}: MetricCardProps) {
  return (
    <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
      <div className="flex items-start justify-between gap-4">
        <div>
          <div
            className="inline-flex rounded-xl px-3 py-1 text-sm font-semibold"
            style={{ backgroundColor: `${accentColor}14`, color: accentColor }}
          >
            {title}
          </div>
          <div className="mt-4 text-3xl font-bold tabular-nums text-slate-900">{value}</div>
          {subtitle && <p className="mt-1 text-sm text-slate-500">{subtitle}</p>}
        </div>
        {icon && (
          <div
            className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-slate-50 text-lg"
            style={{ color: accentColor }}
          >
            {icon}
          </div>
        )}
      </div>
    </div>
  );
}

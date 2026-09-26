"use client";

import type { ReactNode } from "react";

/** Local calendar date as YYYY-MM-DD (not UTC: the factory works on local dates). */
export function isoDate(date: Date) {
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const dayOfMonth = String(date.getDate()).padStart(2, "0");
  return `${date.getFullYear()}-${month}-${dayOfMonth}`;
}

export type DateRange = { from: string; to: string };

export function rangeFor(preset: "today" | "yesterday" | "week" | "month" | "all"): DateRange {
  const now = new Date();
  if (preset === "all") return { from: "", to: "" };
  if (preset === "today") return { from: isoDate(now), to: isoDate(now) };
  if (preset === "yesterday") {
    const yesterday = new Date(now.getFullYear(), now.getMonth(), now.getDate() - 1);
    return { from: isoDate(yesterday), to: isoDate(yesterday) };
  }
  if (preset === "week") return { from: isoDate(new Date(now.getFullYear(), now.getMonth(), now.getDate() - 6)), to: isoDate(now) };
  return { from: isoDate(new Date(now.getFullYear(), now.getMonth(), 1)), to: isoDate(now) };
}

const presets: Array<["today" | "yesterday" | "week" | "month" | "all", string]> = [
  ["today", "Today"],
  ["yesterday", "Yesterday"],
  ["week", "Last 7 days"],
  ["month", "This month"],
  ["all", "All dates"],
];

const chipBase = "rounded-full border px-3 py-1.5 text-xs font-semibold transition";
const inputClass = "rounded-xl border border-slate-300 bg-white px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-blue-500";

/** Quick date chips plus a custom from/to. */
export function DateRangeFilter({ value, onChange }: { value: DateRange; onChange: (range: DateRange) => void }) {
  return (
    <div className="flex flex-wrap items-center gap-2">
      {presets.map(([key, label]) => {
        const preset = rangeFor(key);
        const active = preset.from === value.from && preset.to === value.to;
        return (
          <button key={key} type="button" onClick={() => onChange(preset)} className={`${chipBase} ${active ? "border-blue-600 bg-blue-600 text-white" : "border-slate-300 bg-white text-slate-700 hover:bg-slate-50"}`}>
            {label}
          </button>
        );
      })}
      <span className="ml-1 flex items-center gap-1.5 text-xs text-slate-500">
        <input type="date" aria-label="From date" value={value.from} max={value.to || undefined} onChange={(event) => onChange({ ...value, from: event.target.value })} className={inputClass} />
        to
        <input type="date" aria-label="To date" value={value.to} min={value.from || undefined} onChange={(event) => onChange({ ...value, to: event.target.value })} className={inputClass} />
      </span>
    </div>
  );
}

export function SearchBox({ value, onChange, placeholder = "Search staff number or name" }: { value: string; onChange: (value: string) => void; placeholder?: string }) {
  return <input type="search" value={value} onChange={(event) => onChange(event.target.value)} placeholder={placeholder} className={`${inputClass} w-full sm:w-72`} />;
}

export function Chips<T extends string>({ options, value, onChange }: { options: Array<[T, string]>; value: T; onChange: (value: T) => void }) {
  return (
    <div className="flex flex-wrap gap-2">
      {options.map(([key, label]) => (
        <button key={key} type="button" onClick={() => onChange(key)} className={`${chipBase} ${value === key ? "border-slate-800 bg-slate-800 text-white" : "border-slate-300 bg-white text-slate-700 hover:bg-slate-50"}`}>
          {label}
        </button>
      ))}
    </div>
  );
}

/** A table area that scrolls on its own, so the page itself stays short. The header row stays put. */
export function ScrollArea({ children, maxHeight = "58vh" }: { children: ReactNode; maxHeight?: string }) {
  return (
    <div className="overflow-auto [&_thead_th]:sticky [&_thead_th]:top-0 [&_thead_th]:z-10 [&_thead_th]:bg-slate-50" style={{ maxHeight }}>
      {children}
    </div>
  );
}

export function TabBar<T extends string>({ tabs, value, onChange }: { tabs: Array<[T, string, number | null]>; value: T; onChange: (value: T) => void }) {
  return (
    <div className="mb-6 flex flex-wrap gap-1 rounded-2xl border border-slate-200 bg-white p-1.5 shadow-sm" role="tablist">
      {tabs.map(([key, label, count]) => (
        <button key={key} type="button" role="tab" aria-selected={value === key} onClick={() => onChange(key)} className={`rounded-xl px-4 py-2.5 text-sm font-semibold transition ${value === key ? "bg-blue-600 text-white shadow" : "text-slate-600 hover:bg-slate-100"}`}>
          {label}
          {count !== null && count > 0 && <span className={`ml-2 rounded-full px-2 py-0.5 text-xs ${value === key ? "bg-white/25 text-white" : "bg-amber-100 text-amber-800"}`}>{count}</span>}
        </button>
      ))}
    </div>
  );
}

/** A collapsible panel for setup areas, closed by default so the Setup tab is short. */
export function Panel({ title, subtitle, defaultOpen = false, children }: { title: string; subtitle?: string; defaultOpen?: boolean; children: ReactNode }) {
  return (
    <details open={defaultOpen} className="group mb-4 overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">
      <summary className="flex cursor-pointer list-none items-center justify-between gap-4 px-5 py-4">
        <span>
          <span className="block text-lg font-bold text-slate-900">{title}</span>
          {subtitle && <span className="mt-0.5 block text-sm text-slate-500">{subtitle}</span>}
        </span>
        <span className="text-slate-400 transition group-open:rotate-180" aria-hidden>▾</span>
      </summary>
      <div className="border-t border-slate-200">{children}</div>
    </details>
  );
}

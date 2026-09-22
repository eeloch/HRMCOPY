"use client";

import { useEffect, useRef, useState } from "react";

import { apiFetch } from "@/lib/api";

export type EmployeeOption = { id: number; employee_id: string; full_name: string; department_name: string | null };

/** A searchable employee combobox (name or staff number), replacing a raw numeric ID field wherever one
 * employee needs to be chosen. Only searches active employees. */
export function EmployeePicker({
  value,
  onChange,
  placeholder = "Search name or staff number...",
}: {
  value: EmployeeOption | null;
  onChange: (employee: EmployeeOption | null) => void;
  placeholder?: string;
}) {
  const [term, setTerm] = useState("");
  const [results, setResults] = useState<EmployeeOption[]>([]);
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);
  // A slower, earlier request resolving after a newer one must never overwrite it - so every request is
  // numbered, and a response is only applied if it is still the most recent one issued.
  const latestRequestId = useRef(0);

  useEffect(() => {
    if (!term.trim()) {
      setResults([]);
      return;
    }
    const timer = window.setTimeout(async () => {
      const requestId = ++latestRequestId.current;
      setLoading(true);
      try {
        const response = await apiFetch(`/employees/?search=${encodeURIComponent(term.trim())}&status=active`);
        if (requestId !== latestRequestId.current) return; // a newer search has since started; discard this one
        if (response.ok) {
          const data = await response.json();
          if (requestId !== latestRequestId.current) return;
          setResults((data.results || []).slice(0, 20));
        }
      } finally {
        if (requestId === latestRequestId.current) setLoading(false);
      }
    }, 300);
    return () => window.clearTimeout(timer);
  }, [term]);

  useEffect(() => {
    function onClickOutside(event: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(event.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", onClickOutside);
    return () => document.removeEventListener("mousedown", onClickOutside);
  }, []);

  if (value) {
    return (
      <div className="flex items-center justify-between gap-3 rounded-xl border border-slate-300 bg-slate-50 px-4 py-3">
        <div>
          <p className="font-semibold text-slate-900">{value.full_name}</p>
          <p className="text-sm text-slate-500">{value.employee_id}{value.department_name ? ` · ${value.department_name}` : ""}</p>
        </div>
        <button type="button" onClick={() => { onChange(null); setTerm(""); }} className="text-sm font-semibold text-blue-700 hover:text-blue-800">
          Change
        </button>
      </div>
    );
  }

  return (
    <div ref={containerRef} className="relative">
      <input
        type="text"
        value={term}
        onChange={(event) => { setTerm(event.target.value); setOpen(true); }}
        onFocus={() => setOpen(true)}
        placeholder={placeholder}
        className="w-full rounded-xl border border-slate-300 bg-white px-4 py-3 text-slate-900 outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-500"
      />
      {open && term.trim() && (
        <div className="absolute z-20 mt-1 max-h-72 w-full overflow-y-auto rounded-xl border border-slate-200 bg-white shadow-lg">
          {loading ? (
            <p className="p-4 text-sm text-slate-500">Searching...</p>
          ) : results.length ? (
            results.map((employee) => (
              <button
                type="button"
                key={employee.id}
                onClick={() => { onChange(employee); setOpen(false); setTerm(""); }}
                className="block w-full px-4 py-2.5 text-left hover:bg-slate-50"
              >
                <span className="block font-semibold text-slate-900">{employee.full_name}</span>
                <span className="block text-sm text-slate-500">{employee.employee_id}{employee.department_name ? ` · ${employee.department_name}` : ""}</span>
              </button>
            ))
          ) : (
            <p className="p-4 text-sm text-slate-500">No matching employee.</p>
          )}
        </div>
      )}
    </div>
  );
}

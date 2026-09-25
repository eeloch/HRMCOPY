"use client";

import { useEffect, useRef, useState } from "react";

import { apiFetch } from "@/lib/api";

export type EmployeeOption = { id: number; employee_id: string; full_name: string; department_name: string | null };

/** A searchable employee combobox (name or staff number), replacing a raw numeric ID field wherever one
 * employee needs to be chosen. Only searches active employees.
 *
 * The container (and its outside-click listener) stay mounted in both the "searching" and "selected"
 * states, so there is exactly one persistent field on screen - selecting someone fills it in, the same as
 * typing would, rather than swapping in a different element that could read as the selection vanishing. */
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
  const [error, setError] = useState("");
  const containerRef = useRef<HTMLDivElement>(null);
  // A slower, earlier request resolving after a newer one must never overwrite it - so every request is
  // numbered, and a response is only applied if it is still the most recent one issued.
  const latestRequestId = useRef(0);

  useEffect(() => {
    if (!term.trim()) return; // results/error are reset where the term is cleared (see updateTerm)
    const timer = window.setTimeout(async () => {
      const requestId = ++latestRequestId.current;
      setLoading(true);
      setError("");
      try {
        const response = await apiFetch(`/employees/?search=${encodeURIComponent(term.trim())}&status=active`);
        if (requestId !== latestRequestId.current) return; // a newer search has since started; discard this one
        if (!response.ok) throw new Error("Unable to search employees.");
        const data = await response.json();
        if (requestId !== latestRequestId.current) return;
        setResults((data.results || []).slice(0, 20));
      } catch {
        if (requestId === latestRequestId.current) {
          setResults([]);
          setError("Unable to search right now. Check your connection and try again.");
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

  // Changing the term to blank also drops the previous results and error, so nothing stale reappears
  // when the user starts typing again.
  function updateTerm(next: string) {
    setTerm(next);
    if (!next.trim()) {
      setResults([]);
      setError("");
    }
  }

  function clearSelection() {
    onChange(null);
    updateTerm("");
    setOpen(false);
  }

  function selectResult(employee: EmployeeOption) {
    onChange(employee);
    setOpen(false);
    updateTerm("");
  }

  return (
    <div ref={containerRef} className="relative">
      {value ? (
        // Same size and shape as the search input below, so picking someone reads as "this field is now
        // filled in", not as a different box replacing it.
        <div className="flex w-full items-center justify-between gap-3 rounded-xl border border-emerald-300 bg-emerald-50 px-4 py-3">
          <div className="flex min-w-0 items-center gap-2.5">
            <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-emerald-600 text-xs font-bold text-white">✓</span>
            <div className="min-w-0">
              <p className="truncate font-semibold text-slate-900">{value.full_name}</p>
              <p className="truncate text-sm text-slate-500">{value.employee_id}{value.department_name ? ` · ${value.department_name}` : ""}</p>
            </div>
          </div>
          <button
            type="button"
            onClick={clearSelection}
            title="Choose a different employee"
            aria-label="Choose a different employee"
            className="shrink-0 rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-xs font-semibold text-slate-600 hover:bg-slate-50"
          >
            Change
          </button>
        </div>
      ) : (
        <input
          type="text"
          value={term}
          onChange={(event) => { updateTerm(event.target.value); setOpen(true); }}
          onFocus={() => setOpen(true)}
          onKeyDown={(event) => {
            // Enter must never fall through to submit the surrounding form while still searching; pick the
            // top match instead, the same as clicking it.
            if (event.key === "Enter") {
              event.preventDefault();
              if (results[0]) selectResult(results[0]);
            }
          }}
          placeholder={placeholder}
          className="w-full rounded-xl border border-slate-300 bg-white px-4 py-3 text-slate-900 outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-500"
        />
      )}
      {!value && open && term.trim() && (
        <div className="absolute z-20 mt-1 max-h-72 w-full overflow-y-auto rounded-xl border border-slate-200 bg-white shadow-lg">
          {loading ? (
            <p className="p-4 text-sm text-slate-500">Searching...</p>
          ) : error ? (
            <p className="p-4 text-sm text-red-600">{error}</p>
          ) : results.length ? (
            results.map((employee) => (
              <button
                type="button"
                key={employee.id}
                onClick={() => selectResult(employee)}
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

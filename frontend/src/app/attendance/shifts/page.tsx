"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";

import Sidebar from "@/components/Sidebar";
import { ShiftPlans } from "@/components/shifts/ShiftPlans";
import type { Shift } from "@/components/shifts/types";
import { PageHeader, Section } from "@/components/ui";
import { apiFetch, getAccessToken } from "@/lib/api";

export default function ShiftManagementPage() {
  const router = useRouter();
  const [shifts, setShifts] = useState<Shift[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  async function load() {
    setLoading(true);
    setError("");
    try {
      const response = await apiFetch("/attendance/shifts/");
      if (!response.ok) throw new Error("Unable to load shift definitions.");
      const data = await response.json();
      setShifts(data.results || []);
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "Unable to load shift definitions.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    if (!getAccessToken()) {
      router.push("/login");
      return;
    }
    const loadTimer = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(loadTimer);
  }, [router]);

  return (
    <div className="min-h-screen bg-slate-100">
      <Sidebar />
      <main className="ml-64 min-w-0 p-4 md:p-8">
        <PageHeader
          title="Shift Management"
          description="Put people on a plan once - rotating groups swap Day and Night every week by themselves, and each employee's own page shows their real, current shift."
        />

        <ShiftPlans />

        <Section className="mb-6" title="Shift Definitions" subtitle="Active shift windows used by plans and roster dates.">
          {error ? (
            <div className="p-8 text-center text-red-700"><p>{error}</p><button type="button" onClick={() => void load()} className="mt-4 rounded-xl bg-red-600 px-4 py-2 text-sm font-semibold text-white">Try Again</button></div>
          ) : loading ? (
            <div className="space-y-3 p-6">{Array.from({ length: 4 }).map((_, index) => <div key={index} className="h-12 animate-pulse rounded-xl bg-slate-100" />)}</div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[560px] text-left">
                <thead className="bg-slate-50 text-xs font-semibold uppercase tracking-wide text-slate-500"><tr><th className="px-5 py-3">Shift</th><th className="px-5 py-3">Start</th><th className="px-5 py-3">End</th><th className="px-5 py-3">Schedule</th></tr></thead>
                <tbody className="divide-y divide-slate-100">{shifts.filter((shift) => shift.active).map((shift) => <tr key={shift.id}><td className="px-5 py-4 font-semibold text-slate-900">{shift.name}</td><td className="px-5 py-4 text-sm text-slate-600">{shift.start_time.slice(0, 5)}</td><td className="px-5 py-4 text-sm text-slate-600">{shift.end_time.slice(0, 5)}</td><td className="px-5 py-4 text-sm text-slate-600">{shift.is_overnight ? "Overnight" : "Daytime"}</td></tr>)}</tbody>
              </table>
            </div>
          )}
        </Section>
      </main>
    </div>
  );
}

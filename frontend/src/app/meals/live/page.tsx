"use client";

import { useEffect, useEffectEvent, useState } from "react";
import { useRouter } from "next/navigation";

import Sidebar from "@/components/Sidebar";
import { apiFetch, getAccessToken } from "@/lib/api";

type Collection = { id: number; employee_name: string; work_date: string; timestamp: string; device: string; entitlement: number; sequence: number; rate: string; status: string };

const POLL_INTERVAL_MS = 4000;
const VISIBLE_COUNT = 8;

const statusDisplay: Record<string, { label: string; className: string }> = {
  within_entitlement: { label: "Within Entitlement", className: "bg-emerald-100 text-emerald-800 border-emerald-300" },
  excess: { label: "Excess - Flagged for Deduction", className: "bg-amber-100 text-amber-800 border-amber-300" },
  rest_day: { label: "Not Entitled Today", className: "bg-slate-200 text-slate-700 border-slate-300" },
};

function timeAgo(value: string) {
  const seconds = Math.max(0, Math.floor((Date.now() - new Date(value).getTime()) / 1000));
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  return new Date(value).toLocaleTimeString("en-NG", { hour: "2-digit", minute: "2-digit" });
}

export default function MealsLivePage() {
  const router = useRouter();
  const [collections, setCollections] = useState<Collection[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [, setTick] = useState(0);

  async function load() {
    try {
      const response = await apiFetch("/meals/operations/");
      if (response.status === 403) { setError("Your account does not have permission to view meal operations."); return; }
      if (!response.ok) throw new Error("Unable to load meal collections.");
      const data = await response.json();
      setCollections((data.collections || []).slice(0, VISIBLE_COUNT));
      setError("");
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "Unable to load meal collections.");
    } finally {
      setLoading(false);
    }
  }

  const loadOnMount = useEffectEvent(() => { void load(); });
  useEffect(() => {
    if (!getAccessToken()) { router.push("/login"); return; }
    const timer = window.setTimeout(loadOnMount, 0);
    const dataTimer = window.setInterval(() => void load(), POLL_INTERVAL_MS);
    const clockTimer = window.setInterval(() => setTick((value) => value + 1), 1000);
    return () => { window.clearTimeout(timer); window.clearInterval(dataTimer); window.clearInterval(clockTimer); };
  }, [router]);

  return <div className="min-h-screen bg-slate-100"><Sidebar /><main className="ml-64 min-w-0 p-6 md:p-10">
    <div className="mb-8 flex items-center justify-between">
      <div>
        <h1 className="text-4xl font-bold text-slate-900">Meal Ticket - Live</h1>
        <p className="mt-1 text-lg text-slate-500">Updates automatically every few seconds.</p>
      </div>
      <div className="text-2xl font-semibold text-slate-400">{new Date().toLocaleTimeString("en-NG", { hour: "2-digit", minute: "2-digit", second: "2-digit" })}</div>
    </div>

    {error && <p className="mb-8 rounded-2xl bg-red-50 p-6 text-lg text-red-700">{error}</p>}

    {loading ? (
      <div className="h-96 animate-pulse rounded-3xl bg-slate-200" />
    ) : collections.length === 0 ? (
      <div className="rounded-3xl border border-slate-200 bg-white p-16 text-center text-2xl text-slate-400">Waiting for the next scan...</div>
    ) : (
      <div className="space-y-4">
        {collections.map((item, index) => {
          const display = statusDisplay[item.status] || { label: item.status, className: "bg-slate-100 text-slate-700 border-slate-300" };
          return (
            <div key={item.id} className={`flex items-center justify-between rounded-3xl border-2 bg-white p-6 shadow-sm transition ${index === 0 ? "ring-4 ring-blue-200" : ""}`}>
              <div>
                <p className="text-3xl font-bold text-slate-900">{item.employee_name}</p>
                <p className="mt-1 text-lg text-slate-500">{item.device} &middot; Ticket #{item.sequence} of {item.entitlement} &middot; {timeAgo(item.timestamp)}</p>
              </div>
              <span className={`rounded-2xl border-2 px-6 py-3 text-xl font-bold ${display.className}`}>{display.label}</span>
            </div>
          );
        })}
      </div>
    )}
  </main></div>;
}

"use client";

import { useEffect, useEffectEvent, useState } from "react";

import { Section, StatusBadge } from "@/components/ui";
import { apiFetch } from "@/lib/api";
import { Empty, PanelError, PanelSkeleton, formatDateTime } from "./EmployeeAttendancePanel";

export type BiometricIdentity = { system: string; source_identifier: string; external_user_id: string; is_active: boolean; };
type BiometricEvent = { id: number; timestamp: string; device_name: string | null; device_serial_number: string | null; verification_type: string; external_event_id: string | null; };

export function biometricSystemLabel(system: string) {
  const labels: Record<string, string> = { yunatt: "Yunatt / TIMY", vendor_flask_gateway: "Vendor Flask Gateway", device: "Direct Device", other: "Other" };
  return labels[system] || system.replace(/_/g, " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

export function EmployeeBiometricPanel({ employeeId, identities }: { employeeId: number; identities: BiometricIdentity[] }) {
  const [events, setEvents] = useState<BiometricEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  async function loadEvents() { setLoading(true); setError(""); try { const response = await apiFetch(`/attendance/biometric-events/?employee=${employeeId}`); if (!response.ok) throw new Error("Unable to load biometric punches."); const data: { results?: BiometricEvent[] } = await response.json(); setEvents(data.results || []); } catch (loadError) { setError(loadError instanceof Error ? loadError.message : "Unable to load biometric punches."); } finally { setLoading(false); } }
  const loadOnMount = useEffectEvent(() => { void loadEvents(); });
  useEffect(() => { const timer = window.setTimeout(loadOnMount, 0); return () => window.clearTimeout(timer); }, [employeeId]);
  return <div className="mt-6 space-y-6"><Section title="Biometric Identities" subtitle="Active operational identity mappings only.">{identities.length ? <div className="overflow-x-auto"><table className="w-full min-w-[640px] text-left"><thead className="bg-slate-50 text-xs font-semibold uppercase tracking-wide text-slate-500"><tr><th className="px-5 py-3">System</th><th className="px-5 py-3">Source</th><th className="px-5 py-3">External User ID</th><th className="px-5 py-3">Status</th></tr></thead><tbody className="divide-y divide-slate-100">{identities.map((identity) => <tr key={`${identity.system}-${identity.source_identifier}-${identity.external_user_id}`}><td className="px-5 py-4 font-medium text-slate-800">{biometricSystemLabel(identity.system)}</td><td className="px-5 py-4 text-sm text-slate-600">{identity.source_identifier}</td><td className="px-5 py-4 font-mono text-sm text-slate-700">{identity.external_user_id}</td><td className="px-5 py-4"><StatusBadge status={identity.is_active ? "active" : "inactive"} /></td></tr>)}</tbody></table></div> : <Empty message="No active biometric identities are linked to this employee." />}</Section><Section title="Recent Biometric Punches" subtitle="Read-only raw punches. Processed attendance is shown separately under Attendance." actions={<button type="button" onClick={() => void loadEvents()} disabled={loading} className="rounded-xl border border-slate-300 px-4 py-2 text-sm font-semibold text-slate-700 hover:bg-slate-50 disabled:opacity-50">Refresh</button>}>{error ? <PanelError message={error} retry={loadEvents} /> : loading ? <PanelSkeleton /> : events.length ? <div className="overflow-x-auto"><table className="w-full min-w-[900px] text-left"><thead className="bg-slate-50 text-xs font-semibold uppercase tracking-wide text-slate-500"><tr><th className="px-5 py-3">Punch Time</th><th className="px-5 py-3">Device</th><th className="px-5 py-3">Device Serial</th><th className="px-5 py-3">Verification</th><th className="px-5 py-3">External Event ID</th></tr></thead><tbody className="divide-y divide-slate-100">{events.map((event) => <tr key={event.id} className="hover:bg-slate-50"><td className="px-5 py-4 text-sm text-slate-700">{formatDateTime(event.timestamp)}</td><td className="px-5 py-4 text-sm text-slate-700">{event.device_name || "Unknown"}</td><td className="px-5 py-4 font-mono text-sm text-slate-700">{event.device_serial_number || "Unknown"}</td><td className="px-5 py-4"><StatusBadge status={event.verification_type} /></td><td className="px-5 py-4 font-mono text-xs text-slate-600">{event.external_event_id || "-"}</td></tr>)}</tbody></table></div> : <Empty message="No biometric punches found for this employee." />}</Section></div>;
}

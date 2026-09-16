"use client";

import { useEffect, useRef, useState } from "react";

import type { ShiftEmployee } from "@/components/shifts/types";
import { apiFetch } from "@/lib/api";

type DeviceCommand = {
  id: number;
  command_type: "enroll_user" | "delete_user" | "refresh_enrolled_ids";
  payload: Record<string, unknown>;
  status: "pending" | "sent" | "acked" | "failed";
  result: Record<string, unknown>;
  created_at: string;
};

type Props = {
  deviceId: number;
  employees: ShiftEmployee[];
};

const POLL_INTERVAL_MS = 2000;
const POLL_TIMEOUT_MS = 45000;

export function DeviceCommandPanel({ deviceId, employees }: Props) {
  const [enrollEmployee, setEnrollEmployee] = useState("");
  const [biometricType, setBiometricType] = useState<"face" | "fingerprint">("face");
  const [deleteEmployee, setDeleteEmployee] = useState("");
  const [active, setActive] = useState<DeviceCommand | null>(null);
  const [error, setError] = useState("");
  const pollRef = useRef<number | null>(null);

  useEffect(() => {
    return () => {
      if (pollRef.current) window.clearInterval(pollRef.current);
    };
  }, []);

  function stopPolling() {
    if (pollRef.current) {
      window.clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }

  function startPolling(commandId: number) {
    stopPolling();
    const startedAt = Date.now();
    pollRef.current = window.setInterval(async () => {
      if (Date.now() - startedAt > POLL_TIMEOUT_MS) {
        stopPolling();
        setError("Timed out waiting for the device to respond. Make sure it's online and try again.");
        return;
      }
      try {
        const response = await apiFetch(`/attendance/devices/${deviceId}/commands/`);
        if (!response.ok) return;
        const data: { results: DeviceCommand[] } = await response.json();
        const found = data.results.find((command) => command.id === commandId);
        if (!found) return;
        setActive(found);
        if (found.status === "acked" || found.status === "failed") {
          stopPolling();
        }
      } catch {
        // transient network hiccup - the next tick will retry
      }
    }, POLL_INTERVAL_MS);
  }

  async function queueCommand(body: Record<string, unknown>) {
    setError("");
    setActive(null);
    stopPolling();
    try {
      const response = await apiFetch(`/attendance/devices/${deviceId}/commands/`, {
        method: "POST",
        body: JSON.stringify(body),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(
          response.status === 403
            ? "You don't have permission to manage enrolled staff."
            : data.detail || "Unable to queue the command."
        );
      }
      setActive(data);
      startPolling(data.id);
    } catch (queueError) {
      setError(queueError instanceof Error ? queueError.message : "Unable to queue the command.");
    }
  }

  function statusMessage(): string {
    if (!active) return "";
    if (active.status === "pending") return "Queued — waiting for the device to check in (it reports in roughly every 30 seconds)...";
    if (active.status === "sent") {
      return active.command_type === "enroll_user"
        ? "Sent to the terminal — ask the person to scan now."
        : "Sent to the terminal — waiting for its response...";
    }
    if (active.status === "acked") {
      if (active.command_type === "refresh_enrolled_ids") {
        const ids = (active.result?.record as string[] | undefined) || [];
        return ids.length
          ? `Currently enrolled IDs on this device: ${ids.join(", ")}`
          : "No one is currently enrolled on this device.";
      }
      return active.command_type === "enroll_user" ? "Enrollment saved." : "Removed from the device.";
    }
    if (active.status === "failed") {
      const reason = (active.result?.msg as string) || String(active.result?.reason ?? "unknown reason");
      return `The device reported failure: ${reason}`;
    }
    return "";
  }

  return (
    <div className="grid gap-6 border-t border-slate-200 bg-slate-50 p-5 md:grid-cols-3">
      <div>
        <h4 className="mb-2 text-sm font-semibold text-slate-900">Enroll a Person</h4>
        <select value={enrollEmployee} onChange={(event) => setEnrollEmployee(event.target.value)} className="mb-2 w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm">
          <option value="">Select employee</option>
          {employees.map((employee) => <option key={employee.id} value={employee.id}>{employee.full_name} ({employee.employee_id})</option>)}
        </select>
        <select value={biometricType} onChange={(event) => setBiometricType(event.target.value as "face" | "fingerprint")} className="mb-2 w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm">
          <option value="face">Face</option>
          <option value="fingerprint">Fingerprint</option>
        </select>
        <button
          type="button"
          disabled={!enrollEmployee}
          onClick={() => void queueCommand({ command_type: "enroll_user", employee: Number(enrollEmployee), biometric_type: biometricType })}
          className="w-full rounded-lg bg-blue-600 px-3 py-2 text-sm font-semibold text-white hover:bg-blue-700 disabled:opacity-50"
        >
          Start Enrollment
        </button>
        <p className="mt-2 text-xs text-slate-500">The person must scan at the terminal itself once this is sent.</p>
      </div>

      <div>
        <h4 className="mb-2 text-sm font-semibold text-slate-900">Remove a Person</h4>
        <select value={deleteEmployee} onChange={(event) => setDeleteEmployee(event.target.value)} className="mb-2 w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm">
          <option value="">Select employee</option>
          {employees.map((employee) => <option key={employee.id} value={employee.id}>{employee.full_name} ({employee.employee_id})</option>)}
        </select>
        <button
          type="button"
          disabled={!deleteEmployee}
          onClick={() => void queueCommand({ command_type: "delete_user", employee: Number(deleteEmployee) })}
          className="w-full rounded-lg bg-red-600 px-3 py-2 text-sm font-semibold text-white hover:bg-red-700 disabled:opacity-50"
        >
          Remove From Device
        </button>
      </div>

      <div>
        <h4 className="mb-2 text-sm font-semibold text-slate-900">Enrolled On This Device</h4>
        <p className="mb-2 text-xs text-slate-500">Ask the terminal for its current list of enrolled IDs.</p>
        <button
          type="button"
          onClick={() => void queueCommand({ command_type: "refresh_enrolled_ids" })}
          className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm font-semibold text-slate-700 hover:bg-slate-50"
        >
          Check Who&apos;s Enrolled
        </button>
      </div>

      {(active || error) && (
        <div className="rounded-xl border border-slate-200 bg-white p-4 md:col-span-3">
          {error ? <p className="text-sm text-red-700">{error}</p> : <p className="text-sm text-slate-700">{statusMessage()}</p>}
        </div>
      )}
    </div>
  );
}

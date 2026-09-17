"use client";

import { Fragment, FormEvent, useEffect, useState } from "react";
import { useRouter } from "next/navigation";

import Sidebar from "@/components/Sidebar";
import { DeviceCommandPanel } from "@/components/devices/DeviceCommandPanel";
import type { ShiftEmployee } from "@/components/shifts/types";
import { AppCard, PageHeader, Section, StatusBadge } from "@/components/ui";
import { apiFetch, getAccessToken } from "@/lib/api";

type BiometricDevice = {
  id: number;
  name: string;
  serial_number: string;
  model: string;
  location: string;
  device_type: "factory" | "hostel" | "office";
  purpose: "attendance" | "meal_ticket";
  ip_address: string | null;
  is_online: boolean;
  last_sync_at: string | null;
};

const DEVICE_TYPES: { value: BiometricDevice["device_type"]; label: string }[] = [
  { value: "factory", label: "Factory" },
  { value: "hostel", label: "Hostel" },
  { value: "office", label: "Office" },
];

const PURPOSES: { value: BiometricDevice["purpose"]; label: string }[] = [
  { value: "attendance", label: "Attendance" },
  { value: "meal_ticket", label: "Meal Ticket" },
];

type PersonInformationResult = {
  total_rows: number;
  linked: number;
  already_linked: number;
  conflicts: { user_id: string; employee_id: string; employee_name: string; already_linked_to_user_id: string }[];
  unmatched: { user_id: string; name: string; department: string }[];
  linked_employees: { user_id: string; employee_id: string; employee_name: string }[];
};

function formatLastSeen(value: string | null) {
  if (!value) return "Never";
  return new Date(value).toLocaleString();
}

export default function BiometricDevicesPage() {
  const router = useRouter();
  const [devices, setDevices] = useState<BiometricDevice[]>([]);
  const [employees, setEmployees] = useState<ShiftEmployee[]>([]);
  const [expandedDeviceId, setExpandedDeviceId] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [formError, setFormError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [name, setName] = useState("");
  const [serialNumber, setSerialNumber] = useState("");
  const [model, setModel] = useState("");
  const [location, setLocation] = useState("");
  const [deviceType, setDeviceType] = useState<BiometricDevice["device_type"]>("factory");
  const [purpose, setPurpose] = useState<BiometricDevice["purpose"]>("attendance");
  const [importFile, setImportFile] = useState<File | null>(null);
  const [importing, setImporting] = useState(false);
  const [importError, setImportError] = useState("");
  const [importResult, setImportResult] = useState<PersonInformationResult | null>(null);

  async function loadDevices() {
    setLoading(true);
    setError("");
    try {
      const [deviceResponse, employeeResponse] = await Promise.all([
        apiFetch("/attendance/devices/"),
        apiFetch("/employees/"),
      ]);
      if (!deviceResponse.ok) throw new Error("Unable to load biometric devices.");
      const deviceData: { results?: BiometricDevice[] } = await deviceResponse.json();
      setDevices(deviceData.results || []);
      if (employeeResponse.ok) {
        const employeeData: { results?: ShiftEmployee[] } = await employeeResponse.json();
        setEmployees(employeeData.results || []);
      }
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "Unable to load biometric devices.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    if (!getAccessToken()) {
      router.push("/login");
      return;
    }
    const timer = window.setTimeout(() => void loadDevices(), 0);
    return () => window.clearTimeout(timer);
  }, [router]);

  async function registerDevice(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setFormError("");
    setSubmitting(true);
    try {
      const response = await apiFetch("/attendance/devices/", {
        method: "POST",
        body: JSON.stringify({ name, serial_number: serialNumber, model, location, device_type: deviceType, purpose }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(
          response.status === 403
            ? "You don't have permission to register devices."
            : data.serial_number?.[0] || data.detail || "Unable to register the device."
        );
      }
      setName("");
      setSerialNumber("");
      setModel("");
      setLocation("");
      setDeviceType("factory");
      setPurpose("attendance");
      await loadDevices();
    } catch (submitError) {
      setFormError(submitError instanceof Error ? submitError.message : "Unable to register the device.");
    } finally {
      setSubmitting(false);
    }
  }

  async function importPersonInformation(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!importFile) return;
    setImportError("");
    setImportResult(null);
    setImporting(true);
    try {
      const formData = new FormData();
      formData.append("file", importFile);
      const response = await apiFetch("/attendance/devices/import-person-information/", { method: "POST", body: formData });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.detail || "Unable to import this file.");
      setImportResult(data as PersonInformationResult);
      setImportFile(null);
      await loadDevices();
    } catch (importErr) {
      setImportError(importErr instanceof Error ? importErr.message : "Unable to import this file.");
    } finally {
      setImporting(false);
    }
  }

  async function removeDevice(device: BiometricDevice) {
    if (!window.confirm(`Remove "${device.name}" (${device.serial_number})? This does not affect punches already recorded.`)) return;
    try {
      const response = await apiFetch(`/attendance/devices/${device.id}/`, { method: "DELETE" });
      if (!response.ok && response.status !== 204) {
        throw new Error(response.status === 403 ? "You don't have permission to remove devices." : "Unable to remove the device.");
      }
      await loadDevices();
    } catch (removeError) {
      setError(removeError instanceof Error ? removeError.message : "Unable to remove the device.");
    }
  }

  return (
    <div className="min-h-screen bg-slate-100">
      <Sidebar />
      <main className="ml-64 min-w-0 p-4 md:p-8">
        <PageHeader
          title="Biometric Devices"
          description="Terminals that push attendance or meal-ticket scans into this system. Manage which terminal does what, and see which are online right now."
          actions={<button type="button" onClick={() => void loadDevices()} disabled={loading} className="rounded-xl border border-slate-300 bg-white px-4 py-2.5 text-sm font-semibold text-slate-700 hover:bg-slate-50 disabled:opacity-50">Refresh</button>}
        />

        <Section className="mb-6" title="Register a Device" subtitle="Add a terminal's serial number here after pointing it at this server.">
          <form onSubmit={registerDevice} className="grid gap-4 p-5 md:grid-cols-3">
            <label className="text-sm font-semibold">Name<input required value={name} onChange={(event) => setName(event.target.value)} placeholder="e.g. Main Entrance" className="mt-2 w-full rounded-xl border border-slate-300 px-3 py-2.5 font-normal" /></label>
            <label className="text-sm font-semibold">Serial Number<input required value={serialNumber} onChange={(event) => setSerialNumber(event.target.value)} placeholder="From the terminal's system info screen" className="mt-2 w-full rounded-xl border border-slate-300 px-3 py-2.5 font-normal" /></label>
            <label className="text-sm font-semibold">Model<input value={model} onChange={(event) => setModel(event.target.value)} placeholder="Optional" className="mt-2 w-full rounded-xl border border-slate-300 px-3 py-2.5 font-normal" /></label>
            <label className="text-sm font-semibold">Location<input required value={location} onChange={(event) => setLocation(event.target.value)} placeholder="e.g. Factory gate" className="mt-2 w-full rounded-xl border border-slate-300 px-3 py-2.5 font-normal" /></label>
            <label className="text-sm font-semibold">Site Type<select value={deviceType} onChange={(event) => setDeviceType(event.target.value as BiometricDevice["device_type"])} className="mt-2 w-full rounded-xl border border-slate-300 bg-white px-3 py-2.5 font-normal">{DEVICE_TYPES.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}</select></label>
            <label className="text-sm font-semibold">Purpose<select value={purpose} onChange={(event) => setPurpose(event.target.value as BiometricDevice["purpose"])} className="mt-2 w-full rounded-xl border border-slate-300 bg-white px-3 py-2.5 font-normal">{PURPOSES.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}</select></label>
            <button disabled={submitting} className="rounded-xl bg-blue-600 px-4 py-2.5 text-sm font-semibold text-white hover:bg-blue-700 disabled:opacity-50 md:col-span-3">{submitting ? "Registering..." : "Register Device"}</button>
          </form>
          {formError && <p className="border-t border-slate-200 px-5 py-4 text-sm text-red-700">{formError}</p>}
        </Section>

        <Section className="mb-6" title="Import Person Information" subtitle="Bulk-link staff already enrolled on a terminal, from the vendor's Person Information export (.xls or .xlsx) - no re-scanning needed.">
          <form onSubmit={importPersonInformation} className="grid gap-4 p-5 md:grid-cols-3">
            <label className="text-sm font-semibold md:col-span-2">
              Person Information File
              <input
                required
                type="file"
                accept=".xls,.xlsx"
                onChange={(event) => setImportFile(event.target.files?.[0] ?? null)}
                className="mt-2 w-full rounded-xl border border-slate-300 bg-white px-3 py-2.5 font-normal"
              />
            </label>
            <button disabled={importing || !importFile} className="self-end rounded-xl bg-blue-600 px-4 py-2.5 text-sm font-semibold text-white hover:bg-blue-700 disabled:opacity-50">{importing ? "Importing..." : "Import"}</button>
          </form>
          {importError && <p className="border-t border-slate-200 px-5 py-4 text-sm text-red-700">{importError}</p>}
          {importResult && (
            <div className="border-t border-slate-200 p-5">
              <div className="grid gap-3 sm:grid-cols-4">
                <AppCard className="bg-slate-50 p-3"><p className="text-xs font-semibold uppercase text-slate-500">Rows Read</p><p className="mt-1 font-bold text-slate-900">{importResult.total_rows}</p></AppCard>
                <AppCard className="bg-emerald-50 p-3"><p className="text-xs font-semibold uppercase text-emerald-700">Linked</p><p className="mt-1 font-bold text-emerald-900">{importResult.linked}</p></AppCard>
                <AppCard className="bg-slate-50 p-3"><p className="text-xs font-semibold uppercase text-slate-500">Already Linked</p><p className="mt-1 font-bold text-slate-900">{importResult.already_linked}</p></AppCard>
                <AppCard className="bg-amber-50 p-3"><p className="text-xs font-semibold uppercase text-amber-700">Needs Review</p><p className="mt-1 font-bold text-amber-900">{importResult.unmatched.length + importResult.conflicts.length}</p></AppCard>
              </div>
              {importResult.unmatched.length > 0 && (
                <div className="mt-5">
                  <h3 className="font-bold text-slate-900">Unmatched Rows ({importResult.unmatched.length})</h3>
                  <p className="mt-1 text-xs text-slate-500">No employee matched by staff number or exact name - check spelling, or these may not be entered as employees yet.</p>
                  <div className="mt-3 max-h-64 overflow-y-auto rounded-xl border border-slate-200">
                    <table className="w-full text-left text-sm"><thead className="bg-slate-50 text-xs font-semibold uppercase text-slate-500"><tr><th className="px-4 py-2">Device User ID</th><th className="px-4 py-2">Name</th><th className="px-4 py-2">Department</th></tr></thead><tbody className="divide-y divide-slate-100">{importResult.unmatched.map((row) => <tr key={row.user_id}><td className="px-4 py-2">{row.user_id}</td><td className="px-4 py-2">{row.name}</td><td className="px-4 py-2 text-slate-600">{row.department}</td></tr>)}</tbody></table>
                  </div>
                </div>
              )}
              {importResult.conflicts.length > 0 && (
                <div className="mt-5">
                  <h3 className="font-bold text-slate-900">Conflicts ({importResult.conflicts.length})</h3>
                  <p className="mt-1 text-xs text-slate-500">This employee already has a different device user ID linked - review manually before relinking.</p>
                  <div className="mt-3 max-h-64 overflow-y-auto rounded-xl border border-slate-200">
                    <table className="w-full text-left text-sm"><thead className="bg-slate-50 text-xs font-semibold uppercase text-slate-500"><tr><th className="px-4 py-2">Employee</th><th className="px-4 py-2">File User ID</th><th className="px-4 py-2">Already Linked To</th></tr></thead><tbody className="divide-y divide-slate-100">{importResult.conflicts.map((row) => <tr key={row.employee_id}><td className="px-4 py-2">{row.employee_name} ({row.employee_id})</td><td className="px-4 py-2">{row.user_id}</td><td className="px-4 py-2 text-slate-600">{row.already_linked_to_user_id}</td></tr>)}</tbody></table>
                  </div>
                </div>
              )}
            </div>
          )}
        </Section>

        {error ? (
          <AppCard><div className="p-8 text-center text-red-700"><p>{error}</p><button type="button" onClick={() => void loadDevices()} className="mt-4 rounded-xl bg-red-600 px-4 py-2.5 text-sm font-semibold text-white">Try Again</button></div></AppCard>
        ) : (
          <Section title="Devices" subtitle={`${devices.length} device${devices.length === 1 ? "" : "s"}`}>
            {loading ? (
              <div className="space-y-3 p-6">{Array.from({ length: 3 }).map((_, index) => <div key={index} className="h-16 animate-pulse rounded-xl bg-slate-100" />)}</div>
            ) : devices.length ? (
              <div className="overflow-x-auto">
                <table className="w-full min-w-[920px] text-left">
                  <thead className="bg-slate-50 text-xs font-semibold uppercase tracking-wide text-slate-500"><tr><th className="px-5 py-3">Name</th><th className="px-5 py-3">Serial Number</th><th className="px-5 py-3">Purpose</th><th className="px-5 py-3">Location</th><th className="px-5 py-3">Status</th><th className="px-5 py-3">Last Seen</th><th className="px-5 py-3" /></tr></thead>
                  <tbody className="divide-y divide-slate-100">
                    {devices.map((device) => (
                      <Fragment key={device.id}>
                        <tr className="hover:bg-slate-50">
                          <td className="px-5 py-4 font-semibold text-slate-900">{device.name}</td>
                          <td className="px-5 py-4 font-mono text-sm text-slate-700">{device.serial_number}</td>
                          <td className="px-5 py-4"><StatusBadge status={device.purpose} /></td>
                          <td className="px-5 py-4 text-sm text-slate-600">{device.location}</td>
                          <td className="px-5 py-4"><StatusBadge status={device.is_online ? "active" : "inactive"} /> <span className="ml-1 text-sm text-slate-600">{device.is_online ? "Online" : "Offline"}</span></td>
                          <td className="px-5 py-4 text-sm text-slate-600">{formatLastSeen(device.last_sync_at)}</td>
                          <td className="px-5 py-4 text-right whitespace-nowrap">
                            <button type="button" onClick={() => setExpandedDeviceId(expandedDeviceId === device.id ? null : device.id)} className="mr-4 text-sm font-semibold text-blue-700 hover:underline">
                              {expandedDeviceId === device.id ? "Hide" : "Manage Staff"}
                            </button>
                            <button type="button" onClick={() => void removeDevice(device)} className="text-sm font-semibold text-red-600 hover:underline">Remove</button>
                          </td>
                        </tr>
                        {expandedDeviceId === device.id && (
                          <tr>
                            <td colSpan={7} className="p-0">
                              <DeviceCommandPanel deviceId={device.id} employees={employees} />
                            </td>
                          </tr>
                        )}
                      </Fragment>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <p className="p-12 text-center text-slate-500">No devices registered yet.</p>
            )}
          </Section>
        )}
      </main>
    </div>
  );
}

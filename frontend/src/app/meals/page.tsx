"use client";

import type { FormEvent } from "react";
import { useEffect, useEffectEvent, useState } from "react";
import { useRouter } from "next/navigation";

import Sidebar from "@/components/Sidebar";
import { MetricCard, PageHeader, Section, StatusBadge } from "@/components/ui";
import { apiFetch, getAccessToken, getCurrentUser, type CurrentUser } from "@/lib/api";

type Collection = { id: number; employee_name: string; work_date: string; timestamp: string; device: string; entitlement: number; sequence: number; rate: string; status: string };
type Exception = { id: number; employee_name: string; work_date: string; entitlement: number; collected_quantity: number; excess_quantity: number; proposed_deduction: string; status: string };
type Period = { id: number; year: number; month: number; status: string };
type Device = { id: number; name: string; serial_number: string; active: boolean };
type Rate = { id: number; amount: string; effective_from: string; effective_to: string | null; active: boolean };
type Entitlement = { id: number; employee: number; employee_name: string; tickets_per_work_day: number; effective_from: string; effective_to: string | null; reason: string; is_exceptional_override: boolean };
type Employee = { id: number; employee_id: string; full_name: string };

const inputClass = "w-full rounded-xl border border-slate-300 bg-white px-3 py-2.5 text-sm outline-none focus:ring-2 focus:ring-blue-500";
const money = (value: string) => `₦${Number(value || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
const day = (value: string) => new Intl.DateTimeFormat("en-GB", { day: "2-digit", month: "short", year: "numeric" }).format(new Date(`${value}T00:00:00`));
const dateTime = (value: string) => new Date(value).toLocaleString("en-NG", { dateStyle: "medium", timeStyle: "short" });
const deviceInitial = () => ({ name: "", serial_number: "" });
const rateInitial = () => ({ amount: "", effective_from: "", effective_to: "" });
const entitlementInitial = () => ({ employee: "", tickets_per_work_day: "", effective_from: "", effective_to: "", reason: "", is_exceptional_override: false });

function apiMessage(data: unknown, fallback: string) {
  if (!data || typeof data !== "object") return fallback;
  const payload = data as Record<string, unknown>;
  if (typeof payload.detail === "string") return payload.detail;
  const messages = Object.entries(payload).flatMap(([field, value]) => {
    const message = Array.isArray(value) ? value.join(" ") : typeof value === "string" ? value : "";
    return message ? [`${field.replace(/_/g, " ")}: ${message}`] : [];
  });
  return messages.join(" ") || fallback;
}

export default function MealsPage() {
  const router = useRouter();
  const [currentUser, setCurrentUser] = useState<CurrentUser | null>(null);
  const [collections, setCollections] = useState<Collection[]>([]);
  const [exceptions, setExceptions] = useState<Exception[]>([]);
  const [periods, setPeriods] = useState<Period[]>([]);
  const [devices, setDevices] = useState<Device[]>([]);
  const [rates, setRates] = useState<Rate[]>([]);
  const [entitlements, setEntitlements] = useState<Entitlement[]>([]);
  const [employees, setEmployees] = useState<Employee[]>([]);
  const [loading, setLoading] = useState(true);
  const [acting, setActing] = useState(false);
  const [error, setError] = useState("");
  const [feedback, setFeedback] = useState("");
  const [cancelId, setCancelId] = useState<number | null>(null);
  const [reason, setReason] = useState("");
  const [deviceForm, setDeviceForm] = useState(deviceInitial);
  const [rateForm, setRateForm] = useState(rateInitial);
  const [entitlementForm, setEntitlementForm] = useState(entitlementInitial);
  const [editing, setEditing] = useState<number | null>(null);
  const [deviceEdit, setDeviceEdit] = useState(deviceInitial);
  const canAccess = currentUser !== null && Object.values(currentUser.permissions).some(Boolean);
  const canReview = currentUser?.permissions.review_meal_excess === true;
  const canConfigure = currentUser?.permissions.manage_meal_configuration === true;

  async function load() {
    setLoading(true); setError("");
    try {
      const user = await getCurrentUser();
      setCurrentUser(user);
      if (!Object.values(user.permissions).some(Boolean)) return;
      const [operationsResponse, periodsResponse, devicesResponse, ratesResponse, entitlementsResponse] = await Promise.all([
        apiFetch("/meals/operations/"), apiFetch("/payroll/periods/"), apiFetch("/meals/devices/"), apiFetch("/meals/rates/"), apiFetch("/meals/entitlements/"),
      ]);
      const required = [[operationsResponse, "Unable to load Meals operations."], [devicesResponse, "Unable to load Meal devices."], [ratesResponse, "Unable to load meal ticket rates."], [entitlementsResponse, "Unable to load meal entitlements."]] as const;
      for (const [response, fallback] of required) if (!response.ok) throw new Error(apiMessage(await response.json().catch(() => null), fallback));
      const [operations, deviceData, rateData, entitlementData] = await Promise.all([operationsResponse.json(), devicesResponse.json(), ratesResponse.json(), entitlementsResponse.json()]);
      setCollections(operations.collections || []); setExceptions(operations.exceptions || []); setDevices(deviceData.results || []); setRates(rateData.results || []); setEntitlements(entitlementData.results || []);
      setPeriods(periodsResponse.ok ? ((await periodsResponse.json()).results || []) : []);
      if (user.permissions.manage_meal_configuration) {
        const employeeResponse = await apiFetch("/employees/");
        if (!employeeResponse.ok) throw new Error(apiMessage(await employeeResponse.json().catch(() => null), "Unable to load employees for meal entitlements."));
        setEmployees((await employeeResponse.json()).results || []);
      } else setEmployees([]);
    } catch (loadError) {
      const message = loadError instanceof Error ? loadError.message : "Unable to load Meals.";
      if (message === "Authentication required." || message === "Your session has expired.") { router.push("/login"); return; }
      setError(message);
    } finally { setLoading(false); }
  }

  const loadOnMount = useEffectEvent(() => { void load(); });
  useEffect(() => {
    if (!getAccessToken()) { router.push("/login"); return; }
    const timer = window.setTimeout(loadOnMount, 0);
    return () => window.clearTimeout(timer);
  }, [router]);

  async function request(path: string, method: "POST" | "PATCH", body: unknown, success: string) {
    setActing(true); setError("");
    try {
      const response = await apiFetch(path, { method, body: JSON.stringify(body) });
      const data = await response.json().catch(() => null);
      if (!response.ok) throw new Error(apiMessage(data, "The Meals request could not be completed."));
      setFeedback(success); await load(); return true;
    } catch (actionError) { setError(actionError instanceof Error ? actionError.message : "The Meals request could not be completed."); return false; }
    finally { setActing(false); }
  }

  function periodFor(workDate: string) {
    const value = new Date(`${workDate}T00:00:00`);
    return periods.find((period) => period.year === value.getFullYear() && period.month === value.getMonth() + 1 && !["approved", "paid", "closed"].includes(period.status));
  }
  async function approve(item: Exception) {
    const period = periodFor(item.work_date);
    if (!period) { setError("No editable payroll period is available for this work date."); return; }
    await request(`/meals/excess/${item.id}/approve/`, "POST", { payroll_period: period.id }, "Meal excess approved and sent to payroll.");
  }
  async function cancel() {
    if (cancelId === null || !reason.trim()) return;
    if (await request(`/meals/excess/${cancelId}/cancel/`, "POST", { reason: reason.trim() }, "Meal excess cancelled and recorded in the audit trail.")) { setCancelId(null); setReason(""); }
  }
  async function createDevice(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (await request("/meals/devices/", "POST", { ...deviceForm, active: true }, "Meal device created.")) setDeviceForm(deviceInitial());
  }
  async function createRate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (await request("/meals/rates/", "POST", { ...rateForm, amount: Number(rateForm.amount), effective_to: rateForm.effective_to || null }, "Meal ticket rate created.")) setRateForm(rateInitial());
  }
  async function createEntitlement(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const body = { ...entitlementForm, employee: Number(entitlementForm.employee), tickets_per_work_day: Number(entitlementForm.tickets_per_work_day), effective_to: entitlementForm.effective_to || null };
    if (await request("/meals/entitlements/", "POST", body, "Employee meal entitlement created.")) setEntitlementForm(entitlementInitial());
  }
  async function saveDevice(device: Device) {
    if (await request(`/meals/devices/${device.id}/`, "PATCH", deviceEdit, "Meal device updated.")) setEditing(null);
  }
  const within = collections.filter((item) => item.status === "within_entitlement").length;
  const excess = collections.filter((item) => item.status === "excess").length;

  return <div className="min-h-screen bg-slate-100"><Sidebar /><main className="ml-64 min-w-0 p-4 md:p-8">
    <PageHeader title="Meals" description="Monitor collections, review excesses, and maintain approved Meal configuration." actions={<button type="button" onClick={() => void load()} disabled={loading} className="rounded-xl border border-slate-300 bg-white px-4 py-2.5 text-sm font-semibold text-slate-700 disabled:opacity-50">Refresh</button>} />
    {feedback && <p className="mb-6 rounded-xl bg-emerald-50 p-4 text-sm text-emerald-800">{feedback}</p>}{error && <p className="mb-6 rounded-xl bg-red-50 p-4 text-sm text-red-700">{error}</p>}
    {loading ? <div className="h-64 animate-pulse rounded-2xl bg-slate-200" /> : !canAccess ? <Section title="Meals Access" subtitle="Your account does not have a Meals capability."><p className="p-8 text-sm text-slate-600">Ask an administrator to grant a Meals permission if you need access to operations or configuration.</p></Section> : <>
      <div className="mb-6 grid gap-4 sm:grid-cols-2 xl:grid-cols-4"><MetricCard title="Collections" value={collections.length} subtitle="Recent meal scans" accentColor="#2563eb" /><MetricCard title="Within Entitlement" value={within} subtitle="Accepted collections" accentColor="#059669" /><MetricCard title="Excess" value={excess} subtitle="Over final entitlement" accentColor="#dc2626" /><MetricCard title="Pending Review" value={exceptions.length} subtitle="Awaiting decision" accentColor="#d97706" /></div>
      <Section title="Operations" subtitle="Historical entitlement and rate snapshots are preserved.">{collections.length ? <div className="overflow-x-auto"><table className="w-full min-w-[1050px] text-left"><thead className="bg-slate-50 text-xs font-semibold uppercase tracking-wide text-slate-500"><tr><th className="px-5 py-3">Employee</th><th className="px-5 py-3">Work Date</th><th className="px-5 py-3">Scan Time</th><th className="px-5 py-3">Device</th><th className="px-5 py-3">Sequence</th><th className="px-5 py-3">Entitlement</th><th className="px-5 py-3">Rate</th><th className="px-5 py-3">Status</th></tr></thead><tbody className="divide-y divide-slate-100">{collections.map((item) => <tr key={item.id}><td className="px-5 py-4 font-medium">{item.employee_name}</td><td className="px-5 py-4 text-sm text-slate-600">{day(item.work_date)}</td><td className="px-5 py-4 text-sm text-slate-600">{dateTime(item.timestamp)}</td><td className="px-5 py-4 text-sm text-slate-600">{item.device}</td><td className="px-5 py-4 text-sm text-slate-600">{item.sequence}</td><td className="px-5 py-4 text-sm text-slate-600">{item.entitlement}</td><td className="px-5 py-4 text-sm text-slate-600">{money(item.rate)}</td><td className="px-5 py-4"><StatusBadge status={item.status} /></td></tr>)}</tbody></table></div> : <Empty message="No meal collections found." />}</Section>
      <Section className="mt-6" title="Pending Excess" subtitle="Approve a full historical-rate deduction or cancel with a mandatory reason.">{exceptions.length ? <div className="overflow-x-auto"><table className="w-full min-w-[900px] text-left"><thead className="bg-slate-50 text-xs font-semibold uppercase tracking-wide text-slate-500"><tr><th className="px-5 py-3">Employee</th><th className="px-5 py-3">Work Date</th><th className="px-5 py-3">Entitlement</th><th className="px-5 py-3">Collected</th><th className="px-5 py-3">Excess</th><th className="px-5 py-3">Deduction</th><th className="px-5 py-3">Status</th>{canReview && <th className="px-5 py-3">Actions</th>}</tr></thead><tbody className="divide-y divide-slate-100">{exceptions.map((item) => <tr key={item.id}><td className="px-5 py-4 font-medium">{item.employee_name}</td><td className="px-5 py-4 text-sm text-slate-600">{day(item.work_date)}</td><td className="px-5 py-4">{item.entitlement}</td><td className="px-5 py-4">{item.collected_quantity}</td><td className="px-5 py-4">{item.excess_quantity}</td><td className="px-5 py-4 font-semibold">{money(item.proposed_deduction)}</td><td className="px-5 py-4"><StatusBadge status={item.status} /></td>{canReview && <td className="px-5 py-4"><div className="flex gap-2"><button type="button" disabled={acting} onClick={() => void approve(item)} className="rounded-lg bg-emerald-600 px-3 py-2 text-xs font-semibold text-white disabled:opacity-50">Approve</button><button type="button" disabled={acting} onClick={() => setCancelId(item.id)} className="rounded-lg border border-red-200 px-3 py-2 text-xs font-semibold text-red-700">Cancel</button></div></td>}</tr>)}</tbody></table></div> : <Empty message="No pending meal excess exceptions." />}</Section>
      <Section className="mt-6" title="Devices" subtitle="Registered Meal collection devices.">{canConfigure && <form onSubmit={createDevice} className="grid gap-3 border-b border-slate-200 p-5 md:grid-cols-[1fr_1fr_auto]"><input required value={deviceForm.name} onChange={(event) => setDeviceForm({ ...deviceForm, name: event.target.value })} placeholder="Device name" className={inputClass} /><input required value={deviceForm.serial_number} onChange={(event) => setDeviceForm({ ...deviceForm, serial_number: event.target.value })} placeholder="Serial number" className={inputClass} /><button disabled={acting} className="rounded-xl bg-blue-600 px-4 py-2.5 text-sm font-semibold text-white">Add Device</button></form>}<DeviceTable devices={devices} editable={canConfigure} editing={editing} edit={deviceEdit} acting={acting} onEdit={(device) => { setEditing(device.id); setDeviceEdit({ name: device.name, serial_number: device.serial_number }); }} onChange={setDeviceEdit} onSave={saveDevice} onCancel={() => setEditing(null)} onToggle={(device) => void request(`/meals/devices/${device.id}/`, "PATCH", { active: !device.active }, "Meal device updated.")} /></Section>
      <Section className="mt-6" title="Rates" subtitle="Effective-dated Meal ticket rate history.">{canConfigure && <form onSubmit={createRate} className="grid gap-3 border-b border-slate-200 p-5 md:grid-cols-4"><input required type="number" min="0.01" step="0.01" value={rateForm.amount} onChange={(event) => setRateForm({ ...rateForm, amount: event.target.value })} placeholder="Amount (NGN)" className={inputClass} /><input required type="date" value={rateForm.effective_from} onChange={(event) => setRateForm({ ...rateForm, effective_from: event.target.value })} className={inputClass} /><input type="date" value={rateForm.effective_to} onChange={(event) => setRateForm({ ...rateForm, effective_to: event.target.value })} className={inputClass} /><button disabled={acting} className="rounded-xl bg-blue-600 px-4 py-2.5 text-sm font-semibold text-white">Create Rate</button></form>}{rates.length ? <div className="overflow-x-auto"><table className="w-full min-w-[650px] text-left"><thead className="bg-slate-50 text-xs font-semibold uppercase text-slate-500"><tr><th className="px-5 py-3">Amount</th><th className="px-5 py-3">Effective From</th><th className="px-5 py-3">Effective To</th><th className="px-5 py-3">Status</th></tr></thead><tbody className="divide-y divide-slate-100">{rates.map((rate) => <tr key={rate.id}><td className="px-5 py-4 font-semibold">{money(rate.amount)}</td><td className="px-5 py-4 text-sm text-slate-600">{day(rate.effective_from)}</td><td className="px-5 py-4 text-sm text-slate-600">{rate.effective_to ? day(rate.effective_to) : "Open ended"}</td><td className="px-5 py-4"><StatusBadge status={rate.active ? "active" : "inactive"} /></td></tr>)}</tbody></table></div> : <Empty message="No Meal ticket rates found." />}</Section>
      <Section className="mt-6" title="Entitlements" subtitle="Employee-specific approved entitlement history.">{canConfigure && <form onSubmit={createEntitlement} className="grid gap-3 border-b border-slate-200 p-5 md:grid-cols-2"><select required value={entitlementForm.employee} onChange={(event) => setEntitlementForm({ ...entitlementForm, employee: event.target.value })} className={inputClass}><option value="">Select employee</option>{employees.map((employee) => <option key={employee.id} value={employee.id}>{employee.employee_id} - {employee.full_name}</option>)}</select><input required type="number" min="1" step="1" value={entitlementForm.tickets_per_work_day} onChange={(event) => setEntitlementForm({ ...entitlementForm, tickets_per_work_day: event.target.value })} placeholder="Tickets per WORK day" className={inputClass} /><input required type="date" value={entitlementForm.effective_from} onChange={(event) => setEntitlementForm({ ...entitlementForm, effective_from: event.target.value })} className={inputClass} /><input type="date" value={entitlementForm.effective_to} onChange={(event) => setEntitlementForm({ ...entitlementForm, effective_to: event.target.value })} className={inputClass} /><textarea required value={entitlementForm.reason} onChange={(event) => setEntitlementForm({ ...entitlementForm, reason: event.target.value })} placeholder="Approval reason" className={`${inputClass} min-h-24 md:col-span-2`} />{currentUser?.is_superuser && <label className="flex items-center gap-3 text-sm font-semibold text-slate-700 md:col-span-2"><input type="checkbox" checked={entitlementForm.is_exceptional_override} onChange={(event) => setEntitlementForm({ ...entitlementForm, is_exceptional_override: event.target.checked })} />Exceptional Override</label>}<div className="text-right md:col-span-2"><button disabled={acting} className="rounded-xl bg-blue-600 px-4 py-2.5 text-sm font-semibold text-white">Create Entitlement</button></div></form>}{entitlements.length ? <div className="overflow-x-auto"><table className="w-full min-w-[900px] text-left"><thead className="bg-slate-50 text-xs font-semibold uppercase text-slate-500"><tr><th className="px-5 py-3">Employee</th><th className="px-5 py-3">Tickets</th><th className="px-5 py-3">Effective From</th><th className="px-5 py-3">Effective To</th><th className="px-5 py-3">Override</th><th className="px-5 py-3">Reason</th></tr></thead><tbody className="divide-y divide-slate-100">{entitlements.map((item) => <tr key={item.id}><td className="px-5 py-4 font-medium">{item.employee_name}</td><td className="px-5 py-4">{item.tickets_per_work_day}</td><td className="px-5 py-4 text-sm text-slate-600">{day(item.effective_from)}</td><td className="px-5 py-4 text-sm text-slate-600">{item.effective_to ? day(item.effective_to) : "Open ended"}</td><td className="px-5 py-4"><StatusBadge status={item.is_exceptional_override ? "approved" : "inactive"} /></td><td className="px-5 py-4 text-sm text-slate-600">{item.reason}</td></tr>)}</tbody></table></div> : <Empty message="No employee-specific Meal entitlements found." />}</Section>
    </>}
  </main>{canReview && cancelId !== null && <div className="fixed inset-0 z-20 flex items-center justify-center bg-slate-950/40 p-4"><div className="w-full max-w-lg rounded-2xl bg-white p-6 shadow-xl"><h2 className="text-xl font-bold">Cancel Meal Excess</h2><p className="mt-2 text-sm text-slate-500">The reason is required and retained for review.</p><textarea autoFocus value={reason} onChange={(event) => setReason(event.target.value)} className="mt-5 min-h-32 w-full rounded-xl border border-slate-300 p-3 text-sm" placeholder="Enter cancellation reason" /><div className="mt-5 flex justify-end gap-3"><button type="button" onClick={() => { setCancelId(null); setReason(""); }} className="rounded-xl border border-slate-300 px-4 py-2.5 text-sm font-semibold">Back</button><button type="button" disabled={acting || !reason.trim()} onClick={() => void cancel()} className="rounded-xl bg-red-600 px-4 py-2.5 text-sm font-semibold text-white">Cancel Excess</button></div></div></div>}</div>;
}

function Empty({ message }: { message: string }) {
  return <p className="p-10 text-center text-slate-500">{message}</p>;
}

function DeviceTable({ devices, editable, editing, edit, acting, onEdit, onChange, onSave, onCancel, onToggle }: { devices: Device[]; editable: boolean; editing: number | null; edit: { name: string; serial_number: string }; acting: boolean; onEdit: (device: Device) => void; onChange: (value: { name: string; serial_number: string }) => void; onSave: (device: Device) => void; onCancel: () => void; onToggle: (device: Device) => void }) {
  if (!devices.length) return <Empty message="No Meal devices configured." />;
  return <div className="overflow-x-auto"><table className="w-full min-w-[700px] text-left"><thead className="bg-slate-50 text-xs font-semibold uppercase text-slate-500"><tr><th className="px-5 py-3">Name</th><th className="px-5 py-3">Serial Number</th><th className="px-5 py-3">Status</th>{editable && <th className="px-5 py-3">Actions</th>}</tr></thead><tbody className="divide-y divide-slate-100">{devices.map((device) => editing === device.id ? <tr key={device.id}><td className="px-5 py-3"><input required value={edit.name} onChange={(event) => onChange({ ...edit, name: event.target.value })} className={inputClass} /></td><td className="px-5 py-3"><input required value={edit.serial_number} onChange={(event) => onChange({ ...edit, serial_number: event.target.value })} className={inputClass} /></td><td className="px-5 py-4"><StatusBadge status={device.active ? "active" : "inactive"} /></td><td className="px-5 py-4"><div className="flex gap-2"><button type="button" disabled={acting} onClick={() => void onSave(device)} className="rounded-lg bg-blue-600 px-3 py-2 text-xs font-semibold text-white">Save</button><button type="button" onClick={onCancel} className="rounded-lg border border-slate-300 px-3 py-2 text-xs font-semibold">Cancel</button></div></td></tr> : <tr key={device.id}><td className="px-5 py-4 font-medium">{device.name}</td><td className="px-5 py-4 font-mono text-sm text-slate-600">{device.serial_number}</td><td className="px-5 py-4"><StatusBadge status={device.active ? "active" : "inactive"} /></td>{editable && <td className="px-5 py-4"><div className="flex gap-2"><button type="button" disabled={acting} onClick={() => onEdit(device)} className="rounded-lg border border-slate-300 px-3 py-2 text-xs font-semibold">Edit</button><button type="button" disabled={acting} onClick={() => onToggle(device)} className="rounded-lg border border-slate-300 px-3 py-2 text-xs font-semibold">{device.active ? "Deactivate" : "Activate"}</button></div></td>}</tr>)}</tbody></table></div>;
}

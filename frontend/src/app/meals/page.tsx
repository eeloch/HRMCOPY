"use client";

import type { FormEvent } from "react";
import { useEffect, useEffectEvent, useState } from "react";
import { useRouter } from "next/navigation";

import Sidebar from "@/components/Sidebar";
import { MetricCard, PageHeader, Section, StatusBadge } from "@/components/ui";
import { ExtraTicketAuthorizations } from "@/components/meals/ExtraTicketAuthorizations";
import { apiFetch, getAccessToken, getCurrentUser, type CurrentUser } from "@/lib/api";

type Collection = { id: number; employee_name: string; work_date: string; timestamp: string; device: string; entitlement: number; sequence: number; rate: string; status: string; voided: boolean; void_reason: string; excess_id: number | null; excess_status: string | null };
type OperationsSummary = { today_collections: number; today_within_entitlement: number; today_excess: number; pending_review: number };
type Exception = { id: number; employee_name: string; work_date: string; entitlement: number; collected_quantity: number; excess_quantity: number; proposed_deduction: string; status: string };
type Period = { id: number; year: number; month: number; status: string };
type Device = { id: number; name: string; serial_number: string; active: boolean };
type Rate = { id: number; amount: string; effective_from: string; effective_to: string | null; active: boolean };
type Entitlement = { id: number; employee: number; employee_name: string; tickets_per_work_day: number; effective_from: string; effective_to: string | null; reason: string; is_exceptional_override: boolean };
type Employee = { id: number; employee_id: string; full_name: string };
type Rule = { id: number; employment_type: string; employment_category: string; position: number | null; position_name: string; minimum_months_of_service: number | null; tickets_per_work_day: number; priority: number; description: string; active: boolean };
type PositionOption = { id: number; name: string; department_name: string };
type VendorPayment = { id: number; payroll_period: number; amount: string; payment_date: string; reference: string; notes: string; recorded_by_name: string; created_at: string };
type VendorPeriodData = { payroll_period: number; tickets_issued: number; amount_owed: string; total_paid: string; balance: string; payments: VendorPayment[] };

const inputClass = "w-full rounded-xl border border-slate-300 bg-white px-3 py-2.5 text-sm outline-none focus:ring-2 focus:ring-blue-500";
const money = (value: string) => `₦${Number(value || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
const day = (value: string) => new Intl.DateTimeFormat("en-GB", { day: "2-digit", month: "short", year: "numeric" }).format(new Date(`${value}T00:00:00`));
const dateTime = (value: string) => new Date(value).toLocaleString("en-NG", { dateStyle: "medium", timeStyle: "short" });
const rateInitial = () => ({ amount: "", effective_from: "", effective_to: "" });
const entitlementInitial = () => ({ employee: "", tickets_per_work_day: "", effective_from: "", effective_to: "", reason: "", is_exceptional_override: false });
const ruleInitial = () => ({ employment_type: "", employment_category: "", position: "", minimum_months_of_service: "", tickets_per_work_day: "", priority: "100", description: "" });
const vendorPaymentInitial = () => ({ amount: "", payment_date: "", reference: "", notes: "" });
const employmentTypeOptions = [["permanent", "Permanent"], ["contract", "Contract"], ["casual", "Casual"], ["intern", "Intern"], ["nysc", "NYSC"], ["expatriate", "Expatriate"]];
const employmentCategoryOptions = [["staff", "Staff"], ["management", "Management"], ["executive", "Executive"]];

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
  const [summary, setSummary] = useState<OperationsSummary | null>(null);
  const [exceptions, setExceptions] = useState<Exception[]>([]);
  const [periods, setPeriods] = useState<Period[]>([]);
  const [devices, setDevices] = useState<Device[]>([]);
  const [rates, setRates] = useState<Rate[]>([]);
  const [entitlements, setEntitlements] = useState<Entitlement[]>([]);
  const [employees, setEmployees] = useState<Employee[]>([]);
  const [rules, setRules] = useState<Rule[]>([]);
  const [positions, setPositions] = useState<PositionOption[]>([]);
  const [ruleForm, setRuleForm] = useState(ruleInitial);
  const [loading, setLoading] = useState(true);
  const [acting, setActing] = useState(false);
  const [error, setError] = useState("");
  const [feedback, setFeedback] = useState("");
  const [cancelId, setCancelId] = useState<number | null>(null);
  const [reason, setReason] = useState("");
  const [declineId, setDeclineId] = useState<number | null>(null);
  const [declineReason, setDeclineReason] = useState("");
  const [voidId, setVoidId] = useState<number | null>(null);
  const [voidReason, setVoidReason] = useState("");
  const [rateForm, setRateForm] = useState(rateInitial);
  const [entitlementForm, setEntitlementForm] = useState(entitlementInitial);
  const [vendorPeriodId, setVendorPeriodId] = useState("");
  const [vendorData, setVendorData] = useState<VendorPeriodData | null>(null);
  const [loadingVendor, setLoadingVendor] = useState(false);
  const [vendorPaymentForm, setVendorPaymentForm] = useState(vendorPaymentInitial);
  const canAccess = currentUser !== null && Object.values(currentUser.permissions).some(Boolean);
  const canReview = currentUser?.permissions.review_meal_excess === true;
  const canConfigure = currentUser?.permissions.manage_meal_configuration === true;

  async function load(silent = false) {
    if (!silent) setLoading(true);
    setError("");
    try {
      const user = await getCurrentUser();
      setCurrentUser(user);
      if (!Object.values(user.permissions).some(Boolean)) return;
      const [operationsResponse, periodsResponse, devicesResponse, ratesResponse, entitlementsResponse, rulesResponse] = await Promise.all([
        apiFetch("/meals/operations/"), apiFetch("/payroll/periods/"), apiFetch("/meals/devices/"), apiFetch("/meals/rates/"), apiFetch("/meals/entitlements/"), apiFetch("/meals/rules/"),
      ]);
      const required = [[operationsResponse, "Unable to load Meals operations."], [devicesResponse, "Unable to load Meal devices."], [ratesResponse, "Unable to load meal ticket rates."], [entitlementsResponse, "Unable to load meal entitlements."], [rulesResponse, "Unable to load meal entitlement rules."]] as const;
      for (const [response, fallback] of required) if (!response.ok) throw new Error(apiMessage(await response.json().catch(() => null), fallback));
      const [operations, deviceData, rateData, entitlementData, ruleData] = await Promise.all([operationsResponse.json(), devicesResponse.json(), ratesResponse.json(), entitlementsResponse.json(), rulesResponse.json()]);
      setCollections(operations.collections || []); setSummary(operations.summary || null); setExceptions(operations.exceptions || []); setDevices(deviceData.results || []); setRates(rateData.results || []); setEntitlements(entitlementData.results || []); setRules(ruleData.results || []);
      const periodResults: Period[] = periodsResponse.ok ? (await periodsResponse.json()).results || [] : [];
      setPeriods(periodResults);
      setVendorPeriodId((current) => current || (periodResults[0] ? String(periodResults[0].id) : ""));
      if (user.permissions.manage_meal_configuration) {
        const [employeeResponse, positionResponse] = await Promise.all([apiFetch("/employees/"), apiFetch("/employees/positions/")]);
        if (!employeeResponse.ok) throw new Error(apiMessage(await employeeResponse.json().catch(() => null), "Unable to load employees for meal entitlements."));
        setEmployees((await employeeResponse.json()).results || []);
        setPositions(positionResponse.ok ? await positionResponse.json() : []);
      } else { setEmployees([]); setPositions([]); }
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

  useEffect(() => { if (vendorPeriodId) void loadVendorData(vendorPeriodId); }, [vendorPeriodId]);

  async function request(path: string, method: "POST" | "PATCH", body: unknown, success: string) {
    setActing(true); setError("");
    try {
      const response = await apiFetch(path, { method, body: JSON.stringify(body) });
      const data = await response.json().catch(() => null);
      if (!response.ok) throw new Error(apiMessage(data, "The Meals request could not be completed."));
      setFeedback(success); await load(true); if (vendorPeriodId) await loadVendorData(vendorPeriodId, true); return true;
    } catch (actionError) { setError(actionError instanceof Error ? actionError.message : "The Meals request could not be completed."); return false; }
    finally { setActing(false); }
  }

  async function approve(item: { id: number; work_date: string }) {
    await request(`/meals/excess/${item.id}/approve/`, "POST", {}, "Excess accepted - it comes out of that month's pay.");
  }
  async function cancel() {
    if (cancelId === null) return;
    if (await request(`/meals/excess/${cancelId}/cancel/`, "POST", { reason: reason.trim() }, "Meal excess waived and recorded in the audit trail.")) { setCancelId(null); setReason(""); }
  }
  async function declineExcess() {
    if (declineId === null) return;
    if (await request(`/meals/excess/${declineId}/decline/`, "POST", { reason: declineReason.trim() }, "Excess declined - the vendor isn't billed for it and nothing is deducted.")) { setDeclineId(null); setDeclineReason(""); }
  }
  async function voidTicket() {
    if (voidId === null) return;
    if (await request(`/meals/collections/${voidId}/void/`, "POST", { reason: voidReason.trim() }, "Ticket voided - it no longer counts toward the vendor total.")) { setVoidId(null); setVoidReason(""); }
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
  async function createRule(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const body = {
      employment_type: ruleForm.employment_type,
      employment_category: ruleForm.employment_category,
      position: ruleForm.position ? Number(ruleForm.position) : null,
      minimum_months_of_service: ruleForm.minimum_months_of_service ? Number(ruleForm.minimum_months_of_service) : null,
      tickets_per_work_day: Number(ruleForm.tickets_per_work_day),
      priority: ruleForm.position ? 10 : ruleForm.employment_category || ruleForm.employment_type ? 50 : 100,
      description: ruleForm.description,
    };
    if (await request("/meals/rules/", "POST", body, "Meal entitlement rule created.")) setRuleForm(ruleInitial());
  }
  async function toggleRule(rule: Rule) {
    await request(`/meals/rules/${rule.id}/`, "PATCH", { active: !rule.active }, "Meal entitlement rule updated.");
  }
  async function loadVendorData(periodId: string, silent = false) {
    if (!periodId) { setVendorData(null); return; }
    if (!silent) setLoadingVendor(true);
    try {
      const response = await apiFetch(`/meals/vendor/${periodId}/`);
      if (!response.ok) throw new Error(apiMessage(await response.json().catch(() => null), "Unable to load vendor payment data."));
      setVendorData(await response.json());
    } catch (vendorError) {
      setError(vendorError instanceof Error ? vendorError.message : "Unable to load vendor payment data.");
    } finally { setLoadingVendor(false); }
  }
  async function createVendorPayment(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!vendorPeriodId) return;
    const body = { payroll_period: Number(vendorPeriodId), amount: Number(vendorPaymentForm.amount), payment_date: vendorPaymentForm.payment_date, reference: vendorPaymentForm.reference, notes: vendorPaymentForm.notes };
    if (await request("/meals/vendor/payments/", "POST", body, "Vendor payment recorded.")) { setVendorPaymentForm(vendorPaymentInitial()); await loadVendorData(vendorPeriodId); }
  }
  const excessOutcome: Record<string, string> = { approved: "Accepted - deducted when payroll is generated", deducted: "Accepted - deducted from pay", cancelled: "Waived - no deduction", declined: "Declined - not billed" };
  const voidButton = (item: Collection) => <button type="button" disabled={acting} onClick={() => setVoidId(item.id)} className="text-xs font-semibold text-slate-400 underline hover:text-slate-600">Void</button>;
  function ticketActions(item: Collection) {
    if (item.voided) return null;
    if (item.status === "within_entitlement") return voidButton(item);
    if (item.excess_id !== null && item.excess_status === "pending") {
      return <span className="text-xs font-semibold text-amber-700">Waiting in &ldquo;Needs a decision&rdquo; above</span>;
    }
    return <span className="text-xs text-slate-500">{item.excess_status ? excessOutcome[item.excess_status] || item.excess_status : ""}</span>;
  }

  return <div className="min-h-screen bg-slate-100"><Sidebar /><main className="ml-64 min-w-0 p-4 md:p-8">
    <PageHeader title="Meals" description="Monitor collections, review excesses, and maintain approved Meal configuration." actions={<div className="flex gap-2"><button type="button" onClick={() => router.push("/meals/live")} className="rounded-xl border border-slate-300 bg-white px-4 py-2.5 text-sm font-semibold text-slate-700">Live Screen</button><button type="button" onClick={() => void load()} disabled={loading} className="rounded-xl border border-slate-300 bg-white px-4 py-2.5 text-sm font-semibold text-slate-700 disabled:opacity-50">Refresh</button></div>} />
    {feedback && <p className="mb-6 rounded-xl bg-emerald-50 p-4 text-sm text-emerald-800">{feedback}</p>}{error && <p className="mb-6 rounded-xl bg-red-50 p-4 text-sm text-red-700">{error}</p>}
    {loading ? <div className="h-64 animate-pulse rounded-2xl bg-slate-200" /> : !canAccess ? <Section title="Meals Access" subtitle="Your account does not have a Meals capability."><p className="p-8 text-sm text-slate-600">Ask an administrator to grant a Meals permission if you need access to operations or configuration.</p></Section> : <>
      <div className="mb-6 grid gap-4 sm:grid-cols-2 xl:grid-cols-4"><MetricCard title="Collections Today" value={summary?.today_collections ?? 0} subtitle="Meal scans today (voided excluded)" accentColor="#2563eb" /><MetricCard title="Within Entitlement Today" value={summary?.today_within_entitlement ?? 0} subtitle="Accepted collections today" accentColor="#059669" /><MetricCard title="Excess Today" value={summary?.today_excess ?? 0} subtitle="Not entitled today (over entitlement or off day)" accentColor="#dc2626" /><MetricCard title="Pending Review" value={summary?.pending_review ?? exceptions.length} subtitle="Awaiting decision - includes unresolved cases from earlier days" accentColor="#d97706" /></div>
      <ExtraTicketAuthorizations canReview={canReview} />
      <Section className="mb-6" title="Needs a decision" subtitle="Extra tickets nobody authorised in advance (authorise them under Extra ticket authorisations to avoid this). Accept: charge the employee in that month's payroll (automatic - no need to wait for payroll). Waive: no deduction, the company still pays the vendor. Decline: reject the ticket - the vendor isn't billed and nothing is deducted.">{exceptions.length ? <div className="overflow-x-auto"><table className="w-full min-w-[900px] text-left"><thead className="bg-slate-50 text-xs font-semibold uppercase tracking-wide text-slate-500"><tr><th className="px-5 py-3">Employee</th><th className="px-5 py-3">Work Date</th><th className="px-5 py-3">Entitlement</th><th className="px-5 py-3">Collected</th><th className="px-5 py-3">Excess</th><th className="px-5 py-3">Deduction</th><th className="px-5 py-3">Status</th>{canReview && <th className="px-5 py-3">Actions</th>}</tr></thead><tbody className="divide-y divide-slate-100">{exceptions.map((item) => <tr key={item.id}><td className="px-5 py-4 font-medium">{item.employee_name}</td><td className="px-5 py-4 text-sm text-slate-600">{day(item.work_date)}</td><td className="px-5 py-4">{item.entitlement}</td><td className="px-5 py-4">{item.collected_quantity}</td><td className="px-5 py-4">{item.excess_quantity}</td><td className="px-5 py-4 font-semibold">{money(item.proposed_deduction)}</td><td className="px-5 py-4"><StatusBadge status={item.status} /></td>{canReview && <td className="px-5 py-4"><div className="flex gap-2"><button type="button" disabled={acting} onClick={() => void approve(item)} className="rounded-lg bg-emerald-600 px-3 py-2 text-xs font-semibold text-white disabled:opacity-50">Accept</button><button type="button" disabled={acting} onClick={() => setCancelId(item.id)} className="rounded-lg border border-slate-300 px-3 py-2 text-xs font-semibold text-slate-700">Waive</button><button type="button" disabled={acting} onClick={() => setDeclineId(item.id)} className="rounded-lg border border-red-200 px-3 py-2 text-xs font-semibold text-red-700">Decline</button></div></td>}</tr>)}</tbody></table></div> : <p className="p-6 text-center text-sm text-slate-500">Nothing needs a decision.</p>}</Section>
      <Section title="Operations" subtitle="Historical entitlement and rate snapshots are preserved.">{collections.length ? <div className="overflow-x-auto"><table className="w-full min-w-[1050px] text-left"><thead className="bg-slate-50 text-xs font-semibold uppercase tracking-wide text-slate-500"><tr><th className="px-5 py-3">Employee</th><th className="px-5 py-3">Work Date</th><th className="px-5 py-3">Scan Time</th><th className="px-5 py-3">Device</th><th className="px-5 py-3">Sequence</th><th className="px-5 py-3">Entitlement</th><th className="px-5 py-3">Rate</th><th className="px-5 py-3">Status</th>{canReview && <th className="px-5 py-3">Actions</th>}</tr></thead><tbody className="divide-y divide-slate-100">{collections.map((item) => <tr key={item.id} className={item.voided ? "bg-slate-50 text-slate-400" : ""}><td className="px-5 py-4 font-medium">{item.employee_name}</td><td className="px-5 py-4 text-sm text-slate-600">{day(item.work_date)}</td><td className="px-5 py-4 text-sm text-slate-600">{dateTime(item.timestamp)}</td><td className="px-5 py-4 text-sm text-slate-600">{item.device}</td><td className="px-5 py-4 text-sm text-slate-600">{item.sequence}</td><td className="px-5 py-4 text-sm text-slate-600">{item.entitlement}</td><td className="px-5 py-4 text-sm text-slate-600">{money(item.rate)}</td><td className="px-5 py-4">{item.voided ? <span className="inline-block"><StatusBadge status="voided" /><span className="mt-1 block max-w-xs text-xs text-slate-500">{item.void_reason}</span></span> : item.excess_id !== null && item.excess_status === "pending" ? <span className="inline-block rounded-full border border-amber-300 bg-amber-100 px-3 py-1 text-xs font-semibold text-amber-800">Unauthorised extra</span> : <StatusBadge status={item.status} />}</td>{canReview && <td className="px-5 py-4">{ticketActions(item)}</td>}</tr>)}</tbody></table></div> : <Empty message="No meal collections found." />}</Section>
      <Section className="mt-6" title="Devices" subtitle="Registered Meal collection devices. Add or edit these from the Biometric Devices page (Attendance → Biometrics → Devices) with Purpose set to Meal Ticket."><DeviceTable devices={devices} /></Section>
      <Section className="mt-6" title="Rates" subtitle="Effective-dated Meal ticket rate history.">{canConfigure && <form onSubmit={createRate} className="grid gap-3 border-b border-slate-200 p-5 md:grid-cols-4"><input required type="number" min="0.01" step="0.01" value={rateForm.amount} onChange={(event) => setRateForm({ ...rateForm, amount: event.target.value })} placeholder="Amount (NGN)" className={inputClass} /><input required type="date" value={rateForm.effective_from} onChange={(event) => setRateForm({ ...rateForm, effective_from: event.target.value })} className={inputClass} /><input type="date" value={rateForm.effective_to} onChange={(event) => setRateForm({ ...rateForm, effective_to: event.target.value })} className={inputClass} /><button disabled={acting} className="rounded-xl bg-blue-600 px-4 py-2.5 text-sm font-semibold text-white">Create Rate</button></form>}{rates.length ? <div className="overflow-x-auto"><table className="w-full min-w-[650px] text-left"><thead className="bg-slate-50 text-xs font-semibold uppercase text-slate-500"><tr><th className="px-5 py-3">Amount</th><th className="px-5 py-3">Effective From</th><th className="px-5 py-3">Effective To</th><th className="px-5 py-3">Status</th></tr></thead><tbody className="divide-y divide-slate-100">{rates.map((rate) => <tr key={rate.id}><td className="px-5 py-4 font-semibold">{money(rate.amount)}</td><td className="px-5 py-4 text-sm text-slate-600">{day(rate.effective_from)}</td><td className="px-5 py-4 text-sm text-slate-600">{rate.effective_to ? day(rate.effective_to) : "Open ended"}</td><td className="px-5 py-4"><StatusBadge status={rate.active ? "active" : "inactive"} /></td></tr>)}</tbody></table></div> : <Empty message="No Meal ticket rates found." />}</Section>
      <Section className="mt-6" title="Vendor Payments" subtitle="Tickets issued vs. amount owed and paid, per payroll period.">
        <div className="border-b border-slate-200 p-5"><label className="block text-sm font-semibold text-slate-700">Payroll Period</label><select value={vendorPeriodId} onChange={(event) => setVendorPeriodId(event.target.value)} className={`${inputClass} mt-2 max-w-xs`}><option value="">Select period</option>{periods.map((period) => <option key={period.id} value={period.id}>{period.month}/{period.year}</option>)}</select></div>
        {loadingVendor || !vendorData ? <p className="p-10 text-center text-sm text-slate-500">{vendorPeriodId ? "Loading vendor data..." : "Select a payroll period."}</p> : <>
          <div className="grid gap-4 border-b border-slate-200 p-5 sm:grid-cols-4"><MetricCard title="Tickets Issued" value={vendorData.tickets_issued} subtitle="This period" accentColor="#2563eb" /><MetricCard title="Amount Owed" value={money(vendorData.amount_owed)} subtitle="Tickets x rate at collection" accentColor="#d97706" /><MetricCard title="Total Paid" value={money(vendorData.total_paid)} subtitle="Recorded payments" accentColor="#059669" /><MetricCard title="Balance" value={money(vendorData.balance)} subtitle="Owed minus paid" accentColor="#dc2626" /></div>
          {canConfigure && <form onSubmit={createVendorPayment} className="grid gap-3 border-b border-slate-200 p-5 md:grid-cols-4"><input required type="number" min="0.01" step="0.01" value={vendorPaymentForm.amount} onChange={(event) => setVendorPaymentForm({ ...vendorPaymentForm, amount: event.target.value })} placeholder="Amount paid (NGN)" className={inputClass} /><input required type="date" value={vendorPaymentForm.payment_date} onChange={(event) => setVendorPaymentForm({ ...vendorPaymentForm, payment_date: event.target.value })} className={inputClass} /><input value={vendorPaymentForm.reference} onChange={(event) => setVendorPaymentForm({ ...vendorPaymentForm, reference: event.target.value })} placeholder="Reference" className={inputClass} /><button disabled={acting} className="rounded-xl bg-blue-600 px-4 py-2.5 text-sm font-semibold text-white">Record Payment</button><textarea value={vendorPaymentForm.notes} onChange={(event) => setVendorPaymentForm({ ...vendorPaymentForm, notes: event.target.value })} placeholder="Notes (optional)" className={`${inputClass} md:col-span-4 min-h-16`} /></form>}
          {vendorData.payments.length ? <div className="overflow-x-auto"><table className="w-full min-w-[750px] text-left"><thead className="bg-slate-50 text-xs font-semibold uppercase text-slate-500"><tr><th className="px-5 py-3">Amount</th><th className="px-5 py-3">Payment Date</th><th className="px-5 py-3">Reference</th><th className="px-5 py-3">Recorded By</th><th className="px-5 py-3">Notes</th></tr></thead><tbody className="divide-y divide-slate-100">{vendorData.payments.map((payment) => <tr key={payment.id}><td className="px-5 py-4 font-semibold">{money(payment.amount)}</td><td className="px-5 py-4 text-sm text-slate-600">{day(payment.payment_date)}</td><td className="px-5 py-4 text-sm text-slate-600">{payment.reference || "-"}</td><td className="px-5 py-4 text-sm text-slate-600">{payment.recorded_by_name || "-"}</td><td className="px-5 py-4 text-sm text-slate-600">{payment.notes || "-"}</td></tr>)}</tbody></table></div> : <Empty message="No vendor payments recorded for this period." />}
        </>}
      </Section>
      <Section className="mt-6" title="Entitlement Rules" subtitle="Suggested entitlement rules HR can edit. More specific rules (a position, then a category or type) are matched before general ones.">{canConfigure && <form onSubmit={createRule} className="grid gap-3 border-b border-slate-200 p-5 md:grid-cols-3"><select value={ruleForm.employment_category} onChange={(event) => setRuleForm({ ...ruleForm, employment_category: event.target.value })} className={inputClass}><option value="">Any category</option>{employmentCategoryOptions.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select><select value={ruleForm.employment_type} onChange={(event) => setRuleForm({ ...ruleForm, employment_type: event.target.value })} className={inputClass}><option value="">Any employment type</option>{employmentTypeOptions.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select><select value={ruleForm.position} onChange={(event) => setRuleForm({ ...ruleForm, position: event.target.value })} className={inputClass}><option value="">Any position</option>{positions.map((position) => <option key={position.id} value={position.id}>{position.department_name} - {position.name}</option>)}</select><input type="number" min="0" step="1" value={ruleForm.minimum_months_of_service} onChange={(event) => setRuleForm({ ...ruleForm, minimum_months_of_service: event.target.value })} placeholder="Minimum months of service" className={inputClass} /><input required type="number" min="1" step="1" value={ruleForm.tickets_per_work_day} onChange={(event) => setRuleForm({ ...ruleForm, tickets_per_work_day: event.target.value })} placeholder="Tickets per WORK day" className={inputClass} /><input value={ruleForm.description} onChange={(event) => setRuleForm({ ...ruleForm, description: event.target.value })} placeholder="Description" className={`${inputClass} md:col-span-2`} /><div className="text-right"><button disabled={acting} className="rounded-xl bg-blue-600 px-4 py-2.5 text-sm font-semibold text-white">Add Rule</button></div></form>}{rules.length ? <div className="overflow-x-auto"><table className="w-full min-w-[950px] text-left"><thead className="bg-slate-50 text-xs font-semibold uppercase text-slate-500"><tr><th className="px-5 py-3">Description</th><th className="px-5 py-3">Category</th><th className="px-5 py-3">Type</th><th className="px-5 py-3">Position</th><th className="px-5 py-3">Min. Months</th><th className="px-5 py-3">Tickets</th><th className="px-5 py-3">Status</th>{canConfigure && <th className="px-5 py-3">Actions</th>}</tr></thead><tbody className="divide-y divide-slate-100">{rules.map((rule) => <tr key={rule.id}><td className="px-5 py-4 font-medium">{rule.description || "-"}</td><td className="px-5 py-4 text-sm text-slate-600">{rule.employment_category || "Any"}</td><td className="px-5 py-4 text-sm text-slate-600">{rule.employment_type || "Any"}</td><td className="px-5 py-4 text-sm text-slate-600">{rule.position_name || "Any"}</td><td className="px-5 py-4 text-sm text-slate-600">{rule.minimum_months_of_service ?? "-"}</td><td className="px-5 py-4">{rule.tickets_per_work_day}</td><td className="px-5 py-4"><StatusBadge status={rule.active ? "active" : "inactive"} /></td>{canConfigure && <td className="px-5 py-4"><button type="button" disabled={acting} onClick={() => void toggleRule(rule)} className="rounded-lg border border-slate-300 px-3 py-2 text-xs font-semibold">{rule.active ? "Deactivate" : "Activate"}</button></td>}</tr>)}</tbody></table></div> : <Empty message="No meal entitlement rules configured." />}</Section>
      <Section className="mt-6" title="Entitlements" subtitle="Employee-specific approved entitlement history.">{canConfigure && <form onSubmit={createEntitlement} className="grid gap-3 border-b border-slate-200 p-5 md:grid-cols-2"><select required value={entitlementForm.employee} onChange={(event) => setEntitlementForm({ ...entitlementForm, employee: event.target.value })} className={inputClass}><option value="">Select employee</option>{employees.map((employee) => <option key={employee.id} value={employee.id}>{employee.employee_id} - {employee.full_name}</option>)}</select><input required type="number" min="1" step="1" value={entitlementForm.tickets_per_work_day} onChange={(event) => setEntitlementForm({ ...entitlementForm, tickets_per_work_day: event.target.value })} placeholder="Tickets per WORK day" className={inputClass} /><input required type="date" value={entitlementForm.effective_from} onChange={(event) => setEntitlementForm({ ...entitlementForm, effective_from: event.target.value })} className={inputClass} /><input type="date" value={entitlementForm.effective_to} onChange={(event) => setEntitlementForm({ ...entitlementForm, effective_to: event.target.value })} className={inputClass} /><textarea required value={entitlementForm.reason} onChange={(event) => setEntitlementForm({ ...entitlementForm, reason: event.target.value })} placeholder="Approval reason" className={`${inputClass} min-h-24 md:col-span-2`} />{currentUser?.is_superuser && <label className="flex items-center gap-3 text-sm font-semibold text-slate-700 md:col-span-2"><input type="checkbox" checked={entitlementForm.is_exceptional_override} onChange={(event) => setEntitlementForm({ ...entitlementForm, is_exceptional_override: event.target.checked })} />Exceptional Override</label>}<div className="text-right md:col-span-2"><button disabled={acting} className="rounded-xl bg-blue-600 px-4 py-2.5 text-sm font-semibold text-white">Create Entitlement</button></div></form>}{entitlements.length ? <div className="overflow-x-auto"><table className="w-full min-w-[900px] text-left"><thead className="bg-slate-50 text-xs font-semibold uppercase text-slate-500"><tr><th className="px-5 py-3">Employee</th><th className="px-5 py-3">Tickets</th><th className="px-5 py-3">Effective From</th><th className="px-5 py-3">Effective To</th><th className="px-5 py-3">Override</th><th className="px-5 py-3">Reason</th></tr></thead><tbody className="divide-y divide-slate-100">{entitlements.map((item) => <tr key={item.id}><td className="px-5 py-4 font-medium">{item.employee_name}</td><td className="px-5 py-4">{item.tickets_per_work_day}</td><td className="px-5 py-4 text-sm text-slate-600">{day(item.effective_from)}</td><td className="px-5 py-4 text-sm text-slate-600">{item.effective_to ? day(item.effective_to) : "Open ended"}</td><td className="px-5 py-4"><StatusBadge status={item.is_exceptional_override ? "approved" : "inactive"} /></td><td className="px-5 py-4 text-sm text-slate-600">{item.reason}</td></tr>)}</tbody></table></div> : <Empty message="No employee-specific Meal entitlements found." />}</Section>
    </>}
  </main>{canReview && declineId !== null && <div className="fixed inset-0 z-20 flex items-center justify-center bg-slate-950/40 p-4"><div className="w-full max-w-lg rounded-2xl bg-white p-6 shadow-xl"><h2 className="text-xl font-bold">Decline Meal Excess</h2><p className="mt-2 text-sm text-slate-500">The ticket beyond this employee&apos;s entitlement is rejected: the vendor is not billed for it and nothing is deducted from the employee. Adding a reason is optional.</p><textarea autoFocus value={declineReason} onChange={(event) => setDeclineReason(event.target.value)} className="mt-5 min-h-24 w-full rounded-xl border border-slate-300 p-3 text-sm" placeholder="Reason (optional)" /><div className="mt-5 flex justify-end gap-3"><button type="button" onClick={() => { setDeclineId(null); setDeclineReason(""); }} className="rounded-xl border border-slate-300 px-4 py-2.5 text-sm font-semibold">Back</button><button type="button" disabled={acting} onClick={() => void declineExcess()} className="rounded-xl bg-red-600 px-4 py-2.5 text-sm font-semibold text-white">Decline Excess</button></div></div></div>}
  {canReview && voidId !== null && <div className="fixed inset-0 z-20 flex items-center justify-center bg-slate-950/40 p-4"><div className="w-full max-w-lg rounded-2xl bg-white p-6 shadow-xl"><h2 className="text-xl font-bold">Void Meal Ticket</h2><p className="mt-2 text-sm text-slate-500">Use this for a test or accidental scan. The ticket stays on record but stops counting toward the vendor total and the employee&apos;s daily tickets. Adding a reason is optional.</p><textarea autoFocus value={voidReason} onChange={(event) => setVoidReason(event.target.value)} className="mt-5 min-h-24 w-full rounded-xl border border-slate-300 p-3 text-sm" placeholder="Reason (optional), e.g. Test scan" /><div className="mt-5 flex justify-end gap-3"><button type="button" onClick={() => { setVoidId(null); setVoidReason(""); }} className="rounded-xl border border-slate-300 px-4 py-2.5 text-sm font-semibold">Back</button><button type="button" disabled={acting} onClick={() => void voidTicket()} className="rounded-xl bg-red-600 px-4 py-2.5 text-sm font-semibold text-white">Void Ticket</button></div></div></div>}
  {canReview && cancelId !== null && <div className="fixed inset-0 z-20 flex items-center justify-center bg-slate-950/40 p-4"><div className="w-full max-w-lg rounded-2xl bg-white p-6 shadow-xl"><h2 className="text-xl font-bold">Waive Meal Excess</h2><p className="mt-2 text-sm text-slate-500">No deduction is made from the employee and the vendor is still paid for the ticket - the company absorbs it. Adding a reason is optional.</p><textarea autoFocus value={reason} onChange={(event) => setReason(event.target.value)} className="mt-5 min-h-32 w-full rounded-xl border border-slate-300 p-3 text-sm" placeholder="Reason (optional)" /><div className="mt-5 flex justify-end gap-3"><button type="button" onClick={() => { setCancelId(null); setReason(""); }} className="rounded-xl border border-slate-300 px-4 py-2.5 text-sm font-semibold">Back</button><button type="button" disabled={acting} onClick={() => void cancel()} className="rounded-xl bg-red-600 px-4 py-2.5 text-sm font-semibold text-white">Waive Excess</button></div></div></div>}</div>;
}

function Empty({ message }: { message: string }) {
  return <p className="p-10 text-center text-slate-500">{message}</p>;
}

function DeviceTable({ devices }: { devices: Device[] }) {
  if (!devices.length) return <Empty message="No Meal devices registered yet. Add one from the Biometric Devices page." />;
  return <div className="overflow-x-auto"><table className="w-full min-w-[500px] text-left"><thead className="bg-slate-50 text-xs font-semibold uppercase text-slate-500"><tr><th className="px-5 py-3">Name</th><th className="px-5 py-3">Serial Number</th><th className="px-5 py-3">Status</th></tr></thead><tbody className="divide-y divide-slate-100">{devices.map((device) => <tr key={device.id}><td className="px-5 py-4 font-medium">{device.name}</td><td className="px-5 py-4 font-mono text-sm text-slate-600">{device.serial_number}</td><td className="px-5 py-4"><StatusBadge status={device.active ? "active" : "inactive"} /></td></tr>)}</tbody></table></div>;
}

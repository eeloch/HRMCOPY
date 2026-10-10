"use client";

import type { FormEvent, ReactNode } from "react";
import { useEffect, useEffectEvent, useMemo, useState } from "react";
import { useRouter } from "next/navigation";

import Sidebar from "@/components/Sidebar";
import { MetricCard, PageHeader, Section, StatusBadge } from "@/components/ui";
import { ExtraTicketAuthorizations } from "@/components/meals/ExtraTicketAuthorizations";
import { Chips, DateRangeFilter, Panel, ScrollArea, SearchBox, TabBar, isoDate, rangeFor, type DateRange } from "@/components/meals/MealsUi";
import { apiFetch, getAccessToken, getCurrentUser, type CurrentUser } from "@/lib/api";

type Collection = { id: number; employee_number: string; employee_name: string; work_date: string; timestamp: string; device: string; entitlement: number; sequence: number; rate: string; status: string; voided: boolean; void_reason: string; excess_id: number | null; excess_status: string | null };
type OperationsSummary = { today_collections: number; today_within_entitlement: number; today_excess: number; pending_review: number };
type RangeSummary = { collections: number; within_entitlement: number; excess: number; voided: number };
type Exception = { balances_on_accept?: boolean; id: number; employee_number: string; employee_name: string; work_date: string; entitlement: number; collected_quantity: number; excess_quantity: number; proposed_deduction: string; status: string; card_verified: boolean; comment: string };
type Period = { id: number; year: number; month: number; status: string };
type Device = { id: number; name: string; serial_number: string; active: boolean };
type Rate = { id: number; amount: string; effective_from: string; effective_to: string | null; active: boolean };
type Entitlement = { id: number; employee: number; employee_name: string; tickets_per_work_day: number; effective_from: string; effective_to: string | null; reason: string; is_exceptional_override: boolean };
type Employee = { id: number; employee_id: string; full_name: string };
type Rule = { id: number; employment_type: string; employment_category: string; position: number | null; position_name: string; minimum_months_of_service: number | null; tickets_per_work_day: number; priority: number; description: string; active: boolean };
type PositionOption = { id: number; name: string; department_name: string };
type VendorPayment = { id: number; payroll_period: number | null; amount: string; payment_date: string; covers_from: string | null; covers_to: string | null; reference: string; notes: string; recorded_by_name: string; created_at: string };
type VendorBucket = { key: string; start: string; end: string; tickets: number; claimed: number; unclaimed: number; awaiting_claim: number; claim_status: "" | "claimed" | "partial" | "none"; owed: string; paid: string; balance: string };
type VendorClaim = { id: number; date_from: string; date_to: string; quantity: number; reference: string; notes: string; recorded_by_name: string; issued: number; payable: number; unclaimed: number; over_claimed: number };
type VendorSummary = { date_from: string; date_to: string; group_by: VendorGroup; totals: { tickets: number; claimed: number; unclaimed: number; awaiting_claim: number; owed: string; paid: string; balance: string }; buckets: VendorBucket[]; claims: VendorClaim[]; payments: VendorPayment[] };
type MultipleTickets = { date_from: string; date_to: string; min_extra: number; totals: { people: number; person_days: number; extra_tickets: number }; days: Array<{ date: string; people: number; extra_tickets: number }>; rows: Array<{ employee_number: string; employee_name: string; work_date: string; tickets: number; entitled: number; extra: number; charged: number; waived: number; waiting: number }>; truncated: boolean };
type Chargeback = { id: number; employee_number: string; employee_name: string; work_date: string; quantity: number; amount: string; reason: string; reference: string; status: "pending" | "deducted" | "cancelled"; pay_year: number; pay_month: number; created_by_name: string; created_at: string; cancel_reason: string };
type ChargebackLookup = { employee_name: string; employee_status: string; work_date: string; rate: string | null; taken: number; already_charged: number; charged_back: number; chargeable: number };
type ShiftReview = { decision: "charged" | "waived"; reason: string; by: string; at: string; amount: string | null; chargeback_status: string | null };
type ShiftRow = { employee_number: string; employee_name: string; department: string; shift: string; clock_in: string | null; clock_out: string | null; attendance: string; absence_status: string | null; entitled: number; tickets: number; ticket_times: string[]; result: string; result_label: string; needs_a_look: boolean; needs_decision: boolean; review: ShiftReview | null };
type ShiftWaiting = { employee_number: string; employee_name: string; department: string; work_date: string; shift: string; tickets: number; ticket_times: string[]; absence_status: string | null };
type ShiftOther = { employee_number: string; employee_name: string; tickets: number; ticket_times: string[]; shift: string; status: string };
type ShiftReport = { date: string; scope: "night" | "all"; totals: { rostered: number; clocked_in: number; collected: number; did_not_collect: number; absent_collected: number; waiting_decision: number; needs_a_look: number; tickets_issued: number; value_at_rate: string | null; tickets_for_others: number }; rows: ShiftRow[]; others: ShiftOther[]; waiting: ShiftWaiting[] };
type VendorGroup = "week" | "month" | "day";
type Tab = "review" | "collections" | "night" | "vendor" | "setup"; // "night" is the Meal report tab (kept as the key)
type DecisionFilter = "pending" | "decided" | "all";
type CollectionStatusFilter = "all" | "within" | "excess" | "voided";
type BulkKind = "accept" | "waive" | "decline";

const inputClass = "w-full rounded-xl border border-slate-300 bg-white px-3 py-2.5 text-sm outline-none focus:ring-2 focus:ring-blue-500";
const money = (value: string) => `₦${Number(value || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
const day = (value: string) => new Intl.DateTimeFormat("en-GB", { day: "2-digit", month: "short", year: "numeric" }).format(new Date(`${value}T00:00:00`));
const fullDay = (value: string) => new Intl.DateTimeFormat("en-GB", { weekday: "short", day: "2-digit", month: "short" }).format(new Date(`${value}T00:00:00`));
const dateTime = (value: string) => new Date(value).toLocaleString("en-NG", { dateStyle: "medium", timeStyle: "short" });
const rateInitial = () => ({ amount: "", effective_from: "", effective_to: "" });
const entitlementInitial = () => ({ employee: "", tickets_per_work_day: "", effective_from: "", effective_to: "", reason: "", is_exceptional_override: false });
const ruleInitial = () => ({ employment_type: "", employment_category: "", position: "", minimum_months_of_service: "", tickets_per_work_day: "", priority: "100", description: "" });
const chargebackInitial = () => ({ employee_number: "", work_date: "", quantity: "1", reason: "", reference: "", pay_month: "" });
const chargebackStatusLabel = { pending: "Waiting for payroll", deducted: "Deducted from pay", cancelled: "Cancelled" } as const;
const vendorClaimInitial = () => ({ date_from: "", date_to: "", quantity: "", reference: "" });
const vendorPaymentInitial = () => ({ amount: "", payment_date: "", covers_from: "", covers_to: "", reference: "", notes: "" });
const employmentTypeOptions = [["permanent", "Permanent"], ["contract", "Contract"], ["casual", "Casual"], ["intern", "Intern"], ["nysc", "NYSC"], ["expatriate", "Expatriate"]];
const employmentCategoryOptions = [["staff", "Staff"], ["management", "Management"], ["executive", "Executive"]];
const PAGE_SIZE = 50;
const REVIEW_PAGE_SIZE = 50;
const BULK_CHUNK_SIZE = 200;

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

function queryString(values: Record<string, string>) {
  const params = new URLSearchParams();
  Object.entries(values).forEach(([key, value]) => { if (value) params.set(key, value); });
  return params.toString();
}

/** The day to report on: yesterday, because absences are only known once a day has been judged. */
function defaultNightDate() {
  const now = new Date();
  return isoDate(new Date(now.getFullYear(), now.getMonth(), now.getDate() - 1));
}

function vendorPreset(key: "thisWeek" | "lastWeek" | "fourWeeks" | "thisMonth" | "lastMonth" | "threeMonths"): DateRange {
  const now = new Date();
  const monday = new Date(now.getFullYear(), now.getMonth(), now.getDate() - ((now.getDay() + 6) % 7));
  const plus = (base: Date, days: number) => new Date(base.getFullYear(), base.getMonth(), base.getDate() + days);
  if (key === "thisWeek") return { from: isoDate(monday), to: isoDate(now) };
  if (key === "lastWeek") return { from: isoDate(plus(monday, -7)), to: isoDate(plus(monday, -1)) };
  if (key === "fourWeeks") return { from: isoDate(plus(monday, -21)), to: isoDate(now) };
  if (key === "thisMonth") return { from: isoDate(new Date(now.getFullYear(), now.getMonth(), 1)), to: isoDate(now) };
  if (key === "lastMonth") return { from: isoDate(new Date(now.getFullYear(), now.getMonth() - 1, 1)), to: isoDate(new Date(now.getFullYear(), now.getMonth(), 0)) };
  return { from: isoDate(new Date(now.getFullYear(), now.getMonth() - 2, 1)), to: isoDate(now) };
}

function bucketLabel(bucket: { start: string; end: string }, group: VendorGroup) {
  if (group === "day") return day(bucket.start);
  if (group === "month") return new Intl.DateTimeFormat("en-GB", { month: "long", year: "numeric" }).format(new Date(`${bucket.start}T00:00:00`));
  return `${day(bucket.start)} - ${day(bucket.end)}`;
}

const vendorPresets: Array<["thisWeek" | "lastWeek" | "fourWeeks" | "thisMonth" | "lastMonth" | "threeMonths", string]> = [["thisWeek", "This week"], ["lastWeek", "Last week"], ["fourWeeks", "Last 4 weeks"], ["thisMonth", "This month"], ["lastMonth", "Last month"], ["threeMonths", "Last 3 months"]];

function VendorRangeFilter({ value, onChange }: { value: DateRange; onChange: (range: DateRange) => void }) {
  const chip = "rounded-full border px-3 py-1.5 text-xs font-semibold transition";
  const field = "rounded-xl border border-slate-300 bg-white px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-blue-500";
  return (
    <div className="flex flex-wrap items-center gap-2">
      {vendorPresets.map(([key, label]) => {
        const preset = vendorPreset(key);
        const active = preset.from === value.from && preset.to === value.to;
        return <button key={key} type="button" onClick={() => onChange(preset)} className={`${chip} ${active ? "border-blue-600 bg-blue-600 text-white" : "border-slate-300 bg-white text-slate-700 hover:bg-slate-50"}`}>{label}</button>;
      })}
      <span className="ml-1 flex items-center gap-1.5 text-xs text-slate-500">
        <input type="date" aria-label="From date" value={value.from} max={value.to || undefined} onChange={(event) => onChange({ ...value, from: event.target.value })} className={field} />
        to
        <input type="date" aria-label="To date" value={value.to} min={value.from || undefined} onChange={(event) => onChange({ ...value, to: event.target.value })} className={field} />
      </span>
    </div>
  );
}

export default function MealsPage() {
  const router = useRouter();
  const [currentUser, setCurrentUser] = useState<CurrentUser | null>(null);
  const [tab, setTab] = useState<Tab>("review");
  const [ready, setReady] = useState(false);
  const [summary, setSummary] = useState<OperationsSummary | null>(null);
  // Needs a decision
  const [exceptions, setExceptions] = useState<Exception[]>([]);
  const [exceptionsTotal, setExceptionsTotal] = useState(0);
  const [exceptionsPage, setExceptionsPage] = useState(1);
  const [decisionFilter, setDecisionFilter] = useState<DecisionFilter>("pending");
  const [reviewRange, setReviewRange] = useState<DateRange>(rangeFor("all"));
  const [reviewSearch, setReviewSearch] = useState("");
  const [selected, setSelected] = useState<number[]>([]);
  const [selectAllMatching, setSelectAllMatching] = useState(false);
  const [bulkKind, setBulkKind] = useState<BulkKind | null>(null);
  const [bulkReason, setBulkReason] = useState("");
  const [bulkProgress, setBulkProgress] = useState<{ done: number; total: number } | null>(null);
  const [quickReviewOpen, setQuickReviewOpen] = useState(false);
  const [quickReviewCase, setQuickReviewCase] = useState<Exception | null>(null);
  const [quickReviewRemaining, setQuickReviewRemaining] = useState(0);
  const [quickReviewDecided, setQuickReviewDecided] = useState(0);
  const [quickReviewReasonKind, setQuickReviewReasonKind] = useState<"waive" | "decline" | null>(null);
  const [quickReviewReason, setQuickReviewReason] = useState("");
  const [quickReviewBusy, setQuickReviewBusy] = useState(false);
  const [quickReviewDone, setQuickReviewDone] = useState(false);
  const [quickReviewError, setQuickReviewError] = useState("");
  // Collections
  const [collections, setCollections] = useState<Collection[]>([]);
  const [collectionsTotal, setCollectionsTotal] = useState(0);
  const [rangeSummary, setRangeSummary] = useState<RangeSummary | null>(null);
  const [collectionRange, setCollectionRange] = useState<DateRange>(rangeFor("today"));
  const [collectionSearch, setCollectionSearch] = useState("");
  const [collectionDevice, setCollectionDevice] = useState("");
  const [collectionStatus, setCollectionStatus] = useState<CollectionStatusFilter>("all");
  const [collectionPage, setCollectionPage] = useState(1);
  // Setup and vendor data
  const [devices, setDevices] = useState<Device[]>([]);
  const [rates, setRates] = useState<Rate[]>([]);
  const [entitlements, setEntitlements] = useState<Entitlement[]>([]);
  const [entitlementSearch, setEntitlementSearch] = useState("");
  const [currentOnly, setCurrentOnly] = useState(true);
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
  const [vendorRange, setVendorRange] = useState<DateRange>(() => vendorPreset("fourWeeks"));
  const [vendorGroup, setVendorGroup] = useState<VendorGroup>("week");
  const [vendorSummary, setVendorSummary] = useState<VendorSummary | null>(null);
  const [loadingVendor, setLoadingVendor] = useState(false);
  const [vendorPaymentForm, setVendorPaymentForm] = useState(vendorPaymentInitial);
  const [vendorClaimForm, setVendorClaimForm] = useState(vendorClaimInitial);
  const [shiftDate, setShiftDate] = useState(defaultNightDate);
  const [shiftScope, setShiftScope] = useState<"night" | "all">("all");
  const [shiftFilter, setShiftFilter] = useState<"everyone" | "look" | "absent" | "skipped">("everyone");
  const [shiftReport, setShiftReport] = useState<ShiftReport | null>(null);
  const [loadingShift, setLoadingShift] = useState(false);
  const [multipleMin, setMultipleMin] = useState<1 | 2>(1);
  const [multiple, setMultiple] = useState<MultipleTickets | null>(null);
  const [chargebacks, setChargebacks] = useState<Chargeback[]>([]);
  const [chargebackForm, setChargebackForm] = useState(chargebackInitial);
  const [chargebackLookup, setChargebackLookup] = useState<ChargebackLookup | null>(null);
  const [chargebackNote, setChargebackNote] = useState("");
  const canReview = currentUser?.permissions.review_meal_excess === true;
  const canConfigure = currentUser?.permissions.manage_meal_configuration === true;
  const canOperate = currentUser !== null && (currentUser.permissions.record_meal_operations === true || canReview || canConfigure);
  const canVendor = canOperate || currentUser?.permissions.view_meal_vendor_payments === true || currentUser?.permissions.record_meal_vendor_payments === true;
  const canChargeBack = currentUser?.permissions.charge_back_meal_tickets === true || canReview;
  const canRecordVendor = currentUser?.permissions.record_meal_vendor_payments === true || canConfigure;
  const canAccess = canVendor;

  const reviewFilters = useMemo(() => ({ decisions: decisionFilter, date_from: reviewRange.from, date_to: reviewRange.to, search: reviewSearch.trim() }), [decisionFilter, reviewRange, reviewSearch]);
  const reviewQuery = useMemo(() => queryString({ ...reviewFilters, page_size: "1", exceptions_page: String(exceptionsPage), exceptions_page_size: String(REVIEW_PAGE_SIZE) }), [reviewFilters, exceptionsPage]);
  const collectionsQuery = useMemo(() => queryString({ date_from: collectionRange.from, date_to: collectionRange.to, search: collectionSearch.trim(), device: collectionDevice, status: collectionStatus === "all" ? "" : collectionStatus, page: String(collectionPage), page_size: String(PAGE_SIZE) }), [collectionRange, collectionSearch, collectionDevice, collectionStatus, collectionPage]);

  function fail(loadError: unknown, fallback: string) {
    const message = loadError instanceof Error ? loadError.message : fallback;
    if (message === "Authentication required." || message === "Your session has expired.") { router.push("/login"); return; }
    setError(message);
  }

  async function loadReview(query: string) {
    try {
      const response = await apiFetch(`/meals/operations/?${query}`);
      if (!response.ok) throw new Error(apiMessage(await response.json().catch(() => null), "Unable to load the decisions."));
      const data = await response.json();
      setSummary(data.summary || null); setExceptions(data.exceptions || []); setExceptionsTotal(data.exceptions_total ?? (data.exceptions || []).length);
      setSelected((current) => current.filter((id) => (data.exceptions || []).some((item: Exception) => item.id === id && item.status === "pending")));
    } catch (loadError) { fail(loadError, "Unable to load the decisions."); }
  }

  function resetReviewPaging() { setExceptionsPage(1); setSelected([]); setSelectAllMatching(false); }

  async function loadCollections(query: string) {
    try {
      const response = await apiFetch(`/meals/operations/?${query}`);
      if (!response.ok) throw new Error(apiMessage(await response.json().catch(() => null), "Unable to load meal collections."));
      const data = await response.json();
      setSummary(data.summary || null); setCollections(data.collections || []); setCollectionsTotal(data.collections_total ?? (data.collections || []).length); setRangeSummary(data.range_summary || null);
    } catch (loadError) { fail(loadError, "Unable to load meal collections."); }
  }

  async function load(silent = false) {
    if (!silent) setLoading(true);
    setError("");
    try {
      const user = await getCurrentUser();
      setCurrentUser(user);
      const operates = user.permissions.record_meal_operations === true || user.permissions.review_meal_excess === true || user.permissions.manage_meal_configuration === true;
      if (!operates && !user.permissions.view_meal_vendor_payments && !user.permissions.record_meal_vendor_payments) return;
      if (!operates) { setTab("vendor"); setReady(true); return; } // vendor figures only: nothing else to load
      const [devicesResponse, ratesResponse, entitlementsResponse, rulesResponse] = await Promise.all([
        apiFetch("/meals/devices/"), apiFetch("/meals/rates/"), apiFetch("/meals/entitlements/"), apiFetch("/meals/rules/"),
      ]);
      const required = [[devicesResponse, "Unable to load Meal devices."], [ratesResponse, "Unable to load meal ticket rates."], [entitlementsResponse, "Unable to load meal entitlements."], [rulesResponse, "Unable to load meal entitlement rules."]] as const;
      for (const [response, fallback] of required) if (!response.ok) throw new Error(apiMessage(await response.json().catch(() => null), fallback));
      const [deviceData, rateData, entitlementData, ruleData] = await Promise.all([devicesResponse.json(), ratesResponse.json(), entitlementsResponse.json(), rulesResponse.json()]);
      setDevices(deviceData.results || []); setRates(rateData.results || []); setEntitlements(entitlementData.results || []); setRules(ruleData.results || []);
      if (user.permissions.manage_meal_configuration) {
        const [employeeResponse, positionResponse] = await Promise.all([apiFetch("/employees/"), apiFetch("/employees/positions/")]);
        if (!employeeResponse.ok) throw new Error(apiMessage(await employeeResponse.json().catch(() => null), "Unable to load employees for meal entitlements."));
        setEmployees((await employeeResponse.json()).results || []);
        setPositions(positionResponse.ok ? await positionResponse.json() : []);
      } else { setEmployees([]); setPositions([]); }
      setReady(true);
    } catch (loadError) { fail(loadError, "Unable to load Meals."); } finally { setLoading(false); }
  }

  async function loadVendorSummary(silent = false) {
    if (!silent) setLoadingVendor(true);
    try {
      const response = await apiFetch(`/meals/vendor/summary/?${queryString({ date_from: vendorRange.from, date_to: vendorRange.to, group_by: vendorGroup })}`);
      if (!response.ok) throw new Error(apiMessage(await response.json().catch(() => null), "Unable to load vendor payment data."));
      setVendorSummary(await response.json());
    } catch (vendorError) {
      setError(vendorError instanceof Error ? vendorError.message : "Unable to load vendor payment data.");
    } finally { setLoadingVendor(false); }
  }
  async function loadShiftReport() {
    if (!shiftDate) return;
    setLoadingShift(true);
    try {
      const response = await apiFetch(`/meals/shift-report/?${queryString({ date: shiftDate, scope: shiftScope })}`);
      if (!response.ok) throw new Error(apiMessage(await response.json().catch(() => null), "Unable to load the night meals report."));
      setShiftReport(await response.json());
    } catch (shiftError) { setError(shiftError instanceof Error ? shiftError.message : "Unable to load the night meals report."); } finally { setLoadingShift(false); }
  }
  async function decideAbsentTicket(person: { employee_number: string; employee_name: string; work_date: string; tickets: number }, decision: "charge" | "waive") {
    const question = decision === "charge"
      ? `Charge ${person.employee_name} for the ${person.tickets} meal ticket${person.tickets === 1 ? "" : "s"} collected on ${fullDay(person.work_date)}, when they were absent? Add a note (optional):`
      : `Do not charge ${person.employee_name} for the meal ticket collected on ${fullDay(person.work_date)}. Say why:`;
    const answer = window.prompt(question, decision === "charge" ? "Collected a meal ticket on a day they were absent" : "");
    if (answer === null) return;
    await request("/meals/shift-report/decide/", "POST", { employee_number: person.employee_number, work_date: person.work_date, decision, reason: answer.trim() || "Collected a meal ticket on a day they were absent" }, decision === "charge" ? "Ticket charged back - it comes out of the employee's pay." : "Recorded: not charged.");
    await loadShiftReport();
  }
  function exportShiftCsv() {
    if (!shiftReport) return;
    const rows: Array<Array<string | number>> = [["Staff No", "Name", "Department", "Shift", "Clock in", "Clock out", "Entitled", "Tickets issued", "Ticket times", "Result", "Decision"], ...shiftReport.rows.map((row) => [row.employee_number, row.employee_name, row.department, row.shift, row.clock_in || "", row.clock_out || "", row.entitled, row.tickets, row.ticket_times.join(" "), row.result_label, row.review ? (row.review.decision === "charged" ? `Charged ${row.review.amount ?? ""}` : "Not charged") + ` - ${row.review.reason}` : row.needs_decision ? "Waiting for a decision" : ""]), ...shiftReport.others.map((other) => [other.employee_number, other.employee_name, "", other.shift, "", "", "", other.tickets, other.ticket_times.join(" "), "Ticket for someone not on this shift", ""])];
    const csv = "\uFEFF" + rows.map((row) => row.map((cell) => `"${String(cell).replace(/"/g, '""')}"`).join(",")).join("\n");
    const link = document.createElement("a");
    link.href = URL.createObjectURL(new Blob([csv], { type: "text/csv;charset=utf-8" }));
    link.download = `meal-report-${shiftReport.date}.csv`;
    link.click();
    URL.revokeObjectURL(link.href);
  }
  async function loadMultiple() {
    try {
      const response = await apiFetch(`/meals/vendor/multiple-tickets/?${queryString({ date_from: vendorRange.from, date_to: vendorRange.to, min: String(multipleMin) })}`);
      if (!response.ok) throw new Error(apiMessage(await response.json().catch(() => null), "Unable to load the multiple-ticket report."));
      setMultiple(await response.json());
    } catch (multipleError) { setError(multipleError instanceof Error ? multipleError.message : "Unable to load the multiple-ticket report."); }
  }
  async function loadChargebacks() {
    try {
      const response = await apiFetch(`/meals/chargebacks/?${queryString({ date_from: vendorRange.from, date_to: vendorRange.to })}`);
      if (response.ok) setChargebacks((await response.json()).results || []);
    } catch { /* the list is a convenience; the form still works */ }
  }
  async function lookUpChargeback() {
    setChargebackNote(""); setChargebackLookup(null);
    if (!chargebackForm.employee_number.trim() || !chargebackForm.work_date) { setChargebackNote("Enter the staff number and the date the tickets were taken."); return; }
    const response = await apiFetch(`/meals/chargebacks/lookup/?${queryString({ employee: chargebackForm.employee_number.trim(), date: chargebackForm.work_date })}`);
    const data = await response.json().catch(() => null);
    if (!response.ok) { setChargebackNote(apiMessage(data, "Could not look that up.")); return; }
    setChargebackLookup(data);
    setChargebackForm((current) => ({ ...current, quantity: String(Math.max(1, Math.min(Number(current.quantity) || 1, data.chargeable || 1))) }));
  }
  async function submitChargeback(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = chargebackForm;
    const month = form.pay_month ? form.pay_month.split("-") : [];
    const body = { employee_number: form.employee_number.trim(), work_date: form.work_date, quantity: Number(form.quantity), reason: form.reason, reference: form.reference, pay_year: month[0] ? Number(month[0]) : null, pay_month: month[1] ? Number(month[1]) : null };
    if (await request("/meals/chargebacks/", "POST", body, "Ticket charged back - it comes out of the employee's pay.")) { setChargebackForm(chargebackInitial()); setChargebackLookup(null); setChargebackNote(""); }
  }
  async function cancelChargeback(item: Chargeback) {
    const why = window.prompt(`Cancel the charge of ${money(item.amount)} to ${item.employee_name}? Say why:`);
    if (why === null) return;
    await request(`/meals/chargebacks/${item.id}/cancel/`, "POST", { reason: why }, "Chargeback cancelled - nothing will be deducted.");
  }
  function chargeBackFrom(row: { employee_number: string; work_date: string }) {
    setChargebackForm({ ...chargebackInitial(), employee_number: row.employee_number, work_date: row.work_date });
    setChargebackLookup(null); setChargebackNote("");
    window.setTimeout(() => document.getElementById("chargeback-form")?.scrollIntoView({ behavior: "smooth", block: "center" }), 0);
  }
  async function refreshAll() {
    if (canOperate) await Promise.all([loadReview(reviewQuery), loadCollections(collectionsQuery), load(true)]);
    if (canVendor && tab === "vendor") { await loadVendorSummary(true); await loadMultiple(); if (canChargeBack || canOperate) await loadChargebacks(); }
  }

  const loadOnMount = useEffectEvent(() => { void load(); });
  useEffect(() => {
    if (!getAccessToken()) { router.push("/login"); return; }
    const timer = window.setTimeout(loadOnMount, 0);
    return () => window.clearTimeout(timer);
  }, [router]);

  const runReview = useEffectEvent((query: string) => { void loadReview(query); });
  useEffect(() => {
    if (!ready || !canOperate) return;
    const timer = window.setTimeout(() => runReview(reviewQuery), 250);
    return () => window.clearTimeout(timer);
  }, [ready, canOperate, reviewQuery]);

  const runCollections = useEffectEvent((query: string) => { void loadCollections(query); });
  useEffect(() => {
    if (!ready || !canOperate) return;
    const timer = window.setTimeout(() => runCollections(collectionsQuery), 250);
    return () => window.clearTimeout(timer);
  }, [ready, canOperate, collectionsQuery]);

  const runShiftReport = useEffectEvent(() => { void loadShiftReport(); });
  useEffect(() => {
    if (!ready || !canOperate || tab !== "night") return;
    const timer = window.setTimeout(runShiftReport, 0);
    return () => window.clearTimeout(timer);
  }, [ready, canOperate, tab, shiftDate, shiftScope]);

  const runVendor = useEffectEvent(() => { void loadVendorSummary(); void loadMultiple(); if (canChargeBack || canOperate) void loadChargebacks(); });
  useEffect(() => {
    if (!ready || !canVendor || tab !== "vendor") return;
    const timer = window.setTimeout(runVendor, 0);
    return () => window.clearTimeout(timer);
  }, [ready, canVendor, tab, vendorRange, vendorGroup, multipleMin]);

  const handleQuickReviewKey = useEffectEvent((event: KeyboardEvent) => {
    const target = event.target as HTMLElement | null;
    if (target && (target.tagName === "TEXTAREA" || target.tagName === "INPUT")) {
      if (event.key === "Escape") { (target as HTMLElement).blur(); setQuickReviewReasonKind(null); setQuickReviewReason(""); }
      return;
    }
    if (event.key === "Escape") { event.preventDefault(); closeQuickReview(); return; }
    if (quickReviewBusy || !quickReviewCase) return;
    if (event.key === "a" || event.key === "A") { event.preventDefault(); void decideQuickReview("accept"); }
    else if (event.key === "w" || event.key === "W") { event.preventDefault(); void decideQuickReview("waive"); }
    else if (event.key === "d" || event.key === "D") { event.preventDefault(); void decideQuickReview("decline"); }
  });
  useEffect(() => {
    if (!quickReviewOpen) return;
    window.addEventListener("keydown", handleQuickReviewKey);
    return () => window.removeEventListener("keydown", handleQuickReviewKey);
  }, [quickReviewOpen]);

  async function request(path: string, method: "POST" | "PATCH", body: unknown, success: string | ((data: Record<string, unknown> | null) => string)) {
    setActing(true); setError("");
    try {
      const response = await apiFetch(path, { method, body: JSON.stringify(body) });
      const data = await response.json().catch(() => null);
      if (!response.ok) throw new Error(apiMessage(data, "The Meals request could not be completed."));
      setFeedback(typeof success === "function" ? success(data) : success); await refreshAll(); return true;
    } catch (actionError) { setError(actionError instanceof Error ? actionError.message : "The Meals request could not be completed."); return false; }
    finally { setActing(false); }
  }

  async function approve(item: { id: number; work_date: string }) {
    await request(`/meals/excess/${item.id}/approve/`, "POST", {}, (data) => data && Number(data.balanced_tickets) > 0 ? "Balanced against the entitlement entered later - nothing is charged." : "Excess accepted - it comes out of that month's pay.");
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
  async function resolveBulkIds(): Promise<number[]> {
    if (!selectAllMatching) return selected;
    const response = await apiFetch(`/meals/excess/pending-ids/?${queryString(reviewFilters)}`);
    if (!response.ok) throw new Error(apiMessage(await response.json().catch(() => null), "Unable to fetch the full matching list."));
    const data = await response.json();
    return (data.ids || []) as number[];
  }

  async function runBulk() {
    if (bulkKind === null) return;
    const action = { accept: "accept", waive: "waive", decline: "decline" }[bulkKind];
    const verb = { accept: "accepted", waive: "waived", decline: "declined" }[bulkKind];
    setActing(true); setError("");
    try {
      const ids = await resolveBulkIds();
      if (!ids.length) { setActing(false); return; }
      setBulkProgress({ done: 0, total: ids.length });
      let done = 0; let failed = 0; const failMessages: string[] = [];
      for (let start = 0; start < ids.length; start += BULK_CHUNK_SIZE) {
        const chunk = ids.slice(start, start + BULK_CHUNK_SIZE);
        const response = await apiFetch("/meals/excess/bulk-decision/", { method: "POST", body: JSON.stringify({ action, ids: chunk, reason: bulkReason.trim() }) });
        const data = await response.json().catch(() => null);
        if (!response.ok) { failed += chunk.length; failMessages.push(apiMessage(data, "The request could not be completed.")); }
        else {
          done += (data?.succeeded || []).length;
          failed += (data?.failed || []).length;
          for (const item of data?.failed || []) failMessages.push(`#${item.id}: ${item.error}`);
        }
        setBulkProgress({ done: Math.min(start + chunk.length, ids.length), total: ids.length });
      }
      setFeedback(`${done} decision${done === 1 ? "" : "s"} ${verb}.${failed ? ` ${failed} could not be ${verb}${failMessages.length ? ` (e.g. ${failMessages.slice(0, 3).join("; ")})` : ""}.` : ""}`);
      setSelected([]); setSelectAllMatching(false); setBulkKind(null); setBulkReason("");
      await refreshAll();
    } catch (bulkError) {
      setError(bulkError instanceof Error ? bulkError.message : "The bulk request could not be completed.");
    } finally {
      setBulkProgress(null); setActing(false);
    }
  }

  async function loadNextQuickReviewCase(afterDecision: boolean) {
    setQuickReviewBusy(true);
    try {
      const response = await apiFetch(`/meals/operations/?${queryString({ decisions: "pending", date_from: reviewRange.from, date_to: reviewRange.to, search: reviewSearch.trim(), page_size: "1", exceptions_page: "1", exceptions_page_size: "1" })}`);
      if (!response.ok) throw new Error(apiMessage(await response.json().catch(() => null), "Unable to load the next case."));
      const data = await response.json();
      const next: Exception | undefined = (data.exceptions || [])[0];
      setQuickReviewRemaining(data.exceptions_total ?? 0);
      setQuickReviewCase(next || null);
      setQuickReviewReasonKind(null); setQuickReviewReason("");
      if (afterDecision) setQuickReviewDecided((count) => count + 1);
      if (!next) setQuickReviewDone(true);
    } catch (loadError) {
      fail(loadError, "Unable to load the next case.");
    } finally {
      setQuickReviewBusy(false);
    }
  }

  function openQuickReview() {
    setQuickReviewOpen(true); setQuickReviewDecided(0); setQuickReviewDone(false); setQuickReviewError("");
    void loadNextQuickReviewCase(false);
  }

  async function decideQuickReview(kind: BulkKind) {
    if (!quickReviewCase || quickReviewBusy) return;
    if (kind !== "accept" && quickReviewReasonKind !== kind) { setQuickReviewReasonKind(kind); setQuickReviewError(""); return; }
    setQuickReviewBusy(true); setQuickReviewError("");
    try {
      const path = { accept: "approve", waive: "cancel", decline: "decline" }[kind];
      const response = await apiFetch(`/meals/excess/${quickReviewCase.id}/${path}/`, { method: "POST", body: JSON.stringify(kind === "accept" ? {} : { reason: quickReviewReason.trim() }) });
      if (!response.ok) throw new Error(apiMessage(await response.json().catch(() => null), "The request could not be completed."));
      await loadNextQuickReviewCase(true);
    } catch (decisionError) {
      setQuickReviewError(decisionError instanceof Error ? decisionError.message : "The request could not be completed.");
      setQuickReviewBusy(false);
    }
  }

  function closeQuickReview() {
    setQuickReviewOpen(false); setQuickReviewCase(null); setQuickReviewReasonKind(null); setQuickReviewReason(""); setQuickReviewError("");
    void loadReview(reviewQuery);
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
  async function createVendorPayment(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = vendorPaymentForm;
    const body = { amount: Number(form.amount), payment_date: form.payment_date, covers_from: form.covers_from || null, covers_to: form.covers_to || null, reference: form.reference, notes: form.notes };
    if (await request("/meals/vendor/payments/", "POST", body, "Vendor payment recorded.")) setVendorPaymentForm(vendorPaymentInitial());
  }
  async function createVendorClaim(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = vendorClaimForm;
    if (await request("/meals/vendor/claims/", "POST", { date_from: form.date_from, date_to: form.date_to, quantity: Number(form.quantity), reference: form.reference }, "Claim recorded - what is owed now follows the claimed tickets for those days.")) setVendorClaimForm(vendorClaimInitial());
  }
  async function deleteVendorClaim(claim: VendorClaim) {
    if (!window.confirm(`Delete the claim of ${claim.quantity} tickets for ${day(claim.date_from)} - ${day(claim.date_to)}? Those days go back to being owed as issued until a new claim is entered.`)) return;
    setActing(true); setError("");
    try {
      const response = await apiFetch(`/meals/vendor/claims/${claim.id}/`, { method: "DELETE" });
      if (!response.ok) throw new Error(apiMessage(await response.json().catch(() => null), "The claim could not be deleted."));
      setFeedback("Claim deleted."); await refreshAll();
    } catch (deleteError) { setError(deleteError instanceof Error ? deleteError.message : "The claim could not be deleted."); } finally { setActing(false); }
  }
  function claimBucket(bucket: VendorBucket) {
    setVendorClaimForm({ ...vendorClaimInitial(), date_from: bucket.start, date_to: bucket.end, quantity: String(bucket.tickets) });
    window.setTimeout(() => document.getElementById("vendor-claim-form")?.scrollIntoView({ behavior: "smooth", block: "center" }), 0);
  }
  function payBucket(bucket: VendorBucket) {
    const owing = Number(bucket.balance);
    setVendorPaymentForm({ ...vendorPaymentInitial(), amount: owing > 0 ? String(owing) : "", payment_date: isoDate(new Date()), covers_from: bucket.start, covers_to: bucket.end });
    window.setTimeout(() => document.getElementById("vendor-payment-form")?.scrollIntoView({ behavior: "smooth", block: "center" }), 0);
  }
  function exportVendorCsv() {
    if (!vendorSummary) return;
    const rows: Array<Array<string | number>> = [["Period", "Issued", "Claimed", "Not claimed", "Awaiting a claim count", "Amount owed", "Paid", "Balance"], ...vendorSummary.buckets.map((bucket) => [bucketLabel(bucket, vendorSummary.group_by), bucket.tickets, bucket.claimed, bucket.unclaimed, bucket.awaiting_claim, bucket.owed, bucket.paid, bucket.balance]), ["Total", vendorSummary.totals.tickets, vendorSummary.totals.claimed, vendorSummary.totals.unclaimed, vendorSummary.totals.awaiting_claim, vendorSummary.totals.owed, vendorSummary.totals.paid, vendorSummary.totals.balance]];
    const csv = rows.map((row) => row.map((cell) => `"${String(cell).replace(/"/g, '""')}"`).join(",")).join("\n");
    const link = document.createElement("a");
    link.href = URL.createObjectURL(new Blob([csv], { type: "text/csv" }));
    link.download = `vendor-payments-${vendorSummary.date_from}-to-${vendorSummary.date_to}.csv`;
    link.click();
    URL.revokeObjectURL(link.href);
  }

  const excessOutcome: Record<string, string> = { approved: "Accepted - deducted when payroll is generated", deducted: "Accepted - deducted from pay", cancelled: "Waived - no deduction", declined: "Declined - not billed" };
  const voidButton = (item: Collection) => <button type="button" disabled={acting} onClick={() => setVoidId(item.id)} className="text-xs font-semibold text-slate-400 underline hover:text-slate-600">Void</button>;
  function ticketActions(item: Collection) {
    if (item.voided) return null;
    if (item.status === "within_entitlement") return voidButton(item);
    if (item.excess_id !== null && item.excess_status === "pending") {
      return <span className="text-xs font-semibold text-amber-700">Waiting in the Needs a decision tab</span>;
    }
    return <span className="text-xs text-slate-500">{item.excess_status ? excessOutcome[item.excess_status] || item.excess_status : ""}</span>;
  }

  const pendingRows = exceptions.filter((item) => item.status === "pending");
  const selectedRows = pendingRows.filter((item) => selected.includes(item.id));
  const selectedTotal = selectedRows.reduce((sum, item) => sum + Number(item.proposed_deduction || 0), 0);
  const allSelected = pendingRows.length > 0 && selectedRows.length === pendingRows.length;
  const bulkCount = selectAllMatching ? exceptionsTotal : selectedRows.length;
  const canOfferSelectAllMatching = allSelected && !selectAllMatching && exceptionsTotal > pendingRows.length && decisionFilter === "pending";
  const exceptionsPages = Math.max(1, Math.ceil(exceptionsTotal / REVIEW_PAGE_SIZE));
  const pages = Math.max(1, Math.ceil(collectionsTotal / PAGE_SIZE));
  const showingFrom = collectionsTotal ? (collectionPage - 1) * PAGE_SIZE + 1 : 0;
  const showingTo = Math.min(collectionPage * PAGE_SIZE, collectionsTotal);
  const today = new Date().toISOString().slice(0, 10);
  const shownEntitlements = entitlements.filter((item) => {
    if (currentOnly && item.effective_to && item.effective_to < today) return false;
    const term = entitlementSearch.trim().toLowerCase();
    return !term || item.employee_name.toLowerCase().includes(term);
  });
  const bulkText = bulkKind === "accept"
    ? `Accept ${bulkCount} excess ticket${bulkCount === 1 ? "" : "s"}${selectAllMatching ? "" : `: ${money(String(selectedTotal))} in total`} will be charged to the employees in that month's payroll.`
    : bulkKind === "waive"
      ? `Waive ${bulkCount} excess ticket${bulkCount === 1 ? "" : "s"}: no deduction is made and the company still pays the vendor.`
      : `Decline ${bulkCount} excess ticket${bulkCount === 1 ? "" : "s"}: the vendor is not billed and nothing is deducted.`;

  return <div className="min-h-screen bg-slate-100"><Sidebar /><main className="ml-64 min-w-0 p-4 md:p-8">
    <PageHeader title="Meals" description="Monitor collections, review excesses, and maintain approved Meal configuration." actions={<div className="flex gap-2"><button type="button" onClick={() => router.push("/meals/live")} className="rounded-xl border border-slate-300 bg-white px-4 py-2.5 text-sm font-semibold text-slate-700">Live Screen</button><button type="button" onClick={() => void refreshAll()} disabled={loading} className="rounded-xl border border-slate-300 bg-white px-4 py-2.5 text-sm font-semibold text-slate-700 disabled:opacity-50">Refresh</button></div>} />
    {feedback && <p className="mb-6 flex items-start justify-between gap-4 rounded-xl bg-emerald-50 p-4 text-sm text-emerald-800"><span>{feedback}</span><button type="button" onClick={() => setFeedback("")} className="font-semibold">Dismiss</button></p>}{error && <p className="mb-6 rounded-xl bg-red-50 p-4 text-sm text-red-700">{error}</p>}
    {loading ? <div className="h-64 animate-pulse rounded-2xl bg-slate-200" /> : !canAccess ? <Section title="Meals Access" subtitle="Your account does not have a Meals capability."><p className="p-8 text-sm text-slate-600">Ask an administrator to grant a Meals permission if you need access to operations or configuration.</p></Section> : <>
      {canOperate && <div className="mb-6 grid gap-4 sm:grid-cols-2 xl:grid-cols-4"><MetricCard title="Collections Today" value={summary?.today_collections ?? 0} subtitle="Meal scans today (voided excluded)" accentColor="#2563eb" /><MetricCard title="Within Entitlement Today" value={summary?.today_within_entitlement ?? 0} subtitle="Accepted collections today" accentColor="#059669" /><MetricCard title="Excess Today" value={summary?.today_excess ?? 0} subtitle="Not entitled today (over entitlement or off day)" accentColor="#dc2626" /><MetricCard title="Pending Review" value={summary?.pending_review ?? 0} subtitle="Awaiting decision - includes unresolved cases from earlier days" accentColor="#d97706" /></div>}
      <TabBar<Tab> value={tab} onChange={setTab} tabs={canOperate ? [["review", "Needs a decision", summary?.pending_review ?? null], ["collections", "Collections", null], ["night", "Meal report", shiftReport?.totals.waiting_decision ? shiftReport.totals.waiting_decision : null], ["vendor", "Vendor payments", null], ["setup", "Setup", null]] : [["vendor", "Vendor payments", null]]} />

      {tab === "review" && <>
        <Section title="Needs a decision" subtitle="Extra tickets nobody authorised in advance. Accept: charge the employee in that month's payroll. Waive: no deduction, the company still pays the vendor. Decline: reject the ticket - the vendor isn't billed and nothing is deducted." actions={canReview && <button type="button" disabled={acting} onClick={openQuickReview} className="rounded-xl bg-blue-600 px-4 py-2.5 text-sm font-semibold text-white disabled:opacity-50">⚡ Quick Review</button>}>
          <div className="space-y-3 border-b border-slate-200 p-4">
            <div className="flex flex-wrap items-center justify-between gap-3"><Chips<DecisionFilter> value={decisionFilter} onChange={(value) => { setDecisionFilter(value); resetReviewPaging(); }} options={[["pending", "Waiting for a decision"], ["decided", "Already decided"], ["all", "Everything"]]} /><SearchBox value={reviewSearch} onChange={(value) => { setReviewSearch(value); resetReviewPaging(); }} /></div>
            <DateRangeFilter value={reviewRange} onChange={(range) => { setReviewRange(range); resetReviewPaging(); }} />
            <p className="text-xs text-slate-500">{exceptionsTotal} {exceptionsTotal === 1 ? "case" : "cases"}{reviewRange.from || reviewRange.to ? " in the chosen dates" : " across all dates"}.</p>
          </div>
          {canReview && (selectedRows.length > 0 || selectAllMatching) && <div className="sticky top-0 z-20 border-b border-blue-200 bg-blue-50 text-sm">
            <div className="flex flex-wrap items-center gap-3 px-5 py-3">
              <span className="font-semibold text-blue-900">{selectAllMatching ? `All ${exceptionsTotal} matching this filter selected` : <>{selectedRows.length} selected · {money(String(selectedTotal))}</>}</span>
              <button type="button" disabled={acting} onClick={() => setBulkKind("accept")} className="rounded-lg bg-emerald-600 px-3 py-2 text-xs font-semibold text-white disabled:opacity-50">Accept selected</button>
              <button type="button" disabled={acting} onClick={() => setBulkKind("waive")} className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-xs font-semibold text-slate-700">Waive selected</button>
              <button type="button" disabled={acting} onClick={() => setBulkKind("decline")} className="rounded-lg border border-red-200 bg-white px-3 py-2 text-xs font-semibold text-red-700">Decline selected</button>
              <button type="button" onClick={() => { setSelected([]); setSelectAllMatching(false); }} className="ml-auto text-xs font-semibold text-slate-500 underline">Clear</button>
            </div>
            {bulkProgress && <div className="border-t border-blue-200 px-5 py-2 text-xs text-blue-800">Processing {bulkProgress.done} of {bulkProgress.total}...</div>}
          </div>}
          {canOfferSelectAllMatching && <div className="border-b border-amber-200 bg-amber-50 px-5 py-2 text-xs text-amber-900">All {pendingRows.length} on this page are selected. <button type="button" onClick={() => setSelectAllMatching(true)} className="font-semibold underline">Select all {exceptionsTotal} matching this filter instead</button></div>}
          {exceptions.length ? <><ScrollArea maxHeight="55vh"><table className="w-full min-w-[900px] text-left"><thead className="text-xs font-semibold uppercase tracking-wide text-slate-500"><tr>{canReview && <th className="w-10 px-4 py-3"><input type="checkbox" aria-label="Select all waiting decisions on this page" checked={allSelected} disabled={!pendingRows.length} onChange={(event) => { setSelectAllMatching(false); setSelected(event.target.checked ? pendingRows.map((item) => item.id) : []); }} /></th>}<th className="px-4 py-3">Employee</th><th className="px-4 py-3">Work Date</th><th className="px-4 py-3">Entitlement</th><th className="px-4 py-3">Collected</th><th className="px-4 py-3">Excess</th><th className="px-4 py-3">Deduction</th><th className="px-4 py-3">Status</th>{canReview && <th className="px-4 py-3">Actions</th>}</tr></thead><tbody className="divide-y divide-slate-100">{exceptions.map((item) => <tr key={item.id} className={selected.includes(item.id) || selectAllMatching ? "bg-blue-50/60" : ""}>{canReview && <td className="px-4 py-3">{item.status === "pending" && <input type="checkbox" aria-label={`Select ${item.employee_name}`} checked={selected.includes(item.id) || selectAllMatching} disabled={selectAllMatching} onChange={(event) => setSelected((current) => event.target.checked ? [...current, item.id] : current.filter((id) => id !== item.id))} />}</td>}<td className="px-4 py-3 font-medium">{item.employee_name}<span className="block text-xs font-normal text-slate-500">{item.employee_number}</span>{item.card_verified && <span className="mt-1 inline-block rounded-full border border-red-300 bg-red-50 px-2 py-0.5 text-xs font-semibold text-red-700">Card scan - gating bypassed</span>}</td><td className="px-4 py-3 text-sm text-slate-600">{day(item.work_date)}</td><td className="px-4 py-3">{item.entitlement}</td><td className="px-4 py-3">{item.collected_quantity}</td><td className="px-4 py-3">{item.excess_quantity}</td><td className="px-4 py-3 font-semibold">{money(item.proposed_deduction)}</td><td className="px-4 py-3"><StatusBadge status={item.status} />{item.status === "pending" && item.balances_on_accept && <span className="mt-1 block max-w-[14rem] text-xs font-semibold text-emerald-700">Entitlement now covers this - Accept clears it, no charge</span>}{item.status !== "pending" && item.comment && <span className="mt-1 block max-w-[14rem] text-xs text-slate-500">{item.comment}</span>}</td>{canReview && <td className="px-4 py-3">{item.status === "pending" ? <div className="flex gap-2"><button type="button" disabled={acting} onClick={() => void approve(item)} className="rounded-lg bg-emerald-600 px-3 py-2 text-xs font-semibold text-white disabled:opacity-50">Accept</button><button type="button" disabled={acting} onClick={() => setCancelId(item.id)} className="rounded-lg border border-slate-300 px-3 py-2 text-xs font-semibold text-slate-700">Waive</button><button type="button" disabled={acting} onClick={() => setDeclineId(item.id)} className="rounded-lg border border-red-200 px-3 py-2 text-xs font-semibold text-red-700">Decline</button></div> : <span className="text-xs text-slate-500">{excessOutcome[item.status] || item.status}</span>}</td>}</tr>)}</tbody></table></ScrollArea>
            <div className="flex flex-wrap items-center justify-between gap-3 border-t border-slate-200 px-5 py-3 text-sm text-slate-600"><span>{exceptionsTotal ? `Showing ${(exceptionsPage - 1) * REVIEW_PAGE_SIZE + 1}-${Math.min(exceptionsPage * REVIEW_PAGE_SIZE, exceptionsTotal)} of ${exceptionsTotal}` : "Nothing to show"}</span><span className="flex items-center gap-2"><button type="button" disabled={exceptionsPage <= 1} onClick={() => setExceptionsPage((p) => Math.max(1, p - 1))} className="rounded-lg border border-slate-300 px-3 py-1.5 font-semibold disabled:opacity-40">Previous</button><span>Page {exceptionsPage} of {exceptionsPages}</span><button type="button" disabled={exceptionsPage >= exceptionsPages} onClick={() => setExceptionsPage((p) => Math.min(exceptionsPages, p + 1))} className="rounded-lg border border-slate-300 px-3 py-1.5 font-semibold disabled:opacity-40">Next</button></span></div>
          </> : <p className="p-8 text-center text-sm text-slate-500">{decisionFilter === "pending" ? "Nothing is waiting for a decision for these filters." : "No cases for these filters."}</p>}
        </Section>
        <div className="mt-6"><ExtraTicketAuthorizations canReview={canReview} /></div>
      </>}

      {tab === "collections" && <Section title="Collections" subtitle="Every meal scan, with the entitlement and rate as they were at the time.">
        <div className="space-y-3 border-b border-slate-200 p-4">
          <DateRangeFilter value={collectionRange} onChange={(range) => { setCollectionRange(range); setCollectionPage(1); }} />
          <div className="flex flex-wrap items-center gap-3"><SearchBox value={collectionSearch} onChange={(value) => { setCollectionSearch(value); setCollectionPage(1); }} /><select aria-label="Terminal" value={collectionDevice} onChange={(event) => { setCollectionDevice(event.target.value); setCollectionPage(1); }} className="rounded-xl border border-slate-300 bg-white px-3 py-2 text-sm"><option value="">All terminals</option>{devices.map((device) => <option key={device.id} value={device.name}>{device.name}</option>)}</select><Chips<CollectionStatusFilter> value={collectionStatus} onChange={(value) => { setCollectionStatus(value); setCollectionPage(1); }} options={[["all", "All"], ["within", "Within entitlement"], ["excess", "Excess"], ["voided", "Voided"]]} /></div>
          {rangeSummary && <div className="flex flex-wrap gap-2 text-xs"><span className="rounded-full bg-blue-50 px-3 py-1 font-semibold text-blue-800">{rangeSummary.collections} collected</span><span className="rounded-full bg-emerald-50 px-3 py-1 font-semibold text-emerald-800">{rangeSummary.within_entitlement} within entitlement</span><span className="rounded-full bg-red-50 px-3 py-1 font-semibold text-red-800">{rangeSummary.excess} excess</span><span className="rounded-full bg-slate-100 px-3 py-1 font-semibold text-slate-600">{rangeSummary.voided} voided</span></div>}
        </div>
        {collections.length ? <ScrollArea maxHeight="62vh"><table className="w-full min-w-[1000px] text-left"><thead className="text-xs font-semibold uppercase tracking-wide text-slate-500"><tr><th className="px-4 py-3">Employee</th><th className="px-4 py-3">Work Date</th><th className="px-4 py-3">Scan Time</th><th className="px-4 py-3">Device</th><th className="px-4 py-3">Ticket</th><th className="px-4 py-3">Rate</th><th className="px-4 py-3">Status</th>{canReview && <th className="px-4 py-3">Actions</th>}</tr></thead><tbody className="divide-y divide-slate-100">{collections.map((item) => <tr key={item.id} className={item.voided ? "bg-slate-50 text-slate-400" : ""}><td className="px-4 py-3 font-medium">{item.employee_name}<span className="block text-xs font-normal text-slate-500">{item.employee_number}</span></td><td className="px-4 py-3 text-sm text-slate-600">{day(item.work_date)}</td><td className="px-4 py-3 text-sm text-slate-600">{dateTime(item.timestamp)}</td><td className="px-4 py-3 text-sm text-slate-600">{item.device}</td><td className="px-4 py-3 text-sm text-slate-600">{item.sequence} of {item.entitlement}</td><td className="px-4 py-3 text-sm text-slate-600">{money(item.rate)}</td><td className="px-4 py-3">{item.voided ? <span className="inline-block"><StatusBadge status="voided" /><span className="mt-1 block max-w-xs text-xs text-slate-500">{item.void_reason}</span></span> : item.excess_id !== null && item.excess_status === "pending" ? <span className="inline-block rounded-full border border-amber-300 bg-amber-100 px-3 py-1 text-xs font-semibold text-amber-800">Unauthorised extra</span> : <StatusBadge status={item.status} />}</td>{canReview && <td className="px-4 py-3">{ticketActions(item)}</td>}</tr>)}</tbody></table></ScrollArea> : <p className="p-10 text-center text-slate-500">No meal collections for these filters.</p>}
        <div className="flex flex-wrap items-center justify-between gap-3 border-t border-slate-200 px-5 py-3 text-sm text-slate-600"><span>{collectionsTotal ? `Showing ${showingFrom}-${showingTo} of ${collectionsTotal}` : "Nothing to show"}</span><span className="flex items-center gap-2"><button type="button" disabled={collectionPage <= 1} onClick={() => setCollectionPage((page) => Math.max(1, page - 1))} className="rounded-lg border border-slate-300 px-3 py-1.5 font-semibold disabled:opacity-40">Previous</button><span>Page {collectionPage} of {pages}</span><button type="button" disabled={collectionPage >= pages} onClick={() => setCollectionPage((page) => Math.min(pages, page + 1))} className="rounded-lg border border-slate-300 px-3 py-1.5 font-semibold disabled:opacity-40">Next</button></span></div>
      </Section>}

      {tab === "night" && <Section title="Meal report" subtitle="Day and night: who was rostered, who clocked in, and the tickets the terminals issued. Anyone absent who still collected a ticket is raised here the next day, for you to charge back or let go." actions={shiftReport && <button type="button" onClick={exportShiftCsv} className="rounded-xl border border-slate-300 bg-white px-4 py-2.5 text-sm font-semibold text-slate-700">Download CSV</button>}>
        <div className="flex flex-wrap items-center gap-3 border-b border-slate-200 p-4">
          <label className="text-xs font-semibold text-slate-600">Shift starting on<input type="date" value={shiftDate} max={isoDate(new Date())} onChange={(event) => setShiftDate(event.target.value)} className={`${inputClass} mt-1 w-44`} /></label>
          <button type="button" onClick={() => setShiftDate(defaultNightDate())} className="rounded-xl border border-slate-300 bg-white px-3 py-2 text-xs font-semibold text-slate-700">Yesterday</button>
          <Chips<"all" | "night"> options={[["all", "All shifts"], ["night", "Night shifts"]]} value={shiftScope} onChange={setShiftScope} />
          <Chips<"everyone" | "look" | "absent" | "skipped"> options={[["everyone", "Everyone"], ["look", "Needs a look"], ["absent", "Absent but collected"], ["skipped", "Did not collect"]]} value={shiftFilter} onChange={setShiftFilter} />
        </div>
        {shiftReport && shiftReport.waiting.length > 0 && <div className="border-b border-red-200 bg-red-50/60 p-5">
          <p className="text-sm font-bold text-red-800">Waiting for your decision: {shiftReport.waiting.length} {shiftReport.waiting.length === 1 ? "person was" : "people were"} absent but collected a meal ticket (last 14 days)</p>
          <p className="mt-1 text-xs text-red-700">An absence is only known after the day is judged. Charge the ticket back to the employee, or record why not. An absence that was waived or excused may be a reason not to charge.</p>
          <ScrollArea maxHeight="32vh"><table className="mt-3 w-full min-w-[820px] text-left"><thead className="text-xs font-semibold uppercase text-red-800"><tr><th className="px-3 py-2">Employee</th><th className="px-3 py-2">Day</th><th className="px-3 py-2">Shift</th><th className="px-3 py-2">Tickets</th><th className="px-3 py-2">Absence</th>{canChargeBack && <th className="px-3 py-2" />}</tr></thead><tbody className="divide-y divide-red-100">{shiftReport.waiting.map((person) => <tr key={`${person.employee_number}-${person.work_date}`}><td className="px-3 py-2 text-sm font-medium">{person.employee_name}<span className="block text-xs font-normal text-slate-500">{person.employee_number}{person.department ? ` - ${person.department}` : ""}</span></td><td className="px-3 py-2 text-sm">{fullDay(person.work_date)}</td><td className="px-3 py-2 text-sm text-slate-600">{person.shift || "-"}</td><td className="px-3 py-2 text-sm">{person.tickets}<span className="block text-xs text-slate-500">{person.ticket_times.join(", ")}</span></td><td className="px-3 py-2 text-xs font-semibold text-slate-600">{person.absence_status === "approved" ? "Absence confirmed" : person.absence_status === "waived" ? "Absence waived" : person.absence_status === "held" ? "Absence on hold" : person.absence_status === "pending" ? "Absence not yet decided" : "Absent"}</td>{canChargeBack && <td className="whitespace-nowrap px-3 py-2 text-right"><button type="button" disabled={acting} onClick={() => void decideAbsentTicket(person, "charge")} className="rounded-lg bg-red-600 px-3 py-1.5 text-xs font-semibold text-white disabled:opacity-50">Charge</button><button type="button" disabled={acting} onClick={() => void decideAbsentTicket(person, "waive")} className="ml-2 rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-xs font-semibold text-slate-700 disabled:opacity-50">Don&apos;t charge</button></td>}</tr>)}</tbody></table></ScrollArea>
        </div>}
        {!shiftReport ? <p className="p-10 text-center text-sm text-slate-500">{loadingShift ? "Loading..." : "Choose a date."}</p> : <>
          <div className={`grid gap-4 border-b border-slate-200 p-5 sm:grid-cols-3 xl:grid-cols-7 ${loadingShift ? "opacity-60" : ""}`}><MetricCard title="Rostered" value={shiftReport.totals.rostered} subtitle={day(shiftReport.date)} accentColor="#2563eb" /><MetricCard title="Clocked in" value={shiftReport.totals.clocked_in} subtitle="Of those rostered" accentColor="#059669" /><MetricCard title="Collected a meal" value={shiftReport.totals.collected} subtitle="At least one ticket" accentColor="#7c3aed" /><MetricCard title="Absent but collected" value={shiftReport.totals.absent_collected} subtitle="Raised for a decision" accentColor="#b91c1c" /><MetricCard title="Did not collect" value={shiftReport.totals.did_not_collect} subtitle="Clocked in, no ticket" accentColor="#d97706" /><MetricCard title="Needs a look" value={shiftReport.totals.needs_a_look} subtitle="Extras, no clock-in, no entitlement" accentColor="#dc2626" /><MetricCard title="Tickets issued" value={shiftReport.totals.tickets_issued} subtitle={shiftReport.totals.value_at_rate ? `${money(shiftReport.totals.value_at_rate)} - compare with the vendor's count` : "Compare with the vendor's count"} accentColor="#0f172a" /></div>
          {shiftReport.rows.length ? <ScrollArea maxHeight="55vh"><table className="w-full min-w-[980px] text-left"><thead className="text-xs font-semibold uppercase text-slate-500"><tr><th className="px-4 py-3">Employee</th><th className="px-4 py-3">Shift</th><th className="px-4 py-3">Clock in - out</th><th className="px-4 py-3">Entitled</th><th className="px-4 py-3">Tickets</th><th className="px-4 py-3">Result</th>{canChargeBack && <th className="px-4 py-3" />}</tr></thead><tbody className="divide-y divide-slate-100">{shiftReport.rows.filter((row) => shiftFilter === "everyone" || (shiftFilter === "look" ? row.needs_a_look : shiftFilter === "absent" ? row.result === "absent_collected" : row.result === "did_not_collect")).map((row) => <tr key={row.employee_number} className={row.result === "absent_collected" ? "bg-red-50/70" : row.needs_a_look ? "bg-amber-50/60" : ""}><td className="px-4 py-3 text-sm font-medium">{row.employee_name}<span className="block text-xs font-normal text-slate-500">{row.employee_number}{row.department ? ` - ${row.department}` : ""}</span></td><td className="px-4 py-3 text-sm text-slate-600">{row.shift}</td><td className="px-4 py-3 text-sm">{row.clock_in || row.clock_out ? `${row.clock_in || "-"} - ${row.clock_out || "-"}` : <span className="text-slate-400">No punch</span>}</td><td className="px-4 py-3 text-sm">{row.entitled}</td><td className="px-4 py-3 text-sm">{row.tickets}{row.ticket_times.length > 0 && <span className="block text-xs text-slate-500">{row.ticket_times.join(", ")}</span>}</td><td className={`px-4 py-3 text-sm font-semibold ${row.result === "absent_collected" || row.needs_a_look ? "text-red-700" : row.result === "collected" ? "text-emerald-700" : row.result === "did_not_collect" ? "text-amber-700" : "text-slate-500"}`}>{row.result_label}{row.review && <span className="block text-xs font-normal text-slate-600">{row.review.decision === "charged" ? `Charged back${row.review.amount ? ` ${money(row.review.amount)}` : ""}` : "Not charged"} - {row.review.reason}<span className="block text-slate-400">{row.review.by}</span></span>}</td>{canChargeBack && <td className="whitespace-nowrap px-4 py-3 text-right">{row.needs_decision && <><button type="button" disabled={acting} onClick={() => void decideAbsentTicket({ employee_number: row.employee_number, employee_name: row.employee_name, work_date: shiftReport.date, tickets: row.tickets }, "charge")} className="rounded-lg bg-red-600 px-3 py-1.5 text-xs font-semibold text-white disabled:opacity-50">Charge</button><button type="button" disabled={acting} onClick={() => void decideAbsentTicket({ employee_number: row.employee_number, employee_name: row.employee_name, work_date: shiftReport.date, tickets: row.tickets }, "waive")} className="ml-2 rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-xs font-semibold text-slate-700 disabled:opacity-50">Don&apos;t charge</button></>}</td>}</tr>)}</tbody></table></ScrollArea> : <Empty message="Nobody was rostered for this shift on that date." />}
          {shiftReport.others.length > 0 && <div className="border-t border-slate-200"><div className="px-5 pb-1 pt-4 text-sm font-semibold text-red-700">Tickets issued to people who were not on this shift ({shiftReport.totals.tickets_for_others})</div><ScrollArea maxHeight="30vh"><table className="w-full min-w-[700px] text-left"><thead className="text-xs font-semibold uppercase text-slate-500"><tr><th className="px-4 py-3">Employee</th><th className="px-4 py-3">Tickets</th><th className="px-4 py-3">Times</th></tr></thead><tbody className="divide-y divide-slate-100">{shiftReport.others.map((other) => <tr key={other.employee_number}><td className="px-4 py-3 text-sm font-medium">{other.employee_name}<span className="block text-xs font-normal text-slate-500">{other.employee_number}</span></td><td className="px-4 py-3 text-sm">{other.tickets}</td><td className="px-4 py-3 text-sm text-slate-600">{other.ticket_times.join(", ")}</td></tr>)}</tbody></table></ScrollArea></div>}
        </>}
      </Section>}

      {tab === "vendor" && <Section title="Vendor Payments" subtitle="What the vendor is owed and has been paid, by week, month or day, for any dates. Tickets count on the day they were collected; a payment counts on the days it covers." actions={vendorSummary && <button type="button" onClick={exportVendorCsv} className="rounded-xl border border-slate-300 bg-white px-4 py-2.5 text-sm font-semibold text-slate-700">Download CSV</button>}>
        <div className="space-y-3 border-b border-slate-200 p-4">
          <div className="flex flex-wrap items-center gap-3"><span className="text-sm font-semibold text-slate-700">Group by</span><Chips<VendorGroup> options={[["week", "Weeks"], ["month", "Months"], ["day", "Days"]]} value={vendorGroup} onChange={setVendorGroup} /></div>
          <VendorRangeFilter value={vendorRange} onChange={setVendorRange} />
        </div>
        {!vendorSummary ? <p className="p-10 text-center text-sm text-slate-500">{loadingVendor ? "Loading vendor data..." : "Choose the dates to look at."}</p> : <>
          <div className={`grid gap-4 border-b border-slate-200 p-5 sm:grid-cols-2 xl:grid-cols-5 ${loadingVendor ? "opacity-60" : ""}`}><MetricCard title="Tickets Issued" value={vendorSummary.totals.tickets} subtitle={`${day(vendorSummary.date_from)} to ${day(vendorSummary.date_to)}`} accentColor="#2563eb" /><MetricCard title="Tickets Claimed" value={vendorSummary.totals.claimed} subtitle={`${vendorSummary.totals.unclaimed} issued but not claimed${vendorSummary.totals.awaiting_claim ? `; ${vendorSummary.totals.awaiting_claim} still awaiting a count` : ""}`} accentColor="#7c3aed" /><MetricCard title="Amount Owed" value={money(vendorSummary.totals.owed)} subtitle="Claimed tickets x rate; days with no count are owed as issued" accentColor="#d97706" /><MetricCard title="Total Paid" value={money(vendorSummary.totals.paid)} subtitle="Payments covering these dates" accentColor="#059669" /><MetricCard title="Balance" value={money(vendorSummary.totals.balance)} subtitle="Owed minus paid" accentColor="#dc2626" /></div>

          {canRecordVendor && <form id="vendor-claim-form" onSubmit={createVendorClaim} className="grid gap-3 border-b border-slate-200 p-5 md:grid-cols-5"><p className="text-sm font-semibold text-slate-700 md:col-span-5">Enter what the vendor claimed <span className="font-normal text-slate-500">- a day&apos;s tally or a whole week&apos;s invoice: any dates, one count.</span></p><label className="text-xs font-semibold text-slate-600">From<input required type="date" value={vendorClaimForm.date_from} onChange={(event) => setVendorClaimForm({ ...vendorClaimForm, date_from: event.target.value })} className={`${inputClass} mt-1`} /></label><label className="text-xs font-semibold text-slate-600">To<input required type="date" value={vendorClaimForm.date_to} min={vendorClaimForm.date_from || undefined} onChange={(event) => setVendorClaimForm({ ...vendorClaimForm, date_to: event.target.value })} className={`${inputClass} mt-1`} /></label><label className="text-xs font-semibold text-slate-600">Tickets claimed<input required type="number" min="0" step="1" value={vendorClaimForm.quantity} onChange={(event) => setVendorClaimForm({ ...vendorClaimForm, quantity: event.target.value })} className={`${inputClass} mt-1`} /></label><label className="text-xs font-semibold text-slate-600">Invoice / reference<input value={vendorClaimForm.reference} onChange={(event) => setVendorClaimForm({ ...vendorClaimForm, reference: event.target.value })} className={`${inputClass} mt-1`} /></label><button disabled={acting} className="self-end rounded-xl bg-violet-600 px-4 py-2.5 text-sm font-semibold text-white">Record claim</button></form>}
          {canRecordVendor && <form id="vendor-payment-form" onSubmit={createVendorPayment} className="grid gap-3 border-b border-slate-200 p-5 md:grid-cols-4"><label className="text-xs font-semibold text-slate-600">Amount paid (NGN)<input required type="number" min="0.01" step="0.01" value={vendorPaymentForm.amount} onChange={(event) => setVendorPaymentForm({ ...vendorPaymentForm, amount: event.target.value })} className={`${inputClass} mt-1`} /></label><label className="text-xs font-semibold text-slate-600">Paid on<input required type="date" value={vendorPaymentForm.payment_date} onChange={(event) => setVendorPaymentForm({ ...vendorPaymentForm, payment_date: event.target.value })} className={`${inputClass} mt-1`} /></label><label className="text-xs font-semibold text-slate-600">Covers tickets from<input type="date" value={vendorPaymentForm.covers_from} onChange={(event) => setVendorPaymentForm({ ...vendorPaymentForm, covers_from: event.target.value })} className={`${inputClass} mt-1`} /></label><label className="text-xs font-semibold text-slate-600">Covers tickets to<input type="date" value={vendorPaymentForm.covers_to} min={vendorPaymentForm.covers_from || undefined} onChange={(event) => setVendorPaymentForm({ ...vendorPaymentForm, covers_to: event.target.value })} className={`${inputClass} mt-1`} /></label><input value={vendorPaymentForm.reference} onChange={(event) => setVendorPaymentForm({ ...vendorPaymentForm, reference: event.target.value })} placeholder="Reference" className={`${inputClass} md:col-span-2`} /><textarea value={vendorPaymentForm.notes} onChange={(event) => setVendorPaymentForm({ ...vendorPaymentForm, notes: event.target.value })} placeholder="Notes (optional)" className={`${inputClass} md:col-span-2 min-h-11`} /><p className="text-xs text-slate-500 md:col-span-3">Leave the covered dates empty and the payment counts on the day it was paid. Use "Record payment" on a row below to fill in that row&apos;s dates and amount owed.</p><button disabled={acting} className="rounded-xl bg-blue-600 px-4 py-2.5 text-sm font-semibold text-white">Record Payment</button></form>}
          <ScrollArea maxHeight="50vh"><table className="w-full min-w-[980px] text-left"><thead className="text-xs font-semibold uppercase text-slate-500"><tr><th className="px-5 py-3">{vendorSummary.group_by === "week" ? "Week" : vendorSummary.group_by === "month" ? "Month" : "Day"}</th><th className="px-5 py-3">Issued</th><th className="px-5 py-3">Claimed</th><th className="px-5 py-3">Not claimed</th><th className="px-5 py-3">Amount owed</th><th className="px-5 py-3">Paid</th><th className="px-5 py-3">Balance</th>{canRecordVendor && <th className="px-5 py-3" />}</tr></thead><tbody className="divide-y divide-slate-100">{vendorSummary.buckets.map((bucket) => { const balance = Number(bucket.balance); return <tr key={bucket.key}><td className="px-5 py-3 text-sm font-medium">{bucketLabel(bucket, vendorSummary.group_by)}</td><td className="px-5 py-3 text-sm">{bucket.tickets}</td><td className="px-5 py-3 text-sm">{bucket.claim_status === "none" ? <span className="text-xs font-semibold text-amber-700">No count yet</span> : <>{bucket.claimed}{bucket.claim_status === "partial" && <span className="block text-xs font-semibold text-amber-700">{bucket.awaiting_claim} not counted yet</span>}</>}</td><td className={`px-5 py-3 text-sm ${bucket.unclaimed > 0 ? "font-semibold text-violet-700" : ""}`}>{bucket.claim_status === "none" ? "-" : bucket.unclaimed}</td><td className="px-5 py-3 text-sm">{money(bucket.owed)}</td><td className="px-5 py-3 text-sm">{money(bucket.paid)}</td><td className={`px-5 py-3 text-sm font-semibold ${balance > 0 ? "text-red-600" : balance < 0 ? "text-amber-600" : "text-emerald-600"}`}>{money(bucket.balance)}</td>{canRecordVendor && <td className="whitespace-nowrap px-5 py-3 text-right"><button type="button" onClick={() => claimBucket(bucket)} className="mr-3 text-xs font-semibold text-violet-600 hover:underline">Enter claim</button><button type="button" onClick={() => payBucket(bucket)} className="text-xs font-semibold text-blue-600 hover:underline">Record payment</button></td>}</tr>; })}</tbody><tfoot><tr className="border-t-2 border-slate-300 bg-slate-50 text-sm font-bold"><td className="px-5 py-3">Total</td><td className="px-5 py-3">{vendorSummary.totals.tickets}</td><td className="px-5 py-3">{vendorSummary.totals.claimed}</td><td className="px-5 py-3">{vendorSummary.totals.unclaimed}</td><td className="px-5 py-3">{money(vendorSummary.totals.owed)}</td><td className="px-5 py-3">{money(vendorSummary.totals.paid)}</td><td className="px-5 py-3">{money(vendorSummary.totals.balance)}</td>{canRecordVendor && <td />}</tr></tfoot></table></ScrollArea>
          <div className="border-t border-slate-200 px-5 pb-1 pt-4 text-sm font-semibold text-slate-700">Claims in these dates</div>
          {vendorSummary.claims.length ? <ScrollArea maxHeight="40vh"><table className="w-full min-w-[900px] text-left"><thead className="text-xs font-semibold uppercase text-slate-500"><tr><th className="px-5 py-3">Dates</th><th className="px-5 py-3">Claimed</th><th className="px-5 py-3">Issued then</th><th className="px-5 py-3">Payable</th><th className="px-5 py-3">Not claimed</th><th className="px-5 py-3">Reference</th><th className="px-5 py-3">Entered by</th>{canRecordVendor && <th className="px-5 py-3" />}</tr></thead><tbody className="divide-y divide-slate-100">{vendorSummary.claims.map((claim) => <tr key={claim.id}><td className="px-5 py-3 text-sm text-slate-600">{claim.date_from === claim.date_to ? day(claim.date_from) : `${day(claim.date_from)} - ${day(claim.date_to)}`}</td><td className="px-5 py-3 text-sm font-semibold">{claim.quantity}{claim.over_claimed > 0 && <span className="block text-xs font-semibold text-red-600">{claim.over_claimed} more than were issued - not payable</span>}</td><td className="px-5 py-3 text-sm">{claim.issued}</td><td className="px-5 py-3 text-sm">{claim.payable}</td><td className={`px-5 py-3 text-sm ${claim.unclaimed > 0 ? "font-semibold text-violet-700" : ""}`}>{claim.unclaimed}</td><td className="px-5 py-3 text-sm text-slate-600">{claim.reference || "-"}</td><td className="px-5 py-3 text-sm text-slate-600">{claim.recorded_by_name || "-"}</td>{canRecordVendor && <td className="px-5 py-3 text-right"><button type="button" disabled={acting} onClick={() => void deleteVendorClaim(claim)} className="text-xs font-semibold text-red-600 hover:underline disabled:opacity-50">Delete</button></td>}</tr>)}</tbody></table></ScrollArea> : <Empty message="No claims entered for these dates. Until one is, each day is owed as issued." />}
          <div className="border-t border-slate-200 px-5 pb-1 pt-4 text-sm font-semibold text-slate-700">Payments in these dates</div>
          {vendorSummary.payments.length ? <ScrollArea maxHeight="40vh"><table className="w-full min-w-[800px] text-left"><thead className="text-xs font-semibold uppercase text-slate-500"><tr><th className="px-5 py-3">Amount</th><th className="px-5 py-3">Paid on</th><th className="px-5 py-3">Covers</th><th className="px-5 py-3">Reference</th><th className="px-5 py-3">Recorded By</th><th className="px-5 py-3">Notes</th></tr></thead><tbody className="divide-y divide-slate-100">{vendorSummary.payments.map((payment) => <tr key={payment.id}><td className="px-5 py-3 font-semibold">{money(payment.amount)}</td><td className="px-5 py-3 text-sm text-slate-600">{day(payment.payment_date)}</td><td className="px-5 py-3 text-sm text-slate-600">{payment.covers_from && payment.covers_to ? `${day(payment.covers_from)} - ${day(payment.covers_to)}` : "Its payment date"}</td><td className="px-5 py-3 text-sm text-slate-600">{payment.reference || "-"}</td><td className="px-5 py-3 text-sm text-slate-600">{payment.recorded_by_name || "-"}</td><td className="px-5 py-3 text-sm text-slate-600">{payment.notes || "-"}</td></tr>)}</tbody></table></ScrollArea> : <Empty message="No vendor payments in these dates." />}
        </>}
      </Section>}

      {tab === "vendor" && <div className="mt-6"><Section title="People who took extra tickets" subtitle="Everyone who took more tickets than they are entitled to in a day, for the dates above, and what became of the extra ones. Someone entitled to two who took two is not listed.">
        <div className="space-y-4 p-5">
          <Chips<"1" | "2"> options={[["1", "Any extra ticket"], ["2", "2 or more extra in a day"]]} value={String(multipleMin) as "1" | "2"} onChange={(value) => setMultipleMin(value === "2" ? 2 : 1)} />
          {!multiple ? <p className="text-sm text-slate-500">Loading...</p> : <>
            <div className="grid gap-4 sm:grid-cols-3"><MetricCard title="People" value={multiple.totals.people} subtitle="Different people who took an extra in these dates" accentColor="#7c3aed" /><MetricCard title="Person-days" value={multiple.totals.person_days} subtitle="Days someone took more than their entitlement" accentColor="#2563eb" /><MetricCard title="Extra tickets" value={multiple.totals.extra_tickets} subtitle="Tickets beyond entitlement" accentColor="#d97706" /></div>
            {multiple.days.length > 0 && <div className="flex flex-wrap gap-2">{multiple.days.map((entry) => <span key={entry.date} className="rounded-full border border-slate-200 bg-slate-50 px-3 py-1 text-xs font-semibold text-slate-700">{day(entry.date)}: {entry.people} {entry.people === 1 ? "person" : "people"}</span>)}</div>}
            {multiple.rows.length ? <ScrollArea maxHeight="45vh"><table className="w-full min-w-[900px] text-left"><thead className="text-xs font-semibold uppercase text-slate-500"><tr><th className="px-4 py-3">Employee</th><th className="px-4 py-3">Date</th><th className="px-4 py-3">Took</th><th className="px-4 py-3">Entitled to</th><th className="px-4 py-3">Extra</th><th className="px-4 py-3">Charged</th><th className="px-4 py-3">Waived</th><th className="px-4 py-3">Waiting</th>{canChargeBack && <th className="px-4 py-3" />}</tr></thead><tbody className="divide-y divide-slate-100">{multiple.rows.map((row) => <tr key={`${row.employee_number}-${row.work_date}`}><td className="px-4 py-3 text-sm font-medium">{row.employee_name}<span className="block text-xs font-normal text-slate-500">{row.employee_number}</span></td><td className="px-4 py-3 text-sm text-slate-600">{day(row.work_date)}</td><td className="px-4 py-3 text-sm">{row.tickets}</td><td className="px-4 py-3 text-sm">{row.entitled}</td><td className="px-4 py-3 text-sm font-semibold text-amber-700">{row.extra}</td><td className="px-4 py-3 text-sm">{row.charged}</td><td className="px-4 py-3 text-sm">{row.waived}</td><td className={`px-4 py-3 text-sm ${row.waiting ? "font-semibold text-amber-700" : ""}`}>{row.waiting}</td>{canChargeBack && <td className="px-4 py-3 text-right"><button type="button" onClick={() => chargeBackFrom(row)} className="text-xs font-semibold text-blue-600 hover:underline">Charge back</button></td>}</tr>)}</tbody></table></ScrollArea> : <p className="text-sm text-slate-500">Nobody took more than their entitlement in these dates.</p>}
            {multiple.truncated && <p className="text-xs text-slate-500">Showing the first 500 rows; narrow the dates to see the rest.</p>}
          </>}
        </div>
      </Section></div>}
      {tab === "vendor" && canChargeBack && <div className="mt-6"><Section title="Charge back meal tickets" subtitle="Charge an employee for tickets they have already taken - for example as a penalty for an offence, when taking the day's meal away is no longer possible. The vendor is still paid; the cost comes out of the employee's pay.">
        <form id="chargeback-form" onSubmit={submitChargeback} className="grid gap-3 border-b border-slate-200 p-5 md:grid-cols-4">
          <label className="text-xs font-semibold text-slate-600">Staff number<input required value={chargebackForm.employee_number} onChange={(event) => { setChargebackForm({ ...chargebackForm, employee_number: event.target.value }); setChargebackLookup(null); }} placeholder="e.g. 001361" className={`${inputClass} mt-1`} /></label>
          <label className="text-xs font-semibold text-slate-600">Date the tickets were taken<input required type="date" value={chargebackForm.work_date} onChange={(event) => { setChargebackForm({ ...chargebackForm, work_date: event.target.value }); setChargebackLookup(null); }} className={`${inputClass} mt-1`} /></label>
          <div className="flex items-end"><button type="button" onClick={() => void lookUpChargeback()} className="rounded-xl border border-slate-300 bg-white px-4 py-2.5 text-sm font-semibold text-slate-700">Check what they took</button></div>
          <div className="flex items-center text-sm">{chargebackLookup ? <span className={chargebackLookup.chargeable ? "text-slate-700" : "font-semibold text-red-600"}><b>{chargebackLookup.employee_name}</b>: took {chargebackLookup.taken}{chargebackLookup.already_charged ? `, ${chargebackLookup.already_charged} already charged as excess` : ""}{chargebackLookup.charged_back ? `, ${chargebackLookup.charged_back} already charged back` : ""} - <b>{chargebackLookup.chargeable}</b> can be charged back{chargebackLookup.rate ? ` at ${money(chargebackLookup.rate)} each` : ""}</span> : <span className="text-xs text-slate-500">{chargebackNote}</span>}</div>
          <label className="text-xs font-semibold text-slate-600">Tickets to charge<input required type="number" min="1" max={chargebackLookup?.chargeable || undefined} step="1" value={chargebackForm.quantity} onChange={(event) => setChargebackForm({ ...chargebackForm, quantity: event.target.value })} className={`${inputClass} mt-1`} /></label>
          <label className="text-xs font-semibold text-slate-600">Deduct in month (optional)<input type="month" value={chargebackForm.pay_month} onChange={(event) => setChargebackForm({ ...chargebackForm, pay_month: event.target.value })} className={`${inputClass} mt-1`} /></label>
          <label className="text-xs font-semibold text-slate-600 md:col-span-2">Offence reference (optional)<input value={chargebackForm.reference} onChange={(event) => setChargebackForm({ ...chargebackForm, reference: event.target.value })} className={`${inputClass} mt-1`} /></label>
          <label className="text-xs font-semibold text-slate-600 md:col-span-3">Reason<textarea required value={chargebackForm.reason} onChange={(event) => setChargebackForm({ ...chargebackForm, reason: event.target.value })} placeholder="The offence or reason for charging the meal back" className={`${inputClass} mt-1 min-h-16`} /></label>
          <div className="flex items-end"><button disabled={acting} className="w-full rounded-xl bg-red-600 px-4 py-2.5 text-sm font-semibold text-white disabled:opacity-50">Charge back</button></div>
          <p className="text-xs text-slate-500 md:col-span-4">It is deducted in the month the tickets were taken (or the month you choose). If that month&apos;s payroll has not been generated yet, it waits and is applied when it is. Leave the month empty unless that payroll is already closed.</p>
        </form>
        <div className="border-b border-slate-200 px-5 pb-1 pt-4 text-sm font-semibold text-slate-700">Charged back in these dates</div>
        {chargebacks.length ? <ScrollArea maxHeight="40vh"><table className="w-full min-w-[900px] text-left"><thead className="text-xs font-semibold uppercase text-slate-500"><tr><th className="px-4 py-3">Employee</th><th className="px-4 py-3">Ticket date</th><th className="px-4 py-3">Tickets</th><th className="px-4 py-3">Amount</th><th className="px-4 py-3">Reason</th><th className="px-4 py-3">Status</th><th className="px-4 py-3">By</th><th className="px-4 py-3" /></tr></thead><tbody className="divide-y divide-slate-100">{chargebacks.map((item) => <tr key={item.id}><td className="px-4 py-3 text-sm font-medium">{item.employee_name}<span className="block text-xs font-normal text-slate-500">{item.employee_number}</span></td><td className="px-4 py-3 text-sm text-slate-600">{day(item.work_date)}</td><td className="px-4 py-3 text-sm">{item.quantity}</td><td className="px-4 py-3 text-sm font-semibold">{money(item.amount)}</td><td className="max-w-[18rem] px-4 py-3 text-sm text-slate-600">{item.reason}{item.reference ? <span className="block text-xs text-slate-500">Ref: {item.reference}</span> : null}{item.status === "cancelled" && item.cancel_reason ? <span className="block text-xs text-slate-500">Cancelled: {item.cancel_reason}</span> : null}</td><td className="px-4 py-3 text-xs font-semibold text-slate-600">{chargebackStatusLabel[item.status]}<span className="block font-normal text-slate-500">{String(item.pay_month).padStart(2, "0")}/{item.pay_year} payroll</span></td><td className="px-4 py-3 text-sm text-slate-600">{item.created_by_name || "-"}</td><td className="px-4 py-3 text-right">{item.status !== "cancelled" && <button type="button" disabled={acting} onClick={() => void cancelChargeback(item)} className="text-xs font-semibold text-red-600 hover:underline disabled:opacity-50">Cancel</button>}</td></tr>)}</tbody></table></ScrollArea> : <Empty message="Nothing has been charged back in these dates." />}
      </Section></div>}

      {tab === "setup" && <>
        <Panel title="Entitlements" subtitle={`Employee-specific approved entitlement history - ${shownEntitlements.length} shown`} defaultOpen>
          {canConfigure && <AddNew label="+ Add an entitlement"><form onSubmit={createEntitlement} className="grid gap-3 border-b border-slate-200 p-5 md:grid-cols-2"><select required value={entitlementForm.employee} onChange={(event) => setEntitlementForm({ ...entitlementForm, employee: event.target.value })} className={inputClass}><option value="">Select employee</option>{employees.map((employee) => <option key={employee.id} value={employee.id}>{employee.employee_id} - {employee.full_name}</option>)}</select><input required type="number" min="1" step="1" value={entitlementForm.tickets_per_work_day} onChange={(event) => setEntitlementForm({ ...entitlementForm, tickets_per_work_day: event.target.value })} placeholder="Tickets per WORK day" className={inputClass} /><input required type="date" value={entitlementForm.effective_from} onChange={(event) => setEntitlementForm({ ...entitlementForm, effective_from: event.target.value })} className={inputClass} /><input type="date" value={entitlementForm.effective_to} onChange={(event) => setEntitlementForm({ ...entitlementForm, effective_to: event.target.value })} className={inputClass} /><textarea required value={entitlementForm.reason} onChange={(event) => setEntitlementForm({ ...entitlementForm, reason: event.target.value })} placeholder="Approval reason" className={`${inputClass} min-h-24 md:col-span-2`} />{currentUser?.is_superuser && <label className="flex items-center gap-3 text-sm font-semibold text-slate-700 md:col-span-2"><input type="checkbox" checked={entitlementForm.is_exceptional_override} onChange={(event) => setEntitlementForm({ ...entitlementForm, is_exceptional_override: event.target.checked })} />Exceptional Override</label>}<div className="text-right md:col-span-2"><button disabled={acting} className="rounded-xl bg-blue-600 px-4 py-2.5 text-sm font-semibold text-white">Create Entitlement</button></div></form></AddNew>}
          <div className="flex flex-wrap items-center gap-3 border-b border-slate-200 p-4"><SearchBox value={entitlementSearch} onChange={setEntitlementSearch} placeholder="Search by name" /><label className="flex items-center gap-2 text-sm text-slate-600"><input type="checkbox" checked={currentOnly} onChange={(event) => setCurrentOnly(event.target.checked)} />Current allocations only</label></div>
          {shownEntitlements.length ? <ScrollArea maxHeight="50vh"><table className="w-full min-w-[900px] text-left"><thead className="text-xs font-semibold uppercase text-slate-500"><tr><th className="px-5 py-3">Employee</th><th className="px-5 py-3">Tickets</th><th className="px-5 py-3">Effective From</th><th className="px-5 py-3">Effective To</th><th className="px-5 py-3">Override</th><th className="px-5 py-3">Reason</th></tr></thead><tbody className="divide-y divide-slate-100">{shownEntitlements.map((item) => <tr key={item.id}><td className="px-5 py-3 font-medium">{item.employee_name}</td><td className="px-5 py-3">{item.tickets_per_work_day}</td><td className="px-5 py-3 text-sm text-slate-600">{day(item.effective_from)}</td><td className="px-5 py-3 text-sm text-slate-600">{item.effective_to ? day(item.effective_to) : "Open ended"}</td><td className="px-5 py-3"><StatusBadge status={item.is_exceptional_override ? "approved" : "inactive"} /></td><td className="px-5 py-3 text-sm text-slate-600">{item.reason}</td></tr>)}</tbody></table></ScrollArea> : <Empty message="No employee-specific Meal entitlements found." />}
        </Panel>
        <Panel title="Entitlement Rules" subtitle="Suggested entitlement rules HR can edit. More specific rules (a position, then a category or type) are matched before general ones.">
          {canConfigure && <AddNew label="+ Add a rule"><form onSubmit={createRule} className="grid gap-3 border-b border-slate-200 p-5 md:grid-cols-3"><select value={ruleForm.employment_category} onChange={(event) => setRuleForm({ ...ruleForm, employment_category: event.target.value })} className={inputClass}><option value="">Any category</option>{employmentCategoryOptions.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select><select value={ruleForm.employment_type} onChange={(event) => setRuleForm({ ...ruleForm, employment_type: event.target.value })} className={inputClass}><option value="">Any employment type</option>{employmentTypeOptions.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select><select value={ruleForm.position} onChange={(event) => setRuleForm({ ...ruleForm, position: event.target.value })} className={inputClass}><option value="">Any position</option>{positions.map((position) => <option key={position.id} value={position.id}>{position.department_name} - {position.name}</option>)}</select><input type="number" min="0" step="1" value={ruleForm.minimum_months_of_service} onChange={(event) => setRuleForm({ ...ruleForm, minimum_months_of_service: event.target.value })} placeholder="Minimum months of service" className={inputClass} /><input required type="number" min="1" step="1" value={ruleForm.tickets_per_work_day} onChange={(event) => setRuleForm({ ...ruleForm, tickets_per_work_day: event.target.value })} placeholder="Tickets per WORK day" className={inputClass} /><input value={ruleForm.description} onChange={(event) => setRuleForm({ ...ruleForm, description: event.target.value })} placeholder="Description" className={`${inputClass} md:col-span-2`} /><div className="text-right"><button disabled={acting} className="rounded-xl bg-blue-600 px-4 py-2.5 text-sm font-semibold text-white">Add Rule</button></div></form></AddNew>}
          {rules.length ? <ScrollArea maxHeight="45vh"><table className="w-full min-w-[950px] text-left"><thead className="text-xs font-semibold uppercase text-slate-500"><tr><th className="px-5 py-3">Description</th><th className="px-5 py-3">Category</th><th className="px-5 py-3">Type</th><th className="px-5 py-3">Position</th><th className="px-5 py-3">Min. Months</th><th className="px-5 py-3">Tickets</th><th className="px-5 py-3">Status</th>{canConfigure && <th className="px-5 py-3">Actions</th>}</tr></thead><tbody className="divide-y divide-slate-100">{rules.map((rule) => <tr key={rule.id}><td className="px-5 py-3 font-medium">{rule.description || "-"}</td><td className="px-5 py-3 text-sm text-slate-600">{rule.employment_category || "Any"}</td><td className="px-5 py-3 text-sm text-slate-600">{rule.employment_type || "Any"}</td><td className="px-5 py-3 text-sm text-slate-600">{rule.position_name || "Any"}</td><td className="px-5 py-3 text-sm text-slate-600">{rule.minimum_months_of_service ?? "-"}</td><td className="px-5 py-3">{rule.tickets_per_work_day}</td><td className="px-5 py-3"><StatusBadge status={rule.active ? "active" : "inactive"} /></td>{canConfigure && <td className="px-5 py-3"><button type="button" disabled={acting} onClick={() => void toggleRule(rule)} className="rounded-lg border border-slate-300 px-3 py-2 text-xs font-semibold">{rule.active ? "Deactivate" : "Activate"}</button></td>}</tr>)}</tbody></table></ScrollArea> : <Empty message="No meal entitlement rules configured." />}
        </Panel>
        <Panel title="Rates" subtitle="Effective-dated Meal ticket rate history.">
          {canConfigure && <AddNew label="+ Add a rate"><form onSubmit={createRate} className="grid gap-3 border-b border-slate-200 p-5 md:grid-cols-4"><input required type="number" min="0.01" step="0.01" value={rateForm.amount} onChange={(event) => setRateForm({ ...rateForm, amount: event.target.value })} placeholder="Amount (NGN)" className={inputClass} /><input required type="date" value={rateForm.effective_from} onChange={(event) => setRateForm({ ...rateForm, effective_from: event.target.value })} className={inputClass} /><input type="date" value={rateForm.effective_to} onChange={(event) => setRateForm({ ...rateForm, effective_to: event.target.value })} className={inputClass} /><button disabled={acting} className="rounded-xl bg-blue-600 px-4 py-2.5 text-sm font-semibold text-white">Create Rate</button></form></AddNew>}
          {rates.length ? <ScrollArea maxHeight="35vh"><table className="w-full min-w-[650px] text-left"><thead className="text-xs font-semibold uppercase text-slate-500"><tr><th className="px-5 py-3">Amount</th><th className="px-5 py-3">Effective From</th><th className="px-5 py-3">Effective To</th><th className="px-5 py-3">Status</th></tr></thead><tbody className="divide-y divide-slate-100">{rates.map((rate) => <tr key={rate.id}><td className="px-5 py-3 font-semibold">{money(rate.amount)}</td><td className="px-5 py-3 text-sm text-slate-600">{day(rate.effective_from)}</td><td className="px-5 py-3 text-sm text-slate-600">{rate.effective_to ? day(rate.effective_to) : "Open ended"}</td><td className="px-5 py-3"><StatusBadge status={rate.active ? "active" : "inactive"} /></td></tr>)}</tbody></table></ScrollArea> : <Empty message="No Meal ticket rates found." />}
        </Panel>
        <Panel title="Devices" subtitle="Registered Meal collection devices. Add or edit these from the Biometric Devices page (Attendance → Biometrics → Devices) with Purpose set to Meal Ticket."><DeviceTable devices={devices} /></Panel>
      </>}
    </>}
  </main>
  {canReview && bulkKind !== null && <div className="fixed inset-0 z-30 flex items-center justify-center bg-slate-950/40 p-4"><div className="w-full max-w-lg rounded-2xl bg-white p-6 shadow-xl"><h2 className="text-xl font-bold">{bulkKind === "accept" ? "Accept" : bulkKind === "waive" ? "Waive" : "Decline"} selected excess</h2><p className="mt-2 text-sm text-slate-600">{bulkText}</p>{bulkKind !== "accept" && <textarea autoFocus disabled={acting} value={bulkReason} onChange={(event) => setBulkReason(event.target.value)} className="mt-5 min-h-24 w-full rounded-xl border border-slate-300 p-3 text-sm disabled:opacity-50" placeholder="Reason (optional, applied to every ticket)" />}{bulkProgress && <div className="mt-5"><div className="h-2 w-full overflow-hidden rounded-full bg-slate-100"><div className="h-full rounded-full bg-blue-600 transition-all" style={{ width: `${Math.round((bulkProgress.done / bulkProgress.total) * 100)}%` }} /></div><p className="mt-2 text-xs text-slate-500">Processing {bulkProgress.done} of {bulkProgress.total}...</p></div>}<div className="mt-5 flex justify-end gap-3"><button type="button" disabled={acting} onClick={() => { setBulkKind(null); setBulkReason(""); }} className="rounded-xl border border-slate-300 px-4 py-2.5 text-sm font-semibold disabled:opacity-50">Back</button><button type="button" disabled={acting} onClick={() => void runBulk()} className={`rounded-xl px-4 py-2.5 text-sm font-semibold text-white disabled:opacity-70 ${bulkKind === "accept" ? "bg-emerald-600" : "bg-red-600"}`}>{acting ? "Working..." : `Confirm (${bulkCount})`}</button></div></div></div>}
  {canReview && quickReviewOpen && <div className="fixed inset-0 z-40 flex items-center justify-center bg-slate-950/50 p-4">
    <div className="w-full max-w-xl rounded-2xl bg-white shadow-xl">
      <div className="flex items-center justify-between border-b border-slate-200 px-6 py-4">
        <div><h2 className="text-lg font-bold">⚡ Quick Review</h2><p className="text-xs text-slate-500">{quickReviewDecided} decided this session · {quickReviewRemaining} remaining · keys: A accept, W waive, D decline, Esc close</p></div>
        <button type="button" onClick={closeQuickReview} className="rounded-lg border border-slate-300 px-3 py-1.5 text-sm font-semibold text-slate-700">Close</button>
      </div>
      <div className="p-6">
        {quickReviewBusy && !quickReviewCase && !quickReviewDone ? <p className="py-10 text-center text-sm text-slate-500">Loading...</p>
          : quickReviewDone || !quickReviewCase ? <div className="py-10 text-center"><p className="text-lg font-semibold text-slate-900">All done!</p><p className="mt-1 text-sm text-slate-500">Nothing is waiting for a decision for these filters.</p><button type="button" onClick={closeQuickReview} className="mt-5 rounded-xl bg-blue-600 px-4 py-2.5 text-sm font-semibold text-white">Back to the table</button></div>
          : <>
            <div className="rounded-xl border border-slate-200 p-5">
              <div className="flex items-start justify-between gap-3">
                <div><p className="text-xl font-bold text-slate-900">{quickReviewCase.employee_name}</p><p className="text-sm text-slate-500">{quickReviewCase.employee_number}</p></div>
                <span className="text-sm text-slate-500">{day(quickReviewCase.work_date)}</span>
              </div>
              {quickReviewCase.card_verified && <span className="mt-3 inline-block rounded-full border border-red-300 bg-red-50 px-2 py-0.5 text-xs font-semibold text-red-700">Card scan - gating bypassed</span>}
              <div className="mt-4 grid grid-cols-4 gap-3 text-center">
                <div className="rounded-lg bg-slate-50 p-3"><p className="text-xl font-bold">{quickReviewCase.entitlement}</p><p className="text-xs text-slate-500">Entitlement</p></div>
                <div className="rounded-lg bg-slate-50 p-3"><p className="text-xl font-bold">{quickReviewCase.collected_quantity}</p><p className="text-xs text-slate-500">Collected</p></div>
                <div className="rounded-lg bg-slate-50 p-3"><p className="text-xl font-bold">{quickReviewCase.excess_quantity}</p><p className="text-xs text-slate-500">Excess</p></div>
                <div className="rounded-lg bg-amber-50 p-3"><p className="text-xl font-bold text-amber-800">{money(quickReviewCase.proposed_deduction)}</p><p className="text-xs text-amber-700">Deduction</p></div>
              </div>
            </div>
            {quickReviewError && <p className="mt-4 rounded-xl bg-red-50 p-3 text-sm text-red-700">{quickReviewError}</p>}
            {quickReviewReasonKind ? <div className="mt-5">
              <textarea autoFocus disabled={quickReviewBusy} value={quickReviewReason} onChange={(event) => setQuickReviewReason(event.target.value)} className="min-h-24 w-full rounded-xl border border-slate-300 p-3 text-sm disabled:opacity-50" placeholder="Reason (optional)" />
              <div className="mt-3 flex justify-end gap-3">
                <button type="button" disabled={quickReviewBusy} onClick={() => { setQuickReviewReasonKind(null); setQuickReviewReason(""); }} className="rounded-xl border border-slate-300 px-4 py-2.5 text-sm font-semibold disabled:opacity-50">Back</button>
                <button type="button" disabled={quickReviewBusy} onClick={() => void decideQuickReview(quickReviewReasonKind)} className={`rounded-xl px-4 py-2.5 text-sm font-semibold text-white disabled:opacity-70 ${quickReviewReasonKind === "waive" ? "bg-slate-700" : "bg-red-600"}`}>{quickReviewBusy ? "Working..." : `Confirm ${quickReviewReasonKind === "waive" ? "Waive" : "Decline"}`}</button>
              </div>
            </div> : <div className="mt-5 grid grid-cols-3 gap-3">
              <button type="button" disabled={quickReviewBusy} onClick={() => void decideQuickReview("accept")} className="rounded-xl bg-emerald-600 py-3 text-sm font-semibold text-white disabled:opacity-50">Accept <span className="opacity-70">(A)</span></button>
              <button type="button" disabled={quickReviewBusy} onClick={() => void decideQuickReview("waive")} className="rounded-xl border border-slate-300 bg-white py-3 text-sm font-semibold text-slate-700 disabled:opacity-50">Waive <span className="opacity-60">(W)</span></button>
              <button type="button" disabled={quickReviewBusy} onClick={() => void decideQuickReview("decline")} className="rounded-xl border border-red-200 bg-white py-3 text-sm font-semibold text-red-700 disabled:opacity-50">Decline <span className="opacity-60">(D)</span></button>
            </div>}
          </>}
      </div>
    </div>
  </div>}
  {canReview && declineId !== null && <div className="fixed inset-0 z-30 flex items-center justify-center bg-slate-950/40 p-4"><div className="w-full max-w-lg rounded-2xl bg-white p-6 shadow-xl"><h2 className="text-xl font-bold">Decline Meal Excess</h2><p className="mt-2 text-sm text-slate-500">The ticket beyond this employee&apos;s entitlement is rejected: the vendor is not billed for it and nothing is deducted from the employee. Adding a reason is optional.</p><textarea autoFocus value={declineReason} onChange={(event) => setDeclineReason(event.target.value)} className="mt-5 min-h-24 w-full rounded-xl border border-slate-300 p-3 text-sm" placeholder="Reason (optional)" /><div className="mt-5 flex justify-end gap-3"><button type="button" onClick={() => { setDeclineId(null); setDeclineReason(""); }} className="rounded-xl border border-slate-300 px-4 py-2.5 text-sm font-semibold">Back</button><button type="button" disabled={acting} onClick={() => void declineExcess()} className="rounded-xl bg-red-600 px-4 py-2.5 text-sm font-semibold text-white">Decline Excess</button></div></div></div>}
  {canReview && voidId !== null && <div className="fixed inset-0 z-30 flex items-center justify-center bg-slate-950/40 p-4"><div className="w-full max-w-lg rounded-2xl bg-white p-6 shadow-xl"><h2 className="text-xl font-bold">Void Meal Ticket</h2><p className="mt-2 text-sm text-slate-500">Use this for a test or accidental scan. The ticket stays on record but stops counting toward the vendor total and the employee&apos;s daily tickets. Adding a reason is optional.</p><textarea autoFocus value={voidReason} onChange={(event) => setVoidReason(event.target.value)} className="mt-5 min-h-24 w-full rounded-xl border border-slate-300 p-3 text-sm" placeholder="Reason (optional), e.g. Test scan" /><div className="mt-5 flex justify-end gap-3"><button type="button" onClick={() => { setVoidId(null); setVoidReason(""); }} className="rounded-xl border border-slate-300 px-4 py-2.5 text-sm font-semibold">Back</button><button type="button" disabled={acting} onClick={() => void voidTicket()} className="rounded-xl bg-red-600 px-4 py-2.5 text-sm font-semibold text-white">Void Ticket</button></div></div></div>}
  {canReview && cancelId !== null && <div className="fixed inset-0 z-30 flex items-center justify-center bg-slate-950/40 p-4"><div className="w-full max-w-lg rounded-2xl bg-white p-6 shadow-xl"><h2 className="text-xl font-bold">Waive Meal Excess</h2><p className="mt-2 text-sm text-slate-500">No deduction is made from the employee and the vendor is still paid for the ticket - the company absorbs it. Adding a reason is optional.</p><textarea autoFocus value={reason} onChange={(event) => setReason(event.target.value)} className="mt-5 min-h-32 w-full rounded-xl border border-slate-300 p-3 text-sm" placeholder="Reason (optional)" /><div className="mt-5 flex justify-end gap-3"><button type="button" onClick={() => { setCancelId(null); setReason(""); }} className="rounded-xl border border-slate-300 px-4 py-2.5 text-sm font-semibold">Back</button><button type="button" disabled={acting} onClick={() => void cancel()} className="rounded-xl bg-red-600 px-4 py-2.5 text-sm font-semibold text-white">Waive Excess</button></div></div></div>}</div>;
}

function AddNew({ label, children }: { label: string; children: ReactNode }) {
  return <details className="border-b border-slate-200"><summary className="cursor-pointer list-none px-5 py-3 text-sm font-semibold text-blue-700 hover:bg-slate-50">{label}</summary>{children}</details>;
}

function Empty({ message }: { message: string }) {
  return <p className="p-10 text-center text-slate-500">{message}</p>;
}

function DeviceTable({ devices }: { devices: Device[] }) {
  if (!devices.length) return <Empty message="No Meal devices registered yet. Add one from the Biometric Devices page." />;
  return <div className="overflow-x-auto"><table className="w-full min-w-[500px] text-left"><thead className="bg-slate-50 text-xs font-semibold uppercase text-slate-500"><tr><th className="px-5 py-3">Name</th><th className="px-5 py-3">Serial Number</th><th className="px-5 py-3">Status</th></tr></thead><tbody className="divide-y divide-slate-100">{devices.map((device) => <tr key={device.id}><td className="px-5 py-4 font-medium">{device.name}</td><td className="px-5 py-4 font-mono text-sm text-slate-600">{device.serial_number}</td><td className="px-5 py-4"><StatusBadge status={device.active ? "active" : "inactive"} /></td></tr>)}</tbody></table></div>;
}

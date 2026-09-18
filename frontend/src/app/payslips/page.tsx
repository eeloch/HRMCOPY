"use client";

import { useEffect, useEffectEvent, useState } from "react";
import { useRouter } from "next/navigation";

import Sidebar from "@/components/Sidebar";
import type { ShiftEmployee } from "@/components/shifts/types";
import { PayslipDocument, type PayslipData } from "@/components/payroll/PayslipDocument";
import { AppCard, PageHeader, Section, StatusBadge } from "@/components/ui";
import { apiFetch, getAccessToken } from "@/lib/api";

type PayslipListItem = {
  id: number;
  employee_id: string;
  employee_name: string;
  department_name: string | null;
  payroll_period_name: string;
  basic_salary: string;
  gross_earnings: string;
  total_deductions: string;
  net_pay: string;
  status: string;
  generated_at: string;
};

function money(value: string) {
  return `₦${Number(value || 0).toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
}

export default function PayslipsPage() {
  const router = useRouter();
  const [employees, setEmployees] = useState<ShiftEmployee[]>([]);
  const [selectedEmployee, setSelectedEmployee] = useState("");
  const [query, setQuery] = useState("");
  const [payslips, setPayslips] = useState<PayslipListItem[]>([]);
  const [detail, setDetail] = useState<PayslipData | null>(null);
  const [loading, setLoading] = useState(false);
  const [loadingDetail, setLoadingDetail] = useState(false);
  const [error, setError] = useState("");

  // /payslips?employee=ID&payroll=ID (the Payslip button on the payroll period page) opens that payslip directly.
  const openFromLink = useEffectEvent(() => {
    const link = new URLSearchParams(window.location.search);
    const employeeId = link.get("employee");
    if (!employeeId) return;
    void loadPayslips(employeeId);
    const payrollId = Number(link.get("payroll"));
    if (payrollId) void viewPayslip(payrollId);
  });

  useEffect(() => {
    if (!getAccessToken()) {
      router.push("/login");
      return;
    }
    const timer = window.setTimeout(async () => {
      const response = await apiFetch("/employees/");
      if (response.ok) {
        const data = await response.json();
        setEmployees(data.results || []);
        openFromLink();
      }
    }, 0);
    return () => window.clearTimeout(timer);
  }, [router]);

  async function loadPayslips(employeeId: string) {
    setSelectedEmployee(employeeId);
    setDetail(null);
    setPayslips([]);
    setError("");
    if (!employeeId) return;
    setLoading(true);
    try {
      const response = await apiFetch(`/payroll/employee-payrolls/?employee=${employeeId}`);
      if (!response.ok) throw new Error(response.status === 403 ? "You don't have permission to view payslips." : "Unable to load payslip history.");
      const data = await response.json();
      setPayslips(data.results || []);
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "Unable to load payslip history.");
    } finally {
      setLoading(false);
    }
  }

  async function viewPayslip(payrollId: number) {
    setLoadingDetail(true);
    setError("");
    try {
      const response = await apiFetch(`/payroll/employee-payrolls/${payrollId}/`);
      if (!response.ok) throw new Error("Unable to load this payslip.");
      setDetail(await response.json());
    } catch (detailError) {
      setError(detailError instanceof Error ? detailError.message : "Unable to load this payslip.");
    } finally {
      setLoadingDetail(false);
    }
  }

  return (
    <div className="min-h-screen bg-slate-100">
      <Sidebar />
      <main className="ml-64 min-w-0 p-4 md:p-8 print:ml-0 print:p-0">
        <div className="print:hidden">
          <PageHeader title="Payslips" description="Select an employee to view their payslip history." />

          <AppCard className="mb-6">
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Search by name or staff number..."
              className="mb-4 w-full max-w-md rounded-xl border border-slate-300 px-3 py-2.5 text-sm outline-none focus:ring-2 focus:ring-blue-500"
            />
            <label className="text-sm font-semibold text-slate-700">
              Employee
              <select
                value={selectedEmployee}
                onChange={(event) => void loadPayslips(event.target.value)}
                className="mt-2 w-full max-w-md rounded-xl border border-slate-300 bg-white px-3 py-2.5 font-normal"
              >
                <option value="">Select an employee</option>
                {employees
                  .filter((employee) => String(employee.id) === selectedEmployee || `${employee.full_name} ${employee.employee_id}`.toLowerCase().includes(query.trim().toLowerCase()))
                  .map((employee) => (
                    <option key={employee.id} value={employee.id}>{employee.full_name} ({employee.employee_id})</option>
                  ))}
              </select>
            </label>
          </AppCard>

          {error && (
            <AppCard className="mb-6 border-red-200 bg-red-50">
              <p className="text-sm text-red-700">{error}</p>
            </AppCard>
          )}

          {selectedEmployee && (
            <Section className="mb-6" title="Payslip History" subtitle={`${payslips.length} record${payslips.length === 1 ? "" : "s"}`}>
              {loading ? (
                <div className="space-y-3 p-6">{Array.from({ length: 3 }).map((_, index) => <div key={index} className="h-14 animate-pulse rounded-xl bg-slate-100" />)}</div>
              ) : payslips.length ? (
                <div className="overflow-x-auto">
                  <table className="w-full min-w-[720px] text-left">
                    <thead className="bg-slate-50 text-xs font-semibold uppercase tracking-wide text-slate-500">
                      <tr>
                        <th className="px-5 py-3">Period</th>
                        <th className="px-5 py-3">Status</th>
                        <th className="px-5 py-3">Gross</th>
                        <th className="px-5 py-3">Deductions</th>
                        <th className="px-5 py-3">Net Pay</th>
                        <th className="px-5 py-3" />
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-100">
                      {payslips.map((payslip) => (
                        <tr key={payslip.id} className="hover:bg-slate-50">
                          <td className="px-5 py-4 font-semibold text-slate-900">{payslip.payroll_period_name}</td>
                          <td className="px-5 py-4"><StatusBadge status={payslip.status} /></td>
                          <td className="px-5 py-4 text-slate-700">{money(payslip.gross_earnings)}</td>
                          <td className="px-5 py-4 text-slate-700">{money(payslip.total_deductions)}</td>
                          <td className="px-5 py-4 font-semibold text-slate-900">{money(payslip.net_pay)}</td>
                          <td className="px-5 py-4 text-right">
                            <button type="button" onClick={() => void viewPayslip(payslip.id)} className="rounded-xl bg-slate-900 px-3 py-2 text-sm font-semibold text-white hover:bg-slate-700">View</button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <p className="p-12 text-center text-slate-500">No payslips found for this employee yet.</p>
              )}
            </Section>
          )}
        </div>

        {loadingDetail && <p className="print:hidden text-slate-500">Loading payslip...</p>}

        {detail && (
          <div>
            <div className="mb-4 flex items-center justify-between print:hidden">
              <p className="text-sm text-slate-500">{detail.employee_name} ({detail.employee_id}) - {detail.payroll_period.display_name}</p>
              <button type="button" onClick={() => window.print()} className="rounded-xl bg-slate-900 px-4 py-2.5 text-sm font-semibold text-white hover:bg-slate-700">Print / Save PDF</button>
            </div>
            <PayslipDocument payslip={detail} />
          </div>
        )}
      </main>
    </div>
  );
}

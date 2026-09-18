export type PayslipLineItem = { id: number; item_type: string; code: string; description: string; amount: string };

export type PayslipData = {
  id: number;
  employee_id: string;
  employee_name: string;
  department_name: string | null;
  position_name?: string | null;
  employment_date?: string | null;
  bank_name?: string;
  account_number_masked?: string;
  basic_salary: string;
  gross_earnings: string;
  total_deductions: string;
  net_pay: string;
  status: string;
  payroll_period: { display_name: string; year: number; month: number };
  line_items: PayslipLineItem[];
};

const COMPANY_NAME = "Rotic Aluminium";

function naira(value: string | number) {
  return `₦${Number(value || 0).toLocaleString("en-NG", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function formatDate(value?: string | null) {
  if (!value) return "-";
  return new Date(value).toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "numeric" });
}

function Field({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-[10px] font-semibold uppercase tracking-wider text-slate-500">{label}</dt>
      <dd className="mt-0.5 text-sm font-semibold text-slate-900">{value || "-"}</dd>
    </div>
  );
}

function Column({ title, rows, total, totalLabel }: { title: string; rows: { key: string; label: string; amount: string }[]; total: string; totalLabel: string }) {
  return (
    <div className="flex flex-col border border-slate-300">
      <h3 className="border-b border-slate-300 bg-slate-100 px-4 py-2 text-[11px] font-bold uppercase tracking-wider text-slate-700">{title}</h3>
      <table className="w-full flex-1 text-sm">
        <tbody>
          {rows.length ? rows.map((row) => (
            <tr key={row.key} className="border-b border-slate-100 align-top">
              <td className="px-4 py-2 text-slate-800">{row.label}</td>
              <td className="whitespace-nowrap px-4 py-2 text-right tabular-nums text-slate-900">{naira(row.amount)}</td>
            </tr>
          )) : (
            <tr><td className="px-4 py-3 text-slate-400" colSpan={2}>None this month</td></tr>
          )}
        </tbody>
      </table>
      <div className="flex items-center justify-between border-t border-slate-300 bg-slate-50 px-4 py-2 text-sm font-bold text-slate-900">
        <span>{totalLabel}</span>
        <span className="tabular-nums">{naira(total)}</span>
      </div>
    </div>
  );
}

export function PayslipDocument({ payslip }: { payslip: PayslipData }) {
  const earnings = [
    { key: "basic", label: "Basic Salary", amount: payslip.basic_salary },
    ...payslip.line_items.filter((item) => item.item_type === "earning").map((item) => ({ key: String(item.id), label: item.description || item.code, amount: item.amount })),
  ];
  const deductions = payslip.line_items.filter((item) => item.item_type === "deduction").map((item) => ({ key: String(item.id), label: item.description || item.code, amount: item.amount }));

  return (
    <article className="payslip-sheet mx-auto w-full max-w-[210mm] border border-slate-300 bg-white text-slate-900 shadow-sm print:max-w-none print:border-0 print:shadow-none">
      <header className="payslip-band flex items-end justify-between bg-slate-900 px-8 py-6 text-white">
        <div>
          <p className="text-2xl font-bold tracking-wide">{COMPANY_NAME.toUpperCase()}</p>
          <p className="mt-1 text-xs text-slate-300">Human Resources &amp; Payroll</p>
        </div>
        <div className="text-right">
          <p className="text-xl font-semibold tracking-widest">PAYSLIP</p>
          <p className="mt-1 text-sm text-slate-300">{payslip.payroll_period.display_name}</p>
        </div>
      </header>

      <div className="space-y-6 px-8 py-6">
        <dl className="grid grid-cols-2 gap-x-6 gap-y-4 border border-slate-300 p-4 sm:grid-cols-3">
          <Field label="Employee Name" value={payslip.employee_name} />
          <Field label="Staff Number" value={payslip.employee_id} />
          <Field label="Pay Period" value={payslip.payroll_period.display_name} />
          <Field label="Department" value={payslip.department_name || ""} />
          <Field label="Position" value={payslip.position_name || ""} />
          <Field label="Date Employed" value={formatDate(payslip.employment_date)} />
          <Field label="Bank" value={payslip.bank_name || ""} />
          <Field label="Account Number" value={payslip.account_number_masked || ""} />
        </dl>

        <div className="grid gap-4 sm:grid-cols-2">
          <Column title="Earnings" rows={earnings} total={payslip.gross_earnings} totalLabel="Gross Earnings" />
          <Column title="Deductions" rows={deductions} total={payslip.total_deductions} totalLabel="Total Deductions" />
        </div>

        <div className="payslip-band flex items-center justify-between bg-slate-900 px-6 py-4 text-white">
          <div>
            <p className="text-[11px] font-semibold uppercase tracking-widest text-slate-300">Net Pay</p>
            <p className="mt-0.5 text-xs text-slate-400">Gross Earnings less Total Deductions</p>
          </div>
          <p className="text-3xl font-bold tabular-nums">{naira(payslip.net_pay)}</p>
        </div>

        <footer className="border-t border-slate-200 pt-3 text-center text-[11px] text-slate-500">
          This is a computer-generated payslip and does not require a signature. Please report any discrepancy to the HR department.
        </footer>
      </div>
    </article>
  );
}

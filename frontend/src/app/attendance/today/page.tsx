"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";

import Sidebar from "@/components/Sidebar";
import { apiFetch, getAccessToken } from "@/lib/api";

type AttendanceRecord = {
  id: number;
  employee_id: string;
  employee_name: string;
  shift_name: string | null;
  actual_clock_in: string | null;
  actual_clock_out: string | null;
  late_minutes: number;
  status: string;
};

type EmployeeDirectoryRecord = {
  id: number;
  employee_id: string;
  department_name: string | null;
  position_name: string | null;
};

type AttendanceResponse = {
  date: string;
  count: number;
  results: AttendanceRecord[];
};

type RegisterRecord = AttendanceRecord & {
  employeePk: number | null;
  department: string | null;
  position: string | null;
};

const statusOptions = ["present", "late", "absent", "leave", "leave_punch_conflict"];

export default function TodayAttendancePage() {
  const router = useRouter();
  const [records, setRecords] = useState<RegisterRecord[]>([]);
  const [date, setDate] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [search, setSearch] = useState("");
  const [department, setDepartment] = useState("");
  const [shift, setShift] = useState("");
  const [status, setStatus] = useState("");

  useEffect(() => {
    if (!getAccessToken()) {
      router.push("/login");
      return;
    }

    void loadRegister();
  }, [router]);

  async function loadRegister() {
    setLoading(true);
    setError("");

    try {
      const [attendanceResponse, employeesResponse] = await Promise.all([
        apiFetch("/attendance/today/"),
        apiFetch("/employees/"),
      ]);

      if (!attendanceResponse.ok) {
        throw new Error("Unable to load attendance records.");
      }

      if (!employeesResponse.ok) {
        throw new Error("Unable to load employee details.");
      }

      const attendance: AttendanceResponse = await attendanceResponse.json();
      const employeeData: { results?: EmployeeDirectoryRecord[] } =
        await employeesResponse.json();
      const employeeByNumber = new Map(
        (employeeData.results || []).map((employee) => [employee.employee_id, employee])
      );

      setDate(attendance.date);
      setRecords(
        attendance.results.map((record) => {
          const employee = employeeByNumber.get(record.employee_id);

          return {
            ...record,
            employeePk: employee?.id || null,
            department: employee?.department_name || null,
            position: employee?.position_name || null,
          };
        })
      );
    } catch (loadError) {
      console.error(loadError);
      setError(
        loadError instanceof Error
          ? loadError.message
          : "Unable to load attendance records."
      );
    } finally {
      setLoading(false);
    }
  }

  const departments = Array.from(
    new Set(records.map((record) => record.department).filter((value): value is string => Boolean(value)))
  ).sort();
  const shifts = Array.from(
    new Set(records.map((record) => record.shift_name).filter((value): value is string => Boolean(value)))
  ).sort();
  const searchTerm = search.trim().toLowerCase();
  const filteredRecords = records.filter((record) => {
    const matchesSearch =
      !searchTerm ||
      record.employee_name.toLowerCase().includes(searchTerm) ||
      record.employee_id.toLowerCase().includes(searchTerm);
    const matchesDepartment = !department || record.department === department;
    const matchesShift = !shift || record.shift_name === shift;
    const matchesStatus = !status || record.status === status;

    return matchesSearch && matchesDepartment && matchesShift && matchesStatus;
  });

  function clearFilters() {
    setSearch("");
    setDepartment("");
    setShift("");
    setStatus("");
  }

  return (
    <div className="min-h-screen bg-slate-100">
      <Sidebar />

      <main className="ml-64 min-w-0 p-4 md:p-8">
        <div className="mb-8 flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
          <div>
            <p className="text-sm font-semibold uppercase tracking-wider text-blue-600">
              Attendance
            </p>
            <h1 className="mt-1 text-3xl font-bold text-slate-900">
              Today&apos;s Attendance Register
            </h1>
            <p className="mt-2 text-slate-500">
              {date ? `Attendance records for ${formatDate(date)}.` : "Review today&apos;s attendance records."}
            </p>
          </div>

          {!loading && !error && (
            <button
              type="button"
              onClick={() => void loadRegister()}
              className="rounded-xl border border-slate-300 bg-white px-4 py-2.5 text-sm font-medium text-slate-700 shadow-sm hover:bg-slate-50"
            >
              Refresh
            </button>
          )}
        </div>

        {error ? (
          <div className="rounded-2xl border border-red-200 bg-red-50 p-6 text-red-700">
            <p className="font-semibold">Unable to load attendance records</p>
            <p className="mt-1 text-sm">{error}</p>
            <button
              type="button"
              onClick={() => void loadRegister()}
              className="mt-4 rounded-xl bg-red-600 px-4 py-2.5 text-sm font-semibold text-white hover:bg-red-700"
            >
              Try Again
            </button>
          </div>
        ) : (
          <section className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">
            <div className="border-b border-slate-200 p-5">
              <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-5">
                <input
                  type="search"
                  value={search}
                  onChange={(event) => setSearch(event.target.value)}
                  placeholder="Search employee name or ID..."
                  className="rounded-xl border border-slate-300 px-4 py-3 text-slate-900 outline-none focus:ring-2 focus:ring-blue-500 xl:col-span-2"
                />
                <FilterSelect value={department} onChange={setDepartment} label="All departments">
                  {departments.map((item) => (
                    <option key={item} value={item}>
                      {item}
                    </option>
                  ))}
                </FilterSelect>
                <FilterSelect value={shift} onChange={setShift} label="All shifts">
                  {shifts.map((item) => (
                    <option key={item} value={item}>
                      {item}
                    </option>
                  ))}
                </FilterSelect>
                <FilterSelect value={status} onChange={setStatus} label="All statuses">
                  {statusOptions.map((item) => (
                    <option key={item} value={item}>
                      {capitalize(item)}
                    </option>
                  ))}
                </FilterSelect>
              </div>

              <div className="mt-3 flex items-center justify-between gap-3">
                <p className="text-sm text-slate-500">
                  {!loading && `${filteredRecords.length} record${filteredRecords.length === 1 ? "" : "s"}`}
                </p>
                <button
                  type="button"
                  onClick={clearFilters}
                  className="rounded-xl border border-slate-300 px-4 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50"
                >
                  Clear Filters
                </button>
              </div>
            </div>

            {loading ? (
              <RegisterSkeleton />
            ) : filteredRecords.length ? (
              <div className="overflow-x-auto">
                <table className="w-full min-w-[1120px] text-left">
                  <thead className="bg-slate-50 text-xs font-semibold uppercase tracking-wider text-slate-500">
                    <tr>
                      <th className="px-5 py-3">Employee ID</th>
                      <th className="px-5 py-3">Employee</th>
                      <th className="px-5 py-3">Department</th>
                      <th className="px-5 py-3">Position</th>
                      <th className="px-5 py-3">Shift</th>
                      <th className="px-5 py-3">Clock In</th>
                      <th className="px-5 py-3">Clock Out</th>
                      <th className="px-5 py-3">Late Minutes</th>
                      <th className="px-5 py-3">Status</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-100">
                    {filteredRecords.map((record) => (
                      <tr key={record.id} className="hover:bg-slate-50/80">
                        <td className="px-5 py-4 text-sm font-medium text-slate-700">
                          {record.employee_id}
                        </td>
                        <td className="px-5 py-4">
                          {record.employeePk ? (
                            <button
                              type="button"
                              onClick={() => router.push(`/employees/${record.employeePk}`)}
                              className="font-semibold text-blue-700 hover:text-blue-800 hover:underline"
                            >
                              {record.employee_name}
                            </button>
                          ) : (
                            <span className="font-semibold text-slate-900">
                              {record.employee_name}
                            </span>
                          )}
                        </td>
                        <td className="px-5 py-4 text-sm text-slate-600">
                          {record.department || "Not assigned"}
                        </td>
                        <td className="px-5 py-4 text-sm text-slate-600">
                          {record.position || "Not assigned"}
                        </td>
                        <td className="px-5 py-4 text-sm text-slate-600">
                          {record.shift_name || "No shift"}
                        </td>
                        <td className="px-5 py-4 text-sm text-slate-600">
                          {formatTime(record.actual_clock_in)}
                        </td>
                        <td className="px-5 py-4 text-sm text-slate-600">
                          {formatTime(record.actual_clock_out)}
                        </td>
                        <td className="px-5 py-4 text-sm text-slate-600">
                          {record.late_minutes || 0}
                        </td>
                        <td className="px-5 py-4">
                          <StatusBadge status={record.status} />
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <div className="p-12 text-center text-slate-500">
                No attendance records found.
              </div>
            )}
          </section>
        )}
      </main>
    </div>
  );
}

function FilterSelect({
  value,
  onChange,
  label,
  children,
}: {
  value: string;
  onChange: (value: string) => void;
  label: string;
  children: React.ReactNode;
}) {
  return (
    <select
      value={value}
      onChange={(event) => onChange(event.target.value)}
      className="rounded-xl border border-slate-300 bg-white px-4 py-3 text-slate-900 outline-none focus:ring-2 focus:ring-blue-500"
    >
      <option value="">{label}</option>
      {children}
    </select>
  );
}

function StatusBadge({ status }: { status: string }) {
  const classes: Record<string, string> = {
    present: "bg-emerald-50 text-emerald-700 ring-emerald-100",
    late: "bg-amber-50 text-amber-700 ring-amber-100",
    absent: "bg-red-50 text-red-700 ring-red-100",
    leave: "bg-blue-50 text-blue-700 ring-blue-100",
  };

  return (
    <span
      className={`inline-flex rounded-full px-3 py-1 text-sm font-medium ring-1 ${
        classes[status] || "bg-slate-100 text-slate-700 ring-slate-200"
      }`}
    >
      {capitalize(status)}
    </span>
  );
}

function RegisterSkeleton() {
  return (
    <div className="animate-pulse space-y-4 p-5">
      {[0, 1, 2, 3, 4].map((row) => (
        <div key={row} className="grid grid-cols-5 gap-4 lg:grid-cols-9">
          {[0, 1, 2, 3, 4, 5, 6, 7, 8].map((cell) => (
            <div key={cell} className="h-10 rounded bg-slate-100" />
          ))}
        </div>
      ))}
    </div>
  );
}

function formatTime(value: string | null) {
  if (!value) {
    return "-";
  }

  const date = new Date(value);

  if (Number.isNaN(date.getTime())) {
    return "-";
  }

  return date.toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
  });
}

function formatDate(value: string) {
  return new Date(`${value}T00:00:00`).toLocaleDateString("en-GB", {
    day: "numeric",
    month: "long",
    year: "numeric",
  });
}

function capitalize(value: string) {
  if (value === "leave_punch_conflict") {
    return "Leave / Punch Conflict";
  }

  return value ? value.charAt(0).toUpperCase() + value.slice(1) : "Unknown";
}

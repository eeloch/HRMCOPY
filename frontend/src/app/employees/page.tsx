"use client";

import type { FormEvent } from "react";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";

import Sidebar from "@/components/Sidebar";
import { AppCard, PageHeader, Section, StatusBadge } from "@/components/ui";
import { apiFetch, getAccessToken } from "@/lib/api";

type Employee = {
  id: number;
  employee_id: string;
  biometric_user_id: string | null;
  full_name: string;
  department_name: string | null;
  position_name: string | null;
  employment_type: string;
  phone: string;
  basic_salary: string;
  status: string;
  current_shift: {
    id: number;
    name: string;
    start_time: string;
    end_time: string;
  } | null;
};

export default function EmployeesPage() {
  const router = useRouter();
  const [employees, setEmployees] = useState<Employee[]>([]);
  const [search, setSearch] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!getAccessToken()) {
      router.push("/login");
      return;
    }

    void loadEmployees();
  }, [router]);

  async function loadEmployees(searchValue = "") {
    setLoading(true);

    try {
      const params = new URLSearchParams();

      if (searchValue) {
        params.set("search", searchValue);
      }

      const suffix = params.toString() ? `?${params.toString()}` : "";
      const response = await apiFetch(`/employees/${suffix}`);

      if (!response.ok) {
        console.log("Status:", response.status);

        const body = await response.text();

        console.log("Response:", body);

        throw new Error(`Unable to load employees (${response.status})`);
      }

      const data = await response.json();
      setEmployees(data.results || []);
    } catch (error) {
      console.error(error);
    } finally {
      setLoading(false);
    }
  }

  function submitSearch(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void loadEmployees(search);
  }

  return (
    <div className="min-h-screen bg-slate-100">
      <Sidebar />

      <main className="ml-64 p-8">
        <PageHeader
          title="Employees"
          description="Employee records, biometric IDs and work assignments"
          actions={
            <>
              <button
                type="button"
                onClick={() => router.push("/employees/import")}
                className="rounded-xl border border-slate-300 bg-white px-4 py-3 font-medium text-slate-700 hover:bg-slate-50"
              >
                Bulk Import
              </button>
              <button
                type="button"
                onClick={() => router.push("/employees/new")}
                className="rounded-xl bg-blue-600 px-5 py-3 font-semibold text-white hover:bg-blue-700"
              >
                + Add Employee
              </button>
            </>
          }
        />

        <AppCard className="mb-6">
          <form onSubmit={submitSearch} className="flex gap-3">
            <input
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder="Search name, staff number, biometric ID or phone..."
              className="flex-1 rounded-xl border border-slate-300 px-4 py-3 text-slate-900 outline-none focus:ring-2 focus:ring-blue-500"
            />
            <button
              type="submit"
              className="rounded-xl bg-slate-900 px-6 py-3 font-medium text-white"
            >
              Search
            </button>
            <button
              type="button"
              onClick={() => {
                setSearch("");
                void loadEmployees();
              }}
              className="rounded-xl border border-slate-300 px-5 py-3"
            >
              Clear
            </button>
          </form>
        </AppCard>

        <Section
          title="Employee Directory"
          subtitle={`${employees.length} employee${employees.length === 1 ? "" : "s"}`}
        >
          {loading ? (
            <div className="p-10">Loading employees...</div>
          ) : employees.length === 0 ? (
            <div className="p-12 text-center text-slate-500">No employees found.</div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full">
                <thead className="bg-slate-50">
                  <tr className="text-left text-xs uppercase tracking-wide text-slate-500">
                    <th className="px-5 py-4">Employee</th>
                    <th className="px-5 py-4">Department</th>
                    <th className="px-5 py-4">Position</th>
                    <th className="px-5 py-4">Employment Type</th>
                    <th className="px-5 py-4">Biometric ID</th>
                    <th className="px-5 py-4">Shift</th>
                    <th className="px-5 py-4">Salary</th>
                    <th className="px-5 py-4">Status</th>
                  </tr>
                </thead>
                <tbody>
                  {employees.map((employee) => (
                    <tr
                      key={employee.id}
                      onClick={() => router.push(`/employees/${employee.id}`)}
                      className="cursor-pointer border-t border-slate-100 hover:bg-slate-50"
                    >
                      <td className="px-5 py-5">
                        <div className="font-semibold text-slate-900">{employee.full_name}</div>
                        <div className="text-sm text-slate-500">{employee.employee_id}</div>
                      </td>
                      <td className="px-5 py-5 text-slate-700">
                        {employee.department_name || "-"}
                      </td>
                      <td className="px-5 py-5 text-slate-700">
                        {employee.position_name || "-"}
                      </td>
                      <td className="px-5 py-5">
                        <span className="inline-flex rounded-full bg-blue-50 px-3 py-1 text-xs font-semibold text-blue-700">
                          {formatEmploymentType(employee.employment_type)}
                        </span>
                      </td>
                      <td className="px-5 py-5 text-slate-700">
                        {employee.biometric_user_id || "Not linked"}
                      </td>
                      <td className="px-5 py-5 text-slate-700">
                        {employee.current_shift?.name || "Not assigned"}
                      </td>
                      <td className="px-5 py-5 font-medium text-slate-900">
                        ₦{Number(employee.basic_salary).toLocaleString()}
                      </td>
                      <td className="px-5 py-5">
                        <StatusBadge status={employee.status} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Section>
      </main>
    </div>
  );
}

function formatEmploymentType(value: string) {
  return value === "nysc"
    ? "NYSC"
    : value.charAt(0).toUpperCase() + value.slice(1);
}

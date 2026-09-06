"use client";

import { useEffect, useEffectEvent, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import DocumentList from "@/components/documents/DocumentList";
import { EmployeeShiftPanel } from "@/components/shifts/EmployeeShiftPanel";
import { EmployeeAttendancePanel } from "@/components/employees/EmployeeAttendancePanel";
import { EmployeeBiometricPanel, type BiometricIdentity, biometricSystemLabel } from "@/components/employees/EmployeeBiometricPanel";
import { EmployeeLeavePanel } from "@/components/employees/EmployeeLeavePanel";
import { EmployeePayrollPanel } from "@/components/employees/EmployeePayrollPanel";
import { EmployeePPEPanel } from "@/components/employees/EmployeePPEPanel";
import { EmployeeMealsPanel } from "@/components/employees/EmployeeMealsPanel";

import Sidebar from "@/components/Sidebar";
import { apiFetch, getAccessToken } from "@/lib/api";


type Employee = {
  id: number;
  employee_id: string;
  biometric_user_id: string | null;
  biometric_identities: BiometricIdentity[];

  first_name: string;
  middle_name: string;
  last_name: string;
  full_name: string;

  department: number | null;
  department_name: string | null;

  position: number | null;
  position_name: string | null;

  phone: string;
  email: string;

  date_of_birth: string | null;
  employment_date: string | null;
  employment_type: string;
  employment_category: string;
  years_of_service: number | null;
  service_award_level: string | null;

  basic_salary: string;

  lives_in_company_hostel: boolean;
  hostel_room_number: string;

  status: string;

  current_shift:
    | {
        id: number;
        name: string;
        start_time: string;
        end_time: string;
      }
    | null;
};

type ProfileTab =
  | "overview"
  | "attendance"
  | "shift"
  | "payroll"
  | "biometric"
  | "documents"
  | "leave"
  | "ppe"
  | "meals";


export default function EmployeeProfilePage() {
  const params = useParams();
  const router = useRouter();

  const id = params.id as string;

  const [employee, setEmployee] =
    useState<Employee | null>(null);

  const [loading, setLoading] =
    useState(true);

  const [activeTab, setActiveTab] =
    useState<ProfileTab>(
      "overview"
    );

  const [error, setError] =
    useState("");

  const tabLabels: Record<ProfileTab, string> = {
    overview: "Overview",
    attendance: "Attendance",
    shift: "Shift",
    payroll: "Payroll",
    biometric: "Biometric",
    documents: "Documents",
    leave: "Leave",
    ppe: "PPE",
    meals: "Meals",
  };


  const loadOnMount = useEffectEvent(() => {
    void loadEmployee();
  });

  useEffect(() => {
    if (!getAccessToken()) {
      router.push("/login");
      return;
    }

    const timer = window.setTimeout(loadOnMount, 0);
    return () => window.clearTimeout(timer);
  }, [id, router]);


  async function loadEmployee() {
    setLoading(true);
    setError("");

    try {
      const response = await apiFetch(
        `/employees/${id}/`
      );

      if (!response.ok) {
        throw new Error(
          "Unable to load employee."
        );
      }

      const data = await response.json();

      setEmployee(data);

    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "Unable to load employee."
      );

    } finally {
      setLoading(false);
    }
  }


  if (loading) {
    return (
      <div className="min-h-screen bg-slate-100">
        <Sidebar />

        <main className="ml-64 p-8">
          Loading employee...
        </main>
      </div>
    );
  }


  if (error || !employee) {
    return (
      <div className="min-h-screen bg-slate-100">
        <Sidebar />

        <main className="ml-64 p-8">
          <div className="bg-red-50 border border-red-200 text-red-700 p-5 rounded-xl">
            {error || "Employee not found."}
          </div>
        </main>
      </div>
    );
  }


  return (
    <div className="min-h-screen bg-slate-100">
      <Sidebar />

      <main className="ml-64 p-8">

        <button
          onClick={() =>
            router.push("/employees")
          }
          className="text-blue-600 text-sm font-medium mb-5"
        >
          ← Employees
        </button>


        <div className="bg-white border border-slate-200 rounded-2xl shadow-sm p-7">

          <div className="flex justify-between items-start">

            <div className="flex items-center gap-5">

              <div className="w-16 h-16 rounded-full bg-blue-100 text-blue-700 flex items-center justify-center text-xl font-bold">

                {employee.first_name?.[0]}
                {employee.last_name?.[0]}

              </div>


              <div>

                <div className="flex items-center gap-3">

                  <h1 className="text-3xl font-bold text-slate-900">
                    {employee.full_name}
                  </h1>

                  <StatusBadge
                    status={employee.status}
                  />

                </div>


                <div className="text-slate-500 mt-2">

                  Staff No.{" "}
                  <span className="font-medium text-slate-700">
                    {employee.employee_id}
                  </span>

                  {" • "}

                  {employee.position_name ||
                    "No position"}

                  {" • "}

                  {employee.department_name ||
                    "No department"}

                </div>

              </div>

            </div>


            <button
              onClick={() =>
                router.push(
                  `/employees/${employee.id}/edit`
                )
              }
              className="border border-slate-300 hover:bg-slate-50 px-5 py-2.5 rounded-xl font-medium"
            >
              Edit Employee
            </button>

          </div>

        </div>


        <div className="mt-6 bg-white border border-slate-200 rounded-xl px-4">

          <div className="flex overflow-x-auto">

            <Tab
              active={activeTab === "overview"}
              onClick={() =>
                setActiveTab("overview")
              }
            >
              Overview
            </Tab>

            <Tab
              active={activeTab === "attendance"}
              onClick={() =>
                setActiveTab("attendance")
              }
            >
              Attendance
            </Tab>

            <Tab
              active={activeTab === "shift"}
              onClick={() =>
                setActiveTab("shift")
              }
            >
              Shift
            </Tab>

            <Tab
              active={activeTab === "payroll"}
              onClick={() =>
                setActiveTab("payroll")
              }
            >
              Payroll
            </Tab>

            <Tab
              active={activeTab === "biometric"}
              onClick={() =>
                setActiveTab("biometric")
              }
            >
              Biometric
            </Tab>

            <Tab
              active={activeTab === "documents"}
              onClick={() =>
                setActiveTab("documents")
              }
            >
              Documents
            </Tab>

            <Tab
              active={activeTab === "leave"}
              onClick={() =>
                setActiveTab("leave")
              }
            >
              Leave
            </Tab>

            <Tab
              active={activeTab === "ppe"}
              onClick={() =>
                setActiveTab("ppe")
              }
            >
              PPE
            </Tab>

            <Tab
              active={activeTab === "meals"}
              onClick={() =>
                setActiveTab("meals")
              }
            >
              Meals
            </Tab>

          </div>

        </div>


        {activeTab === "overview" && (

          <>

            <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-5 mt-6">

              <SummaryCard
                label="Monthly Basic Salary"
                value={
                  `₦${Number(
                    employee.basic_salary
                  ).toLocaleString()}`
                }
              />

              <SummaryCard
                label="Current Shift"
                value={
                  employee.current_shift?.name ||
                  "Not assigned"
                }
              />

              <SummaryCard
                label="Biometric Status"
                value={
                  employee.biometric_identities.length
                    ? "Linked"
                    : "Not linked"
                }
              />

              <SummaryCard
                label="Accommodation"
                value={
                  employee.lives_in_company_hostel
                    ? employee.hostel_room_number
                      ? `Hostel - Room ${employee.hostel_room_number}`
                      : "Company Hostel"
                    : "Not Resident"
                }
              />

            </div>


            <div className="grid grid-cols-1 xl:grid-cols-2 gap-6 mt-6">

              <section className="bg-white border border-slate-200 rounded-2xl shadow-sm">

                <SectionHeader
                  title="Personal Information"
                />

                <div className="p-6 grid grid-cols-2 gap-6">

                  <Info
                    label="First Name"
                    value={employee.first_name}
                  />

                  <Info
                    label="Middle Name"
                    value={
                      employee.middle_name || "—"
                    }
                  />

                  <Info
                    label="Last Name"
                    value={employee.last_name}
                  />

                  <Info
                    label="Phone"
                    value={
                      employee.phone || "—"
                    }
                  />

                  <Info
                    label="Email"
                    value={
                      employee.email || "—"
                    }
                  />

                  <Info
                    label="Date of Birth"
                    value={
                      employee.date_of_birth ||
                      "—"
                    }
                  />

                </div>

              </section>


              <section className="bg-white border border-slate-200 rounded-2xl shadow-sm">

                <SectionHeader
                  title="Employment Information"
                />

                <div className="p-6 grid grid-cols-2 gap-6">

                  <Info
                    label="Staff Number"
                    value={employee.employee_id}
                  />

                  <Info
                    label="Department"
                    value={
                      employee.department_name ||
                      "—"
                    }
                  />

                  <Info
                    label="Position"
                    value={
                      employee.position_name ||
                      "—"
                    }
                  />

                  <Info
                    label="Employment Date"
                    value={
                      employee.employment_date ||
                      "—"
                    }
                  />

                  <Info
                    label="Employment Type"
                    value={
                      formatEmploymentLabel(
                        employee.employment_type
                      )
                    }
                  />

                  <Info
                    label="Employment Category"
                    value={
                      formatEmploymentLabel(
                        employee.employment_category
                      )
                    }
                  />

                  <Info
                    label="Years of Service"
                    value={
                      employee.years_of_service === null
                        ? "—"
                        : `${employee.years_of_service} years`
                    }
                  />

                  <Info
                    label="Service Award"
                    value={
                      employee.service_award_level ||
                      "Not eligible yet"
                    }
                  />

                  <Info
                    label="Status"
                    value={employee.status}
                  />

                  <Info
                    label="Biometric Status"
                    value={
                      employee.biometric_identities.length
                        ? "Linked"
                        : "Not linked"
                    }
                  />

                </div>

              </section>

            </div>


            <div className="grid grid-cols-1 xl:grid-cols-2 gap-6 mt-6">

              <section className="bg-white border border-slate-200 rounded-2xl shadow-sm">

                <SectionHeader
                  title="Biometric Connection"
                />

                <div className="p-6">

                  {employee.biometric_identities.length ? (

                    <div>

                      <div className="flex items-center gap-2">

                        <span className="w-2.5 h-2.5 rounded-full bg-green-500" />

                        <span className="font-semibold text-green-700">
                          Linked
                        </span>

                      </div>

                      <div className="mt-4 space-y-3">
                        {employee.biometric_identities.map((identity) => (
                          <div
                            key={`${identity.system}-${identity.source_identifier}-${identity.external_user_id}`}
                            className="rounded-xl bg-slate-50 px-4 py-3"
                          >
                            <p className="font-medium text-slate-800">
                              {biometricSystemLabel(identity.system)}
                            </p>
                            <p className="mt-1 text-sm text-slate-500">
                              User ID: {identity.external_user_id} · Source: {identity.source_identifier}
                            </p>
                          </div>
                        ))}
                      </div>

                    </div>

                  ) : (

                    <div>

                      <div className="font-semibold text-amber-700">
                        Not linked
                      </div>

                      <p className="text-sm text-slate-500 mt-2">
                        This employee does not currently have an active biometric identity.
                      </p>

                    </div>

                  )}

                </div>

              </section>


              <section className="bg-white border border-slate-200 rounded-2xl shadow-sm">

                <SectionHeader
                  title="Shift Assignment"
                />

                <div className="p-6">

                  {employee.current_shift ? (

                    <>
                      <div className="font-bold text-xl">
                        {employee.current_shift.name}
                      </div>

                      <div className="text-slate-500 mt-2">
                        {formatTime(
                          employee.current_shift.start_time
                        )}
                        {" → "}
                        {formatTime(
                          employee.current_shift.end_time
                        )}
                      </div>
                    </>

                  ) : (

                    <div>

                      <div className="font-semibold text-amber-700">
                        No active shift
                      </div>

                      <p className="text-sm text-slate-500 mt-2">
                        Assign a work shift before
                        processing attendance.
                      </p>

                    </div>

                  )}

                </div>

              </section>

            </div>

          </>

        )}

        {activeTab === "documents" && (

          <div className="mt-8">

            <SectionHeader
              title="Employee Documents"
            />

            <DocumentList
              employeeId={employee.id}
            />

          </div>

        )}

        {activeTab === "shift" && (

          <EmployeeShiftPanel
            employee={{
              id: employee.id,
              employee_id: employee.employee_id,
              full_name: employee.full_name,
              department_name: employee.department_name,
            }}
            onAssignmentChanged={() => void loadEmployee()}
          />

        )}

        {activeTab === "attendance" && (
          <EmployeeAttendancePanel employeeId={employee.id} />
        )}

        {activeTab === "biometric" && (
          <EmployeeBiometricPanel
            employeeId={employee.id}
            identities={employee.biometric_identities}
          />
        )}

        {activeTab === "leave" && (
          <EmployeeLeavePanel employeeId={employee.id} />
        )}

        {activeTab === "payroll" && (
          <EmployeePayrollPanel employeeId={employee.id} />
        )}

        {activeTab === "ppe" && (
          <EmployeePPEPanel employeeId={employee.id} />
        )}

        {activeTab === "meals" && (
          <EmployeeMealsPanel employeeId={employee.id} />
        )}

        {activeTab !== "overview" &&
          activeTab !== "documents" &&
          activeTab !== "shift" && (
          activeTab !== "attendance" &&
          activeTab !== "biometric" &&
          activeTab !== "leave" &&
          activeTab !== "payroll" &&
          activeTab !== "ppe" &&
          activeTab !== "meals" &&

          <div className="mt-6 bg-white border border-slate-200 rounded-2xl shadow-sm p-8 text-center">
            <div className="font-semibold text-slate-900">
              {tabLabels[activeTab]}
            </div>

            <p className="text-sm text-slate-500 mt-2">
              This section is coming soon.
            </p>
          </div>

        )}

      </main>
    </div>
  );
}


function SummaryCard({
  label,
  value,
}: {
  label: string;
  value: string;
}) {
  return (
    <div className="bg-white border border-slate-200 rounded-2xl p-5 shadow-sm">

      <div className="text-sm text-slate-500">
        {label}
      </div>

      <div className="font-bold text-xl text-slate-900 mt-2">
        {value}
      </div>

    </div>
  );
}


function SectionHeader({
  title,
}: {
  title: string;
}) {
  return (
    <div className="px-6 py-4 border-b border-slate-200">

      <h2 className="font-bold text-slate-900">
        {title}
      </h2>

    </div>
  );
}


function Info({
  label,
  value,
}: {
  label: string;
  value: string;
}) {
  return (
    <div>

      <div className="text-xs uppercase tracking-wide text-slate-400">
        {label}
      </div>

      <div className="font-medium text-slate-800 mt-1 capitalize">
        {value}
      </div>

    </div>
  );
}


function StatusBadge({
  status,
}: {
  status: string;
}) {
  const active =
    status === "active";

  return (
    <span
      className={
        `px-3 py-1 rounded-full text-xs font-semibold capitalize ${
          active
            ? "bg-green-100 text-green-700"
            : "bg-slate-100 text-slate-700"
        }`
      }
    >
      {status}
    </span>
  );
}


function Tab({
  children,
  active = false,
  onClick,
}: {
  children: React.ReactNode;
  active?: boolean;
  onClick?: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={
        `px-5 py-4 text-sm font-medium whitespace-nowrap border-b-2 ${
          active
            ? "border-blue-600 text-blue-600"
            : "border-transparent text-slate-500 hover:text-slate-900"
        }`
      }
    >
      {children}
    </button>
  );
}


function formatTime(
  time: string
) {
  const [
    hours,
    minutes,
  ] = time.split(":");

  const date = new Date();

  date.setHours(
    Number(hours),
    Number(minutes)
  );

  return date.toLocaleTimeString(
    "en-NG",
    {
      hour: "numeric",
      minute: "2-digit",
    }
  );
}


function formatEmploymentLabel(
  value: string
) {
  return value === "nysc"
    ? "NYSC"
    : value.charAt(0).toUpperCase() +
      value.slice(1);
}

"use client";

import type { FormEvent } from "react";
import { useCallback, useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";

import Sidebar from "@/components/Sidebar";
import EmployeeForm, {
  type Department,
  type EmployeeFormValues,
  type Position,
} from "@/components/employees/EmployeeForm";
import { apiFetch, getAccessToken } from "@/lib/api";

type EmployeeResponse = {
  id: number;
  employee_id: string;
  biometric_user_id: string | null;
  first_name: string;
  middle_name: string;
  last_name: string;
  department: number | null;
  position: number | null;
  phone: string;
  email: string;
  date_of_birth: string | null;
  employment_date: string | null;
  exit_date: string | null;
  employment_type: string;
  employment_category: string;
  // Absent (not just blank) when the current user lacks permission to view salary.
  basic_salary?: string;
  // Absent (not just blank) when the current user lacks permission to view bank details.
  bank_name?: string;
  account_number?: string;
  bank_code?: string;
  lives_in_company_hostel: boolean;
  hostel_room_number: string;
  lives_in_external_accommodation: boolean;
  external_accommodation_address: string;
  status: string;
};

const emptyForm: EmployeeFormValues = {
  employee_id: "",
  biometric_user_id: "",
  first_name: "",
  middle_name: "",
  last_name: "",
  department: "",
  position: "",
  phone: "",
  email: "",
  date_of_birth: "",
  employment_date: "",
  exit_date: "",
  employment_type: "permanent",
  employment_category: "staff",
  basic_salary: "",
  bank_name: "",
  account_number: "",
  bank_code: "",
  lives_in_company_hostel: false,
  hostel_room_number: "",
  lives_in_external_accommodation: false,
  external_accommodation_address: "",
  status: "active",
};

export default function EditEmployeePage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const id = params.id;

  const [form, setForm] = useState<EmployeeFormValues>(emptyForm);
  const [departments, setDepartments] = useState<Department[]>([]);
  const [positions, setPositions] = useState<Position[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadingPositions, setLoadingPositions] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [canEditSalary, setCanEditSalary] = useState(false);
  const [canEditBankDetails, setCanEditBankDetails] = useState(false);

  const loadPositions = useCallback(async (departmentId: string) => {
    if (!departmentId) {
      setPositions([]);
      return;
    }

    setLoadingPositions(true);

    try {
      const response = await apiFetch(
        `/employees/positions/?department=${departmentId}`
      );

      if (!response.ok) {
        throw new Error("Unable to load positions.");
      }

      const data: Position[] = await response.json();
      setPositions(data);
    } catch (loadError) {
      console.error(loadError);
      setPositions([]);
      setError("Unable to load positions.");
    } finally {
      setLoadingPositions(false);
    }
  }, []);

  const loadPageData = useCallback(async () => {
    setLoading(true);
    setError("");

    try {
      const [employeeResponse, departmentsResponse] = await Promise.all([
        apiFetch(`/employees/${id}/`),
        apiFetch("/employees/departments/"),
      ]);

      if (!employeeResponse.ok) {
        throw new Error("Unable to load employee.");
      }

      if (!departmentsResponse.ok) {
        throw new Error("Unable to load departments.");
      }

      const employee: EmployeeResponse = await employeeResponse.json();
      const departmentData: Department[] = await departmentsResponse.json();

      setDepartments(departmentData);
      setForm(toFormValues(employee));
      setCanEditSalary(employee.basic_salary !== undefined);
      setCanEditBankDetails(employee.bank_name !== undefined);

      if (employee.department) {
        await loadPositions(String(employee.department));
      } else {
        setPositions([]);
      }
    } catch (loadError) {
      console.error(loadError);
      setError(
        loadError instanceof Error
          ? loadError.message
          : "Unable to load employee."
      );
    } finally {
      setLoading(false);
    }
  }, [id, loadPositions]);

  useEffect(() => {
    if (!getAccessToken()) {
      router.push("/login");
      return;
    }

    // eslint-disable-next-line react-hooks/set-state-in-effect -- fetch-on-mount/param-change; the loader only sets its loading/error flags, no derived state
    void loadPageData();
  }, [router, loadPageData]);

  function updateField(
    field: keyof EmployeeFormValues,
    value: string | boolean
  ) {
    setForm((current) => ({ ...current, [field]: value }) as EmployeeFormValues);
  }

  function handleDepartmentChange(departmentId: string) {
    setForm((current) => ({
      ...current,
      department: departmentId,
      position: "",
    }));
    void loadPositions(departmentId);
  }

  async function saveEmployee(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    setSaving(true);

    try {
      const payload: Record<string, unknown> = {
        biometric_user_id: form.biometric_user_id || null,
        first_name: form.first_name,
        middle_name: form.middle_name,
        last_name: form.last_name,
        department: form.department ? Number(form.department) : null,
        position: form.position ? Number(form.position) : null,
        phone: form.phone,
        email: form.email,
        date_of_birth: form.date_of_birth || null,
        employment_date: form.employment_date || null,
        exit_date: form.exit_date || null,
        employment_type: form.employment_type,
        employment_category: form.employment_category,
        lives_in_company_hostel: form.lives_in_company_hostel,
        hostel_room_number: form.lives_in_company_hostel
          ? form.hostel_room_number.trim()
          : "",
        lives_in_external_accommodation: form.lives_in_external_accommodation,
        external_accommodation_address: form.lives_in_external_accommodation
          ? form.external_accommodation_address.trim()
          : "",
        status: form.status,
      };

      // Never submitted for a user who can't see it in the first place -
      // the field would only ever hold an empty-string fallback, and
      // sending that would silently zero out the employee's real salary.
      if (canEditSalary) {
        payload.basic_salary = form.basic_salary || "0";
      }

      if (canEditBankDetails) {
        payload.bank_name = form.bank_name;
        payload.account_number = form.account_number;
        payload.bank_code = form.bank_code;
      }

      const response = await apiFetch(`/employees/${id}/`, {
        method: "PATCH",
        body: JSON.stringify(payload),
      });

      if (!response.ok) {
        setError(await responseError(response, "Unable to save employee."));
        return;
      }

      router.push(`/employees/${id}`);
    } catch (saveError) {
      console.error(saveError);
      setError("Unable to save employee. Please try again.");
    } finally {
      setSaving(false);
    }
  }

  if (loading) {
    return <PageFrame>Loading employee...</PageFrame>;
  }

  if (error && !form.employee_id) {
    return (
      <PageFrame>
        <div className="rounded-xl border border-red-200 bg-red-50 p-5 text-red-700">
          {error}
        </div>
      </PageFrame>
    );
  }

  return (
    <PageFrame>
      <button
        type="button"
        onClick={() => router.push(`/employees/${id}`)}
        className="mb-5 text-sm font-medium text-blue-600"
      >
        Back to Employee Profile
      </button>

      <div className="rounded-2xl border border-slate-200 bg-white p-7 shadow-sm">
        <h1 className="text-3xl font-bold text-slate-900">Edit Employee</h1>
        <p className="mt-2 text-slate-500">
          Update employee information, employment details, and assigned records.
        </p>
      </div>

      <EmployeeForm
        values={form}
        departments={departments}
        positions={positions}
        loadingPositions={loadingPositions}
        saving={saving}
        error={error}
        onValueChange={updateField}
        onDepartmentChange={handleDepartmentChange}
        onSubmit={saveEmployee}
        onCancel={() => router.push(`/employees/${id}`)}
        salaryEditable={canEditSalary}
        bankDetailsEditable={canEditBankDetails}
      />
    </PageFrame>
  );
}

function PageFrame({ children }: { children: React.ReactNode }) {
  return (
    <div className="min-h-screen bg-slate-100">
      <Sidebar />
      <main className="ml-64 p-8">{children}</main>
    </div>
  );
}

function toFormValues(employee: EmployeeResponse): EmployeeFormValues {
  return {
    employee_id: employee.employee_id || "",
    biometric_user_id: employee.biometric_user_id || "",
    first_name: employee.first_name || "",
    middle_name: employee.middle_name || "",
    last_name: employee.last_name || "",
    department: employee.department ? String(employee.department) : "",
    position: employee.position ? String(employee.position) : "",
    phone: employee.phone || "",
    email: employee.email || "",
    date_of_birth: dateValue(employee.date_of_birth),
    employment_date: dateValue(employee.employment_date),
    exit_date: dateValue(employee.exit_date),
    employment_type: employee.employment_type || "permanent",
    employment_category: employee.employment_category || "staff",
    basic_salary: employee.basic_salary || "",
    bank_name: employee.bank_name || "",
    account_number: employee.account_number || "",
    bank_code: employee.bank_code || "",
    lives_in_company_hostel: employee.lives_in_company_hostel,
    hostel_room_number: employee.hostel_room_number || "",
    lives_in_external_accommodation: employee.lives_in_external_accommodation,
    external_accommodation_address: employee.external_accommodation_address || "",
    status: employee.status || "active",
  };
}

function dateValue(value: string | null) {
  return value ? value.slice(0, 10) : "";
}

async function responseError(response: Response, fallback: string) {
  const data: unknown = await response.json().catch(() => null);

  if (!data || typeof data !== "object") {
    return fallback;
  }

  return Object.entries(data)
    .map(([field, value]) => {
      const message = Array.isArray(value) ? value.join(" ") : String(value);
      return `${field}: ${message}`;
    })
    .join(" | ");
}

"use client";

import {
  FormEvent,
  useEffect,
  useState,
} from "react";

import {
  useRouter,
} from "next/navigation";

import Sidebar
  from "@/components/Sidebar";

import {
  apiFetch,
} from "@/lib/api";


type Department = {
  id: number;
  name: string;
};


type Position = {
  id: number;
  name: string;
  department: number;
};


type OrganizationCreateDepartment = {
  name: string;
  positions: string[];
};


export default function AddEmployeePage() {

  const router =
    useRouter();

  const [
    departments,
    setDepartments,
  ] = useState<Department[]>([]);

  const [
    positions,
    setPositions,
  ] = useState<Position[]>([]);

  const [
    organizationSetupName,
    setOrganizationSetupName,
  ] = useState("");

  const [
    organizationSetupPositions,
    setOrganizationSetupPositions,
  ] = useState("");

  const [
    organizationSetupBusy,
    setOrganizationSetupBusy,
  ] = useState(false);

  const [
    organizationSetupMessage,
    setOrganizationSetupMessage,
  ] = useState("");

  const [
    organizationSetupError,
    setOrganizationSetupError,
  ] = useState("");

  const [
    error,
    setError,
  ] = useState("");

  const [
    saving,
    setSaving,
  ] = useState(false);


  const [
    form,
    setForm,
  ] = useState({

    employee_id: "",

    biometric_user_id: "",

    first_name: "",

    middle_name: "",

    last_name: "",

    department: "",

    position: "",

    phone: "",

    email: "",

    employment_date: "",

    employment_type: "permanent",

    employment_category: "staff",

    basic_salary: "",

    lives_in_company_hostel:
      false,

    hostel_room_number: "",

    status: "active",

  });


  useEffect(() => {

    loadDepartments();

  }, []);


  async function loadDepartments() {

    try {

      const response =
        await apiFetch(
          "/employees/departments/"
        );

      if (!response.ok) {

        throw new Error(
          "Unable to load departments."
        );
      }

      const data =
        await response.json();

      setDepartments(data);

    } catch (error) {

      console.error(error);

      setError(
        "Unable to load departments."
      );
    }
  }


  async function loadPositions(
    departmentId: string
  ) {

    if (!departmentId) {

      setPositions([]);

      return;
    }

    try {

      const response =
        await apiFetch(
          `/employees/positions/?department=${departmentId}`
        );

      if (!response.ok) {

        throw new Error(
          "Unable to load positions."
        );
      }

      const data =
        await response.json();

      setPositions(data);

    } catch (error) {

      console.error(error);

      setPositions([]);

      setError(
        "Unable to load positions."
      );
    }
  }


  async function refreshOrganizationState() {
    await loadDepartments();

    if (form.department) {
      await loadPositions(
        form.department
      );
    }
  }


  function parseSetupPositions() {
    return organizationSetupPositions
      .split(/[\n,]/)
      .map((position) => position.trim())
      .filter(Boolean);
  }


  async function createSelectedOrganization() {
    const departmentName =
      organizationSetupName.trim();
    const positionsToCreate =
      parseSetupPositions();

    if (!departmentName) {
      setOrganizationSetupError(
        "Enter a department name first."
      );
      return;
    }

    setOrganizationSetupBusy(true);
    setOrganizationSetupError("");
    setOrganizationSetupMessage("");

    try {
      const response =
        await apiFetch(
          "/employees/import/organization/create/",
          {
            method: "POST",
            body: JSON.stringify({
              departments: [
                {
                  name: departmentName,
                  positions:
                    positionsToCreate,
                } as OrganizationCreateDepartment,
              ],
            }),
          }
        );

      const data = await response.json();

      if (!response.ok) {
        setOrganizationSetupError(
          data.detail ||
            "Unable to create the selected department and positions."
        );
        return;
      }

      const createdPositionNames =
        Array.isArray(data.created_positions)
          ? data.created_positions.map(
              (item: {
                department: string;
                position: string;
              }) =>
                `${item.department} - ${item.position}`
            )
          : [];

      const existingPositionNames =
        Array.isArray(data.existing_positions)
          ? data.existing_positions.map(
              (item: {
                department: string;
                position: string;
              }) =>
                `${item.department} - ${item.position}`
            )
          : [];

      setOrganizationSetupMessage(
        [
          data.created_departments?.length
            ? `Created departments: ${data.created_departments.join(", ")}`
            : "",

          createdPositionNames.length
            ? `Created positions: ${createdPositionNames.join(", ")}`
            : "",

          data.existing_departments?.length
            ? `Existing departments: ${data.existing_departments.join(", ")}`
            : "",

          existingPositionNames.length
            ? `Existing positions: ${existingPositionNames.join(", ")}`
            : "",
        ]
          .filter(Boolean)
          .join(" | ")
      );

      await refreshOrganizationState();
    } catch (error) {
      console.error(error);
      setOrganizationSetupError(
        "Unable to create the selected organization items."
      );
    } finally {
      setOrganizationSetupBusy(false);
    }
  }


  function updateField(
    name: string,
    value:
      | string
      | boolean
  ) {

    setForm(
      (current) => ({
        ...current,
        [name]: value,
      })
    );
  }


  async function submit(
    event: FormEvent
  ) {

    event.preventDefault();

    setError("");
    setSaving(true);

    try {

      const payload = {

        ...form,

        department:
          form.department
            ? Number(
                form.department
              )
            : null,

        position:
          form.position
            ? Number(
                form.position
              )
            : null,

        basic_salary:
          form.basic_salary ||
          "0",

        biometric_user_id:
          form.biometric_user_id ||
          null,

        hostel_room_number:
          form.lives_in_company_hostel
            ? form.hostel_room_number.trim()
            : "",

      };


      const response =
        await apiFetch(
          "/employees/",
          {
            method: "POST",

            body:
              JSON.stringify(
                payload
              ),
          }
        );


      if (!response.ok) {

        const data =
          await response.json();

        if (
          typeof data === "object"
          && data !== null
        ) {

          const messages =
            Object.entries(data)
            .map(
              ([field, value]) => {

                const text =
                  Array.isArray(value)
                    ? value.join(" ")
                    : String(value);

                return `${field}: ${text}`;
              }
            )
            .join(" | ");

          setError(messages);

        } else {

          setError(
            "Unable to create employee."
          );
        }

        return;
      }


      const employee =
        await response.json();


      router.push(
        `/employees/${employee.id}`
      );

    } catch (error) {

      console.error(error);

      setError(
        "Unable to create employee. Please try again."
      );

    } finally {

      setSaving(false);
    }
  }


  return (

    <div className="min-h-screen bg-slate-100">

      <Sidebar />


      <main className="ml-64 p-8">


        <div className="max-w-5xl">


          <button
            type="button"
            onClick={() =>
              router.push(
                "/employees"
              )
            }
            className="text-sm text-blue-600 hover:text-blue-700 font-medium mb-4"
          >
            ← Employees
          </button>


          <h1 className="text-3xl font-bold text-slate-900">
            Add Employee
          </h1>


          <p className="text-slate-500 mt-1 mb-8">
            Create a new employee record.
          </p>


          <form
            onSubmit={submit}
            className="bg-white rounded-2xl border border-slate-200 shadow-sm p-8"
          >

            {/* ORGANIZATION SETUP */}

            <div className="mb-8 rounded-2xl border border-blue-100 bg-blue-50 p-5">

              <div className="flex items-start justify-between gap-4">

                <div>
                  <h2 className="font-bold text-lg text-slate-900">
                    Organization Setup
                  </h2>

                  <p className="text-sm text-slate-600 mt-1">
                    Create a selected department and its positions, then reload the available options before saving the employee.
                  </p>
                </div>

                <button
                  type="button"
                  onClick={createSelectedOrganization}
                  disabled={organizationSetupBusy}
                  className="bg-blue-600 hover:bg-blue-700 disabled:bg-blue-400 text-white px-4 py-2 rounded-xl font-semibold"
                >
                  {organizationSetupBusy
                    ? "Creating..."
                    : "Create Selected"}
                </button>

              </div>

              <div className="mt-5 grid grid-cols-1 md:grid-cols-2 gap-4">

                <Field
                  label="Department Name"
                  value={organizationSetupName}
                  placeholder="Example: Finance"
                  onChange={(value) => {
                    setOrganizationSetupName(value);
                    setOrganizationSetupMessage("");
                    setOrganizationSetupError("");
                  }}
                />

                <Field
                  label="Positions"
                  value={organizationSetupPositions}
                  placeholder="Example: Officer, Assistant"
                  onChange={(value) => {
                    setOrganizationSetupPositions(value);
                    setOrganizationSetupMessage("");
                    setOrganizationSetupError("");
                  }}
                />

              </div>

              <div className="mt-3 text-xs text-slate-500">
                Separate multiple positions with commas or line breaks. After creation, the page refreshes the available departments and positions.
              </div>

              {organizationSetupMessage && (
                <div className="mt-4 rounded-xl border border-green-200 bg-green-50 px-4 py-3 text-sm text-green-800">
                  {organizationSetupMessage}
                </div>
              )}

              {organizationSetupError && (
                <div className="mt-4 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
                  {organizationSetupError}
                </div>
              )}

            </div>


            {/* IDENTITY */}

            <h2 className="font-bold text-lg text-slate-900 mb-5">
              Identity
            </h2>


            <div className="grid grid-cols-1 md:grid-cols-2 gap-5">


              <Field
                label="Employee / Staff Number"
                value={
                  form.employee_id
                }
                required
                onChange={(value) =>
                  updateField(
                    "employee_id",
                    value
                  )
                }
              />


              <Field
                label="Biometric User ID"
                value={
                  form.biometric_user_id
                }
                onChange={(value) =>
                  updateField(
                    "biometric_user_id",
                    value
                  )
                }
              />


              <Field
                label="First Name"
                value={
                  form.first_name
                }
                required
                onChange={(value) =>
                  updateField(
                    "first_name",
                    value
                  )
                }
              />


              <Field
                label="Middle Name"
                value={
                  form.middle_name
                }
                onChange={(value) =>
                  updateField(
                    "middle_name",
                    value
                  )
                }
              />


              <Field
                label="Last Name"
                value={
                  form.last_name
                }
                required
                onChange={(value) =>
                  updateField(
                    "last_name",
                    value
                  )
                }
              />


              <Field
                label="Phone"
                value={
                  form.phone
                }
                onChange={(value) =>
                  updateField(
                    "phone",
                    value
                  )
                }
              />


              <Field
                label="Email"
                type="email"
                value={
                  form.email
                }
                onChange={(value) =>
                  updateField(
                    "email",
                    value
                  )
                }
              />


            </div>


            <hr className="my-8 border-slate-200" />


            {/* WORK INFORMATION */}

            <h2 className="font-bold text-lg text-slate-900 mb-5">
              Work Information
            </h2>


            <div className="grid grid-cols-1 md:grid-cols-2 gap-5">


              <div>

                <label className="block text-sm font-medium text-slate-700 mb-2">
                  Department
                </label>

                <select
                  value={
                    form.department
                  }
                  onChange={(event) => {

                    const value =
                      event.target.value;

                    updateField(
                      "department",
                      value
                    );

                    updateField(
                      "position",
                      ""
                    );

                    loadPositions(
                      value
                    );
                  }}
                  className="w-full border border-slate-300 rounded-xl px-4 py-3 text-slate-900 bg-white outline-none focus:ring-2 focus:ring-blue-500"
                >

                  <option value="">
                    Select department
                  </option>

                  {departments.map(
                    (department) => (

                      <option
                        key={
                          department.id
                        }
                        value={
                          department.id
                        }
                      >
                        {department.name}
                      </option>

                    )
                  )}

                </select>

              </div>


              <div>

                <label className="block text-sm font-medium text-slate-700 mb-2">
                  Position
                </label>

                <select
                  value={
                    form.position
                  }
                  disabled={
                    !form.department
                  }
                  onChange={(event) =>
                    updateField(
                      "position",
                      event.target.value
                    )
                  }
                  className="w-full border border-slate-300 rounded-xl px-4 py-3 text-slate-900 bg-white outline-none focus:ring-2 focus:ring-blue-500 disabled:bg-slate-100 disabled:text-slate-400"
                >

                  <option value="">
                    {form.department
                      ? "Select position"
                      : "Select department first"}
                  </option>

                  {positions.map(
                    (position) => (

                      <option
                        key={
                          position.id
                        }
                        value={
                          position.id
                        }
                      >
                        {position.name}
                      </option>

                    )
                  )}

                </select>

              </div>


              <Field
                label="Employment Date"
                type="date"
                value={
                  form.employment_date
                }
                onChange={(value) =>
                  updateField(
                    "employment_date",
                    value
                  )
                }
              />


              <div>

                <label className="block text-sm font-medium text-slate-700 mb-2">
                  Employment Type
                </label>

                <select
                  value={
                    form.employment_type
                  }
                  onChange={(event) =>
                    updateField(
                      "employment_type",
                      event.target.value
                    )
                  }
                  className="w-full border border-slate-300 rounded-xl px-4 py-3 text-slate-900 bg-white outline-none focus:ring-2 focus:ring-blue-500"
                >
                  <option value="permanent">Permanent</option>
                  <option value="contract">Contract</option>
                  <option value="casual">Casual</option>
                  <option value="intern">Intern</option>
                  <option value="nysc">NYSC</option>
                  <option value="expatriate">Expatriate</option>
                </select>

              </div>


              <div>

                <label className="block text-sm font-medium text-slate-700 mb-2">
                  Employment Category
                </label>

                <select
                  value={
                    form.employment_category
                  }
                  onChange={(event) =>
                    updateField(
                      "employment_category",
                      event.target.value
                    )
                  }
                  className="w-full border border-slate-300 rounded-xl px-4 py-3 text-slate-900 bg-white outline-none focus:ring-2 focus:ring-blue-500"
                >
                  <option value="staff">Staff</option>
                  <option value="management">Management</option>
                  <option value="executive">Executive</option>
                </select>

              </div>


              <Field
                label="Monthly Basic Salary"
                type="number"
                value={
                  form.basic_salary
                }
                onChange={(value) =>
                  updateField(
                    "basic_salary",
                    value
                  )
                }
              />


            </div>


            <hr className="my-8 border-slate-200" />


            {/* ACCOMMODATION */}

            <h2 className="font-bold text-lg text-slate-900 mb-5">
              Company Accommodation
            </h2>


            <div className="bg-slate-50 border border-slate-200 rounded-xl p-5">


              <label className="flex items-center gap-3 cursor-pointer">

                <input
                  type="checkbox"
                  checked={
                    form.lives_in_company_hostel
                  }
                  onChange={(event) => {

                    const checked =
                      event.target.checked;

                    updateField(
                      "lives_in_company_hostel",
                      checked
                    );

                    if (!checked) {

                      updateField(
                        "hostel_room_number",
                        ""
                      );
                    }
                  }}
                  className="w-4 h-4"
                />


                <div>

                  <div className="text-sm font-medium text-slate-800">
                    Employee lives in company hostel
                  </div>

                  <div className="text-xs text-slate-500 mt-1">
                    Enable this if accommodation has been assigned to this employee.
                  </div>

                </div>

              </label>


              {form.lives_in_company_hostel && (

                <div className="mt-5 max-w-md">

                  <Field
                    label="Hostel Room Number"
                    value={
                      form.hostel_room_number
                    }
                    required
                    placeholder="Example: B12"
                    onChange={(value) =>
                      updateField(
                        "hostel_room_number",
                        value
                      )
                    }
                  />

                </div>

              )}


            </div>


            <hr className="my-8 border-slate-200" />


            {/* EMPLOYMENT STATUS */}

            <h2 className="font-bold text-lg text-slate-900 mb-5">
              Employment Status
            </h2>


            <div className="max-w-md">

              <label className="block text-sm font-medium text-slate-700 mb-2">
                Status
              </label>

              <select
                value={
                  form.status
                }
                onChange={(event) =>
                  updateField(
                    "status",
                    event.target.value
                  )
                }
                className="w-full border border-slate-300 rounded-xl px-4 py-3 text-slate-900 bg-white outline-none focus:ring-2 focus:ring-blue-500"
              >

                <option value="active">
                  Active
                </option>

                <option value="inactive">
                  Inactive
                </option>

                <option value="suspended">
                  Suspended
                </option>

                <option value="terminated">
                  Terminated
                </option>

              </select>

            </div>


            {/* ERROR */}

            {error && (

              <div className="mt-6 bg-red-50 border border-red-200 text-red-700 p-4 rounded-xl text-sm">
                {error}
              </div>

            )}


            {/* ACTIONS */}

            <div className="mt-8 flex justify-end gap-3">


              <button
                type="button"
                onClick={() =>
                  router.push(
                    "/employees"
                  )
                }
                className="border border-slate-300 hover:bg-slate-50 px-6 py-3 rounded-xl font-medium text-slate-700"
              >
                Cancel
              </button>


              <button
                disabled={saving}
                type="submit"
                className="bg-blue-600 hover:bg-blue-700 disabled:bg-blue-400 text-white px-6 py-3 rounded-xl font-semibold"
              >

                {saving
                  ? "Saving..."
                  : "Create Employee"}

              </button>


            </div>


          </form>


        </div>


      </main>


    </div>
  );
}


function Field({
  label,
  value,
  onChange,
  type = "text",
  required = false,
  placeholder = "",
}: {
  label: string;
  value: string;
  onChange:
    (value: string) => void;
  type?: string;
  required?: boolean;
  placeholder?: string;
}) {

  return (

    <div>

      <label className="block text-sm font-medium text-slate-700 mb-2">
        {label}

        {required && (
          <span className="text-red-500 ml-1">
            *
          </span>
        )}
      </label>

      <input
        type={type}
        required={required}
        value={value}
        placeholder={placeholder}
        min={
          type === "number"
            ? "0"
            : undefined
        }
        onChange={(event) =>
          onChange(
            event.target.value
          )
        }
        className="w-full border border-slate-300 rounded-xl px-4 py-3 text-slate-900 outline-none focus:ring-2 focus:ring-blue-500"
      />

    </div>
  );
}

"use client";

import {
  ChangeEvent,
  DragEvent,
  useRef,
  useState,
} from "react";

import { useRouter } from "next/navigation";

import Sidebar from "@/components/Sidebar";
import { apiFetch } from "@/lib/api";


type ImportRow = {
  row: number;
  valid: boolean;
  errors: string[];
  warnings?: string[];

  data: {
    employee_id: string;
    full_name?: string;

    first_name: string;
    middle_name: string;
    last_name: string;

    department: number | null;
    department_name: string;

    position: number | null;
    position_name: string;

    phone: string;
    email: string;

    employment_date: string | null;
    basic_salary: string;

    lives_in_company_hostel: boolean;
    hostel_room_number: string;

    status: string;

    biometric_system: string;
    biometric_source: string;
    biometric_user_id: string;

    bank?: string;
    account_number?: string;
  };
};


type PreviewResponse = {
  total_rows: number;
  valid_rows: number;
  error_rows: number;
  can_import: boolean;
  results: ImportRow[];
};


type OrganizationPosition = {
  id: number | null;
  name: string;
  exists: boolean;
};


type OrganizationDepartment = {
  id: number | null;
  name: string;
  exists: boolean;
  positions: OrganizationPosition[];
};


type OrganizationResponse = {
  departments: OrganizationDepartment[];
  total_departments: number;
  missing_departments: number;
  missing_positions: number;
};


export default function EmployeeImportPage() {
  const router = useRouter();

  const inputRef =
    useRef<HTMLInputElement>(null);

  const [file, setFile] =
    useState<File | null>(null);

  const [preview, setPreview] =
    useState<PreviewResponse | null>(null);

  const [
    organization,
    setOrganization,
  ] =
    useState<OrganizationResponse | null>(
      null
    );

  const [uploading, setUploading] =
    useState(false);

  const [importing, setImporting] =
    useState(false);

  const [
    loadingOrganization,
    setLoadingOrganization,
  ] =
    useState(false);

  const [dragging, setDragging] =
    useState(false);

  const [
    selectedDepartments,
    setSelectedDepartments,
  ] = useState<Record<string, boolean>>({});

  const [
    selectedPositions,
    setSelectedPositions,
  ] = useState<Record<string, boolean>>({});

  const [
    creatingOrganization,
    setCreatingOrganization,
  ] = useState(false);

  const [
    organizationMessage,
    setOrganizationMessage,
  ] = useState("");

  const [error, setError] =
    useState("");

  const [importMessage, setImportMessage] =
    useState("");


  function selectFile(
    selectedFile: File | null
  ) {
    setError("");
    setPreview(null);
    setOrganization(null);
    setSelectedDepartments({});
    setSelectedPositions({});
    setOrganizationMessage("");
    setImportMessage("");

    if (!selectedFile) {
      setFile(null);
      return;
    }

    const filename =
      selectedFile.name.toLowerCase();

    if (
      !filename.endsWith(".xlsx") &&
      !filename.endsWith(".csv")
    ) {
      setFile(null);

      setError(
        "Please select an Excel (.xlsx) or CSV (.csv) file."
      );

      return;
    }

    setFile(selectedFile);
  }


  function handleFileInput(
    event: ChangeEvent<HTMLInputElement>
  ) {
    selectFile(
      event.target.files?.[0] ?? null
    );
  }


  function handleDrop(
    event: DragEvent<HTMLDivElement>
  ) {
    event.preventDefault();

    setDragging(false);

    selectFile(
      event.dataTransfer.files?.[0] ??
        null
    );
  }


  async function validateFile() {
    if (!file) {
      setError(
        "Select an employee spreadsheet first."
      );

      return;
    }

    setUploading(true);
    setError("");
    setPreview(null);
    setOrganization(null);

    try {
      const body =
        new FormData();

      body.append(
        "file",
        file
      );

      const response =
        await apiFetch(
          "/employees/import/preview/",
          {
            method: "POST",
            body,
          }
        );

      const data =
        await response.json();

      if (!response.ok) {
        setError(
          data.detail ||
            "Unable to validate employee file."
        );

        return;
      }

      setPreview(data);

      await loadOrganization();

    } catch (error) {
      console.error(error);

      setError(
        "Unable to upload and validate the spreadsheet."
      );

    } finally {
      setUploading(false);
    }
  }


  async function importEmployees() {
    if (
      !file ||
      preview?.can_import !== true ||
      importing
    ) {
      return;
    }

    setImporting(true);
    setError("");
    setImportMessage("");

    let importSucceeded = false;

    try {
      const body = new FormData();

      body.append(
        "file",
        file
      );

      const response = await apiFetch(
        "/employees/import/",
        {
          method: "POST",
          body,
        }
      );

      const data = await response.json();

      if (!response.ok) {
        setError(
          data.detail ||
            "Unable to import employees."
        );
        return;
      }

      const imported = data.summary?.imported ?? 0;

      importSucceeded = true;

      setImportMessage(
        `${imported} employee${
          imported === 1 ? "" : "s"
        } imported successfully. Redirecting to Employees...`
      );

      window.setTimeout(
        () => router.push("/employees"),
        1200
      );

    } catch (error) {
      console.error(error);

      setError(
        "Unable to import employees. Please try again."
      );

    } finally {
      if (!importSucceeded) {
        setImporting(false);
      }
    }
  }


  async function loadOrganization() {
    if (!file) {
      return;
    }

    setLoadingOrganization(true);

    try {
      const body =
        new FormData();

      body.append(
        "file",
        file
      );

      const response =
        await apiFetch(
          "/employees/import/organization/",
          {
            method: "POST",
            body,
          }
        );

      const data =
        await response.json();

      if (!response.ok) {
        setError(
          data.detail ||
            "Unable to analyze organisation structure."
        );

        return;
      }

      setOrganization(
        data
      );

      const departmentSelections:
        Record<string, boolean> = {};

      const positionSelections:
        Record<string, boolean> = {};

      for (const department of data.departments ?? []) {
        if (!department.exists) {
          departmentSelections[
            department.name
          ] = true;
        }

        for (const position of department.positions ?? []) {
          if (!position.exists) {
            positionSelections[
              `${department.name}::${position.name}`
            ] = true;
          }
        }
      }

      setSelectedDepartments(
        departmentSelections
      );

      setSelectedPositions(
        positionSelections
      );

    } catch (error) {
      console.error(error);

      setError(
        "Unable to analyze departments and positions."
      );

    } finally {
      setLoadingOrganization(
        false
      );
    }
  }


  async function createSelectedOrganization() {
    if (!organization) {
      return;
    }

    const departments = organization.departments
      .map((department) => {
        const selectedMissingPositions =
          department.positions
            .filter(
              (position) =>
                !position.exists &&
                (
                  selectedPositions[
                    `${department.name}::${position.name}`
                  ] ?? false
                )
            )
            .map(
              (position) =>
                position.name
            );

        const departmentSelected =
          department.exists ||
          (
            selectedDepartments[
              department.name
            ] ?? false
          );

        if (
          !departmentSelected &&
          selectedMissingPositions.length === 0
        ) {
          return null;
        }

        if (
          department.exists &&
          selectedMissingPositions.length === 0
        ) {
          return null;
        }

        return {
          name: department.name,
          positions:
            selectedMissingPositions,
        };
      })
      .filter(
        (
          item
        ): item is {
          name: string;
          positions: string[];
        } => item !== null
      );

    if (departments.length === 0) {
      setError(
        "Select at least one missing department or position to create."
      );
      return;
    }

    setCreatingOrganization(true);
    setError("");
    setOrganizationMessage("");

    try {
      const response =
        await apiFetch(
          "/employees/import/organization/create/",
          {
            method: "POST",
            body: JSON.stringify({
              departments,
            }),
          }
        );

      const data =
        await response.json();

      if (!response.ok) {
        setError(
          data.detail ||
            "Unable to create the selected departments and positions."
        );
        return;
      }

      const departmentsCreated =
        data.summary?.departments_created ?? 0;

      const positionsCreated =
        data.summary?.positions_created ?? 0;

      setOrganizationMessage(
        `${departmentsCreated} department${
          departmentsCreated === 1 ? "" : "s"
        } and ${positionsCreated} position${
          positionsCreated === 1 ? "" : "s"
        } created. The spreadsheet has been revalidated.`
      );

      await validateFile();

    } catch (error) {
      console.error(error);

      setError(
        "Unable to create the selected departments and positions."
      );

    } finally {
      setCreatingOrganization(false);
    }
  }


  function clearFile() {
    setFile(null);
    setPreview(null);
    setOrganization(null);
    setSelectedDepartments({});
    setSelectedPositions({});
    setOrganizationMessage("");
    setImportMessage("");
    setError("");

    if (inputRef.current) {
      inputRef.current.value = "";
    }
  }


  const errorRows =
    preview?.results.filter(
      (row) => !row.valid
    ) ?? [];


  const canImport =
    preview?.can_import === true &&
    !uploading &&
    !importing;


  return (
    <div className="min-h-screen bg-slate-100">

      <Sidebar />


      <main className="ml-64 p-8">

        <div className="max-w-7xl mx-auto">


          <button
            type="button"
            onClick={() =>
              router.push(
                "/employees"
              )
            }
            className="text-sm text-blue-600 hover:text-blue-700 font-medium"
          >
            ← Employees
          </button>


          <div className="mt-4 mb-8">

            <h1 className="text-3xl font-bold text-slate-900">
              Bulk Employee Import
            </h1>

            <p className="text-slate-500 mt-2">
              Upload and validate employee records
              before anything is created in the HRM.
            </p>

          </div>


          {/* UPLOAD */}

          <div className="bg-white border border-slate-200 rounded-2xl shadow-sm p-8">

            <div
              onDragOver={(event) => {
                event.preventDefault();
                setDragging(true);
              }}
              onDragLeave={() =>
                setDragging(false)
              }
              onDrop={handleDrop}
              className={[
                "border-2 border-dashed rounded-2xl p-10 text-center transition",

                dragging
                  ? "border-blue-500 bg-blue-50"
                  : "border-slate-300 bg-slate-50",

              ].join(" ")}
            >

              <div className="text-4xl mb-3">
                📄
              </div>

              <h2 className="text-lg font-semibold text-slate-900">
                Drop employee spreadsheet here
              </h2>

              <p className="text-sm text-slate-500 mt-2">
                Excel .xlsx and CSV files are supported
              </p>


              <input
                ref={inputRef}
                type="file"
                accept=".xlsx,.csv"
                onChange={
                  handleFileInput
                }
                className="hidden"
              />


              <button
                type="button"
                onClick={() =>
                  inputRef.current?.click()
                }
                className="mt-5 bg-white border border-slate-300 hover:bg-slate-100 px-5 py-2.5 rounded-xl font-medium text-slate-700"
              >
                Choose File
              </button>

            </div>


            {file && (

              <div className="mt-6 flex items-center justify-between border border-slate-200 rounded-xl p-4">

                <div>

                  <div className="font-semibold text-slate-900">
                    {file.name}
                  </div>

                  <div className="text-sm text-slate-500 mt-1">
                    {formatFileSize(
                      file.size
                    )}
                  </div>

                </div>


                <button
                  type="button"
                  onClick={
                    clearFile
                  }
                  className="text-sm text-red-600 hover:text-red-700 font-medium"
                >
                  Remove
                </button>

              </div>

            )}


            {error && (

              <div className="mt-6 bg-red-50 border border-red-200 text-red-700 rounded-xl p-4">
                {error}
              </div>

            )}


            <div className="mt-6 flex justify-end">

              <button
                type="button"
                disabled={
                  !file ||
                  uploading ||
                  importing
                }
                onClick={
                  validateFile
                }
                className="bg-blue-600 hover:bg-blue-700 disabled:bg-slate-300 disabled:cursor-not-allowed text-white px-6 py-3 rounded-xl font-semibold"
              >

                {uploading
                  ? "Validating..."
                  : "Validate Spreadsheet"}

              </button>

            </div>

          </div>


          {/* VALIDATION SUMMARY */}

          {preview && (

            <div className="grid grid-cols-1 md:grid-cols-4 gap-4 mt-8">

              <SummaryCard
                label="Total Rows"
                value={
                  preview.total_rows
                }
              />

              <SummaryCard
                label="Valid"
                value={
                  preview.valid_rows
                }
              />

              <SummaryCard
                label="Errors"
                value={
                  preview.error_rows
                }
              />

              <SummaryCard
                label="Ready to Import"
                value={
                  preview.can_import
                    ? "Yes"
                    : "No"
                }
              />

            </div>

          )}


          {/* ORGANISATION REVIEW */}

          {loadingOrganization && (

            <div className="mt-8 bg-white border border-slate-200 rounded-2xl p-8 text-slate-500">
              Analysing departments and positions...
            </div>

          )}


          {organization && (

            <div className="mt-8 bg-white border border-slate-200 rounded-2xl shadow-sm overflow-hidden">

              <div className="p-6 border-b border-slate-200">

                <h2 className="text-xl font-bold text-slate-900">
                  Organisation Review
                </h2>

                <p className="text-sm text-slate-500 mt-1">
                  Review departments and positions detected
                  in the spreadsheet before creating anything.
                </p>

              </div>


              <div className="grid grid-cols-1 md:grid-cols-3 gap-4 p-6 bg-slate-50">

                <SummaryCard
                  label="Departments Detected"
                  value={
                    organization.total_departments
                  }
                />

                <SummaryCard
                  label="Missing Departments"
                  value={
                    organization.missing_departments
                  }
                />

                <SummaryCard
                  label="Missing Positions"
                  value={
                    organization.missing_positions
                  }
                />

              </div>


              <div className="divide-y divide-slate-200">

                {organization.departments.map(
                  (
                    department
                  ) => (

                    <DepartmentReview
                      key={
                        department.name
                      }
                      department={
                        department
                      }
                      selectedDepartment={
                        selectedDepartments[
                          department.name
                        ] ?? false
                      }
                      selectedPositions={
                        selectedPositions
                      }
                      onDepartmentChange={(
                        checked
                      ) => {
                        setSelectedDepartments(
                          (current) => ({
                            ...current,
                            [department.name]:
                              checked,
                          })
                        );

                        setSelectedPositions(
                          (current) => {
                            const next = {
                              ...current,
                            };

                            for (
                              const position
                              of department.positions
                            ) {
                              if (!position.exists) {
                                next[
                                  `${department.name}::${position.name}`
                                ] = checked;
                              }
                            }

                            return next;
                          }
                        );
                      }}
                      onPositionChange={(
                        positionName,
                        checked
                      ) => {
                        setSelectedPositions(
                          (current) => ({
                            ...current,
                            [`${department.name}::${positionName}`]:
                              checked,
                          })
                        );

                        if (
                          checked &&
                          !department.exists
                        ) {
                          setSelectedDepartments(
                            (current) => ({
                              ...current,
                              [department.name]:
                                true,
                            })
                          );
                        }
                      }}
                    />

                  )
                )}

              </div>


              {organizationMessage && (

                <div className="mx-6 mb-4 bg-green-50 border border-green-200 text-green-700 rounded-xl p-4">
                  {organizationMessage}
                </div>

              )}


              {(organization.missing_departments >
                0 ||
                organization.missing_positions >
                  0) && (

                <div className="p-6 border-t border-slate-200 flex flex-col md:flex-row md:items-center md:justify-between gap-4">

                  <div>
                    <div className="font-semibold text-slate-900">
                      Create approved organisation records
                    </div>

                    <div className="text-sm text-slate-500 mt-1">
                      Only checked missing departments and positions will be created.
                    </div>
                  </div>

                  <button
                    type="button"
                    disabled={
                      creatingOrganization
                    }
                    onClick={
                      createSelectedOrganization
                    }
                    className="bg-blue-600 hover:bg-blue-700 disabled:bg-slate-300 disabled:cursor-not-allowed text-white px-6 py-3 rounded-xl font-semibold"
                  >
                    {creatingOrganization
                      ? "Creating..."
                      : "Create Selected Departments & Positions"}
                  </button>

                </div>

              )}


              {(organization.missing_departments >
                0 ||
                organization.missing_positions >
                  0) && (

                <div className="p-6 bg-amber-50 border-t border-amber-200">

                  <div className="font-semibold text-amber-900">
                    Organisation setup required
                  </div>

                  <div className="text-sm text-amber-700 mt-1">
                    Missing departments and positions must
                    be reviewed before employee import can
                    proceed.
                  </div>

                </div>

              )}

            </div>

          )}


          {/* VALIDATION ERRORS */}

          {preview &&
            errorRows.length >
              0 && (

              <div className="mt-8 bg-white border border-slate-200 rounded-2xl shadow-sm overflow-hidden">

                <div className="px-6 py-5 border-b border-slate-200">

                  <h2 className="font-bold text-slate-900 text-lg">
                    Validation Errors
                  </h2>

                  <p className="text-sm text-slate-500 mt-1">
                    {errorRows.length} spreadsheet rows
                    require attention.
                  </p>

                </div>


                <div className="overflow-x-auto max-h-[600px] overflow-y-auto">

                  <table className="w-full text-sm">

                    <thead className="bg-slate-50 text-slate-600 sticky top-0">

                      <tr>

                        <th className="text-left px-6 py-3">
                          Row
                        </th>

                        <th className="text-left px-6 py-3">
                          Employee ID
                        </th>

                        <th className="text-left px-6 py-3">
                          Employee
                        </th>

                        <th className="text-left px-6 py-3">
                          Department
                        </th>

                        <th className="text-left px-6 py-3">
                          Error
                        </th>

                      </tr>

                    </thead>


                    <tbody className="divide-y divide-slate-100">

                      {errorRows.map(
                        (row) => (

                          <tr
                            key={
                              row.row
                            }
                            className="hover:bg-slate-50"
                          >

                            <td className="px-6 py-4 font-semibold text-slate-900">
                              {row.row}
                            </td>


                            <td className="px-6 py-4 text-slate-700">
                              {row.data.employee_id ||
                                "—"}
                            </td>


                            <td className="px-6 py-4 text-slate-700">

                              {[
                                row.data.first_name,
                                row.data.middle_name,
                                row.data.last_name,
                              ]
                                .filter(
                                  Boolean
                                )
                                .join(
                                  " "
                                ) ||
                                row.data.full_name ||
                                "—"}

                            </td>


                            <td className="px-6 py-4 text-slate-700">
                              {row.data.department_name ||
                                "—"}
                            </td>


                            <td className="px-6 py-4">

                              <div className="space-y-1">

                                {row.errors.map(
                                  (
                                    message,
                                    index
                                  ) => (

                                    <div
                                      key={
                                        index
                                      }
                                      className="text-red-600"
                                    >
                                      {message}
                                    </div>

                                  )
                                )}

                              </div>

                            </td>

                          </tr>

                        )
                      )}

                    </tbody>

                  </table>

                </div>

              </div>

            )}


            {importMessage && (

              <div className="mt-6 bg-green-50 border border-green-200 text-green-700 rounded-xl p-4">
                {importMessage}
              </div>

            )}


          {/* FINAL IMPORT */}

          {preview && (

            <div className="mt-8 bg-white border border-slate-200 rounded-2xl p-6 flex items-center justify-between">

              <div>

                <div className="font-semibold text-slate-900">
                  Final Employee Import
                </div>

                <div className="text-sm text-slate-500 mt-1">
                  Validated employees will be created in the
                  HRM and then shown in the employee list.
                </div>

              </div>


              <button
                type="button"
                disabled={
                  !canImport
                }
                onClick={
                  importEmployees
                }
                className={
                  canImport
                    ? "bg-blue-600 hover:bg-blue-700 text-white px-6 py-3 rounded-xl font-semibold"
                    : "bg-slate-300 text-white px-6 py-3 rounded-xl font-semibold cursor-not-allowed"
                }
              >
                {importing
                  ? "Importing..."
                  : "Import Employees"}
              </button>

            </div>

          )}

        </div>

      </main>

    </div>
  );
}


function DepartmentReview({
  department,
  selectedDepartment,
  selectedPositions,
  onDepartmentChange,
  onPositionChange,
}: {
  department:
    OrganizationDepartment;
  selectedDepartment:
    boolean;
  selectedPositions:
    Record<string, boolean>;
  onDepartmentChange:
    (checked: boolean) => void;
  onPositionChange:
    (
      positionName: string,
      checked: boolean
    ) => void;
}) {

  return (

    <div className="p-6">

      <div className="flex items-center justify-between">

        <div className="flex items-center gap-3">

          {!department.exists && (

            <input
              type="checkbox"
              checked={
                selectedDepartment
              }
              onChange={(event) =>
                onDepartmentChange(
                  event.target.checked
                )
              }
              className="w-4 h-4"
            />

          )}

          <StatusIcon
            exists={
              department.exists
            }
          />

          <div>

            <div className="font-bold text-slate-900">
              {department.name}
            </div>

            <div className="text-xs text-slate-500 mt-1">
              {department.positions.length} position
              {department.positions.length ===
              1
                ? ""
                : "s"}
            </div>

          </div>

        </div>


        <StatusBadge
          exists={
            department.exists
          }
          type="Department"
        />

      </div>


      {department.positions.length >
        0 && (

        <div className="mt-5 ml-8 border-l border-slate-200 pl-5 space-y-3">

          {department.positions.map(
            (position) => {

              const positionKey =
                `${department.name}::${position.name}`;

              return (

                <div
                  key={
                    position.name
                  }
                  className="flex items-center justify-between gap-4"
                >

                  <div className="flex items-center gap-3">

                    {!position.exists && (

                      <input
                        type="checkbox"
                        checked={
                          selectedPositions[
                            positionKey
                          ] ?? false
                        }
                        onChange={(event) =>
                          onPositionChange(
                            position.name,
                            event.target.checked
                          )
                        }
                        className="w-4 h-4"
                      />

                    )}

                    <StatusIcon
                      exists={
                        position.exists
                      }
                    />

                    <span className="text-sm text-slate-700">
                      {position.name}
                    </span>

                  </div>


                  <StatusBadge
                    exists={
                      position.exists
                    }
                    type="Position"
                  />

                </div>

              );
            }
          )}

        </div>

      )}

    </div>
  );
}


function StatusIcon({
  exists,
}: {
  exists: boolean;
}) {

  return (

    <div
      className={[
        "w-6 h-6 rounded-full flex items-center justify-center text-xs font-bold",

        exists
          ? "bg-green-100 text-green-700"
          : "bg-amber-100 text-amber-700",

      ].join(" ")}
    >
      {exists
        ? "✓"
        : "+"}
    </div>

  );
}


function StatusBadge({
  exists,
  type,
}: {
  exists: boolean;
  type: string;
}) {

  return (

    <span
      className={[
        "text-xs font-semibold rounded-full px-3 py-1",

        exists
          ? "bg-green-100 text-green-700"
          : "bg-amber-100 text-amber-700",

      ].join(" ")}
    >

      {exists
        ? "Exists"
        : `New ${type}`}

    </span>

  );
}


function SummaryCard({
  label,
  value,
}: {
  label: string;
  value:
    | string
    | number;
}) {

  return (

    <div className="bg-white border border-slate-200 rounded-2xl p-5 shadow-sm">

      <div className="text-sm text-slate-500">
        {label}
      </div>

      <div className="text-2xl font-bold text-slate-900 mt-2">
        {value}
      </div>

    </div>

  );
}


function formatFileSize(
  bytes: number
) {

  if (bytes < 1024) {
    return `${bytes} bytes`;
  }

  if (
    bytes <
    1024 * 1024
  ) {
    return `${(
      bytes / 1024
    ).toFixed(1)} KB`;
  }

  return `${(
    bytes /
    (1024 * 1024)
  ).toFixed(1)} MB`;
}

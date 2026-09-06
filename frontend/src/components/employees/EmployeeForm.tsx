"use client";

import type { FormEvent } from "react";

export type Department = {
  id: number;
  name: string;
};

export type Position = {
  id: number;
  name: string;
  department: number;
};

export type EmployeeFormValues = {
  employee_id: string;
  biometric_user_id: string;
  first_name: string;
  middle_name: string;
  last_name: string;
  department: string;
  position: string;
  phone: string;
  email: string;
  date_of_birth: string;
  employment_date: string;
  employment_type: string;
  employment_category: string;
  basic_salary: string;
  lives_in_company_hostel: boolean;
  hostel_room_number: string;
  status: string;
};

type EmployeeFormProps = {
  values: EmployeeFormValues;
  departments: Department[];
  positions: Position[];
  loadingPositions: boolean;
  saving: boolean;
  error: string;
  onValueChange: (
    field: keyof EmployeeFormValues,
    value: string | boolean
  ) => void;
  onDepartmentChange: (departmentId: string) => void;
  onSubmit: (event: FormEvent<HTMLFormElement>) => void;
  onCancel: () => void;
};

export default function EmployeeForm({
  values,
  departments,
  positions,
  loadingPositions,
  saving,
  error,
  onValueChange,
  onDepartmentChange,
  onSubmit,
  onCancel,
}: EmployeeFormProps) {
  return (
    <form
      onSubmit={onSubmit}
      className="mt-6 rounded-2xl border border-slate-200 bg-white p-7 shadow-sm"
    >
      <FormSection title="Personal Information">
        <div className="grid grid-cols-1 gap-5 md:grid-cols-2">
          <Field
            label="Employee / Staff Number"
            value={values.employee_id}
            readOnly
            helpText="Employee ID cannot be changed."
          />
          <Field
            label="First Name"
            value={values.first_name}
            required
            onChange={(value) => onValueChange("first_name", value)}
          />
          <Field
            label="Middle Name"
            value={values.middle_name}
            onChange={(value) => onValueChange("middle_name", value)}
          />
          <Field
            label="Last Name"
            value={values.last_name}
            required
            onChange={(value) => onValueChange("last_name", value)}
          />
          <Field
            label="Phone"
            value={values.phone}
            onChange={(value) => onValueChange("phone", value)}
          />
          <Field
            label="Email"
            type="email"
            value={values.email}
            onChange={(value) => onValueChange("email", value)}
          />
          <Field
            label="Date of Birth"
            type="date"
            value={values.date_of_birth}
            onChange={(value) => onValueChange("date_of_birth", value)}
          />
        </div>
      </FormSection>

      <FormSection title="Employment">
        <div className="grid grid-cols-1 gap-5 md:grid-cols-2">
          <SelectField
            label="Department"
            value={values.department}
            onChange={onDepartmentChange}
          >
            <option value="">Select department</option>
            {departments.map((department) => (
              <option key={department.id} value={department.id}>
                {department.name}
              </option>
            ))}
          </SelectField>

          <SelectField
            label="Position"
            value={values.position}
            disabled={!values.department || loadingPositions}
            onChange={(value) => onValueChange("position", value)}
          >
            <option value="">
              {loadingPositions
                ? "Loading positions..."
                : values.department
                  ? "Select position"
                  : "Select department first"}
            </option>
            {positions.map((position) => (
              <option key={position.id} value={position.id}>
                {position.name}
              </option>
            ))}
          </SelectField>

          <Field
            label="Employment Date"
            type="date"
            value={values.employment_date}
            onChange={(value) => onValueChange("employment_date", value)}
          />

          <SelectField
            label="Employment Type"
            value={values.employment_type}
            onChange={(value) => onValueChange("employment_type", value)}
          >
            <option value="permanent">Permanent</option>
            <option value="contract">Contract</option>
            <option value="casual">Casual</option>
            <option value="intern">Intern</option>
            <option value="nysc">NYSC</option>
            <option value="expatriate">Expatriate</option>
          </SelectField>

          <SelectField
            label="Employment Category"
            value={values.employment_category}
            onChange={(value) => onValueChange("employment_category", value)}
          >
            <option value="staff">Staff</option>
            <option value="management">Management</option>
            <option value="executive">Executive</option>
          </SelectField>

          <SelectField
            label="Status"
            value={values.status}
            onChange={(value) => onValueChange("status", value)}
          >
            <option value="active">Active</option>
            <option value="inactive">Inactive</option>
            <option value="suspended">Suspended</option>
            <option value="terminated">Terminated</option>
          </SelectField>
        </div>
      </FormSection>

      <FormSection title="Salary">
        <div className="max-w-md">
          <Field
            label="Monthly Basic Salary"
            type="number"
            value={values.basic_salary}
            min="0"
            step="0.01"
            onChange={(value) => onValueChange("basic_salary", value)}
          />
        </div>
      </FormSection>

      <FormSection title="Accommodation">
        <div className="rounded-xl border border-slate-200 bg-slate-50 p-5">
          <label className="flex cursor-pointer items-center gap-3">
            <input
              type="checkbox"
              checked={values.lives_in_company_hostel}
              onChange={(event) => {
                const livesInHostel = event.target.checked;
                onValueChange("lives_in_company_hostel", livesInHostel);

                if (!livesInHostel) {
                  onValueChange("hostel_room_number", "");
                }
              }}
              className="h-4 w-4"
            />
            <span>
              <span className="block text-sm font-medium text-slate-800">
                Employee lives in company hostel
              </span>
              <span className="mt-1 block text-xs text-slate-500">
                Enable this when company accommodation has been assigned.
              </span>
            </span>
          </label>

          {values.lives_in_company_hostel && (
            <div className="mt-5 max-w-md">
              <Field
                label="Hostel Room Number"
                value={values.hostel_room_number}
                required
                placeholder="Example: B12"
                onChange={(value) => onValueChange("hostel_room_number", value)}
              />
            </div>
          )}
        </div>
      </FormSection>

      <FormSection title="Biometrics" last>
        <div className="max-w-md">
          <Field
            label="Biometric User ID"
            value={values.biometric_user_id}
            onChange={(value) => onValueChange("biometric_user_id", value)}
          />
        </div>
      </FormSection>

      {error && (
        <div className="mt-6 rounded-xl border border-red-200 bg-red-50 p-4 text-sm text-red-700">
          {error}
        </div>
      )}

      <div className="mt-8 flex justify-end gap-3">
        <button
          type="button"
          onClick={onCancel}
          disabled={saving}
          className="rounded-xl border border-slate-300 px-6 py-3 font-medium text-slate-700 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-60"
        >
          Cancel
        </button>
        <button
          type="submit"
          disabled={saving}
          className="rounded-xl bg-blue-600 px-6 py-3 font-semibold text-white hover:bg-blue-700 disabled:cursor-not-allowed disabled:bg-blue-400"
        >
          {saving ? "Saving..." : "Save Changes"}
        </button>
      </div>
    </form>
  );
}

function FormSection({
  title,
  children,
  last = false,
}: {
  title: string;
  children: React.ReactNode;
  last?: boolean;
}) {
  return (
    <section className={last ? "" : "border-b border-slate-200 py-8 first:pt-0"}>
      <h2 className="mb-5 text-lg font-bold text-slate-900">{title}</h2>
      {children}
    </section>
  );
}

function Field({
  label,
  value,
  onChange,
  type = "text",
  required = false,
  readOnly = false,
  placeholder = "",
  helpText,
  min,
  step,
}: {
  label: string;
  value: string;
  onChange?: (value: string) => void;
  type?: string;
  required?: boolean;
  readOnly?: boolean;
  placeholder?: string;
  helpText?: string;
  min?: string;
  step?: string;
}) {
  return (
    <div>
      <label className="mb-2 block text-sm font-medium text-slate-700">
        {label}
        {required && <span className="ml-1 text-red-500">*</span>}
      </label>
      <input
        type={type}
        required={required}
        readOnly={readOnly}
        value={value}
        placeholder={placeholder}
        min={min}
        step={step}
        onChange={(event) => onChange?.(event.target.value)}
        className="w-full rounded-xl border border-slate-300 px-4 py-3 text-slate-900 outline-none focus:ring-2 focus:ring-blue-500 read-only:cursor-not-allowed read-only:bg-slate-100"
      />
      {helpText && <p className="mt-1 text-xs text-slate-500">{helpText}</p>}
    </div>
  );
}

function SelectField({
  label,
  value,
  onChange,
  disabled = false,
  children,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  disabled?: boolean;
  children: React.ReactNode;
}) {
  return (
    <div>
      <label className="mb-2 block text-sm font-medium text-slate-700">
        {label}
      </label>
      <select
        value={value}
        disabled={disabled}
        onChange={(event) => onChange(event.target.value)}
        className="w-full rounded-xl border border-slate-300 bg-white px-4 py-3 text-slate-900 outline-none focus:ring-2 focus:ring-blue-500 disabled:bg-slate-100 disabled:text-slate-400"
      >
        {children}
      </select>
    </div>
  );
}

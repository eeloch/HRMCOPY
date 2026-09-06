"use client";

import type { FormEvent } from "react";
import { useState } from "react";

import { apiFetch } from "@/lib/api";

const documentTypes = [
    { value: "appointment", label: "Appointment Letter" },
    { value: "contract", label: "Employment Contract" },
    { value: "national_id", label: "National ID" },
    { value: "passport", label: "International Passport" },
    { value: "drivers_license", label: "Driver's Licence" },
    { value: "passport_photo", label: "Passport Photograph" },
    { value: "medical", label: "Medical Certificate" },
    { value: "guarantor", label: "Guarantor Form" },
    { value: "certificate", label: "Certificate" },
    { value: "promotion", label: "Promotion Letter" },
    { value: "warning", label: "Warning Letter" },
    { value: "termination", label: "Termination Letter" },
    { value: "other", label: "Other" },
];

export default function DocumentUploadModal({
    employeeId,
    onClose,
    onUploaded,
}: {
    employeeId: number;
    onClose: () => void;
    onUploaded: () => void;
}) {
    const [documentType, setDocumentType] = useState("appointment");
    const [title, setTitle] = useState("");
    const [description, setDescription] = useState("");
    const [expiryDate, setExpiryDate] = useState("");
    const [file, setFile] = useState<File | null>(null);
    const [uploading, setUploading] = useState(false);
    const [error, setError] = useState("");

    async function uploadDocument(event: FormEvent<HTMLFormElement>) {
        event.preventDefault();

        if (!file) {
            setError("Choose a document to upload.");
            return;
        }

        setUploading(true);
        setError("");

        const formData = new FormData();
        formData.append("employee", String(employeeId));
        formData.append("document_type", documentType);
        formData.append("title", title.trim());
        formData.append("description", description.trim());
        formData.append("file", file);

        if (expiryDate) {
            formData.append("expiry_date", expiryDate);
        }

        try {
            const response = await apiFetch("/documents/", {
                method: "POST",
                body: formData,
            });

            if (!response.ok) {
                setError(await responseError(response));
                return;
            }

            onUploaded();
        } catch (uploadError) {
            console.error(uploadError);
            setError("Unable to upload document. Please try again.");
        } finally {
            setUploading(false);
        }
    }

    return (
        <div
            className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/40 p-4"
            role="dialog"
            aria-modal="true"
            aria-labelledby="upload-document-title"
        >
            <form
                onSubmit={uploadDocument}
                className="max-h-[90vh] w-full max-w-2xl overflow-y-auto rounded-2xl border border-slate-200 bg-white p-6 shadow-xl"
            >
                <div className="flex items-start justify-between gap-4">
                    <div>
                        <h2
                            id="upload-document-title"
                            className="text-xl font-bold text-slate-900"
                        >
                            Upload Document
                        </h2>
                        <p className="mt-1 text-sm text-slate-500">
                            Add a document to this employee's record.
                        </p>
                    </div>
                    <button
                        type="button"
                        onClick={onClose}
                        disabled={uploading}
                        aria-label="Close upload dialog"
                        className="rounded-lg px-2 py-1 text-xl leading-none text-slate-500 hover:bg-slate-100 disabled:cursor-not-allowed disabled:opacity-50"
                    >
                        x
                    </button>
                </div>

                <div className="mt-6 grid grid-cols-1 gap-5 md:grid-cols-2">
                    <FieldLabel label="Document Type" required>
                        <select
                            value={documentType}
                            onChange={(event) => setDocumentType(event.target.value)}
                            className={inputClassName}
                        >
                            {documentTypes.map((type) => (
                                <option key={type.value} value={type.value}>
                                    {type.label}
                                </option>
                            ))}
                        </select>
                    </FieldLabel>

                    <FieldLabel label="Title" required>
                        <input
                            type="text"
                            required
                            value={title}
                            onChange={(event) => setTitle(event.target.value)}
                            placeholder="Example: Signed employment contract"
                            className={inputClassName}
                        />
                    </FieldLabel>

                    <FieldLabel label="Expiry Date (optional)">
                        <input
                            type="date"
                            value={expiryDate}
                            onChange={(event) => setExpiryDate(event.target.value)}
                            className={inputClassName}
                        />
                    </FieldLabel>

                    <FieldLabel label="File" required>
                        <input
                            type="file"
                            required
                            onChange={(event) => setFile(event.target.files?.[0] || null)}
                            className="block w-full cursor-pointer rounded-xl border border-slate-300 bg-white px-3 py-2.5 text-sm text-slate-700 file:mr-3 file:rounded-lg file:border-0 file:bg-blue-50 file:px-3 file:py-1.5 file:font-medium file:text-blue-700 hover:file:bg-blue-100"
                        />
                    </FieldLabel>

                    <div className="md:col-span-2">
                        <FieldLabel label="Description">
                            <textarea
                                value={description}
                                onChange={(event) => setDescription(event.target.value)}
                                rows={4}
                                placeholder="Optional notes about this document"
                                className={inputClassName}
                            />
                        </FieldLabel>
                    </div>
                </div>

                {error && (
                    <div className="mt-5 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
                        {error}
                    </div>
                )}

                <div className="mt-7 flex justify-end gap-3">
                    <button
                        type="button"
                        onClick={onClose}
                        disabled={uploading}
                        className="rounded-xl border border-slate-300 px-5 py-2.5 text-sm font-medium text-slate-700 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50"
                    >
                        Cancel
                    </button>
                    <button
                        type="submit"
                        disabled={uploading}
                        className="rounded-xl bg-blue-600 px-5 py-2.5 text-sm font-semibold text-white hover:bg-blue-700 disabled:cursor-not-allowed disabled:bg-blue-400"
                    >
                        {uploading ? "Uploading..." : "Upload Document"}
                    </button>
                </div>
            </form>
        </div>
    );
}

const inputClassName =
    "w-full rounded-xl border border-slate-300 bg-white px-4 py-3 text-slate-900 outline-none focus:ring-2 focus:ring-blue-500";

function FieldLabel({
    label,
    required = false,
    children,
}: {
    label: string;
    required?: boolean;
    children: React.ReactNode;
}) {
    return (
        <label className="block text-sm font-medium text-slate-700">
            <span className="mb-2 block">
                {label}
                {required && <span className="ml-1 text-red-500">*</span>}
            </span>
            {children}
        </label>
    );
}

async function responseError(response: Response) {
    const data: unknown = await response.json().catch(() => null);

    if (!data || typeof data !== "object") {
        return "Unable to upload document.";
    }

    return Object.entries(data)
        .map(([field, value]) => {
            const message = Array.isArray(value) ? value.join(" ") : String(value);
            return `${field}: ${message}`;
        })
        .join(" | ");
}

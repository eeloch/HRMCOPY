"use client";

import { useEffect, useState } from "react";
import { apiFetch } from "@/lib/api";
import DocumentUploadModal from "@/components/documents/DocumentUploadModal";

type Document = {
    id: number;
    document_type: string;
    title: string;
    file_name: string | null;
    download_url: string | null;
    uploaded_at: string;
    expiry_date: string | null;
    is_active: boolean;
};

type DocumentFilter =
    | "all"
    | "appointment"
    | "contract"
    | "identity"
    | "medical"
    | "training"
    | "certificate"
    | "other";

const documentFilters: {
    label: string;
    value: DocumentFilter;
}[] = [
    { label: "All", value: "all" },
    { label: "Appointment", value: "appointment" },
    { label: "Contract", value: "contract" },
    { label: "Identity", value: "identity" },
    { label: "Medical", value: "medical" },
    { label: "Training", value: "training" },
    { label: "Certificate", value: "certificate" },
    { label: "Other", value: "other" },
];

const documentTypeLabels: Record<string, string> = {
    appointment: "Appointment Letter",
    certificate: "Certificate",
    contract: "Employment Contract",
    drivers_license: "Driver's Licence",
    guarantor: "Guarantor Form",
    identity: "Identity",
    medical: "Medical",
    national_id: "National ID",
    other: "Other",
    passport: "International Passport",
    passport_photo: "Passport Photograph",
    promotion: "Promotion Letter",
    termination: "Termination Letter",
    training: "Training",
    warning: "Warning Letter",
};

function getFileIcon(fileName: string | null) {
    const extension = fileName
        ?.split(".")
        .pop()
        ?.toLowerCase();

    if (extension === "pdf") {
        return "📄";
    }

    if (
        extension === "png" ||
        extension === "jpg" ||
        extension === "jpeg" ||
        extension === "gif" ||
        extension === "webp"
    ) {
        return "🖼️";
    }

    if (extension === "doc" || extension === "docx") {
        return "📝";
    }

    if (extension === "xls" || extension === "xlsx") {
        return "📊";
    }

    return "📁";
}

function getDocumentTypeLabel(documentType: string) {
    if (documentTypeLabels[documentType]) {
        return documentTypeLabels[documentType];
    }

    return documentType
        .replace(/_/g, " ")
        .replace(/\b\w/g, (character) =>
            character.toUpperCase()
        );
}

function formatDate(value: string) {
    return new Date(value).toLocaleDateString(
        "en-GB",
        {
            day: "2-digit",
            month: "short",
            year: "numeric",
        }
    );
}

async function openDocumentFile(
    doc: Document,
    mode: "view" | "download"
) {
    if (!doc.download_url) {
        window.alert("This document has no file attached.");
        return;
    }

    try {
        /*
         * Downloads must go through apiFetch (not a plain <a href>) so the
         * request carries the Authorization header the API requires - a
         * bare link would hit the endpoint with no bearer token and get a
         * 401.
         */
        const response = await apiFetch(doc.download_url);

        if (!response.ok) {
            throw new Error("Unable to open this document.");
        }

        const blob = await response.blob();
        const blobUrl = window.URL.createObjectURL(blob);
        const link = window.document.createElement("a");

        link.href = blobUrl;

        if (mode === "download") {
            link.download = doc.file_name ?? doc.title;
        } else {
            link.target = "_blank";
            link.rel = "noreferrer";
        }

        window.document.body.appendChild(link);
        link.click();
        window.document.body.removeChild(link);

        window.setTimeout(
            () => window.URL.revokeObjectURL(blobUrl),
            60_000
        );

    } catch (err) {
        console.error(err);
        window.alert(
            err instanceof Error
                ? err.message
                : "Unable to open this document."
        );
    }
}

export default function DocumentList({
    employeeId,
}: {
    employeeId: number;
}) {

    const [documents, setDocuments] = useState<Document[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState("");
    const [deletingId, setDeletingId] = useState<number | null>(null);
    const [openingId, setOpeningId] = useState<number | null>(null);
    const [search, setSearch] = useState("");
    const [activeFilter, setActiveFilter] =
        useState<DocumentFilter>("all");
    const [uploadModalOpen, setUploadModalOpen] = useState(false);
    const [successMessage, setSuccessMessage] = useState("");

    const searchTerm = search.trim().toLowerCase();
    const filteredDocuments = documents.filter(
        (document) => {
            const matchesSearch =
                document.title.toLowerCase().includes(searchTerm) ||
                document.document_type.toLowerCase().includes(searchTerm) ||
                getDocumentTypeLabel(document.document_type)
                    .toLowerCase()
                    .includes(searchTerm);

            const matchesFilter =
                activeFilter === "all" ||
                document.document_type === activeFilter;

            return matchesSearch && matchesFilter;
        }
    );

    useEffect(() => {
        loadDocuments();
    }, [employeeId]);

    async function loadDocuments() {

        setLoading(true);
        setError("");

        try {

            const response = await apiFetch(
                `/documents/?employee=${employeeId}`
            );

            if (!response.ok) {
                throw new Error(
                    "Unable to load documents."
                );
            }

            const data = await response.json();

            setDocuments(data);

        } catch (err) {

            setError(
                err instanceof Error
                    ? err.message
                    : "Unable to load documents."
            );

        } finally {

            setLoading(false);

        }
    }

    async function handleOpen(
        document: Document,
        mode: "view" | "download"
    ) {
        setOpeningId(document.id);
        await openDocumentFile(document, mode);
        setOpeningId(null);
    }

    async function deleteDocument(document: Document) {

        const confirmed = window.confirm(
            "Are you sure you want to permanently delete this document?"
        );

        if (!confirmed) {
            return;
        }

        setDeletingId(document.id);

        try {

            const response = await apiFetch(
                `/documents/${document.id}/`,
                {
                    method: "DELETE",
                }
            );

            if (!response.ok) {
                throw new Error(
                    "Unable to delete document."
                );
            }

            setDocuments((current) =>
                current.filter(
                    (item) => item.id !== document.id
                )
            );

        } catch (err) {

            console.error(err);

            window.alert(
                "Unable to delete document."
            );

        } finally {

            setDeletingId(null);

        }
    }

    return (
        <div className="space-y-4">
            <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                <p className="text-sm text-slate-500">
                    Upload, review, and manage this employee's records.
                </p>
                <button
                    type="button"
                    onClick={() => {
                        setSuccessMessage("");
                        setUploadModalOpen(true);
                    }}
                    className="rounded-xl bg-blue-600 px-5 py-2.5 font-semibold text-white shadow-sm hover:bg-blue-700"
                >
                    Upload Document
                </button>
            </div>

            {successMessage && (
                <div className="rounded-xl border border-green-200 bg-green-50 px-4 py-3 text-sm text-green-800">
                    {successMessage}
                </div>
            )}

            {error && (
                <div className="rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
                    {error}
                </div>
            )}

            {loading ? (
                <div className="bg-white border rounded-xl p-8 text-center text-slate-500">
                    Loading documents...
                </div>
            ) : (
                <>
            <input
                type="search"
                value={search}
                onChange={(event) =>
                    setSearch(event.target.value)
                }
                placeholder="Search documents..."
                className="w-full border border-slate-300 rounded-xl px-4 py-3 text-slate-900 outline-none focus:ring-2 focus:ring-blue-500"
            />

            <div className="flex flex-wrap gap-2">
                {documentFilters.map((filter) => (
                    <button
                        key={filter.value}
                        type="button"
                        onClick={() =>
                            setActiveFilter(filter.value)
                        }
                        className={
                            activeFilter === filter.value
                                ? "bg-blue-600 text-white px-4 py-2 rounded-full text-sm font-medium"
                                : "bg-white border border-slate-300 hover:bg-slate-50 text-slate-700 px-4 py-2 rounded-full text-sm font-medium"
                        }
                    >
                        {filter.label}
                    </button>
                ))}
            </div>

            {!documents.length ? (
                <div className="bg-white border rounded-xl p-8 text-center text-slate-500">
                    No documents uploaded.
                </div>
            ) : !filteredDocuments.length && (
                <div className="bg-white border rounded-xl p-8 text-center text-slate-500">
                    No documents found.
                </div>
            )}

            {filteredDocuments.map((document) => (
                <div
                    key={document.id}
                    className="border border-slate-200 rounded-2xl bg-white p-5 shadow-sm"
                >
                    <div className="flex flex-col gap-5 lg:flex-row lg:items-center lg:justify-between">
                        <div className="flex items-start gap-4">
                            <div className="w-14 h-14 shrink-0 rounded-2xl bg-blue-50 flex items-center justify-center text-3xl">
                                {getFileIcon(document.file_name)}
                            </div>

                            <div>
                                <div className="font-semibold text-slate-900">
                                    {document.title}
                                </div>

                                <div className="text-sm text-slate-500 mt-1">
                                    {getDocumentTypeLabel(
                                        document.document_type
                                    )}
                                </div>

                                <div className="flex flex-wrap gap-x-4 gap-y-1 text-sm text-slate-500 mt-3">
                                    <span>
                                        Uploaded {formatDate(
                                            document.uploaded_at
                                        )}
                                    </span>

                                    {document.expiry_date && (
                                        <span>
                                            Expires {formatDate(
                                                document.expiry_date
                                            )}
                                        </span>
                                    )}
                                </div>
                            </div>
                        </div>

                        <div className="flex flex-wrap items-center gap-3 lg:justify-end">
                            <button
                                type="button"
                                disabled={openingId === document.id}
                                onClick={() =>
                                    handleOpen(document, "view")
                                }
                                className="border border-slate-300 hover:bg-slate-50 disabled:bg-slate-100 disabled:text-slate-400 disabled:cursor-not-allowed text-slate-700 px-4 py-2 rounded-xl font-medium text-sm"
                            >
                                View
                            </button>

                            <button
                                type="button"
                                disabled={openingId === document.id}
                                onClick={() =>
                                    handleOpen(document, "download")
                                }
                                className="bg-blue-600 hover:bg-blue-700 disabled:bg-blue-300 text-white px-4 py-2 rounded-xl font-medium text-sm"
                            >
                                Download
                            </button>

                            <button
                                type="button"
                                disabled={deletingId === document.id}
                                onClick={() =>
                                    deleteDocument(document)
                                }
                                className="border border-red-200 hover:bg-red-50 disabled:bg-slate-100 disabled:text-slate-400 disabled:cursor-not-allowed text-red-600 px-4 py-2 rounded-xl font-medium text-sm"
                            >
                                {deletingId === document.id
                                    ? "Deleting..."
                                    : "Delete"}
                            </button>
                        </div>
                    </div>
                </div>
            ))}
                </>
            )}

            {uploadModalOpen && (
                <DocumentUploadModal
                    employeeId={employeeId}
                    onClose={() => setUploadModalOpen(false)}
                    onUploaded={() => {
                        setUploadModalOpen(false);
                        setSuccessMessage("Document uploaded successfully.");
                        void loadDocuments();
                    }}
                />
            )}
        </div>
    );
}

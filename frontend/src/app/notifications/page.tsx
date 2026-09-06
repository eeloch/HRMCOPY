"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";

import Sidebar from "@/components/Sidebar";
import { NotificationItem } from "@/components/notifications/NotificationItem";
import { getNotifications, markAllNotificationsRead, type Notification } from "@/components/notifications/types";
import { AppCard, PageHeader, Section } from "@/components/ui";
import { getAccessToken } from "@/lib/api";

const severityOptions = ["", "info", "success", "warning", "error"];

export default function NotificationsPage() {
  const router = useRouter();
  const [notifications, setNotifications] = useState<Notification[]>([]);
  const [page, setPage] = useState(1);
  const [totalPages, setTotalPages] = useState(1);
  const [count, setCount] = useState(0);
  const [tab, setTab] = useState<"all" | "unread">("all");
  const [severity, setSeverity] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => { if (!getAccessToken()) { router.push("/login"); return; } void loadNotifications(); }, [router, page]);
  async function loadNotifications(
    resetPage = false,
    nextTab = tab,
    nextSeverity = severity,
  ) {
    const nextPage = resetPage ? 1 : page;
    if (resetPage) setPage(1);
    setLoading(true); setError("");
    try { const data = await getNotifications({ page: String(nextPage), page_size: "20", ...(nextTab === "unread" ? { unread: "true" } : {}), ...(nextSeverity ? { severity: nextSeverity } : {}) }); setNotifications(data.results); setCount(data.count); setTotalPages(data.total_pages); }
    catch (loadError) { setError(loadError instanceof Error ? loadError.message : "Unable to load notifications."); }
    finally { setLoading(false); }
  }
  function changeTab(nextTab: "all" | "unread") { setTab(nextTab); void loadNotifications(true, nextTab, severity); }
  function changeSeverity(nextSeverity: string) { setSeverity(nextSeverity); void loadNotifications(true, tab, nextSeverity); }
  async function markAllRead() { try { await markAllNotificationsRead(); setNotifications((current) => current.map((notification) => ({ ...notification, is_read: true }))); void loadNotifications(); } catch (actionError) { setError(actionError instanceof Error ? actionError.message : "Unable to mark notifications as read."); } }
  return <div className="min-h-screen bg-slate-100"><Sidebar /><main className="ml-64 min-w-0 p-4 md:p-8"><PageHeader title="Notifications" description="Updates that are relevant to your account." actions={<button onClick={() => void markAllRead()} disabled={!notifications.some((notification) => !notification.is_read)} className="rounded-xl bg-blue-600 px-4 py-2.5 text-sm font-semibold text-white hover:bg-blue-700 disabled:cursor-not-allowed disabled:bg-blue-300">Mark all as read</button>} />{error ? <ErrorPanel message={error} retry={loadNotifications} /> : <><AppCard className="mb-6"><div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between"><div className="flex gap-2"><button onClick={() => changeTab("all")} className={`rounded-xl px-4 py-2 text-sm font-semibold ${tab === "all" ? "bg-slate-900 text-white" : "bg-slate-100 text-slate-600 hover:bg-slate-200"}`}>All</button><button onClick={() => changeTab("unread")} className={`rounded-xl px-4 py-2 text-sm font-semibold ${tab === "unread" ? "bg-slate-900 text-white" : "bg-slate-100 text-slate-600 hover:bg-slate-200"}`}>Unread</button></div><select value={severity} onChange={(event) => changeSeverity(event.target.value)} className="rounded-xl border border-slate-300 bg-white px-4 py-2.5 text-sm text-slate-700"><option value="">All severities</option>{severityOptions.slice(1).map((item) => <option key={item} value={item}>{capitalize(item)}</option>)}</select></div></AppCard><Section title="Notification Center" subtitle={`${count} notification${count === 1 ? "" : "s"}.`} actions={<button onClick={() => void loadNotifications()} className="rounded-xl border border-slate-300 px-4 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50">Refresh</button>}>{loading ? <CenterSkeleton /> : notifications.length ? <div>{notifications.map((notification) => <NotificationItem key={notification.id} notification={notification} onRead={(updated) => setNotifications((current) => current.map((item) => item.id === updated.id ? updated : item))} />)}</div> : <p className="p-10 text-center text-slate-500">No notifications found.</p>}<Pagination page={page} totalPages={totalPages} onChange={setPage} /></Section></>}</main></div>;
}
function Pagination({ page, totalPages, onChange }: { page: number; totalPages: number; onChange: (page: number) => void }) { return <div className="flex items-center justify-between border-t border-slate-200 px-5 py-4"><p className="text-sm text-slate-500">Page {page} of {totalPages}</p><div className="flex gap-2"><button type="button" disabled={page === 1} onClick={() => onChange(page - 1)} className="rounded-lg border border-slate-300 px-3 py-2 text-sm disabled:cursor-not-allowed disabled:opacity-50">Previous</button><button type="button" disabled={page >= totalPages} onClick={() => onChange(page + 1)} className="rounded-lg border border-slate-300 px-3 py-2 text-sm disabled:cursor-not-allowed disabled:opacity-50">Next</button></div></div>; }
function CenterSkeleton() { return <div className="space-y-3 p-5">{[1, 2, 3, 4].map((item) => <div key={item} className="h-24 animate-pulse rounded-xl bg-slate-100" />)}</div>; }
function ErrorPanel({ message, retry }: { message: string; retry: () => void }) { return <div className="rounded-2xl border border-red-200 bg-red-50 p-6 text-red-700"><p className="font-semibold">Unable to load notifications</p><p className="mt-1 text-sm">{message}</p><button onClick={() => void retry()} className="mt-4 rounded-xl bg-red-600 px-4 py-2.5 text-sm font-semibold text-white">Try Again</button></div>; }
function capitalize(value: string) { return value.charAt(0).toUpperCase() + value.slice(1); }

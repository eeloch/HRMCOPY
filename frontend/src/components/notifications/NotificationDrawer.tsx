"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { NotificationItem } from "./NotificationItem";
import { getNotifications, markAllNotificationsRead, type Notification } from "./types";

export function NotificationDrawer({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [notifications, setNotifications] = useState<Notification[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => { if (open) void loadNotifications(); }, [open]);
  async function loadNotifications() { setLoading(true); setError(""); try { const data = await getNotifications({ page: "1", page_size: "6" }); setNotifications(data.results); } catch (loadError) { setError(loadError instanceof Error ? loadError.message : "Unable to load notifications."); } finally { setLoading(false); } }
  async function markAllRead() { try { await markAllNotificationsRead(); setNotifications((current) => current.map((notification) => ({ ...notification, is_read: true }))); } catch (actionError) { setError(actionError instanceof Error ? actionError.message : "Unable to mark notifications as read."); } }
  if (!open) return null;
  return <><button aria-label="Close notifications" onClick={onClose} className="fixed inset-0 z-40 cursor-default bg-slate-950/20" /><aside className="fixed bottom-0 right-0 top-0 z-50 flex w-full max-w-md flex-col bg-white shadow-2xl" aria-label="Notifications"><div className="flex items-center justify-between border-b border-slate-200 px-5 py-4"><div><h2 className="text-lg font-bold text-slate-900">Notifications</h2><p className="text-sm text-slate-500">Your most recent updates</p></div><button onClick={onClose} className="rounded-lg p-2 text-slate-500 hover:bg-slate-100" aria-label="Close">×</button></div><div className="min-h-0 flex-1 overflow-y-auto">{loading ? <DrawerSkeleton /> : error ? <div className="p-6 text-sm text-red-700"><p>{error}</p><button onClick={() => void loadNotifications()} className="mt-3 font-semibold underline">Try Again</button></div> : notifications.length ? notifications.map((notification) => <NotificationItem key={notification.id} notification={notification} onRead={(updated) => setNotifications((current) => current.map((item) => item.id === updated.id ? updated : item))} />) : <p className="p-10 text-center text-sm text-slate-500">You have no notifications.</p>}</div><div className="flex items-center justify-between border-t border-slate-200 p-4"><button onClick={() => void markAllRead()} disabled={!notifications.some((notification) => !notification.is_read)} className="text-sm font-semibold text-blue-700 disabled:cursor-not-allowed disabled:text-slate-400">Mark all as read</button><Link onClick={onClose} href="/notifications" className="rounded-xl bg-slate-900 px-4 py-2 text-sm font-semibold text-white hover:bg-slate-800">View all notifications</Link></div></aside></>;
}
function DrawerSkeleton() { return <div className="space-y-3 p-4">{[1,2,3,4].map((item) => <div key={item} className="h-24 animate-pulse rounded-xl bg-slate-100" />)}</div>; }

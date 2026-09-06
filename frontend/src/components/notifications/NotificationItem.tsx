"use client";

import { useRouter } from "next/navigation";
import type { MouseEvent } from "react";

import { StatusBadge } from "@/components/ui";
import { formatNotificationTime, markNotificationRead, type Notification } from "./types";

export function NotificationItem({ notification, onRead }: { notification: Notification; onRead: (notification: Notification) => void }) {
  const router = useRouter();
  async function openNotification() {
    if (!notification.related_url) return;
    if (!notification.is_read) {
      try { await markNotificationRead(notification.id); onRead({ ...notification, is_read: true, read_at: new Date().toISOString() }); }
      catch { return; }
    }
    router.push(notification.related_url);
  }
  async function markRead(event: MouseEvent<HTMLButtonElement>) {
    event.stopPropagation();
    try { const data = await markNotificationRead(notification.id); onRead(data.result); } catch { /* Parent error state remains unchanged for an item-level retry. */ }
  }
  const clickable = Boolean(notification.related_url);
  return <article onClick={() => void openNotification()} className={`relative border-b border-slate-100 px-4 py-4 last:border-b-0 ${clickable ? "cursor-pointer hover:bg-slate-50" : ""} ${!notification.is_read ? "bg-blue-50/50" : "bg-white"}`}>
    {!notification.is_read && <span className="absolute left-2 top-5 h-2 w-2 rounded-full bg-blue-600" aria-label="Unread notification" />}
    <div className="ml-2 flex gap-3"><div className="min-w-0 flex-1"><div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between"><p className="font-semibold text-slate-900">{notification.title}</p><StatusBadge status={notification.severity} /></div><p className="mt-1 text-sm text-slate-600">{notification.message}</p><div className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-slate-500"><span>{formatNotificationTime(notification.created_at)}</span>{!notification.is_read && <span className="font-semibold text-blue-700">Unread</span>}{!notification.is_read && <button type="button" onClick={markRead} className="font-semibold text-blue-700 hover:underline">Mark as read</button>}</div></div></div>
  </article>;
}

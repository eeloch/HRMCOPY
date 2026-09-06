import { apiFetch } from "@/lib/api";

export type Notification = {
  id: number;
  event_type: string;
  title: string;
  message: string;
  severity: string;
  employee_id: number | null;
  employee_name: string | null;
  related_url: string;
  metadata: Record<string, unknown>;
  is_read: boolean;
  read_at: string | null;
  created_at: string;
};

export type NotificationResponse = {
  count: number;
  page: number;
  page_size: number;
  total_pages: number;
  results: Notification[];
};

export async function getNotifications(params: Record<string, string> = {}) {
  const query = new URLSearchParams(params);
  const response = await apiFetch(`/notifications/${query.size ? `?${query}` : ""}`);
  if (!response.ok) throw new Error("Unable to load notifications.");
  return response.json() as Promise<NotificationResponse>;
}

export async function getUnreadCount() {
  const response = await apiFetch("/notifications/unread-count/");
  if (!response.ok) throw new Error("Unable to load unread notifications.");
  const data: { count: number } = await response.json();
  return data.count;
}

export async function markNotificationRead(notificationId: number) {
  const response = await apiFetch(`/notifications/${notificationId}/read/`, { method: "POST" });
  if (!response.ok) throw new Error("Unable to mark notification as read.");
  announceNotificationChange();
  return response.json() as Promise<{ result: Notification }>;
}

export async function markAllNotificationsRead() {
  const response = await apiFetch("/notifications/read-all/", { method: "POST" });
  if (!response.ok) throw new Error("Unable to mark notifications as read.");
  announceNotificationChange();
  return response.json() as Promise<{ updated: number }>;
}

export function announceNotificationChange() {
  window.dispatchEvent(new Event("notifications:changed"));
}

export function formatNotificationTime(value: string) {
  const date = new Date(value);
  const difference = Date.now() - date.getTime();
  const minutes = Math.floor(difference / 60000);
  if (minutes < 1) return "Just now";
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return new Intl.DateTimeFormat("en-GB", { day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" }).format(date);
}

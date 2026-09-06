"use client";

import { useEffect, useState } from "react";

import { NotificationDrawer } from "./NotificationDrawer";
import { getUnreadCount } from "./types";

export function NotificationBell() {
  const [open, setOpen] = useState(false);
  const [count, setCount] = useState(0);
  useEffect(() => { void loadUnreadCount(); const refresh = () => void loadUnreadCount(); window.addEventListener("notifications:changed", refresh); return () => window.removeEventListener("notifications:changed", refresh); }, []);
  async function loadUnreadCount() { try { setCount(await getUnreadCount()); } catch { setCount(0); } }
  return <><button onClick={() => setOpen(true)} className="relative rounded-xl p-2 text-slate-200 transition hover:bg-slate-800 hover:text-white" aria-label="Open notifications"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="h-5 w-5"><path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9" /><path d="M13.7 21a2 2 0 0 1-3.4 0" /></svg>{count > 0 && <span className="absolute -right-2 -top-2 min-w-5 rounded-full bg-red-500 px-1 text-center text-[11px] font-bold leading-5 text-white">{count > 9 ? "9+" : count}</span>}</button><NotificationDrawer open={open} onClose={() => { setOpen(false); void loadUnreadCount(); }} /></>;
}

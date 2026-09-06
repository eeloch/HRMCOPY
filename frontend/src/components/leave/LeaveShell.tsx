"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";

import Sidebar from "@/components/Sidebar";

const links = [
  { href: "/leave", label: "Dashboard" },
  { href: "/leave/requests", label: "My Requests" },
  { href: "/leave/new", label: "Request Leave" },
  { href: "/leave/approvals", label: "Approvals" },
  { href: "/leave/calendar", label: "Calendar" },
];

export function LeaveShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();

  return (
    <div className="min-h-screen bg-slate-100">
      <Sidebar />
      <main className="ml-64 min-w-0 p-4 md:p-8">
        <nav className="mb-8 flex gap-2 overflow-x-auto pb-1" aria-label="Leave navigation">
          {links.map((link) => {
            const active = pathname === link.href;
            return (
              <Link
                key={link.href}
                href={link.href}
                className={`whitespace-nowrap rounded-xl px-4 py-2.5 text-sm font-semibold transition ${
                  active
                    ? "bg-blue-600 text-white shadow-sm"
                    : "bg-white text-slate-600 ring-1 ring-slate-200 hover:bg-slate-50"
                }`}
              >
                {link.label}
              </Link>
            );
          })}
        </nav>
        {children}
      </main>
    </div>
  );
}

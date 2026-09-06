"use client";

import Link from "next/link";

import {
  usePathname,
  useRouter,
} from "next/navigation";

import {
  logout as logoutRequest,
} from "@/lib/api";
import { NotificationBell } from "@/components/notifications/NotificationBell";


const menu = [
  {
    label: "Dashboard",
    href: "/dashboard",
  },
  {
    label: "Employees",
    href: "/employees",
  },
  {
    label: "Attendance",
    href: "/attendance",
  },
  {
    label: "Leave",
    href: "/leave",
  },
  {
    label: "Activity",
    href: "/activity",
  },
  {
    label: "Notifications",
    href: "/notifications",
  },
  {
    label: "Exceptions",
    href: "/attendance/exceptions",
  },
  {
    label: "Shifts",
    href: "/attendance/shifts",
  },
  {
    label: "Work Roster",
    href: "/attendance/roster",
  },
  {
    label: "Overtime",
    href: "/attendance/overtime",
  },
  {
    label: "Biometric Punches",
    href: "/attendance/biometric",
  },
  {
    label: "Payroll",
    href: "/payroll",
  },
  {
    label: "PPE",
    href: "/ppe",
  },
  {
    label: "Meals",
    href: "/meals",
  },
  {
    label: "Payslips",
    href: "/payslips",
  },
  {
    label: "Biometric Devices",
    href: "/devices",
  },
  {
    label: "Settings",
    href: "/settings",
  },
];


export default function Sidebar() {

  const pathname =
    usePathname();

  const router =
    useRouter();


  async function logout() {
    await logoutRequest();

    router.push(
      "/login"
    );
  }


  return (
    <aside className="w-64 bg-slate-950 text-white min-h-screen fixed left-0 top-0">

      <div className="flex items-start justify-between gap-3 p-6 border-b border-slate-800">

        <div>
          <div className="text-xs font-bold tracking-widest text-blue-400">
            ROTIC
          </div>

          <div className="font-bold text-xl mt-1">
            HRM System
          </div>
        </div>

        <NotificationBell />

      </div>


      <nav className="p-4 space-y-1">

        {menu.map((item) => {

          const active =
            pathname === item.href;

          return (
            <Link
              key={item.href}
              href={item.href}
              className={
                `block px-4 py-3 rounded-lg transition ${
                  active
                    ? "bg-blue-600 text-white"
                    : "text-slate-300 hover:bg-slate-800"
                }`
              }
            >
              {item.label}
            </Link>
          );
        })}

      </nav>


      <div className="absolute bottom-0 left-0 right-0 p-4 border-t border-slate-800">

        <button
          onClick={logout}
          className="w-full text-left px-4 py-3 rounded-lg text-slate-300 hover:bg-slate-800"
        >
          Sign Out
        </button>

      </div>

    </aside>
  );
}

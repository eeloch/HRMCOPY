"use client";

import { useEffect, useState } from "react";

import Link from "next/link";

import {
  usePathname,
  useRouter,
} from "next/navigation";

import {
  getCurrentUser,
  logout as logoutRequest,
  type CurrentUser,
} from "@/lib/api";
import { canOpen, forgetUser, homeFor, loadUser } from "@/lib/access";
import { NotificationBell } from "@/components/notifications/NotificationBell";


type MenuChild = {
  label: string;
  href: string;
};

type MenuItem = {
  label: string;
  href?: string;
  children?: MenuChild[];
};

const menu: MenuItem[] = [
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
    children: [
      { label: "Exceptions", href: "/attendance/exceptions" },
      { label: "Shifts", href: "/attendance/shifts" },
      { label: "Work Roster", href: "/attendance/roster" },
      { label: "Overtime", href: "/attendance/overtime" },
    ],
  },
  {
    label: "Biometrics",
    href: "/biometrics",
    children: [
      { label: "Biometric Punches", href: "/attendance/biometric" },
      { label: "Biometric Devices", href: "/devices" },
    ],
  },
  {
    label: "Leave",
    href: "/leave",
  },
  {
    label: "Payroll",
    href: "/payroll",
    children: [
      { label: "Payslips", href: "/payslips" },
      { label: "Deferred Funds", href: "/deferred-funds" },
      { label: "Bonuses", href: "/bonuses" },
    ],
  },
  {
    label: "PPE",
    href: "/ppe",
  },
  {
    label: "Salary Advances",
    href: "/advances",
  },
  {
    label: "Meals",
    href: "/meals",
  },
  {
    label: "Offences & Rewards",
    href: "/offences",
  },
  {
    label: "Reports",
    href: "/reports",
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
    label: "Settings",
    href: "/settings",
  },
];


export default function Sidebar() {

  const pathname =
    usePathname();

  const router =
    useRouter();

  const [user, setUser] = useState<CurrentUser | null>(null);

  useEffect(() => {
    let alive = true;
    loadUser(getCurrentUser).then((loaded) => {
      if (!alive) return;
      setUser(loaded);
      // someone who types or follows a link to a page they may not open goes to their own first page instead
      if (!canOpen(loaded, pathname)) router.replace(homeFor(loaded));
    }).catch(() => undefined);
    return () => { alive = false; };
  }, [pathname, router]);

  // until the user is known show only what everyone may see, so a tab is never shown and then taken away
  const visibleMenu: MenuItem[] = [];
  for (const item of menu) {
    const children = item.children?.filter((child) => user !== null && canOpen(user, child.href));
    const own = item.href !== undefined && user !== null && canOpen(user, item.href);
    if (!own && !(children && children.length)) continue;
    visibleMenu.push({ label: item.label, href: own ? item.href : undefined, children: item.children ? children : undefined });
  }

  const [expanded, setExpanded] = useState<Set<string>>(() => {
    const initial = new Set<string>();
    for (const item of menu) {
      if (item.children?.some((child) => pathname === child.href)) {
        initial.add(item.label);
      }
    }
    return initial;
  });

  function toggle(label: string) {
    setExpanded((current) => {
      const next = new Set(current);
      if (next.has(label)) {
        next.delete(label);
      } else {
        next.add(label);
      }
      return next;
    });
  }


  async function logout() {
    await logoutRequest();
    forgetUser();

    router.push(
      "/login"
    );
  }


  return (
    <aside className="w-64 bg-slate-950 text-white h-screen fixed left-0 top-0 flex flex-col print:hidden">

      <div className="flex items-start justify-between gap-3 p-6 border-b border-slate-800 shrink-0">

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


      <nav className="flex-1 min-h-0 overflow-y-auto p-4 space-y-1">

        {visibleMenu.map((item) => {

          const active =
            item.href !== undefined && pathname === item.href;

          const childActive =
            item.children?.some((child) => pathname === child.href) ?? false;

          const isExpanded =
            expanded.has(item.label) || childActive;

          return (
            <div key={item.label}>

              <div
                className={
                  `flex items-center rounded-lg transition ${
                    active
                      ? "bg-blue-600 text-white"
                      : childActive
                        ? "bg-slate-800 text-white"
                        : "text-slate-300 hover:bg-slate-800"
                  }`
                }
              >
                {item.href ? (
                  <Link
                    href={item.href}
                    className="flex-1 px-4 py-3"
                  >
                    {item.label}
                  </Link>
                ) : (
                  <button
                    type="button"
                    onClick={() => toggle(item.label)}
                    className="flex-1 px-4 py-3 text-left"
                  >
                    {item.label}
                  </button>
                )}

                {item.children && (
                  <button
                    type="button"
                    onClick={() => toggle(item.label)}
                    aria-label={isExpanded ? `Collapse ${item.label}` : `Expand ${item.label}`}
                    className="px-3 py-3 text-slate-400 hover:text-white"
                  >
                    {isExpanded ? "−" : "+"}
                  </button>
                )}
              </div>

              {item.children && isExpanded && (
                <div className="mt-1 ml-3 space-y-1 border-l border-slate-800 pl-3">
                  {item.children.map((child) => {
                    const childIsActive = pathname === child.href;

                    return (
                      <Link
                        key={child.href}
                        href={child.href}
                        className={
                          `block px-3 py-2 rounded-lg text-sm transition ${
                            childIsActive
                              ? "bg-blue-600 text-white"
                              : "text-slate-400 hover:bg-slate-800 hover:text-white"
                          }`
                        }
                      >
                        {child.label}
                      </Link>
                    );
                  })}
                </div>
              )}

            </div>
          );
        })}

      </nav>


      <div className="p-4 border-t border-slate-800 shrink-0">

        <Link
          href="/account/password"
          className={`block w-full text-left px-4 py-3 rounded-lg hover:bg-slate-800 ${pathname === "/account/password" ? "bg-slate-800 text-white" : "text-slate-300"}`}
        >
          Change Password
        </Link>

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

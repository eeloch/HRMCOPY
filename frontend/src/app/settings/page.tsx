"use client";

import { FormEvent, Fragment, useEffect, useState } from "react";
import { useRouter } from "next/navigation";

import Sidebar from "@/components/Sidebar";
import { AppCard, PageHeader, Section, StatusBadge } from "@/components/ui";
import { apiFetch, getAccessToken, getCurrentUser } from "@/lib/api";

type PermissionEntry = { codename: string; label: string; group: string };

type Account = {
  id: number;
  username: string;
  is_active: boolean;
  is_superuser: boolean;
  date_joined: string;
  last_login: string | null;
  permissions: Record<string, boolean>;
};

function groupPermissions(registry: PermissionEntry[]) {
  const groups: Record<string, PermissionEntry[]> = {};
  for (const entry of registry) {
    (groups[entry.group] ||= []).push(entry);
  }
  return groups;
}

export default function SettingsPage() {
  const router = useRouter();
  const [checkingAccess, setCheckingAccess] = useState(true);
  const [allowed, setAllowed] = useState(false);
  const [registry, setRegistry] = useState<PermissionEntry[]>([]);
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [expandedId, setExpandedId] = useState<number | null>(null);
  const [temporaryPassword, setTemporaryPassword] = useState<{ username: string; password: string } | null>(null);

  const [newUsername, setNewUsername] = useState("");
  const [newPermissions, setNewPermissions] = useState<string[]>([]);
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState("");

  async function load() {
    setLoading(true);
    setError("");
    try {
      const response = await apiFetch("/auth/users/");
      if (!response.ok) throw new Error(response.status === 403 ? "You don't have permission to manage users." : "Unable to load accounts.");
      const data: { permission_registry: PermissionEntry[]; results: Account[] } = await response.json();
      setRegistry(data.permission_registry);
      setAccounts(data.results);
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "Unable to load accounts.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    if (!getAccessToken()) {
      router.push("/login");
      return;
    }
    const timer = window.setTimeout(async () => {
      try {
        const user = await getCurrentUser();
        setAllowed(user.is_superuser);
        if (user.is_superuser) await load();
      } finally {
        setCheckingAccess(false);
      }
    }, 0);
    return () => window.clearTimeout(timer);
  }, [router]);

  async function createAccount(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setCreateError("");
    setCreating(true);
    try {
      const response = await apiFetch("/auth/users/", {
        method: "POST",
        body: JSON.stringify({ username: newUsername, permissions: newPermissions }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.detail || "Unable to create the account.");
      setTemporaryPassword({ username: newUsername, password: data.temporary_password });
      setNewUsername("");
      setNewPermissions([]);
      await load();
    } catch (createErr) {
      setCreateError(createErr instanceof Error ? createErr.message : "Unable to create the account.");
    } finally {
      setCreating(false);
    }
  }

  if (checkingAccess) {
    return <div className="min-h-screen bg-slate-100"><Sidebar /><main className="ml-64 p-8 text-slate-500">Loading...</main></div>;
  }

  if (!allowed) {
    return (
      <div className="min-h-screen bg-slate-100">
        <Sidebar />
        <main className="ml-64 min-w-0 p-4 md:p-8">
          <PageHeader title="Settings" description="Manage user accounts and permissions." />
          <AppCard><p className="p-8 text-center text-slate-600">You don't have permission to view this page. Only administrators can manage users and permissions.</p></AppCard>
        </main>
      </div>
    );
  }

  const groups = groupPermissions(registry);

  return (
    <div className="min-h-screen bg-slate-100">
      <Sidebar />
      <main className="ml-64 min-w-0 p-4 md:p-8">
        <PageHeader title="Settings" description="Manage who has a login, and exactly what each account can do." />

        {temporaryPassword && (
          <AppCard className="mb-6 border-emerald-200 bg-emerald-50">
            <p className="text-sm font-semibold text-emerald-900">
              Account created for &ldquo;{temporaryPassword.username}&rdquo;. Temporary password (shown once — share it now):
            </p>
            <p className="mt-2 rounded-lg bg-white px-4 py-2 font-mono text-lg text-emerald-900">{temporaryPassword.password}</p>
            <button type="button" onClick={() => setTemporaryPassword(null)} className="mt-3 text-sm font-semibold text-emerald-700 hover:underline">Dismiss</button>
          </AppCard>
        )}

        <Section className="mb-6" title="Add a User" subtitle="Choose exactly which of the app's gated actions this account can perform.">
          <form onSubmit={createAccount} className="p-5">
            <label className="block max-w-md text-sm font-semibold text-slate-700">
              Username
              <input required value={newUsername} onChange={(event) => setNewUsername(event.target.value)} className="mt-2 w-full rounded-xl border border-slate-300 px-3 py-2.5 font-normal" />
            </label>

            <div className="mt-5 grid gap-5 sm:grid-cols-2 lg:grid-cols-3">
              {Object.entries(groups).map(([group, entries]) => (
                <div key={group}>
                  <h4 className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">{group}</h4>
                  <div className="space-y-2">
                    {entries.map((entry) => (
                      <label key={entry.codename} className="flex items-start gap-2 text-sm text-slate-700">
                        <input
                          type="checkbox"
                          className="mt-0.5 h-4 w-4 rounded border-slate-300"
                          checked={newPermissions.includes(entry.codename)}
                          onChange={(event) => {
                            setNewPermissions((current) =>
                              event.target.checked ? [...current, entry.codename] : current.filter((code) => code !== entry.codename)
                            );
                          }}
                        />
                        {entry.label}
                      </label>
                    ))}
                  </div>
                </div>
              ))}
            </div>

            {createError && <p className="mt-4 text-sm text-red-700">{createError}</p>}

            <button disabled={creating} className="mt-5 rounded-xl bg-blue-600 px-5 py-2.5 text-sm font-semibold text-white hover:bg-blue-700 disabled:opacity-50">
              {creating ? "Creating..." : "Create Account"}
            </button>
          </form>
        </Section>

        {error ? (
          <AppCard><div className="p-8 text-center text-red-700"><p>{error}</p><button type="button" onClick={() => void load()} className="mt-4 rounded-xl bg-red-600 px-4 py-2.5 text-sm font-semibold text-white">Try Again</button></div></AppCard>
        ) : (
          <Section title="Accounts" subtitle={`${accounts.length} account${accounts.length === 1 ? "" : "s"}`}>
            {loading ? (
              <div className="space-y-3 p-6">{Array.from({ length: 3 }).map((_, index) => <div key={index} className="h-14 animate-pulse rounded-xl bg-slate-100" />)}</div>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full min-w-[720px] text-left">
                  <thead className="bg-slate-50 text-xs font-semibold uppercase tracking-wide text-slate-500">
                    <tr><th className="px-5 py-3">Username</th><th className="px-5 py-3">Status</th><th className="px-5 py-3">Role</th><th className="px-5 py-3" /></tr>
                  </thead>
                  <tbody className="divide-y divide-slate-100">
                    {accounts.map((account) => (
                      <Fragment key={account.id}>
                        <tr className="hover:bg-slate-50">
                          <td className="px-5 py-4 font-semibold text-slate-900">{account.username}</td>
                          <td className="px-5 py-4"><StatusBadge status={account.is_active ? "active" : "inactive"} /></td>
                          <td className="px-5 py-4 text-sm text-slate-600">{account.is_superuser ? "Superuser (all permissions)" : "Standard"}</td>
                          <td className="px-5 py-4 text-right">
                            {!account.is_superuser && (
                              <button type="button" onClick={() => setExpandedId(expandedId === account.id ? null : account.id)} className="text-sm font-semibold text-blue-700 hover:underline">
                                {expandedId === account.id ? "Hide" : "Manage"}
                              </button>
                            )}
                          </td>
                        </tr>
                        {expandedId === account.id && (
                          <tr>
                            <td colSpan={4} className="p-0">
                              <AccountPanel account={account} registry={registry} onChanged={load} />
                            </td>
                          </tr>
                        )}
                      </Fragment>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Section>
        )}
      </main>
    </div>
  );
}

function AccountPanel({ account, registry, onChanged }: { account: Account; registry: PermissionEntry[]; onChanged: () => Promise<void> }) {
  const [selected, setSelected] = useState<string[]>(
    Object.entries(account.permissions).filter(([, granted]) => granted).map(([code]) => code)
  );
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [resetPassword, setResetPassword] = useState<string | null>(null);

  const groups = groupPermissions(registry);

  async function savePermissions() {
    setSaving(true);
    setError("");
    setMessage("");
    try {
      const response = await apiFetch(`/auth/users/${account.id}/`, {
        method: "PATCH",
        body: JSON.stringify({ permissions: selected }),
      });
      if (!response.ok) throw new Error("Unable to update permissions.");
      setMessage("Permissions updated.");
      await onChanged();
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : "Unable to update permissions.");
    } finally {
      setSaving(false);
    }
  }

  async function toggleActive() {
    setSaving(true);
    setError("");
    try {
      const response = await apiFetch(`/auth/users/${account.id}/`, {
        method: "PATCH",
        body: JSON.stringify({ is_active: !account.is_active }),
      });
      if (!response.ok) throw new Error("Unable to update this account.");
      await onChanged();
    } catch (toggleError) {
      setError(toggleError instanceof Error ? toggleError.message : "Unable to update this account.");
    } finally {
      setSaving(false);
    }
  }

  async function resetPasswordNow() {
    if (!window.confirm(`Reset the password for "${account.username}"? Their current password will stop working immediately.`)) return;
    setSaving(true);
    setError("");
    try {
      const response = await apiFetch(`/auth/users/${account.id}/`, {
        method: "PATCH",
        body: JSON.stringify({ reset_password: true }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error("Unable to reset the password.");
      setResetPassword(data.temporary_password);
    } catch (resetError) {
      setError(resetError instanceof Error ? resetError.message : "Unable to reset the password.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="border-t border-slate-200 bg-slate-50 p-5">
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <button type="button" disabled={saving} onClick={() => void toggleActive()} className="rounded-xl border border-slate-300 bg-white px-4 py-2 text-sm font-semibold text-slate-700 hover:bg-slate-100 disabled:opacity-50">
          {account.is_active ? "Deactivate Account" : "Reactivate Account"}
        </button>
        <button type="button" disabled={saving} onClick={() => void resetPasswordNow()} className="rounded-xl border border-slate-300 bg-white px-4 py-2 text-sm font-semibold text-slate-700 hover:bg-slate-100 disabled:opacity-50">
          Reset Password
        </button>
      </div>

      {resetPassword && (
        <div className="mb-4 rounded-xl border border-emerald-200 bg-emerald-50 p-4">
          <p className="text-sm font-semibold text-emerald-900">New temporary password (shown once):</p>
          <p className="mt-2 rounded-lg bg-white px-4 py-2 font-mono text-lg text-emerald-900">{resetPassword}</p>
        </div>
      )}

      <div className="grid gap-5 sm:grid-cols-2 lg:grid-cols-3">
        {Object.entries(groups).map(([group, entries]) => (
          <div key={group}>
            <h4 className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">{group}</h4>
            <div className="space-y-2">
              {entries.map((entry) => (
                <label key={entry.codename} className="flex items-start gap-2 text-sm text-slate-700">
                  <input
                    type="checkbox"
                    className="mt-0.5 h-4 w-4 rounded border-slate-300"
                    checked={selected.includes(entry.codename)}
                    onChange={(event) => {
                      setSelected((current) =>
                        event.target.checked ? [...current, entry.codename] : current.filter((code) => code !== entry.codename)
                      );
                    }}
                  />
                  {entry.label}
                </label>
              ))}
            </div>
          </div>
        ))}
      </div>

      {error && <p className="mt-4 text-sm text-red-700">{error}</p>}
      {message && <p className="mt-4 text-sm text-emerald-700">{message}</p>}

      <button type="button" disabled={saving} onClick={() => void savePermissions()} className="mt-5 rounded-xl bg-blue-600 px-5 py-2.5 text-sm font-semibold text-white hover:bg-blue-700 disabled:opacity-50">
        {saving ? "Saving..." : "Save Permissions"}
      </button>
    </div>
  );
}

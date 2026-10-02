"use client";

import { FormEvent, useEffect, useState } from "react";
import { useRouter } from "next/navigation";

import Sidebar from "@/components/Sidebar";
import { AppCard, PageHeader } from "@/components/ui";
import { apiFetch, getAccessToken, getCurrentUser } from "@/lib/api";

const fieldClass = "w-full rounded-xl border border-slate-300 bg-white px-4 py-3 text-slate-900 outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-500";

export default function ChangePasswordPage() {
  const router = useRouter();
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [done, setDone] = useState(false);
  const [mustChange, setMustChange] = useState(false);

  useEffect(() => {
    if (!getAccessToken()) {
      router.push("/login");
      return;
    }
    getCurrentUser().then((user) => setMustChange(user.must_change_password)).catch(() => {});
  }, [router]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    setDone(false);
    if (newPassword !== confirmPassword) {
      setError("The new password and its confirmation do not match.");
      return;
    }
    setSaving(true);
    try {
      const response = await apiFetch("/auth/change-password/", {
        method: "POST",
        body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }),
      });
      const data = await response.json().catch(() => null);
      if (!response.ok) {
        setError(data?.detail || "Unable to change your password. Please try again.");
        return;
      }
      setCurrentPassword("");
      setNewPassword("");
      setConfirmPassword("");
      setDone(true);
      if (mustChange) window.setTimeout(() => router.push("/dashboard"), 1500);
    } catch {
      setError("Unable to change your password. Check your connection and try again.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="min-h-screen bg-slate-100">
      <Sidebar />
      <main className="ml-64 min-w-0 p-4 md:p-8">
        <PageHeader title="Change Password" description="Choose a password only you know. Use at least 8 characters, not just numbers, and nothing easy to guess." />
        <AppCard>
          <form onSubmit={submit} className="max-w-md space-y-5 p-6">
            {mustChange && !done && <div className="rounded-xl border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">You are using a temporary password. Choose your own password to continue.</div>}
            {error && <div className="rounded-xl border border-red-200 bg-red-50 p-3 text-sm text-red-700">{error}</div>}
            {done && <div className="rounded-xl border border-green-200 bg-green-50 p-3 text-sm text-green-800">Your password has been changed. Use it the next time you sign in.{mustChange ? " Taking you to the dashboard..." : ""}</div>}
            <label className="block">
              <span className="mb-2 block text-sm font-semibold text-slate-700">Current password</span>
              <input required type="password" autoComplete="current-password" value={currentPassword} onChange={(event) => setCurrentPassword(event.target.value)} className={fieldClass} />
            </label>
            <label className="block">
              <span className="mb-2 block text-sm font-semibold text-slate-700">New password</span>
              <input required type="password" autoComplete="new-password" minLength={8} value={newPassword} onChange={(event) => setNewPassword(event.target.value)} className={fieldClass} />
            </label>
            <label className="block">
              <span className="mb-2 block text-sm font-semibold text-slate-700">Confirm new password</span>
              <input required type="password" autoComplete="new-password" minLength={8} value={confirmPassword} onChange={(event) => setConfirmPassword(event.target.value)} className={fieldClass} />
            </label>
            <button disabled={saving} className="rounded-xl bg-blue-600 px-5 py-3 text-sm font-semibold text-white hover:bg-blue-700 disabled:cursor-not-allowed disabled:bg-blue-300">
              {saving ? "Saving..." : "Change password"}
            </button>
          </form>
        </AppCard>
      </main>
    </div>
  );
}

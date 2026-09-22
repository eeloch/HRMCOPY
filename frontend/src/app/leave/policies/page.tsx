"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";

import { LeaveShell } from "@/components/leave/LeaveShell";
import type { LeavePolicyMatrix } from "@/components/leave/types";
import { Section } from "@/components/ui";
import { apiFetch, getAccessToken, getCurrentUser, type CurrentUser } from "@/lib/api";

const inputClass = "w-24 rounded-lg border border-slate-300 px-2 py-1.5 text-center text-sm outline-none focus:ring-2 focus:ring-blue-500";

function cellKey(leaveTypeId: number, employmentType: string) {
  return `${leaveTypeId}:${employmentType}`;
}

export default function LeavePoliciesPage() {
  const router = useRouter();
  const [matrix, setMatrix] = useState<LeavePolicyMatrix | null>(null);
  const [edits, setEdits] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [feedback, setFeedback] = useState("");
  const [currentUser, setCurrentUser] = useState<CurrentUser | null>(null);

  useEffect(() => {
    if (!getAccessToken()) { router.push("/login"); return; }
    void load();
    getCurrentUser().then(setCurrentUser).catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [router]);

  async function load() {
    setLoading(true);
    setError("");
    try {
      const response = await apiFetch("/leave/policies/");
      if (!response.ok) throw new Error("Unable to load leave policies.");
      setMatrix(await response.json());
      setEdits({});
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "Unable to load leave policies.");
    } finally {
      setLoading(false);
    }
  }

  function existingCell(leaveTypeId: number, employmentType: string) {
    return matrix?.policies.find((p) => p.leave_type === leaveTypeId && p.employment_type === employmentType) || null;
  }

  function valueFor(leaveTypeId: number, employmentType: string) {
    const key = cellKey(leaveTypeId, employmentType);
    if (key in edits) return edits[key];
    return existingCell(leaveTypeId, employmentType)?.allocated_days ?? "";
  }

  function setValue(leaveTypeId: number, employmentType: string, value: string) {
    setEdits((current) => ({ ...current, [cellKey(leaveTypeId, employmentType)]: value }));
  }

  const dirtyCount = Object.keys(edits).filter((key) => {
    const [leaveTypeIdStr, employmentType] = key.split(":");
    const existing = existingCell(Number(leaveTypeIdStr), employmentType);
    return edits[key].trim() !== "" && edits[key] !== (existing?.allocated_days ?? "");
  }).length;

  async function saveChanges() {
    if (!matrix) return;
    setSaving(true);
    setError("");
    setFeedback("");
    try {
      const jobs = Object.entries(edits).filter(([, value]) => value.trim() !== "");
      for (const [key, value] of jobs) {
        const [leaveTypeIdStr, employmentType] = key.split(":");
        const leaveTypeId = Number(leaveTypeIdStr);
        const leaveType = matrix.leave_types.find((lt) => lt.id === leaveTypeId);
        const existing = existingCell(leaveTypeId, employmentType);
        if (value === (existing?.allocated_days ?? "")) continue; // unchanged, skip
        const response = await apiFetch("/leave/policies/set/", {
          method: "POST",
          body: JSON.stringify({
            leave_type: leaveTypeId,
            employment_type: employmentType,
            allocated_days: value,
            is_paid: existing ? existing.is_paid : leaveType?.code !== "UNPAID",
            requires_approval: existing ? existing.requires_approval : true,
          }),
        });
        if (!response.ok) {
          const data = await response.json().catch(() => ({}));
          throw new Error(typeof data.detail === "string" ? data.detail : "Unable to save one of the entitlements.");
        }
      }
      setFeedback(`${jobs.length} entitlement${jobs.length === 1 ? "" : "s"} saved.`);
      await load();
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : "Unable to save changes.");
    } finally {
      setSaving(false);
    }
  }

  const canManage = currentUser?.is_superuser || currentUser?.permissions.manage_leave_policy;

  return (
    <LeaveShell>
      <Section
        title="Leave Policies"
        subtitle="How many days each employment type gets, per leave type. A blank cell means that group has no entitlement at all for that leave type - their requests will be rejected."
        actions={canManage ? (
          <button type="button" disabled={saving || dirtyCount === 0} onClick={() => void saveChanges()} className="rounded-xl bg-blue-600 px-4 py-2.5 text-sm font-semibold text-white disabled:bg-blue-300">
            {saving ? "Saving..." : dirtyCount ? `Save ${dirtyCount} Change${dirtyCount === 1 ? "" : "s"}` : "Save Changes"}
          </button>
        ) : undefined}
      >
        {feedback && <p className="mx-5 mt-5 rounded-xl bg-emerald-50 p-3 text-sm text-emerald-800">{feedback}</p>}
        {error && <p className="mx-5 mt-5 rounded-xl bg-red-50 p-3 text-sm text-red-700">{error}</p>}
        {!canManage && (
          <p className="mx-5 mt-5 rounded-xl bg-slate-50 p-3 text-sm text-slate-600">
            You can view the current entitlements below. Ask an administrator for the &quot;Set leave entitlements per employment type&quot; permission to change them.
          </p>
        )}
        {loading || !matrix ? (
          <div className="p-10">Loading...</div>
        ) : (
          <div className="overflow-x-auto p-5">
            <table className="w-full min-w-[820px] text-left">
              <thead className="text-xs font-semibold uppercase tracking-wide text-slate-500">
                <tr>
                  <th className="px-3 py-3">Leave Type</th>
                  {matrix.employment_types.map((type) => (
                    <th key={type.value} className="px-3 py-3 text-center">{type.label}</th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {matrix.leave_types.map((leaveType) => (
                  <tr key={leaveType.id}>
                    <td className="px-3 py-3">
                      <div className="font-semibold text-slate-900">{leaveType.name}</div>
                      <div className="text-xs text-slate-400">{leaveType.code}</div>
                    </td>
                    {matrix.employment_types.map((type) => {
                      const existing = existingCell(leaveType.id, type.value);
                      const value = valueFor(leaveType.id, type.value);
                      const key = cellKey(leaveType.id, type.value);
                      const isDirty = key in edits && edits[key] !== (existing?.allocated_days ?? "");
                      return (
                        <td key={type.value} className="px-3 py-3 text-center">
                          <input
                            type="number"
                            min="0"
                            step="0.5"
                            disabled={!canManage}
                            value={value}
                            onChange={(event) => setValue(leaveType.id, type.value, event.target.value)}
                            placeholder="-"
                            className={`${inputClass} ${isDirty ? "border-blue-500 bg-blue-50" : !existing ? "border-amber-300 bg-amber-50" : ""} disabled:bg-slate-50 disabled:text-slate-500`}
                          />
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="mt-4 text-xs text-slate-500">
              <span className="mr-1 inline-block h-3 w-3 rounded bg-amber-50 align-middle ring-1 ring-amber-300" /> No entitlement set yet ·{" "}
              <span className="mr-1 ml-3 inline-block h-3 w-3 rounded bg-blue-50 align-middle ring-1 ring-blue-500" /> Unsaved change
            </p>
          </div>
        )}
      </Section>
    </LeaveShell>
  );
}

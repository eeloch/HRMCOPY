"use client";

import { useEffect, useEffectEvent, useMemo, useState } from "react";
import { useRouter } from "next/navigation";

import Sidebar from "@/components/Sidebar";
import { AppCard, PageHeader, Section } from "@/components/ui";
import { apiFetch, getAccessToken, getCurrentUser, type CurrentUser } from "@/lib/api";

type Occupant = { employee: number; employee_id: string; name: string; bed: number | null; department: string | null };
type Room = { id: number; name: string; capacity: number | null; capacity_estimated: boolean; occupied: number; free: number | null; state: string; active: boolean; notes: string; occupants: Occupant[] };
type Building = { id: number; name: string; kind: string; address: string; rooms: Room[]; occupied: number; capacity: number; full_rooms: number; empty_rooms: number; rooms_with_space: number };
type Summary = { active_staff: number; inside: number; inside_with_room: number; inside_without_room: number; outside: number; outside_placed: number; outside_unplaced: number; none: number; beds_total: number; beds_occupied: number; beds_free: number; rooms_full: number; rooms_with_space: number; rooms_empty: number };
type Person = { id: number; employee_id: string; name: string; department: string | null; where: string; place: string; room: number | null };
type Report = { dry_run: boolean; active_rows: number; placed_inside: number; outside_unplaced: number; no_accommodation: number; buildings_created: number; rooms_created: number; moved: number; unchanged: number; unmatched_ids: string[]; unreadable_rooms: { employee_id: string; value: string }[]; bed_conflicts: { employee_id: string; room: string; bed: number; already: string }[]; mixed_gender_rooms: string[]; freed_beds_from_inactive: number };

const stateStyle: Record<string, { label: string; card: string; bar: string }> = {
  full: { label: "Full", card: "border-red-200 bg-red-50", bar: "bg-red-500" },
  space: { label: "Space", card: "border-emerald-200 bg-emerald-50", bar: "bg-emerald-500" },
  empty: { label: "Empty", card: "border-slate-300 bg-white", bar: "bg-slate-300" },
  unknown: { label: "Capacity not set", card: "border-amber-200 bg-amber-50", bar: "bg-amber-400" },
  over: { label: "Over capacity", card: "border-red-400 bg-red-100", bar: "bg-red-700" },
  closed: { label: "Closed", card: "border-slate-200 bg-slate-100 opacity-60", bar: "bg-slate-300" },
};
const filters = [["all", "All rooms"], ["full", "Full"], ["space", "Has space"], ["empty", "Empty"], ["unknown", "Capacity not set"]] as const;
const inputClass = "mt-1 w-full rounded-xl border border-slate-300 bg-white px-3 py-2.5 text-sm outline-none focus:ring-2 focus:ring-blue-500";

function apiError(data: unknown, fallback: string) {
  if (data && typeof data === "object") {
    const payload = data as Record<string, unknown>;
    if (typeof payload.detail === "string") return payload.detail;
    const first = Object.values(payload).flat()[0];
    if (typeof first === "string") return first;
  }
  return fallback;
}

export default function AccommodationPage() {
  const router = useRouter();
  const [user, setUser] = useState<CurrentUser | null>(null);
  const [summary, setSummary] = useState<Summary | null>(null);
  const [buildings, setBuildings] = useState<Building[]>([]);
  const [people, setPeople] = useState<Person[]>([]);
  const [tab, setTab] = useState<"rooms" | "people" | "import">("rooms");
  const [filter, setFilter] = useState<(typeof filters)[number][0]>("all");
  const [where, setWhere] = useState("");
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [feedback, setFeedback] = useState("");
  const [openRoom, setOpenRoom] = useState<{ room: Room; building: Building } | null>(null);
  const [pick, setPick] = useState({ q: "", employee: "", bed: "", capacity: "" });
  const [file, setFile] = useState<File | null>(null);
  const [report, setReport] = useState<Report | null>(null);

  const canManage = !!user?.permissions.manage_accommodation;

  async function load() {
    try {
      const me = await getCurrentUser();
      setUser(me);
      if (!(me.permissions.view_accommodation || me.permissions.manage_accommodation)) return;
      const [overview, peopleResponse] = await Promise.all([apiFetch("/accommodation/"), apiFetch("/accommodation/people/")]);
      if (!overview.ok) throw new Error(apiError(await overview.json().catch(() => null), "Unable to load accommodation."));
      const data = await overview.json();
      setSummary(data.summary);
      setBuildings(data.buildings);
      if (peopleResponse.ok) setPeople((await peopleResponse.json()).results || []);
      setOpenRoom((current) => { if (!current) return current; for (const building of data.buildings as Building[]) { const room = building.rooms.find((item) => item.id === current.room.id); if (room) return { room, building }; } return null; });
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "Unable to load accommodation.");
    } finally {
      setLoading(false);
    }
  }
  const loadOnMount = useEffectEvent(() => { void load(); });
  useEffect(() => {
    if (!getAccessToken()) { router.push("/login"); return; }
    const timer = window.setTimeout(loadOnMount, 0);
    return () => window.clearTimeout(timer);
  }, [router]);

  async function send(path: string, method: string, body: object, success: string) {
    setBusy(true); setError("");
    try {
      const response = await apiFetch(path, { method, body: JSON.stringify(body) });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(apiError(data, "That did not work."));
      setFeedback(success);
      await load();
      return true;
    } catch (actionError) {
      setError(actionError instanceof Error ? actionError.message : "That did not work.");
      return false;
    } finally { setBusy(false); }
  }

  async function runImport(dryRun: boolean) {
    if (!file) return;
    setBusy(true); setError("");
    try {
      const form = new FormData();
      form.append("file", file);
      form.append("dry_run", dryRun ? "true" : "false");
      const response = await apiFetch("/accommodation/import/", { method: "POST", body: form });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(apiError(data, "Unable to read that spreadsheet."));
      setReport(data);
      if (!dryRun) { setFeedback("Spreadsheet applied."); setFile(null); await load(); }
    } catch (importError) {
      setError(importError instanceof Error ? importError.message : "Unable to read that spreadsheet.");
    } finally { setBusy(false); }
  }

  const shownPeople = useMemo(() => people.filter((p) => (!where || p.where === where) && (!query.trim() || `${p.name} ${p.employee_id} ${p.place}`.toLowerCase().includes(query.trim().toLowerCase()))), [people, where, query]);
  const candidates = useMemo(() => people.filter((p) => p.room !== openRoom?.room.id && `${p.name} ${p.employee_id}`.toLowerCase().includes(pick.q.trim().toLowerCase())).slice(0, 60), [people, pick.q, openRoom]);

  if (!loading && user && !summary) {
    return <div className="min-h-screen bg-slate-100"><Sidebar /><main className="ml-64 p-8"><AppCard><p className="p-8 text-center text-slate-600">{error || "Your account does not have access to accommodation."}</p></AppCard></main></div>;
  }

  const stat = (label: string, value: number | string, note: string, tone: string) => (
    <div className="rounded-2xl border border-slate-200 bg-white p-5"><p className="text-sm font-semibold text-slate-500">{label}</p><p className={`mt-1 text-3xl font-bold ${tone}`}>{value}</p><p className="mt-1 text-xs text-slate-500">{note}</p></div>
  );

  return (
    <div className="min-h-screen bg-slate-100">
      <Sidebar />
      <main className="ml-64 min-w-0 p-4 md:p-8">
        <PageHeader title="Accommodation" description="Who lives in company accommodation, who lives outside, and which rooms are full or have space." />
        {feedback && <p className="mb-6 rounded-xl bg-emerald-50 p-4 text-sm text-emerald-800">{feedback}</p>}
        {error && <p className="mb-6 rounded-xl bg-red-50 p-4 text-sm text-red-700">{error}</p>}

        {summary && (
          <div className="mb-6 grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
            {stat("In company accommodation", summary.inside, `${summary.inside_with_room} in a room${summary.inside_without_room ? `, ${summary.inside_without_room} with no room recorded` : ""}`, "text-blue-700")}
            {stat("In outside accommodation", summary.outside, summary.outside_unplaced ? `${summary.outside_unplaced} with the place not recorded yet` : "all placed", "text-violet-700")}
            {stat("No accommodation", summary.none, `of ${summary.active_staff} active staff`, "text-slate-800")}
            {stat("Beds free", summary.beds_free, `${summary.beds_occupied} of ${summary.beds_total} beds taken · ${summary.rooms_full} rooms full · ${summary.rooms_with_space} with space · ${summary.rooms_empty} empty`, "text-emerald-700")}
          </div>
        )}

        <div className="mb-4 flex flex-wrap gap-2">
          {([["rooms", "Rooms"], ["people", "People"], ...(canManage ? [["import", "Import spreadsheet"]] : [])] as [string, string][]).map(([key, label]) => (
            <button key={key} type="button" onClick={() => setTab(key as "rooms" | "people" | "import")} className={`rounded-full px-4 py-2 text-sm font-semibold ${tab === key ? "bg-slate-900 text-white" : "bg-white text-slate-700 hover:bg-slate-50"}`}>{label}</button>
          ))}
        </div>

        {loading && <div className="h-40 animate-pulse rounded-2xl bg-slate-200" />}

        {!loading && tab === "rooms" && (
          <>
            <div className="mb-4 flex flex-wrap gap-2">
              {filters.map(([key, label]) => <button key={key} type="button" onClick={() => setFilter(key)} className={`rounded-full border px-3 py-1.5 text-sm font-semibold ${filter === key ? "border-blue-600 bg-blue-600 text-white" : "border-slate-300 bg-white text-slate-700"}`}>{label}</button>)}
            </div>
            {buildings.map((building) => {
              const rooms = building.rooms.filter((room) => filter === "all" || room.state === filter);
              if (!rooms.length) return null;
              return (
                <Section key={building.id} className="mb-6" title={building.name} subtitle={`${building.occupied} people · ${building.capacity} beds · ${building.full_rooms} full, ${building.rooms_with_space} with space, ${building.empty_rooms} empty${building.kind === "external" ? " · outside accommodation" : ""}`}>
                  <div className="grid gap-3 p-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
                    {rooms.map((room) => {
                      const style = stateStyle[room.state] || stateStyle.empty;
                      const pct = room.capacity ? Math.min(100, Math.round((room.occupied / room.capacity) * 100)) : 0;
                      return (
                        <button key={room.id} type="button" onClick={() => { setOpenRoom({ room, building }); setPick({ q: "", employee: "", bed: "", capacity: room.capacity ? String(room.capacity) : "" }); }} className={`rounded-xl border p-4 text-left ${style.card}`}>
                          <div className="flex items-center justify-between"><p className="font-bold text-slate-900">{room.name}</p><span className="text-xs font-semibold text-slate-600">{style.label}</span></div>
                          <p className="mt-2 text-2xl font-bold text-slate-900">{room.occupied}<span className="text-base font-semibold text-slate-500"> / {room.capacity ?? "?"}</span></p>
                          <div className="mt-2 h-2 rounded-full bg-white"><div className={`h-2 rounded-full ${style.bar}`} style={{ width: `${pct}%` }} /></div>
                          <p className="mt-2 text-xs text-slate-600">{room.free !== null ? `${room.free} bed${room.free === 1 ? "" : "s"} free` : "Set the capacity"}{room.capacity_estimated ? " (estimated)" : ""}</p>
                        </button>
                      );
                    })}
                  </div>
                </Section>
              );
            })}
            {!buildings.length && <AppCard><p className="p-8 text-center text-slate-500">No rooms recorded yet. Use Import spreadsheet to load them from your staff file.</p></AppCard>}
          </>
        )}

        {!loading && tab === "people" && (
          <Section title="Active staff" subtitle={`${shownPeople.length} shown`}>
            <div className="flex flex-wrap gap-3 p-4">
              <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search name, staff number or room..." className="w-full max-w-sm rounded-xl border border-slate-300 px-3 py-2.5 text-sm outline-none focus:ring-2 focus:ring-blue-500" />
              {([["", "Everyone"], ["inside", "Company accommodation"], ["outside", "Outside"], ["none", "None"]] as const).map(([key, label]) => <button key={key} type="button" onClick={() => setWhere(key)} className={`rounded-full border px-3 py-1.5 text-sm font-semibold ${where === key ? "border-blue-600 bg-blue-600 text-white" : "border-slate-300 bg-white text-slate-700"}`}>{label}</button>)}
            </div>
            <div className="max-h-[560px] overflow-auto">
              <table className="w-full min-w-[700px] text-left">
                <thead className="sticky top-0 bg-slate-50 text-xs font-semibold uppercase tracking-wide text-slate-500"><tr><th className="px-5 py-3">Staff</th><th className="px-5 py-3">Department</th><th className="px-5 py-3">Lives</th><th className="px-5 py-3">Where</th></tr></thead>
                <tbody className="divide-y divide-slate-100">
                  {shownPeople.map((p) => <tr key={p.id}><td className="px-5 py-3"><p className="font-semibold text-slate-900">{p.name}</p><p className="text-xs text-slate-500">{p.employee_id}</p></td><td className="px-5 py-3 text-sm text-slate-600">{p.department || "-"}</td><td className="px-5 py-3"><span className={`rounded-full px-3 py-1 text-xs font-semibold ${p.where === "inside" ? "bg-blue-100 text-blue-800" : p.where === "outside" ? "bg-violet-100 text-violet-800" : "bg-slate-100 text-slate-600"}`}>{p.where === "inside" ? "Company" : p.where === "outside" ? "Outside" : "None"}</span></td><td className="px-5 py-3 text-sm text-slate-700">{p.place || "-"}</td></tr>)}
                </tbody>
              </table>
            </div>
          </Section>
        )}

        {!loading && tab === "import" && canManage && (
          <AppCard title="Load from the staff spreadsheet">
            <div className="space-y-4 p-1">
              <p className="text-sm text-slate-600">Upload your staff file (.xlsx) with the columns <b>ID</b>, <b>EMPLOYMENT STATUS</b>, <b>Accommodation</b> and <b>ROOM ALLOCATED</b>. Active staff with a room are placed in that room and bed; those marked YES with no room are recorded as living outside; NO means no accommodation. Nothing changes until you press Apply.</p>
              <input type="file" accept=".xlsx" onChange={(event) => { setFile(event.target.files?.[0] || null); setReport(null); }} className="block text-sm" />
              <div className="flex gap-3">
                <button type="button" disabled={!file || busy} onClick={() => void runImport(true)} className="rounded-xl border border-slate-300 bg-white px-4 py-2.5 text-sm font-semibold text-slate-700 disabled:opacity-50">{busy ? "Reading..." : "Preview"}</button>
                <button type="button" disabled={!report || !report.dry_run || busy} onClick={() => void runImport(false)} className="rounded-xl bg-blue-600 px-4 py-2.5 text-sm font-semibold text-white disabled:bg-blue-300">Apply</button>
              </div>
              {report && (
                <div className="rounded-xl border border-slate-200 p-4 text-sm">
                  <p className="font-semibold text-slate-900">{report.dry_run ? "Preview - nothing has been changed yet" : "Applied"}</p>
                  <ul className="mt-2 grid gap-1 text-slate-700 sm:grid-cols-2">
                    <li>{report.active_rows} active staff in the file</li>
                    <li><b>{report.placed_inside}</b> placed in a room ({report.unchanged} already correct, {report.moved} moved)</li>
                    <li><b>{report.outside_unplaced}</b> living outside (place not recorded)</li>
                    <li><b>{report.no_accommodation}</b> with no accommodation</li>
                    <li>{report.buildings_created} new building(s), {report.rooms_created} new room(s)</li>
                    <li>{report.freed_beds_from_inactive} bed(s) freed by staff who have left</li>
                  </ul>
                  {report.unmatched_ids.length > 0 && <p className="mt-3 rounded-lg bg-amber-50 p-3 text-xs text-amber-900">{report.unmatched_ids.length} staff number(s) are not in the system and were skipped: {report.unmatched_ids.slice(0, 20).join(", ")}</p>}
                  {report.bed_conflicts.length > 0 && <p className="mt-3 rounded-lg bg-amber-50 p-3 text-xs text-amber-900">Two people had the same bed: {report.bed_conflicts.map((c) => `${c.employee_id} in ${c.room} bed ${c.bed} (already ${c.already})`).join("; ")}. The later one is placed with no bed number.</p>}
                  {report.unreadable_rooms.length > 0 && <p className="mt-3 rounded-lg bg-amber-50 p-3 text-xs text-amber-900">Room text not understood: {report.unreadable_rooms.slice(0, 10).map((u) => `${u.employee_id} "${u.value}"`).join("; ")}</p>}
                  {report.mixed_gender_rooms.length > 0 && <p className="mt-3 rounded-lg bg-amber-50 p-3 text-xs text-amber-900">Men and women in the same room: {report.mixed_gender_rooms.join(", ")}</p>}
                </div>
              )}
            </div>
          </AppCard>
        )}

        {openRoom && (
          <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/40 p-4">
            <div className="flex max-h-[90vh] w-full max-w-xl flex-col rounded-2xl bg-white shadow-xl">
              <div className="border-b border-slate-200 p-5"><h2 className="text-lg font-bold text-slate-900">{openRoom.building.name} · {openRoom.room.name}</h2><p className="text-sm text-slate-500">{openRoom.room.occupied} of {openRoom.room.capacity ?? "?"} beds taken{openRoom.room.capacity_estimated ? " (capacity estimated from the spreadsheet)" : ""}</p></div>
              <div className="overflow-y-auto p-5">
                {error && <p className="mb-3 rounded-xl bg-red-50 p-3 text-sm text-red-700">{error}</p>}
                <ul className="divide-y divide-slate-100">
                  {openRoom.room.occupants.map((o) => (
                    <li key={o.employee} className="flex items-center justify-between py-2 text-sm"><span><b className="text-slate-900">{o.name}</b> <span className="text-slate-500">{o.employee_id}{o.bed ? ` · bed ${o.bed}` : ""}{o.department ? ` · ${o.department}` : ""}</span></span>{canManage && <button type="button" disabled={busy} onClick={() => void send("/accommodation/unassign/", "POST", { employee: o.employee }, `${o.name} removed from ${openRoom.room.name}.`)} className="rounded-lg border border-slate-300 px-2 py-1 text-xs font-semibold text-slate-700">Remove</button>}</li>
                  ))}
                  {!openRoom.room.occupants.length && <li className="py-3 text-sm text-slate-500">Nobody is in this room.</li>}
                </ul>
                {canManage && (
                  <div className="mt-5 space-y-3 rounded-xl bg-slate-50 p-4">
                    <p className="text-sm font-semibold text-slate-800">Put someone in this room</p>
                    <input value={pick.q} onChange={(event) => setPick({ ...pick, q: event.target.value })} placeholder="Search name or staff number..." className={inputClass} />
                    <select value={pick.employee} onChange={(event) => setPick({ ...pick, employee: event.target.value })} className={inputClass}><option value="">Select a person</option>{candidates.map((p) => <option key={p.id} value={p.id}>{p.name} ({p.employee_id}){p.place ? ` - now ${p.place}` : ""}</option>)}</select>
                    <div className="flex gap-3"><input type="number" min="1" value={pick.bed} onChange={(event) => setPick({ ...pick, bed: event.target.value })} placeholder="Bed number (optional)" className={inputClass} /><button type="button" disabled={!pick.employee || busy} onClick={() => void send("/accommodation/assign/", "POST", { employee: Number(pick.employee), room: openRoom.room.id, bed: pick.bed || null }, "Placed in the room.").then((ok) => { if (ok) setPick({ ...pick, employee: "", bed: "", q: "" }); })} className="mt-1 rounded-xl bg-blue-600 px-4 text-sm font-semibold text-white disabled:bg-blue-300">Place</button></div>
                    <div className="flex items-end gap-3 border-t border-slate-200 pt-3"><label className="text-sm font-semibold text-slate-700">Room capacity<input type="number" min="1" max="100" value={pick.capacity} onChange={(event) => setPick({ ...pick, capacity: event.target.value })} className={inputClass} /></label><button type="button" disabled={busy} onClick={() => void send(`/accommodation/rooms/${openRoom.room.id}/`, "PATCH", { capacity: pick.capacity || null }, "Capacity saved.")} className="rounded-xl border border-slate-300 bg-white px-4 py-2.5 text-sm font-semibold text-slate-700">Save</button></div>
                  </div>
                )}
              </div>
              <div className="flex justify-end border-t border-slate-200 p-4"><button type="button" onClick={() => setOpenRoom(null)} className="rounded-xl border border-slate-300 px-4 py-2.5 text-sm font-semibold text-slate-700">Close</button></div>
            </div>
          </div>
        )}
      </main>
    </div>
  );
}

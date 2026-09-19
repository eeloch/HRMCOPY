"use client";

import { useEffect, useEffectEvent, useMemo, useState } from "react";

import { AppCard, Section } from "@/components/ui";
import { apiFetch, getCurrentUser, type CurrentUser } from "@/lib/api";

type Occupant = { employee: number; employee_id: string; name: string; gender: string; bed: number | null; department: string | null };
type Room = { id: number; name: string; gender: string; capacity: number | null; capacity_estimated: boolean; occupied: number; free: number | null; state: string; active: boolean; notes: string; occupants: Occupant[] };
type Building = { id: number; name: string; kind: string; address: string; rooms: Room[]; occupied: number; capacity: number; full_rooms: number; empty_rooms: number; rooms_with_space: number };
type FloorStats = { rooms: number; beds: number; occupied: number; free: number; rooms_full: number; rooms_with_space: number; waiting_for_a_room: number };
type Summary = { active_staff: number; inside: number; inside_with_room: number; inside_without_room: number; outside: number; outside_placed: number; outside_unplaced: number; none: number; beds_total: number; beds_occupied: number; beds_free: number; rooms_full: number; rooms_with_space: number; rooms_empty: number; by_gender: { male: FloorStats; female: FloorStats } };
type Person = { id: number; employee_id: string; name: string; department: string | null; gender: string; where: string; place: string; room: number | null };
type SetupReport = { dry_run: boolean; rooms_in_file: number; male_rooms: number; male_beds: number; female_rooms: number; female_beds: number; created: string[]; updated: string[]; unchanged: number; removed: string[]; kept_with_people: { room: string; people: number }[]; gender_conflicts: { room: string; gender: string; people: string[] }[] };
type StaffReport = { dry_run: boolean; active_rows: number; placed_inside: number; outside_unplaced: number; no_accommodation: number; moved: number; unchanged: number; genders_set: number; unmatched_ids: string[]; unreadable_rooms: { employee_id: string; value: string }[]; unknown_rooms: Record<string, number>; gender_mismatches: { employee_id: string; name: string; gender: string; room: string; room_gender: string }[]; bed_conflicts: { employee_id: string; room: string; bed: number; already: string }[]; over_capacity_rooms: string[]; freed_beds_from_inactive: number };

const stateStyle: Record<string, { label: string; card: string; bar: string }> = {
  full: { label: "Full", card: "border-red-200 bg-red-50", bar: "bg-red-500" },
  space: { label: "Space", card: "border-emerald-200 bg-emerald-50", bar: "bg-emerald-500" },
  empty: { label: "Empty", card: "border-slate-300 bg-white", bar: "bg-slate-300" },
  unknown: { label: "Capacity not set", card: "border-amber-200 bg-amber-50", bar: "bg-amber-400" },
  over: { label: "Over capacity", card: "border-red-400 bg-red-100", bar: "bg-red-700" },
  closed: { label: "Closed", card: "border-slate-200 bg-slate-100 opacity-60", bar: "bg-slate-300" },
};
const stateFilters = [["all", "All rooms"], ["full", "Full"], ["space", "Has space"], ["empty", "Empty"], ["unknown", "Capacity not set"]] as const;
const genderBadge: Record<string, string> = { male: "bg-sky-100 text-sky-800", female: "bg-pink-100 text-pink-800" };
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

function Sample({ items, empty }: { items: string[]; empty?: string }) {
  if (!items.length) return empty ? <span className="text-slate-500">{empty}</span> : null;
  return <span>{items.slice(0, 12).join(", ")}{items.length > 12 ? ` and ${items.length - 12} more` : ""}</span>;
}

/** Rooms, beds, the male and female floors, and who lives where. Lives on the Employees page, Accommodation tab. */
export function AccommodationTracker() {
  const [user, setUser] = useState<CurrentUser | null>(null);
  const [summary, setSummary] = useState<Summary | null>(null);
  const [buildings, setBuildings] = useState<Building[]>([]);
  const [people, setPeople] = useState<Person[]>([]);
  const [tab, setTab] = useState<"rooms" | "people">("rooms");
  const [filter, setFilter] = useState<(typeof stateFilters)[number][0]>("all");
  const [floor, setFloor] = useState<"" | "male" | "female">("");
  const [where, setWhere] = useState("");
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [feedback, setFeedback] = useState("");
  const [openRoom, setOpenRoom] = useState<{ room: Room; building: Building } | null>(null);
  const [pick, setPick] = useState({ q: "", employee: "", bed: "", capacity: "", gender: "" });
  const [adding, setAdding] = useState(false);
  const [newRoom, setNewRoom] = useState({ building: "", newBuilding: "", name: "", gender: "male", capacity: "" });
  const [importing, setImporting] = useState(false);
  const [trackerFile, setTrackerFile] = useState<File | null>(null);
  const [setupReport, setSetupReport] = useState<SetupReport | null>(null);
  const [staffFile, setStaffFile] = useState<File | null>(null);
  const [staffReport, setStaffReport] = useState<StaffReport | null>(null);

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
    const timer = window.setTimeout(loadOnMount, 0);
    return () => window.clearTimeout(timer);
  }, []);

  async function send(path: string, method: string, body: object | null, success: string) {
    setBusy(true); setError("");
    try {
      const response = await apiFetch(path, { method, ...(body ? { body: JSON.stringify(body) } : {}) });
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

  async function upload(path: string, file: File, dryRun: boolean) {
    setBusy(true); setError("");
    try {
      const form = new FormData();
      form.append("file", file);
      form.append("dry_run", dryRun ? "true" : "false");
      const response = await apiFetch(path, { method: "POST", body: form });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(apiError(data, "Unable to read that file."));
      return data;
    } catch (uploadError) {
      setError(uploadError instanceof Error ? uploadError.message : "Unable to read that file.");
      return null;
    } finally { setBusy(false); }
  }

  async function runTracker(dryRun: boolean) {
    if (!trackerFile) return;
    const data = await upload("/accommodation/import-rooms/", trackerFile, dryRun);
    if (!data) return;
    setSetupReport(data);
    if (!dryRun) { setFeedback("Rooms updated from the tracker."); setTrackerFile(null); await load(); }
  }

  async function runStaff(dryRun: boolean) {
    if (!staffFile) return;
    const data = await upload("/accommodation/import/", staffFile, dryRun);
    if (!data) return;
    setStaffReport(data);
    if (!dryRun) { setFeedback("Staff placed from the spreadsheet."); setStaffFile(null); await load(); }
  }

  async function addRoom() {
    const body = { name: newRoom.name, gender: newRoom.gender, capacity: newRoom.capacity || null, ...(newRoom.building === "__new" ? { building_name: newRoom.newBuilding } : newRoom.building ? { building: Number(newRoom.building) } : {}) };
    if (await send("/accommodation/rooms/", "POST", body, `${newRoom.name} added.`)) { setAdding(false); setNewRoom({ ...newRoom, name: "", capacity: "" }); }
  }

  const shownPeople = useMemo(() => people.filter((p) => (!where || p.where === where) && (!query.trim() || `${p.name} ${p.employee_id} ${p.place}`.toLowerCase().includes(query.trim().toLowerCase()))), [people, where, query]);
  const candidates = useMemo(() => people.filter((p) => p.room !== openRoom?.room.id && (!openRoom?.room.gender || !p.gender || p.gender === openRoom.room.gender) && `${p.name} ${p.employee_id}`.toLowerCase().includes(pick.q.trim().toLowerCase())).slice(0, 60), [people, pick.q, openRoom]);

  if (!loading && user && !summary) {
    return <AppCard><p className="p-8 text-center text-slate-600">{error || "Your account does not have access to room and bed details."}</p></AppCard>;
  }

  const stat = (label: string, value: number | string, note: string, tone: string) => (
    <div className="rounded-2xl border border-slate-200 bg-white p-5"><p className="text-sm font-semibold text-slate-500">{label}</p><p className={`mt-1 text-3xl font-bold ${tone}`}>{value}</p><p className="mt-1 text-xs text-slate-500">{note}</p></div>
  );
  const floorCard = (title: string, stats: FloorStats, badge: string) => (
    <div className="rounded-2xl border border-slate-200 bg-white p-5">
      <div className="flex items-center justify-between"><p className="text-sm font-semibold text-slate-500">{title}</p><span className={`rounded-full px-3 py-1 text-xs font-semibold ${badge}`}>{stats.rooms} rooms</span></div>
      <p className="mt-1 text-3xl font-bold text-slate-900">{stats.free}<span className="text-base font-semibold text-slate-500"> beds left of {stats.beds}</span></p>
      <div className="mt-2 h-2 rounded-full bg-slate-100"><div className="h-2 rounded-full bg-slate-700" style={{ width: `${stats.beds ? Math.round((stats.occupied / stats.beds) * 100) : 0}%` }} /></div>
      <p className="mt-2 text-xs text-slate-500">{stats.occupied} taken · {stats.rooms_full} rooms full · {stats.rooms_with_space} with space{stats.waiting_for_a_room ? ` · ${stats.waiting_for_a_room} waiting for a room` : ""}</p>
    </div>
  );

  return (
    <div className="mt-6">
      {feedback && <p className="mb-6 rounded-xl bg-emerald-50 p-4 text-sm text-emerald-800">{feedback}</p>}
      {error && !openRoom && !adding && !importing && <p className="mb-6 rounded-xl bg-red-50 p-4 text-sm text-red-700">{error}</p>}

      {summary && (
        <>
          <div className="mb-4 grid gap-4 sm:grid-cols-2">
            {floorCard("Male floor", summary.by_gender.male, genderBadge.male)}
            {floorCard("Female floor", summary.by_gender.female, genderBadge.female)}
          </div>
          <div className="mb-6 grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
            {stat("In company accommodation", summary.inside, `${summary.inside_with_room} in a room${summary.inside_without_room ? `, ${summary.inside_without_room} with no room recorded` : ""}`, "text-blue-700")}
            {stat("In outside accommodation", summary.outside, summary.outside_unplaced ? `${summary.outside_unplaced} with the place not recorded yet` : "all placed", "text-violet-700")}
            {stat("No accommodation", summary.none, `of ${summary.active_staff} active staff`, "text-slate-800")}
            {stat("Beds free", summary.beds_free, `${summary.beds_occupied} of ${summary.beds_total} beds taken`, "text-emerald-700")}
          </div>
        </>
      )}

      <div className="mb-4 flex flex-wrap items-center gap-2">
        {([["rooms", "Rooms"], ["people", "People"]] as const).map(([key, label]) => (
          <button key={key} type="button" onClick={() => setTab(key)} className={`rounded-full px-4 py-2 text-sm font-semibold ${tab === key ? "bg-slate-900 text-white" : "bg-white text-slate-700 hover:bg-slate-50"}`}>{label}</button>
        ))}
        {canManage && (
          <div className="ml-auto flex gap-2">
            <button type="button" onClick={() => { setError(""); setAdding(true); }} className="rounded-xl bg-blue-600 px-4 py-2 text-sm font-semibold text-white">+ Add room</button>
            <button type="button" onClick={() => { setError(""); setImporting(true); }} className="rounded-xl border border-slate-300 bg-white px-4 py-2 text-sm font-semibold text-slate-700">Import / re-upload</button>
          </div>
        )}
      </div>

      {loading && <div className="h-40 animate-pulse rounded-2xl bg-slate-200" />}

      {!loading && tab === "rooms" && (
        <>
          <div className="mb-4 flex flex-wrap gap-2">
            {([["", "Both floors"], ["male", "Male floor"], ["female", "Female floor"]] as const).map(([key, label]) => <button key={key} type="button" onClick={() => setFloor(key)} className={`rounded-full border px-3 py-1.5 text-sm font-semibold ${floor === key ? "border-slate-900 bg-slate-900 text-white" : "border-slate-300 bg-white text-slate-700"}`}>{label}</button>)}
            <span className="mx-1 self-center text-slate-300">|</span>
            {stateFilters.map(([key, label]) => <button key={key} type="button" onClick={() => setFilter(key)} className={`rounded-full border px-3 py-1.5 text-sm font-semibold ${filter === key ? "border-blue-600 bg-blue-600 text-white" : "border-slate-300 bg-white text-slate-700"}`}>{label}</button>)}
          </div>
          {buildings.map((building) => {
            const rooms = building.rooms.filter((room) => (filter === "all" || room.state === filter) && (!floor || room.gender === floor));
            if (!rooms.length) return null;
            return (
              <Section key={building.id} className="mb-6" title={building.name} subtitle={`${building.occupied} people · ${building.capacity} beds · ${building.full_rooms} full, ${building.rooms_with_space} with space, ${building.empty_rooms} empty${building.kind === "external" ? " · outside accommodation" : ""}`}>
                <div className="grid gap-3 p-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
                  {rooms.map((room) => {
                    const style = stateStyle[room.state] || stateStyle.empty;
                    const pct = room.capacity ? Math.min(100, Math.round((room.occupied / room.capacity) * 100)) : 0;
                    return (
                      <button key={room.id} type="button" onClick={() => { setError(""); setOpenRoom({ room, building }); setPick({ q: "", employee: "", bed: "", capacity: room.capacity ? String(room.capacity) : "", gender: room.gender }); }} className={`rounded-xl border p-4 text-left ${style.card}`}>
                        <div className="flex items-center justify-between gap-2"><p className="font-bold text-slate-900">{room.name}</p><div className="flex items-center gap-1">{room.gender && <span className={`rounded-full px-2 py-0.5 text-[10px] font-bold uppercase ${genderBadge[room.gender]}`}>{room.gender === "male" ? "M" : "F"}</span>}<span className="text-xs font-semibold text-slate-600">{style.label}</span></div></div>
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
          {!buildings.length && <AppCard><p className="p-8 text-center text-slate-500">No rooms yet. Use Import / re-upload to load them from the accommodation tracker, or Add room.</p></AppCard>}
        </>
      )}

      {!loading && tab === "people" && (
        <Section title="Active staff" subtitle={`${shownPeople.length} shown`}>
          <div className="flex flex-wrap gap-3 p-4">
            <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search name, staff number or room..." className="w-full max-w-sm rounded-xl border border-slate-300 px-3 py-2.5 text-sm outline-none focus:ring-2 focus:ring-blue-500" />
            {([["", "Everyone"], ["inside", "Company accommodation"], ["outside", "Outside"], ["none", "None"]] as const).map(([key, label]) => <button key={key} type="button" onClick={() => setWhere(key)} className={`rounded-full border px-3 py-1.5 text-sm font-semibold ${where === key ? "border-blue-600 bg-blue-600 text-white" : "border-slate-300 bg-white text-slate-700"}`}>{label}</button>)}
          </div>
          <div className="max-h-[560px] overflow-auto">
            <table className="w-full min-w-[760px] text-left">
              <thead className="sticky top-0 bg-slate-50 text-xs font-semibold uppercase tracking-wide text-slate-500"><tr><th className="px-5 py-3">Staff</th><th className="px-5 py-3">Department</th><th className="px-5 py-3">Gender</th><th className="px-5 py-3">Lives</th><th className="px-5 py-3">Where</th></tr></thead>
              <tbody className="divide-y divide-slate-100">
                {shownPeople.map((p) => <tr key={p.id}><td className="px-5 py-3"><p className="font-semibold text-slate-900">{p.name}</p><p className="text-xs text-slate-500">{p.employee_id}</p></td><td className="px-5 py-3 text-sm text-slate-600">{p.department || "-"}</td><td className="px-5 py-3 text-sm capitalize text-slate-600">{p.gender || "-"}</td><td className="px-5 py-3"><span className={`rounded-full px-3 py-1 text-xs font-semibold ${p.where === "inside" ? "bg-blue-100 text-blue-800" : p.where === "outside" ? "bg-violet-100 text-violet-800" : "bg-slate-100 text-slate-600"}`}>{p.where === "inside" ? "Company" : p.where === "outside" ? "Outside" : "None"}</span></td><td className="px-5 py-3 text-sm text-slate-700">{p.place || "-"}</td></tr>)}
              </tbody>
            </table>
          </div>
        </Section>
      )}

      {openRoom && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/40 p-4">
          <div className="flex max-h-[90vh] w-full max-w-xl flex-col rounded-2xl bg-white shadow-xl">
            <div className="border-b border-slate-200 p-5"><h2 className="text-lg font-bold text-slate-900">{openRoom.building.name} · {openRoom.room.name} {openRoom.room.gender && <span className={`ml-2 rounded-full px-2 py-0.5 text-xs font-bold uppercase ${genderBadge[openRoom.room.gender]}`}>{openRoom.room.gender}</span>}</h2><p className="text-sm text-slate-500">{openRoom.room.occupied} of {openRoom.room.capacity ?? "?"} beds taken</p></div>
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
                  <p className="text-sm font-semibold text-slate-800">Put someone in this room{openRoom.room.gender ? ` (${openRoom.room.gender} staff only)` : ""}</p>
                  <input value={pick.q} onChange={(event) => setPick({ ...pick, q: event.target.value })} placeholder="Search name or staff number..." className={inputClass} />
                  <select value={pick.employee} onChange={(event) => setPick({ ...pick, employee: event.target.value })} className={inputClass}><option value="">Select a person</option>{candidates.map((p) => <option key={p.id} value={p.id}>{p.name} ({p.employee_id}){p.place ? ` - now ${p.place}` : ""}</option>)}</select>
                  <div className="flex gap-3"><input type="number" min="1" value={pick.bed} onChange={(event) => setPick({ ...pick, bed: event.target.value })} placeholder="Bed number (optional)" className={inputClass} /><button type="button" disabled={!pick.employee || busy} onClick={() => void send("/accommodation/assign/", "POST", { employee: Number(pick.employee), room: openRoom.room.id, bed: pick.bed || null }, "Placed in the room.").then((ok) => { if (ok) setPick({ ...pick, employee: "", bed: "", q: "" }); })} className="mt-1 rounded-xl bg-blue-600 px-4 text-sm font-semibold text-white disabled:bg-blue-300">Place</button></div>
                  <div className="grid grid-cols-2 gap-3 border-t border-slate-200 pt-3">
                    <label className="text-sm font-semibold text-slate-700">Beds<input type="number" min="1" max="100" value={pick.capacity} onChange={(event) => setPick({ ...pick, capacity: event.target.value })} className={inputClass} /></label>
                    <label className="text-sm font-semibold text-slate-700">Floor<select value={pick.gender} onChange={(event) => setPick({ ...pick, gender: event.target.value })} className={inputClass}><option value="">Not set</option><option value="male">Male</option><option value="female">Female</option></select></label>
                    <button type="button" disabled={busy} onClick={() => void send(`/accommodation/rooms/${openRoom.room.id}/`, "PATCH", { capacity: pick.capacity || null, gender: pick.gender }, "Room saved.")} className="col-span-2 rounded-xl border border-slate-300 bg-white px-4 py-2.5 text-sm font-semibold text-slate-700">Save beds and floor</button>
                  </div>
                  <div className="flex items-center justify-between border-t border-slate-200 pt-3"><p className="text-xs text-slate-500">Room does not exist? Remove it. Only an empty room can be removed.</p><button type="button" disabled={busy} onClick={() => { if (window.confirm(`Remove ${openRoom.room.name} from ${openRoom.building.name}?`)) void send(`/accommodation/rooms/${openRoom.room.id}/`, "DELETE", null, `${openRoom.room.name} removed.`).then((ok) => { if (ok) setOpenRoom(null); }); }} className="rounded-xl border border-red-300 px-3 py-2 text-xs font-semibold text-red-700">Remove room</button></div>
                </div>
              )}
            </div>
            <div className="flex justify-end border-t border-slate-200 p-4"><button type="button" onClick={() => setOpenRoom(null)} className="rounded-xl border border-slate-300 px-4 py-2.5 text-sm font-semibold text-slate-700">Close</button></div>
          </div>
        </div>
      )}

      {adding && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/40 p-4">
          <div className="w-full max-w-md rounded-2xl bg-white p-6 shadow-xl">
            <h2 className="text-lg font-bold text-slate-900">Add a room</h2>
            {error && <p className="mt-3 rounded-xl bg-red-50 p-3 text-sm text-red-700">{error}</p>}
            <label className="mt-4 block text-sm font-semibold text-slate-700">Building<select value={newRoom.building} onChange={(event) => setNewRoom({ ...newRoom, building: event.target.value })} className={inputClass}><option value="">Work it out from the room name</option>{buildings.map((b) => <option key={b.id} value={b.id}>{b.name}</option>)}<option value="__new">A new building...</option></select></label>
            {newRoom.building === "__new" && <input value={newRoom.newBuilding} onChange={(event) => setNewRoom({ ...newRoom, newBuilding: event.target.value })} placeholder="New building name" className={inputClass} />}
            <label className="mt-3 block text-sm font-semibold text-slate-700">Room name or number<input value={newRoom.name} onChange={(event) => setNewRoom({ ...newRoom, name: event.target.value })} placeholder="e.g. Room 305" className={inputClass} /></label>
            <div className="mt-3 grid grid-cols-2 gap-3">
              <label className="text-sm font-semibold text-slate-700">Floor<select value={newRoom.gender} onChange={(event) => setNewRoom({ ...newRoom, gender: event.target.value })} className={inputClass}><option value="male">Male</option><option value="female">Female</option></select></label>
              <label className="text-sm font-semibold text-slate-700">Beds<input type="number" min="1" max="100" value={newRoom.capacity} onChange={(event) => setNewRoom({ ...newRoom, capacity: event.target.value })} className={inputClass} /></label>
            </div>
            <div className="mt-6 flex justify-end gap-3"><button type="button" onClick={() => setAdding(false)} className="rounded-xl border border-slate-300 px-4 py-2.5 text-sm font-semibold text-slate-700">Cancel</button><button type="button" disabled={busy || !newRoom.name.trim() || (newRoom.building === "__new" && !newRoom.newBuilding.trim())} onClick={() => void addRoom()} className="rounded-xl bg-blue-600 px-4 py-2.5 text-sm font-semibold text-white disabled:bg-blue-300">Add room</button></div>
          </div>
        </div>
      )}

      {importing && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/40 p-4">
          <div className="flex max-h-[92vh] w-full max-w-3xl flex-col rounded-2xl bg-white shadow-xl">
            <div className="border-b border-slate-200 p-5"><h2 className="text-lg font-bold text-slate-900">Import / re-upload</h2><p className="text-sm text-slate-500">Do step 1 first, then step 2. Every upload is previewed first and nothing changes until you press Apply.</p></div>
            <div className="space-y-6 overflow-y-auto p-5 text-sm">
              {error && <p className="rounded-xl bg-red-50 p-3 text-red-700">{error}</p>}

              <div className="rounded-xl border border-slate-200 p-4">
                <p className="font-semibold text-slate-900">1. Accommodation tracker (the rooms)</p>
                <p className="mt-1 text-slate-600">The tracker workbook (.xlsb or .xlsx). Its <b>Room Setup</b> sheet decides the rooms, which are male or female, and how many beds each has. Rooms that are not in it are removed (a room with people in it is kept and listed).</p>
                <input type="file" accept=".xlsb,.xlsx" onChange={(event) => { setTrackerFile(event.target.files?.[0] || null); setSetupReport(null); }} className="mt-3 block text-sm" />
                <div className="mt-3 flex gap-3">
                  <button type="button" disabled={!trackerFile || busy} onClick={() => void runTracker(true)} className="rounded-xl border border-slate-300 bg-white px-4 py-2 font-semibold text-slate-700 disabled:opacity-50">{busy ? "Reading..." : "Preview"}</button>
                  <button type="button" disabled={!setupReport || !setupReport.dry_run || busy} onClick={() => void runTracker(false)} className="rounded-xl bg-blue-600 px-4 py-2 font-semibold text-white disabled:bg-blue-300">Apply</button>
                </div>
                {setupReport && (
                  <div className="mt-3 space-y-2 rounded-xl bg-slate-50 p-3">
                    <p className="font-semibold text-slate-900">{setupReport.dry_run ? "Preview - nothing changed yet" : "Applied"}</p>
                    <p>{setupReport.rooms_in_file} rooms in the file: <b>{setupReport.male_rooms}</b> male ({setupReport.male_beds} beds) and <b>{setupReport.female_rooms}</b> female ({setupReport.female_beds} beds).</p>
                    <p>{setupReport.created.length} to add · {setupReport.updated.length} to change · {setupReport.unchanged} already right · <b className={setupReport.removed.length ? "text-red-700" : ""}>{setupReport.removed.length} to remove</b></p>
                    {setupReport.removed.length > 0 && <p className="text-xs text-red-700">Removed: <Sample items={setupReport.removed} /></p>}
                    {setupReport.updated.length > 0 && <p className="text-xs text-slate-600">Changed: <Sample items={setupReport.updated} /></p>}
                    {setupReport.kept_with_people.length > 0 && <p className="rounded-lg bg-amber-50 p-2 text-xs text-amber-900">Not in the tracker but still has people, so kept: {setupReport.kept_with_people.map((k) => `${k.room} (${k.people})`).join(", ")}. Move them, then remove the room.</p>}
                    {setupReport.gender_conflicts.length > 0 && <p className="rounded-lg bg-amber-50 p-2 text-xs text-amber-900">Wrong floor for the people inside: {setupReport.gender_conflicts.map((g) => `${g.room} is ${g.gender} but has ${g.people.slice(0, 3).join(", ")}`).join("; ")}</p>}
                  </div>
                )}
              </div>

              <div className="rounded-xl border border-slate-200 p-4">
                <p className="font-semibold text-slate-900">2. Staff file (who sleeps where)</p>
                <p className="mt-1 text-slate-600">The staff spreadsheet (.xlsx) with ID, GENDER, EMPLOYMENT STATUS, Accommodation and ROOM ALLOCATED. Staff go into the rooms from step 1. A room that is not one of yours is never created: that person stays in company accommodation with no room, and the room is listed below. (Employees &gt; Bulk Import does the same.)</p>
                <input type="file" accept=".xlsx" onChange={(event) => { setStaffFile(event.target.files?.[0] || null); setStaffReport(null); }} className="mt-3 block text-sm" />
                <div className="mt-3 flex gap-3">
                  <button type="button" disabled={!staffFile || busy} onClick={() => void runStaff(true)} className="rounded-xl border border-slate-300 bg-white px-4 py-2 font-semibold text-slate-700 disabled:opacity-50">{busy ? "Reading..." : "Preview"}</button>
                  <button type="button" disabled={!staffReport || !staffReport.dry_run || busy} onClick={() => void runStaff(false)} className="rounded-xl bg-blue-600 px-4 py-2 font-semibold text-white disabled:bg-blue-300">Apply</button>
                </div>
                {staffReport && (
                  <div className="mt-3 space-y-2 rounded-xl bg-slate-50 p-3">
                    <p className="font-semibold text-slate-900">{staffReport.dry_run ? "Preview - nothing changed yet" : "Applied"}</p>
                    <p>{staffReport.active_rows} active staff · <b>{staffReport.placed_inside}</b> in company accommodation ({staffReport.unchanged} already right, {staffReport.moved} moved) · <b>{staffReport.outside_unplaced}</b> outside · <b>{staffReport.no_accommodation}</b> none · {staffReport.freed_beds_from_inactive} beds freed by leavers</p>
                    {Object.keys(staffReport.unknown_rooms).length > 0 && <p className="rounded-lg bg-amber-50 p-2 text-xs text-amber-900"><b>Rooms in the staff file that are not in your rooms</b> (people are in company accommodation but have no room yet): {Object.entries(staffReport.unknown_rooms).map(([room, count]) => `${room} (${count})`).join(", ")}. Add the room, or correct its name in the file, then upload again.</p>}
                    {staffReport.gender_mismatches.length > 0 && <p className="rounded-lg bg-amber-50 p-2 text-xs text-amber-900">Wrong floor, not placed: {staffReport.gender_mismatches.map((m) => `${m.name} (${m.gender}) in ${m.room} (${m.room_gender})`).join("; ")}</p>}
                    {staffReport.unmatched_ids.length > 0 && <p className="rounded-lg bg-amber-50 p-2 text-xs text-amber-900">Staff numbers not in the system, skipped: <Sample items={staffReport.unmatched_ids} /></p>}
                    {staffReport.bed_conflicts.length > 0 && <p className="rounded-lg bg-amber-50 p-2 text-xs text-amber-900">Same bed twice: {staffReport.bed_conflicts.map((c) => `${c.employee_id} in ${c.room} bed ${c.bed}`).join("; ")}</p>}
                    {staffReport.over_capacity_rooms.length > 0 && <p className="rounded-lg bg-amber-50 p-2 text-xs text-amber-900">Bed number beyond the room&apos;s beds: {staffReport.over_capacity_rooms.join(", ")}</p>}
                    {staffReport.unreadable_rooms.length > 0 && <p className="rounded-lg bg-amber-50 p-2 text-xs text-amber-900">Room text not understood: {staffReport.unreadable_rooms.slice(0, 8).map((u) => `${u.employee_id} "${u.value}"`).join("; ")}</p>}
                  </div>
                )}
              </div>
            </div>
            <div className="flex justify-end border-t border-slate-200 p-4"><button type="button" onClick={() => setImporting(false)} className="rounded-xl border border-slate-300 px-4 py-2.5 text-sm font-semibold text-slate-700">Close</button></div>
          </div>
        </div>
      )}
    </div>
  );
}

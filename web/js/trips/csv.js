// The Stats view's "Download as CSV": one row for each trip, for a spreadsheet. Pure functions (unit-tested in Node); the page only
// hands the text to a download.
//
// Everything is in SI units with the unit in the column's name (metres, seconds, metres a second), so a spreadsheet does not depend on the
// units the person chose on the page. A figure the trip does not have is an empty cell. The relay's per-trip CSV (relay/thrashdash/
// tripexport.py) follows the same conventions.

import { impactsOf, tripCost, tripTitle } from "./model.js";

export const STATS_COLUMNS = [
  "id", "start_utc", "name", "vehicle", "tags", "starred", "imported", "ended", "distance_m", "duration_s", "moving_s", "stopped_s",
  "average_speed_ms", "average_moving_speed_ms", "top_speed_ms", "altitude_gain_m", "altitude_loss_m", "energy_used", "energy_unit",
  "energy_per_100km", "price_per_unit", "cost", "currency", "countries", "places", "peak_g", "impacts", "body_damage_end_pct",
  "over_posted_limit_s", "refuels",
];

// A cell a spreadsheet would take for a formula starts with one of these. Names and tags are typed by people (and a backup can come from
// anyone), so none of them may run as anything: such a cell is made text with a leading apostrophe.
const FORMULA_START = /^[=+\-@\t\r]/;

/** A text cell: control characters out, and not something a spreadsheet would run as a formula. */
export function csvText(value) {
  const text = String(value ?? "").replace(/[\u0000-\u001f\u007f]/g, " ");
  return FORMULA_START.test(text) ? `'${text}` : text;
}

/** One cell as CSV: quoted only when it holds a comma, a quote or a line break. */
export function csvField(value) {
  const text = String(value);
  return /[",\r\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
}

const finite = (v) => typeof v === "number" && Number.isFinite(v);
/** A number to `places` decimals, or "" when it is not one. */
const num = (v, places) => (finite(v) ? (places === undefined ? String(v) : v.toFixed(places)) : "");
const iso = (ts) => (finite(ts) && ts > 0 ? new Date(ts * 1000).toISOString().replace(/\.\d{3}Z$/, "Z") : "");

/** The cells of one trip, in STATS_COLUMNS order. `trip` is a list item ({id, ts, vehicle, title, tags, starred, price, stats...}). */
export function statsRow(trip, rule) {
  const s = trip.stats ?? {};
  const fuel = s.fuel ?? null;
  const cost = tripCost(trip);
  return [
    csvText(trip.id), iso(trip.ts), csvText(tripTitle(trip)), csvText(trip.vehicle ?? ""), csvText((trip.tags ?? []).join("; ")), trip.starred ? "true" : "false",
    trip.imported ? "true" : "false", csvText(trip.reason ?? ""),
    num(s.dist, 1), num(s.dur, 1), num(s.moving_s, 1), num(s.stopped_s, 1), num(s.avg_ms, 2), num(s.avg_moving_ms, 2), num(s.top_ms, 2),
    num(s.alt?.gain, 1), num(s.alt?.loss, 1), fuel ? num(fuel.used, 3) : "", fuel ? csvText(fuel.unit ?? "") : "", fuel ? num(fuel.per_100km, 2) : "",
    cost ? num(cost.rate) : "", cost ? cost.amount.toFixed(2) : "", cost ? csvText(cost.currency) : "",
    csvText((s.countries ?? []).join("; ")), csvText((s.places ?? []).join("; ")), num(s.g?.max, 2), Array.isArray(s.knocks) ? String(impactsOf(s, rule).length) : "",
    num(s.damage?.end, 2), num(s.limits?.over_s, 0), Array.isArray(s.refuels) ? String(s.refuels.length) : "",
  ];
}

/**
 * The trips as CSV text: UTF-8 with a byte order mark (Excel on Windows reads names like Taupo or Whanganui with their macrons right only
 * if the file says it is UTF-8), CRLF line ends, one row for each trip that has finished, oldest first.
 */
export function statsCsv(trips, rule) {
  const done = trips.filter((t) => t.stats && !t.recording).sort((a, b) => (a.ts ?? 0) - (b.ts ?? 0));
  const rows = [STATS_COLUMNS, ...done.map((t) => statsRow(t, rule))];
  return `\ufeff${rows.map((r) => r.map(csvField).join(",")).join("\r\n")}\r\n`;
}

/** A name for the file: thrashdash-trips-2026-11-21.csv. */
export const statsCsvName = (date = new Date()) => `thrashdash-trips-${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}.csv`;

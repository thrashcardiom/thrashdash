// The stops of a route being planned on the Trips page, and the words about a route. Pure functions (unit-tested in Node); the page is route.js.
//
// A stop is {lat, lon, name?}. A route has one to MAX_STOPS of them and the last is where it ends. BeamNG.Earth plans the road route; this
// only says where to go.

import { resolveUnit } from "../units.js";

export const MAX_STOPS = 11; // BeamNG.Earth's limit: up to 10 stops on the way, and the destination
export const MAX_PLACES = 40;
const NEAR_M = 30; // a place this close to a stop already in the list is that stop

const R = 6371008.8;
const rad = (d) => (d * Math.PI) / 180;

/** A distance in the person's unit with the places they would say: 0.8, 5.0, 9.9, then whole numbers (never "10.0"). */
const nice = (value) => {
  const tenths = Math.round(value * 10) / 10;
  return tenths < 10 ? tenths.toFixed(1) : String(Math.round(value));
};

/** Metres between two {lat, lon}. */
export function metresBetween(a, b) {
  const x = rad(b.lon - a.lon) * R * Math.cos(rad((a.lat + b.lat) / 2));
  const y = rad(b.lat - a.lat) * R;
  return Math.hypot(x, y);
}

const valid = (p) => p && Number.isFinite(p.lat) && Number.isFinite(p.lon) && Math.abs(p.lat) <= 90 && Math.abs(p.lon) <= 180;
const tidy = (name) => (typeof name === "string" ? name.replace(/\s+/g, " ").trim().slice(0, 60) : "");

/**
 * The stops with `place` added at the end. Not added (and `error` says why) when the route is full, the place is not a place, or it is where the
 * last stop already is (a double click).
 * @returns {{stops: object[], error: string}}
 */
export function addStop(stops, place) {
  if (!valid(place)) return { stops, error: "That is not a place." };
  if (stops.length >= MAX_STOPS) return { stops, error: `A route can have ${MAX_STOPS} stops, the last the destination: take one away first.` };
  const last = stops[stops.length - 1];
  if (last && metresBetween(last, place) < NEAR_M) return { stops, error: "" }; // (the same place twice in a row: ignored, not an error)
  const stop = { lat: Math.round(place.lat * 1e6) / 1e6, lon: Math.round(place.lon * 1e6) / 1e6 };
  const name = tidy(place.name);
  if (name) stop.name = name;
  return { stops: [...stops, stop], error: "" };
}

/**
 * The stops with `place` put first, to be driven to next (a filling station on the way). Not added (and `error` says why) when the route
 * is full, the place is not a place, or it is the next stop already.
 * @returns {{stops: object[], error: string}}
 */
export function insertFirst(stops, place) {
  if (stops.length && valid(place) && metresBetween(stops[0], place) < NEAR_M) return { stops, error: "That is already the next stop." };
  const got = addStop([], place);
  if (got.error) return { stops, error: got.error };
  if (stops.length >= MAX_STOPS) return { stops, error: `A route can have ${MAX_STOPS} stops, the last the destination: take one away first.` };
  return { stops: [...got.stops, ...stops], error: "" };
}

/**
 * The route a stop is to be put into. A draft the person is working on is kept; with no draft, or one already sent and not touched since,
 * it is what is left of the route in the game (stops already passed are gone from that), when one is set.
 * @param {object[]} draft the page's stops
 * @param {boolean} sent whether the draft is just what was last sent
 * @param {object | null} plan GET /api/route
 */
export function routeToExtend(draft, sent, plan) {
  if (draft.length && !sent) return draft;
  if (!plan?.active || !valid(plan.dest)) return draft;
  const place = (p, name) => ({ lat: p.lat, lon: p.lon, ...(tidy(p.name || name) ? { name: tidy(p.name || name) } : {}) });
  return [...(plan.waypoints ?? []).filter(valid).map((w) => place(w)), place(plan.dest, plan.name)];
}

export function removeStop(stops, index) {
  return stops.filter((_, i) => i !== index);
}

/** The stops with the one at `index` moved by `step` (-1 up, +1 down). */
export function moveStop(stops, index, step) {
  const to = index + step;
  if (index < 0 || index >= stops.length || to < 0 || to >= stops.length) return [...stops];
  const out = [...stops];
  [out[index], out[to]] = [out[to], out[index]];
  return out;
}

/** How a stop is called in the list: its name, else "Stop 2", and the last one "Destination" when it has no name. */
export function stopLabel(stops, index) {
  const stop = stops[index];
  if (stop?.name) return stop.name;
  return index === stops.length - 1 ? "Destination" : `Stop ${index + 1}`;
}

/** What the route about to be sent is, in a line: "3 stops, 12.4 km in a straight line" (the real distance is for BeamNG.Earth to say). */
export function stopsSummary(stops, prefs) {
  if (!stops.length) return "No stops yet";
  let metres = 0;
  for (let i = 1; i < stops.length; i++) metres += metresBetween(stops[i - 1], stops[i]);
  const unit = resolveUnit("distance", prefs ?? {});
  const text = stops.length > 1 ? `, ${nice(unit.convert(metres))} ${unit.label} in a straight line between them` : "";
  return `${stops.length} ${stops.length === 1 ? "stop" : "stops"}${text}`;
}

/**
 * Places from earlier trips, to start a route from: where each trip began and ended, by the name the trip summary gave it, newest first,
 * a name once. `routes` are the all-trips list's entries ({ts, from, to, line}).
 * @returns {{name: string, lat: number, lon: number, when: number}[]}
 */
export function placesFromRoutes(routes, limit = MAX_PLACES) {
  const seen = new Set();
  const out = [];
  for (const route of [...routes].sort((a, b) => (b.ts ?? 0) - (a.ts ?? 0))) {
    const line = route.line ?? [];
    if (line.length < 2) continue;
    for (const [name, point] of [[route.to, line[line.length - 1]], [route.from, line[0]]]) {
      const label = tidy(name);
      const key = label.toLowerCase();
      if (!label || seen.has(key) || !Array.isArray(point) || !valid({ lat: point[0], lon: point[1] })) continue;
      seen.add(key);
      out.push({ name: label, lat: point[0], lon: point[1], when: route.ts ?? 0 });
      if (out.length >= limit) return out;
    }
  }
  return out;
}

/** How far off a place found nearby is, in a straight line, in the person's unit: "0.8 km", "12 km". Empty when unknown. */
export function distanceText(metres, prefs) {
  if (!Number.isFinite(metres)) return "";
  const unit = resolveUnit("distance", prefs ?? {});
  return `${nice(unit.convert(metres))} ${unit.label}`;
}

/** The planned route in a line: "Home: 1.1 km, 5 min left" (the destination, the distance and the time left, as far as they are known). */
export function routeSummary(state, prefs) {
  if (!state?.active) return "";
  const unit = resolveUnit("distance", prefs ?? {});
  const bits = [];
  if (Number.isFinite(state.remaining)) {
    bits.push(`${nice(unit.convert(state.remaining))} ${unit.label}`);
  }
  if (Number.isFinite(state.eta)) bits.push(state.eta < 60 ? "under a minute left" : `${formatMinutes(state.eta)} left`);
  const where = state.name || state.dest?.name || "";
  return [where, bits.join(", ")].filter(Boolean).join(": ") || "Route set";
}

/** 5400 -> "1 h 30 min", 540 -> "9 min". */
export function formatMinutes(seconds) {
  const minutes = Math.round(seconds / 60);
  const hours = Math.floor(minutes / 60);
  return hours ? `${hours} h ${String(minutes % 60).padStart(2, "0")} min` : `${minutes} min`;
}


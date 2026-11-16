// The Trips page's logic with no browser in it: colours for speed, the route cut into coloured runs, the speed/altitude
// graph's geometry, the numbers shown for a trip. Kept apart from trips.js (which draws) so Node can test it.
//
// A trip arrives from /api/triplog/{id} as {header, points, info, events, end, title, stats}. A point is
// {t (s into the trip), la, lo, al (m), s (m/s), h, d (m driven so far), f, fv, dm}.

import { resolveUnit } from "../units.js";
import { formatConsumption, formatDuration, formatEnergy, resolveEconomy } from "../economy.js";

/** Slow to fast (viridis: reads in colour and in greyscale, and survives the common kinds of colour blindness). */
export const RAMP = ["#440154", "#443983", "#31688e", "#21908d", "#35b779", "#90d743", "#fde725"];

const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
const finite = (v) => typeof v === "number" && Number.isFinite(v);

/** The speed (m/s) that the top of the colour scale stands for on this trip: its top speed, but never less than 10 m/s. */
export function colourScaleTop(points) {
  let top = 0;
  for (const p of points) if (finite(p.s)) top = Math.max(top, p.s);
  return Math.max(10, top);
}

/** Which colour of RAMP a speed gets, given the speed at the top of the scale. */
export function speedBucket(ms, top) {
  if (!finite(ms) || !(top > 0)) return 0;
  return Math.round(clamp(ms / top, 0, 1) * (RAMP.length - 1));
}

/**
 * The route as runs of one colour: [{bucket, coords: [[lat, lon], ...]}]. A run's last point is the next run's first, so
 * the line has no gaps. A segment takes the colour of its average speed.
 */
export function routeRuns(points, top = colourScaleTop(points)) {
  const runs = [];
  for (let i = 1; i < points.length; i++) {
    const a = points[i - 1];
    const b = points[i];
    if (!finite(a.la) || !finite(a.lo) || !finite(b.la) || !finite(b.lo)) continue;
    const bucket = speedBucket(((a.s ?? 0) + (b.s ?? 0)) / 2, top);
    const last = runs[runs.length - 1];
    if (last && last.bucket === bucket) last.coords.push([b.la, b.lo]);
    else runs.push({ bucket, coords: [[a.la, a.lo], [b.la, b.lo]] });
  }
  return runs;
}

/** Where a time (seconds into the trip) falls in the points, interpolated: {t, d, s, al, la, lo, h, i}. */
export function sampleAtTime(points, t) {
  if (!points.length) return null;
  if (t <= points[0].t) return { ...points[0], i: 0 };
  const last = points.length - 1;
  if (t >= points[last].t) return { ...points[last], i: last };
  let lo = 0;
  let hi = last;
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    if (points[mid].t <= t) lo = mid;
    else hi = mid;
  }
  return lerpPoints(points[lo], points[hi], (t - points[lo].t) / (points[hi].t - points[lo].t || 1), lo);
}

/** The same by distance driven. */
export function sampleAtDistance(points, d) {
  if (!points.length) return null;
  if (d <= points[0].d) return { ...points[0], i: 0 };
  const last = points.length - 1;
  if (d >= points[last].d) return { ...points[last], i: last };
  let lo = 0;
  let hi = last;
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    if (points[mid].d <= d) lo = mid;
    else hi = mid;
  }
  return lerpPoints(points[lo], points[hi], (d - points[lo].d) / (points[hi].d - points[lo].d || 1), lo);
}

function lerpPoints(a, b, f, i) {
  const mix = (k) => (finite(a[k]) && finite(b[k]) ? a[k] + (b[k] - a[k]) * f : (a[k] ?? b[k]));
  return { t: mix("t"), d: mix("d"), s: mix("s"), al: mix("al"), la: mix("la"), lo: mix("lo"), h: a.h, i };
}

/** The distance driven at a time (seconds into the trip). */
export function distanceAtTime(points, t) {
  const p = sampleAtTime(points, t);
  return p ? p.d : 0;
}

/** Round tick positions from 0 up to the first one at or past `max` (the values a person would pick: 1, 2, 5, 10, 20, 50 ...), about `count` of them. */
export function niceTicks(max, count = 4) {
  if (!(max > 0)) return [0];
  const raw = max / count;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 5, 10].map((m) => m * mag).find((s) => s >= raw) ?? 10 * mag;
  const out = [];
  for (let n = 0; ; n++) {
    out.push(+(n * step).toPrecision(12));
    if (n * step >= max - step * 1e-9) break;
  }
  return out;
}

/**
 * The speed and altitude graph for `width` x `height` pixels: distance along the bottom, speed up the side, the altitude as
 * a faint area behind, the posted limits as a line, and the stretches over the limit marked.
 * `unit` converts metres per second to the speed unit shown; `distance` converts metres to the distance unit shown.
 */
export function graphModel(trip, { width, height, speed = (v) => v * 3.6, distance = (v) => v / 1000, pad = { l: 38, r: 8, t: 8, b: 20 } }) {
  const points = trip.points ?? [];
  const inner = { w: Math.max(1, width - pad.l - pad.r), h: Math.max(1, height - pad.t - pad.b) };
  const total = Math.max(1, points.length ? points[points.length - 1].d : 1);
  const x = (d) => pad.l + (clamp(d, 0, total) / total) * inner.w;

  const speedTicks = niceTicks(speed(colourScaleTop(points)) * 1.05, 4);
  const speedMax = speedTicks[speedTicks.length - 1] || 1;
  const y = (ms) => pad.t + inner.h - clamp(speed(ms) / speedMax, 0, 1) * inner.h;

  const alts = points.filter((p) => finite(p.al));
  let altitude = null;
  if (alts.length > 1) {
    let lo = Math.min(...alts.map((p) => p.al));
    let hi = Math.max(...alts.map((p) => p.al));
    if (hi - lo < 10) {
      const mid = (hi + lo) / 2; // a flat trip should look flat, not like a mountain range
      lo = mid - 5;
      hi = mid + 5;
    }
    const ya = (m) => pad.t + inner.h - ((m - lo) / (hi - lo)) * inner.h;
    const line = alts.map((p, n) => `${n ? "L" : "M"}${x(p.d).toFixed(1)} ${ya(p.al).toFixed(1)}`).join("");
    const base = (pad.t + inner.h).toFixed(1);
    altitude = { area: `${line}L${x(alts[alts.length - 1].d).toFixed(1)} ${base}L${x(alts[0].d).toFixed(1)} ${base}Z`, min: lo, max: hi };
  }

  const speedLine = points.filter((p) => finite(p.s)).map((p, n) => `${n ? "L" : "M"}${x(p.d).toFixed(1)} ${y(p.s).toFixed(1)}`).join("");

  const limits = (trip.stats?.limits?.posted ?? []).map(([t0, t1, ms]) => ({ x0: x(distanceAtTime(points, t0)), x1: x(distanceAtTime(points, t1)), y: y(ms) }));
  const over = (trip.stats?.limits?.spans ?? []).map(([t0, t1]) => ({ x0: x(distanceAtTime(points, t0)), x1: x(distanceAtTime(points, t1)) }));

  const distMax = distance(total);
  const xTicks = niceTicks(distMax, Math.max(2, Math.floor(inner.w / 90))).filter((v) => v <= distMax + 1e-9).map((v) => ({ x: x((v / distMax) * total), label: String(v) }));
  const yTicks = speedTicks.map((v) => ({ y: y(v / speed(1)), label: String(v) }));

  return { pad, inner, total, speedLine, altitude, limits, over, xTicks, yTicks, x, y, dFromX: (px) => clamp(((px - pad.l) / inner.w) * total, 0, total) };
}

/** A distance of metres as {value, label} in the chosen unit ("km" or "mi"). */
export function distanceText(metres, prefs) {
  const u = resolveUnit("distance", prefs);
  const v = u.convert(metres);
  return { value: v >= 100 ? v.toFixed(0) : v >= 10 ? v.toFixed(1) : v.toFixed(2), label: u.label };
}

export function speedText(ms, prefs) {
  const u = resolveUnit("speed", prefs);
  return { value: String(Math.round(u.convert(ms))), label: u.label };
}

const sentence = (parts) => parts.filter(Boolean).join(", ");

/**
 * The figures shown for a trip, as tiles: [{key, label, value, sub, tone}]. A tile is left out when the trip has nothing for
 * it (no fuel readings, no posted speed limit). `tone` is "warn" for time spent over the limit.
 */
export function statTiles(trip, prefs) {
  const s = trip.stats;
  if (!s) return [];
  const tiles = [];
  const add = (key, label, value, sub = "", tone = "") => tiles.push({ key, label, value, sub, tone });

  const dist = distanceText(s.dist, prefs);
  add("distance", "Distance", `${dist.value} ${dist.label}`);
  add("time", "Time", formatDuration(s.dur), `${formatDuration(s.moving_s)} moving`);
  const avg = speedText(s.avg_moving_ms, prefs);
  add("average", "Average speed", `${avg.value} ${avg.label}`, "while moving");
  const top = speedText(s.top_ms, prefs);
  add("top", "Top speed", `${top.value} ${top.label}`);

  if (s.fuel) {
    const kind = trip.header?.energy;
    const economy = resolveEconomy(prefs);
    const used = formatEnergy(s.fuel.used, kind, economy);
    const per = formatConsumption(s.fuel.per_100km, kind, economy);
    if (used) add("fuel", kind === "electric" ? "Energy used" : "Fuel used", `${used.value.toFixed(used.value >= 10 ? 1 : 2)} ${used.unit}`, per ? `${per.value.toFixed(1)} ${per.unit}` : "");
  }

  if (s.alt && (s.alt.gain || s.alt.loss)) {
    const imperial = prefs?.distance === "mi";
    const unit = imperial ? "ft" : "m";
    const f = (m) => Math.round(imperial ? m * 3.28084 : m);
    add("climb", "Climb", `+${f(s.alt.gain)} ${unit}`, s.alt.loss ? `-${f(s.alt.loss)} ${unit} down` : "");
  }

  if (s.damage && s.damage.max > 0) add("damage", "Damage", `${s.damage.end.toFixed(1)}%`, s.damage.max > s.damage.end ? `worst ${s.damage.max.toFixed(1)}%` : "of the body broken");

  if (s.wx && finite(s.wx.temp_avg)) {
    const t = resolveUnit("temp", prefs);
    const wind = resolveUnit("speed", prefs);
    const range = s.wx.temp_min === s.wx.temp_max ? `${Math.round(t.convert(s.wx.temp_avg))}${t.label}` : `${Math.round(t.convert(s.wx.temp_min))} to ${Math.round(t.convert(s.wx.temp_max))}${t.label}`;
    add("weather", "Weather", range, sentence([finite(s.wx.wind_max_ms) ? `wind to ${Math.round(wind.convert(s.wx.wind_max_ms))} ${wind.label}` : "", s.wx.rain_s > 0 ? `rain ${formatDuration(s.wx.rain_s)}` : "", s.wx.snow_s > 0 ? `snow ${formatDuration(s.wx.snow_s)}` : ""]));
  }

  if (s.limits) {
    const over = s.limits.over_s;
    add("limits", "Over the limit", over > 0 ? formatDuration(over) : "None", `of ${formatDuration(s.limits.checked_s)} with a posted limit`, over > 0 ? "warn" : "");
  } else {
    add("limits", "Over the limit", "Not checked", "no posted speed limit on this route", "muted");
  }

  if (s.places?.length || s.countries?.length) {
    const towns = s.places?.length > 3 ? `${s.places.slice(0, 3).join(", ")} and ${s.places.length - 3} more` : (s.places ?? []).join(", ");
    add("places", "Places", towns || (s.countries ?? []).join(", "), towns ? (s.countries ?? []).join(", ") : "");
  }
  return tiles;
}

/** The name to show for a trip: the one the user gave it, else the one worked out, else a plain "Trip". */
export function tripTitle(trip) {
  return trip.title || trip.stats?.name || "Trip";
}

/** The trips whose text contains every word of `query` (any case, any order). `text(trip)` gives the words a trip can be found by:
 * its name, vehicle and date, as the list shows them. An empty query keeps every trip. */
export function filterTrips(trips, query, text) {
  const words = String(query ?? "").toLowerCase().split(/\s+/).filter(Boolean);
  if (!words.length) return trips;
  return trips.filter((trip) => {
    const hay = String(text(trip)).toLowerCase();
    return words.every((w) => hay.includes(w));
  });
}

/** What the trips of the last `days` days add up to, for the line at the top of the list: {count, distance (m), duration (s)}.
 * A trip counts by when it began. Trips with no distance yet count as zero metres. */
export function recentTotals(trips, nowMs = Date.now(), days = 7) {
  const since = nowMs / 1000 - days * 86400;
  let count = 0, distance = 0, duration = 0;
  for (const t of trips) {
    if (!(t.ts >= since)) continue;
    count++;
    distance += Number.isFinite(t.distance) ? t.distance : 0;
    duration += Number.isFinite(t.duration) ? t.duration : 0;
  }
  return { count, distance, duration };
}

/** The browser tab's title: the page's own, with a mark in front while a trip is being recorded, so a window hidden behind the game
 * still shows in the taskbar that it is recording. */
export function tabTitle(base, recording) {
  return recording ? `\u25CF Recording: ${base}` : base;
}

/** True when two reads of the same trip hold nothing different (so the page need not redraw): same points, notes, events, end and name. */
export function sameTrip(a, b) {
  if (!a || !b || a.header?.id !== b.header?.id) return false;
  const lastT = (trip) => trip.points[trip.points.length - 1]?.t;
  return a.points.length === b.points.length && lastT(a) === lastT(b) && a.info.length === b.info.length && a.events.length === b.events.length
    && Boolean(a.end) === Boolean(b.end) && (a.title ?? null) === (b.title ?? null);
}

// ---- replay -----------------------------------------------------------------------------------------------------------------

/** How many times faster than real life a replay can run. */
export const REPLAY_RATES = [1, 4, 16, 64];
const MAX_FRAME_S = 0.25; // a window that was hidden comes back with a huge gap between frames: that is not time to skip

/** The replay clock one frame on: `dt` real seconds at `rate` times real speed. {t, ended}; stops at `duration`. */
export function advanceReplay(t, dt, rate, duration) {
  const step = Math.min(Math.max(0, dt), MAX_FRAME_S) * rate;
  const next = t + step;
  return next >= duration ? { t: duration, ended: true } : { t: next, ended: false };
}

/** The posted limit (m/s) in force at a time (seconds into the trip), or null when none was read there. */
export function limitAt(trip, t) {
  for (const [t0, t1, ms] of trip.stats?.limits?.posted ?? []) if (t >= t0 && t <= t1) return ms;
  return null;
}

/** Whether the car was over a posted limit at that time. */
export function isOver(trip, t) {
  return (trip.stats?.limits?.spans ?? []).some(([t0, t1]) => t >= t0 && t <= t1);
}

/** What to show while a trip replays at time `t`: {text, over, here} (`here` is the interpolated point, or null). */
export function replayReadout(trip, t, prefs) {
  const here = sampleAtTime(trip.points ?? [], t);
  if (!here) return { text: "", over: false, here: null };
  const speed = speedText(here.s ?? 0, prefs);
  const dist = distanceText(here.d ?? 0, prefs);
  const parts = [`${speed.value} ${speed.label}`];
  const limit = limitAt(trip, t);
  if (limit !== null) {
    const l = speedText(limit, prefs);
    parts.push(`limit ${l.value}`);
  }
  parts.push(`${dist.value} ${dist.label}`);
  return { text: parts.join(" · "), over: isOver(trip, t), here };
}

/** The clock text: "0:01:05 / 0:02:26". */
export function clockText(t, duration) {
  return `${formatDuration(t)} / ${formatDuration(duration)}`;
}

// ---- all trips on one map -------------------------------------------------------------------------------------------------

export const WHEN_OPTIONS = [{ id: "all", label: "All time" }, { id: "7", label: "Last 7 days" }, { id: "30", label: "Last 30 days" }, { id: "year", label: "This year" }];

/** The trips (routes from /api/triplog/routes) that pass the filters: {when, vehicle, country}. Empty strings and "all" mean no filter. */
export function filterRoutes(routes, { when = "all", vehicle = "", country = "" } = {}, nowMs = Date.now()) {
  let cutoff = -Infinity;
  if (when === "7" || when === "30") cutoff = nowMs / 1000 - Number(when) * 86400;
  else if (when === "year") cutoff = new Date(new Date(nowMs).getFullYear(), 0, 1).getTime() / 1000;
  return routes.filter((r) => r.ts >= cutoff && (!vehicle || r.vehicle === vehicle) && (!country || (r.countries ?? []).includes(country)));
}

/** What the vehicle and country pickers offer: every one that occurs, sorted. */
export function filterOptions(routes) {
  const sorted = (set) => [...set].sort((a, b) => a.localeCompare(b));
  return {
    vehicles: sorted(new Set(routes.map((r) => r.vehicle).filter(Boolean))),
    countries: sorted(new Set(routes.flatMap((r) => r.countries ?? []).filter(Boolean))),
  };
}

/** Totals over some trips: {trips, dist (m), dur (s), top (m/s), longest (m)}. */
export function routeTotals(routes) {
  const t = { trips: routes.length, dist: 0, dur: 0, top: 0, longest: 0 };
  for (const r of routes) {
    t.dist += r.dist || 0;
    t.dur += r.dur || 0;
    t.top = Math.max(t.top, r.top || 0);
    t.longest = Math.max(t.longest, r.dist || 0);
  }
  return t;
}

/** [[south, west], [north, east]] around every route, or null when there is nothing to draw. */
export function routesBounds(routes) {
  let south = Infinity, west = Infinity, north = -Infinity, east = -Infinity;
  for (const r of routes) {
    for (const [la, lo] of r.line ?? []) {
      south = Math.min(south, la); north = Math.max(north, la);
      west = Math.min(west, lo); east = Math.max(east, lo);
    }
  }
  return south <= north ? [[south, west], [north, east]] : null;
}

/** The tiles shown above the all-trips map. */
export function overviewTiles(routes, prefs) {
  const t = routeTotals(routes);
  const dist = distanceText(t.dist, prefs);
  const longest = distanceText(t.longest, prefs);
  const top = speedText(t.top, prefs);
  return [
    { key: "trips", label: "Trips", value: String(t.trips), sub: "" },
    { key: "distance", label: "Distance", value: `${dist.value} ${dist.label}`, sub: "" },
    { key: "time", label: "Driving time", value: formatDuration(t.dur), sub: "" },
    { key: "longest", label: "Longest trip", value: `${longest.value} ${longest.label}`, sub: "" },
    { key: "top", label: "Top speed", value: `${top.value} ${top.label}`, sub: "" },
  ];
}

/**
 * The map's turn, one step: from `current` towards `target` (both degrees from north) the short way round, easing in so the map
 * does not jerk at every change of heading. `dt` is real seconds since the last step; with no `dt` (a jump in the trip) it snaps.
 * A target that is not a number leaves the turn where it was.
 */
export function turnToward(current, target, dt, tau = 0.35) {
  const wrap = (d) => ((d % 360) + 360) % 360;
  if (!Number.isFinite(target)) return current;
  if (!Number.isFinite(current) || dt === undefined || dt === null) return wrap(target);
  const diff = ((target - current + 540) % 360 + 360) % 360 - 180; // -180 to 180: which way is shorter
  const k = 1 - Math.exp(-Math.max(0, dt) / tau);
  return wrap(current + diff * k);
}

/** The trip being recorded that the page has not yet moved to, or null. `openedId` is the one it last moved to (so it moves once per trip). */
export function nextAutoOpen(trips, openedId) {
  const live = (trips ?? []).find((t) => t.recording);
  return live && live.id !== openedId ? live.id : null;
}

// ---- following a trip being driven ----------------------------------------------------------------------------------------------

/**
 * The clock for following a trip live. The relay's points arrive about once a second (and the page fetches them about once a
 * second), so showing the very newest point would move the car in jerks. Instead the car is shown a couple of seconds behind
 * the newest point, played at real speed between the points it has, which is smooth. `t` is where the car is now, `latest` the
 * time of the newest point. It never goes backwards or past the delayed target, and if it is far behind (the first time, or a
 * burst of points after a pause) it jumps to the target.
 */
export function advanceLive(t, dt, latest, delay = 2, maxLag = 6) {
  const target = Math.max(0, latest - delay);
  if (!Number.isFinite(t) || target - t > maxLag) return target;
  const next = t + Math.min(Math.max(0, dt), MAX_FRAME_S);
  return next > target ? Math.max(t, target) : next;
}


// ---- the live position feed -----------------------------------------------------------------------------------------------------

/**
 * Where the car is at game time `rt` (the extension's own clock, seconds), from the live positions `samples` (oldest first, each
 * {rt, lat, lon, hdg?, spd?}), by a straight line between the two either side of it, the heading turning the short way round.
 * Before the first sample it is the first, after the last the last (the car holds where it was last seen). null with no samples.
 */
export function interpolateFeed(samples, rt) {
  if (!samples.length) return null;
  const first = samples[0];
  const last = samples[samples.length - 1];
  const pick = (a, b, f) => {
    const mix = (k) => (Number.isFinite(a[k]) && Number.isFinite(b[k]) ? a[k] + (b[k] - a[k]) * f : (a[k] ?? b[k]));
    let hdg = a.hdg;
    if (Number.isFinite(a.hdg) && Number.isFinite(b.hdg)) hdg = (((a.hdg + ((((b.hdg - a.hdg + 540) % 360) + 360) % 360 - 180) * f) % 360) + 360) % 360;
    else if (Number.isFinite(b.hdg)) hdg = b.hdg;
    return { lat: mix("lat"), lon: mix("lon"), hdg, spd: mix("spd") };
  };
  if (rt <= first.rt) return pick(first, first, 0);
  if (rt >= last.rt) return pick(last, last, 0);
  let lo = 0;
  let hi = samples.length - 1;
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    if (samples[mid].rt <= rt) lo = mid;
    else hi = mid;
  }
  const span = samples[hi].rt - samples[lo].rt || 1;
  return pick(samples[lo], samples[hi], (rt - samples[lo].rt) / span);
}

/**
 * What the live view shows when the car's position comes from the live feed rather than the recorded points: the speed and
 * heading it is going at this moment, with the limit and distance of the newest recorded point. {text, over, here}.
 */
export function liveReadout(trip, live, prefs) {
  const last = trip.points?.[trip.points.length - 1];
  if (!live || !last) return { text: "", over: false, here: null };
  const speed = speedText(live.spd ?? last.s ?? 0, prefs);
  const parts = [`${speed.value} ${speed.label}`];
  const limit = limitAt(trip, last.t);
  if (limit !== null) parts.push(`limit ${speedText(limit, prefs).value}`);
  const dist = distanceText(last.d ?? 0, prefs);
  parts.push(`${dist.value} ${dist.label}`);
  return { text: parts.join(" \u00B7 "), over: isOver(trip, last.t), here: { t: last.t, d: last.d, la: live.lat, lo: live.lon, h: Number.isFinite(live.hdg) ? live.hdg : last.h, s: live.spd ?? last.s } };
}

// ---- the size of the maps ---------------------------------------------------------------------------------------------------------

export const MAP_MIN_PX = 200;
export const MAP_STEP_PX = 120; // what one click of "bigger" or "smaller" changes the height by
const MAP_ROOM_PX = 140; // room left for the rest of the page when the map is as tall as it may be

/** A map height kept sensible: no shorter than MAP_MIN_PX, no taller than the window less room for the replay bar and the title. */
export function clampMapHeight(px, windowHeight) {
  const most = Math.max(MAP_MIN_PX, (Number.isFinite(windowHeight) ? windowHeight : 800) - MAP_ROOM_PX);
  return Math.round(Math.min(most, Math.max(MAP_MIN_PX, Number.isFinite(px) ? px : MAP_MIN_PX)));
}

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

// ---- what a drive cost ------------------------------------------------------------------------------------------------------------
// The price of fuel is saved WITH each trip (the relay copies the setting into the trip as it starts, and a trip's price can be changed
// by hand), so a later change of price never changes what an old drive cost. A trip with no price has no cost: nothing is guessed.

/** 12.5 + "$" -> "$12.50"; 12.5 + "EUR" -> "12.50 EUR"; no currency -> "12.50". A symbol goes in front, a code (letters) after. */
export function moneyText(amount, currency = "") {
  const n = (Math.round(amount * 100) / 100).toFixed(2);
  if (!currency) return n;
  return /^[A-Za-z]{2,}$/.test(currency) ? `${n} ${currency}` : `${currency}${n}`;
}

/**
 * What a trip's fuel (or charge) cost: {amount, currency, rate (price per litre or kWh), unit}, or null when it has no price for the
 * energy it used, or no fuel reading. `trip` is a list item or an open trip: it has `price` and `stats.fuel`.
 */
export function tripCost(trip) {
  const price = trip?.price;
  const fuel = trip?.stats?.fuel;
  if (!price || !fuel || !(fuel.used > 0)) return null;
  const rate = fuel.unit === "kWh" ? price.electric : fuel.unit === "L" ? price.liquid : 0;
  if (!(rate > 0)) return null;
  return { amount: fuel.used * rate, currency: price.currency ?? "", rate, unit: fuel.unit };
}

/** The cost over 100 km (100 miles when the person's distance unit is miles) of a trip that went `dist` metres: {amount, per} or null for a very short one. */
export function costPer100(cost, dist, prefs) {
  if (!cost || !(dist >= 500)) return null;
  const miles = prefs?.distance === "mi";
  return { amount: cost.amount / (dist / (miles ? 160934.4 : 100000)), per: miles ? "100 mi" : "100 km" };
}

/**
 * The figures shown for a trip, as tiles: [{key, label, value, sub, tone}]. A tile is left out when the trip has nothing for
 * it (no fuel readings, no posted speed limit). `tone` is "warn" for time spent over the limit.
 */
export function statTiles(trip, prefs, rule) {
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
    const cost = tripCost(trip);
    if (cost) {
      const per100 = costPer100(cost, s.dist, prefs);
      add("cost", kind === "electric" ? "Energy cost" : "Fuel cost", moneyText(cost.amount, cost.currency), per100 ? `${moneyText(per100.amount, cost.currency)} per ${per100.per}` : "");
    }
  }

  if (s.alt && (s.alt.gain || s.alt.loss)) {
    const imperial = prefs?.distance === "mi";
    const unit = imperial ? "ft" : "m";
    const f = (m) => Math.round(imperial ? m * 3.28084 : m);
    add("climb", "Climb", `+${f(s.alt.gain)} ${unit}`, s.alt.loss ? `-${f(s.alt.loss)} ${unit} down` : "");
  }

  if (s.damage && s.damage.max > 0) add("damage", "Damage", `${s.damage.end.toFixed(1)}%`, s.damage.max > s.damage.end ? `worst ${s.damage.max.toFixed(1)}%` : "of the body broken");

  if (s.g && finite(s.g.max)) add("peakg", "Peak G", `${s.g.max.toFixed(2)} g`, s.g.at ? `at ${s.g.at}` : "the strongest force on the car");
  const hits = impactsOf(s, rule);
  if (hits.length) add("impacts", "Impacts", String(hits.length), impactWorst(hits), "warn");
  if (s.route) {
    const r = s.route;
    const left = distanceText(r.left_m ?? 0, prefs);
    const planned = distanceText(r.planned_m, prefs);
    const extras = sentence([`${planned.value} ${planned.label} planned`, r.replans ? `replanned ${r.replans} ${r.replans === 1 ? "time" : "times"}` : "", r.off_max_m >= 20 ? `up to ${Math.round(r.off_max_m)} m off the route` : ""]);
    if (r.arrived_s !== null && r.arrived_s !== undefined) {
      const diff = finite(r.early_s) && Math.abs(r.early_s) >= 5 ? `${formatDuration(Math.abs(r.early_s))} ${r.early_s > 0 ? "faster" : "slower"} than the game said` : "";
      add("route", "Route", `Arrived in ${formatDuration(r.arrived_s)}`, sentence([diff, extras]));
    } else {
      add("route", "Route", "Not finished", sentence([`${left.value} ${left.label} left`, extras]));
    }
  }

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

  if (s.stops?.length) {
    const total = s.stops.reduce((sum, x) => sum + (x.dur || 0), 0);
    add("stops", "Stops", String(s.stops.length), `${formatDuration(total)} standing still`);
  }
  if (s.refuels?.length) {
    const kind = trip.header?.energy;
    const economy = resolveEconomy(prefs);
    const total = s.refuels.reduce((sum, x) => sum + x.added, 0);
    const added = formatEnergy(total, kind, economy);
    if (added) add("refuels", kind === "electric" ? "Charged" : "Refuelled", `+${added.value.toFixed(added.value >= 10 ? 1 : 2)} ${added.unit}`, s.refuels.length === 1 ? "one stop" : `${s.refuels.length} stops`);
  }

  if (s.places?.length || s.countries?.length) {
    const towns = s.places?.length > 3 ? `${s.places.slice(0, 3).join(", ")} and ${s.places.length - 3} more` : (s.places ?? []).join(", ");
    add("places", "Places", towns || (s.countries ?? []).join(", "), towns ? (s.countries ?? []).join(", ") : "");
  }
  return tiles;
}

/** The words for a stop's marker on the map: "Stopped 3 min 20 s at Cuba Street". */
export function stopText(stop) {
  const near = (stop.poi ?? []).map((p) => p.n).filter(Boolean);
  return `Stopped ${formatDuration(stop.dur)}${stop.at ? ` at ${stop.at}` : ""}${near.length ? ` (${near.join(", ")})` : ""}`;
}

/** What counts as an impact until the person says otherwise: body damage of 1% or more, or a force of 4 g or more. */
export const DEFAULT_IMPACT_RULE = { damage: 1, g: 4 };

/**
 * The impacts in a trip's figures: the knocks (every candidate the relay kept: see tripstats.py) that pass the rule, which is the
 * person's setting, {damage: % of the body, g: force or 0 for "ignore force"}. A knock counts when it damaged the body by `damage` or
 * more, or reached `g`. Applied here, not when the trip is recorded, so changing the rule changes every trip at once.
 */
export function impactsOf(stats, rule) {
  const r = { ...DEFAULT_IMPACT_RULE, ...(rule ?? {}) };
  return (stats?.knocks ?? []).filter((k) => (finite(k.rise) && k.rise >= r.damage) || (r.g > 0 && finite(k.g) && k.g >= r.g));
}

/** "7.5 g" and "+3.1% of the body", for whichever of the two a knock has. */
function impactFigures(impact) {
  return [finite(impact.g) ? `${impact.g.toFixed(1)} g` : "", impact.rise > 0 ? `+${impact.rise.toFixed(1)}% of the body` : ""].filter(Boolean).join(", ");
}

/** The tile's small print: the hardest force and the worst damage among these impacts. */
function impactWorst(hits) {
  const g = Math.max(0, ...hits.map((i) => (finite(i.g) ? i.g : 0)));
  const rise = Math.max(0, ...hits.map((i) => i.rise || 0));
  return [g > 0 ? `up to ${g.toFixed(1)} g` : "", rise > 0 ? `worst +${rise.toFixed(1)}% of the body` : ""].filter(Boolean).join(", ");
}

/** The words for an impact's marker: "Impact: 7.5 g, +3.2% of the body at Cuba Street". */
export function impactText(impact) {
  return `Impact: ${impactFigures(impact) || "a knock"}${impact.at ? ` at ${impact.at}` : ""}`;
}

/**
 * What a marker's hover text and click popup say about an impact, as lines: the heading, when and how fast the car was going, how hard
 * and how much damage, and where. `points` are the trip's route (the speed is read from it at the time of the knock).
 */
export function impactDetails(impact, points, prefs) {
  const p = Array.isArray(points) && points.length && finite(impact.t) ? sampleAtTime(points, impact.t) : null;
  const speed = p && finite(p.s) ? speedText(p.s, prefs) : null;
  const when = finite(impact.t) ? `${formatDuration(impact.t)} into the trip` : "";
  return [
    "Impact",
    [when, speed ? `${speed.value} ${speed.label}` : ""].filter(Boolean).join(" \u00B7 "),
    impactFigures(impact) || "a knock",
    impact.at || "",
  ].filter(Boolean);
}

/** What an impact says about itself in the list under the map: {t, figures, at}. */
export function impactRow(impact) {
  return { t: impact.t, figures: impactFigures(impact) || "a knock", at: impact.at || "" };
}

const ROAD_NUMBER = /^[A-Za-z]{0,3}\s?[0-9]{1,4}[A-Za-z]?$/; // (the same pattern as tripstats.py: "2", "SH2", "A40")

/** A short name for a place reading, the way the relay names one: its street, or the town when the street is only a road number. */
export function placeLabel(place) {
  if (!place || typeof place !== "object") return null;
  const street = place.street;
  const town = place.place || place.region;
  if (street && !ROAD_NUMBER.test(street)) return street;
  return town || street || place.country || null;
}

/**
 * The waypoints the driver dropped in a trip, for the list under the figures: [{t, la, lo, at, text}] in order. `at` is where, by name
 * (the place reading in force at the time); `text` is the note written against it, or "".
 */
export function waypointRows(trip) {
  const notes = new Map((trip.notes ?? []).map((n) => [Math.round(n.t * 100), n.text]));
  const readings = (trip.info ?? []).filter((i) => finite(i.t) && i.place);
  return (trip.events ?? []).filter((e) => e.name === "waypoint" && finite(e.t)).map((e) => {
    let here = null;
    for (const r of readings) {
      if (r.t <= e.t) here = r;
      else break;
    }
    return { t: e.t, la: e.la, lo: e.lo, at: placeLabel((here ?? readings[0])?.place), text: notes.get(Math.round(e.t * 100)) ?? "" };
  });
}

/** The words for a refuel's marker: "Refuelled +13.5 L at Z Petone" ("Charged" for an electric car). */
export function refuelText(refuel, kind, prefs) {
  const added = formatEnergy(refuel.added, kind, resolveEconomy(prefs));
  const amount = added ? `+${added.value.toFixed(added.value >= 10 ? 1 : 2)} ${added.unit}` : "";
  return `${kind === "electric" ? "Charged" : "Refuelled"} ${amount}${refuel.at ? ` at ${refuel.at}` : ""}`.replace(/\s+/g, " ").trim();
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

// ---- tags -------------------------------------------------------------------------------------------------------------------
// Short labels on a trip (commute, track...). A fixed set is offered to begin with (the relay's, sent with the trip list: this is only the
// fallback for a page that has not heard it), and anyone may add their own, which are then offered too. The rules here are the relay's
// (triplog.clean_tags): it has the last word, but a page that follows them rarely hears "no".

export const DEFAULT_PRESET_TAGS = ["Commute", "Tour", "Track", "Off-road", "Test", "Event"];
export const MAX_TAGS = 8;
export const MAX_TAG_CHARS = 24;

const sameTag = (a, b) => String(a).toLowerCase() === String(b).toLowerCase();

/** What was typed as a tag, tidied: one line, no control characters, at most MAX_TAG_CHARS, and spelled the way a tag already known is
 * (typing "commute" gives "Commute"). "" if nothing is left. */
export function cleanTag(text, known = []) {
  const tidy = String(text ?? "").replace(/[\u0000-\u001f\u007f]/g, " ").replace(/\s+/g, " ").trim().slice(0, MAX_TAG_CHARS).trim();
  return known.find((k) => sameTag(k, tidy)) ?? tidy;
}

/** Whether a trip (or route) has this tag; an empty tag means "any". */
export const hasTag = (item, tag) => !tag || (item.tags ?? []).some((t) => sameTag(t, tag));

/** The tags with `tag` added if it is not there, taken away if it is. */
export function toggleTag(tags, tag) {
  return tags.some((t) => sameTag(t, tag)) ? tags.filter((t) => !sameTag(t, tag)) : [...tags, tag];
}

/** Adding a tag somebody typed. @returns {{tags: string[], tag: string, error: string}} `error` is a sentence when it cannot be added. */
export function addTypedTag(tags, text, known = []) {
  const tag = cleanTag(text, known);
  if (!tag) return { tags, tag: "", error: "Type a tag first." };
  if (tags.some((t) => sameTag(t, tag))) return { tags, tag, error: `This trip already has the tag ${tag}.` };
  if (tags.length >= MAX_TAGS) return { tags, tag, error: `A trip can have ${MAX_TAGS} tags: take one away first.` };
  return { tags: [...tags, tag], tag, error: "" };
}

/** Every tag the page offers: the fixed set, then each other one in use (on any trip, or this one), alphabetically. */
export function knownTags(trips, presets = DEFAULT_PRESET_TAGS, extra = []) {
  const out = [...presets];
  const own = [];
  for (const t of [...trips.flatMap((trip) => trip.tags ?? []), ...extra]) {
    if (!out.some((o) => sameTag(o, t)) && !own.some((o) => sameTag(o, t))) own.push(t);
  }
  return [...out, ...own.sort((a, b) => a.localeCompare(b))];
}

/** The tags that occur on these trips (for a filter): the fixed set that is used, in its order, then the others alphabetically. */
export function tagsInUse(trips, presets = DEFAULT_PRESET_TAGS) {
  const used = new Set(trips.flatMap((t) => t.tags ?? []).map((t) => t.toLowerCase()));
  return knownTags(trips, presets).filter((t) => used.has(t.toLowerCase()));
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

/** The trips (routes from /api/triplog/routes) that pass the filters: {when, vehicle, country, tag}. Empty strings and "all" mean no filter. */
export function filterRoutes(routes, { when = "all", vehicle = "", country = "", tag = "" } = {}, nowMs = Date.now()) {
  let cutoff = -Infinity;
  if (when === "7" || when === "30") cutoff = nowMs / 1000 - Number(when) * 86400;
  else if (when === "year") cutoff = new Date(new Date(nowMs).getFullYear(), 0, 1).getTime() / 1000;
  return routes.filter((r) => r.ts >= cutoff && (!vehicle || r.vehicle === vehicle) && (!country || (r.countries ?? []).includes(country)) && hasTag(r, tag));
}

/** What the vehicle and country pickers offer: every one that occurs, sorted. */
export function filterOptions(routes) {
  const sorted = (set) => [...set].sort((a, b) => a.localeCompare(b));
  return {
    vehicles: sorted(new Set(routes.map((r) => r.vehicle).filter(Boolean))),
    countries: sorted(new Set(routes.flatMap((r) => r.countries ?? []).filter(Boolean))),
    tags: tagsInUse(routes),
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

/**
 * What all the finished trips in the list add up to, for the Stats view. `trips` are list items ({id, ts, vehicle, title, stats});
 * one without a summary yet (still being recorded) is left out. Returns
 *   {trips, dist (m), dur (s), moving (s), days, countries[], towns, fuel: {L, kWh}, cost: {byCurrency: [{currency, amount, dist, trips}], priced, fuelTrips},
 *    records: {longest, longestTime, top, highest, climb, countries}  each {value, id, trip} or null,
 *    vehicles: [{vehicle, trips, dist, dur, used, unit, per100}], months: [{key "2026-11", dist, trips}] oldest first}.
 */
export function lifetimeStats(trips, rule) {
  const done = trips.filter((t) => t.stats);
  const out = { trips: done.length, impacts: 0, dist: 0, dur: 0, moving: 0, days: 0, countries: [], towns: 0, fuel: {}, cost: { byCurrency: [], priced: 0, fuelTrips: 0 }, records: {}, vehicles: [], months: [] };
  const spent = new Map();
  const days = new Set(), countries = new Set(), towns = new Set(), months = new Map(), vehicles = new Map();
  const best = (name, value, trip) => {
    if (!(value > 0)) return;
    if (!out.records[name] || value > out.records[name].value) out.records[name] = { value, id: trip.id, trip };
  };
  for (const t of done) {
    const s = t.stats;
    out.dist += s.dist || 0;
    out.dur += s.dur || 0;
    out.moving += s.moving_s || 0;
    out.impacts += impactsOf(s, rule).length;
    const d = new Date(t.ts * 1000);
    days.add(`${d.getFullYear()}-${d.getMonth() + 1}-${d.getDate()}`);
    for (const c of s.countries ?? []) countries.add(c);
    for (const p of s.places ?? []) towns.add(p);
    const month = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
    const m = months.get(month) ?? { key: month, dist: 0, trips: 0 };
    m.dist += s.dist || 0;
    m.trips += 1;
    months.set(month, m);
    best("longest", s.dist, t);
    best("longestTime", s.dur, t);
    best("top", s.top_ms, t);
    best("highest", s.alt?.max, t);
    best("climb", s.alt?.gain, t);
    best("peakG", s.g?.max, t);
    best("countries", (s.countries ?? []).length > 1 ? s.countries.length : 0, t);
    const key = t.vehicle || "";
    const v = vehicles.get(key) ?? { vehicle: key, trips: 0, dist: 0, dur: 0, used: 0, usedDist: 0, unit: "" };
    v.trips += 1;
    v.dist += s.dist || 0;
    v.dur += s.dur || 0;
    if (s.fuel && s.fuel.used > 0 && (s.dist || 0) >= 500) {
      v.used += s.fuel.used;
      v.usedDist += s.dist;
      v.unit = s.fuel.unit;
      out.fuel[s.fuel.unit] = (out.fuel[s.fuel.unit] || 0) + s.fuel.used;
      out.cost.fuelTrips += 1; // (the same trips as the fuel above: a short one's reading is too noisy)
      const cost = tripCost(t);
      if (cost) {
        out.cost.priced += 1;
        const c = spent.get(cost.currency) ?? { currency: cost.currency, amount: 0, dist: 0, trips: 0 };
        c.amount += cost.amount;
        c.dist += s.dist;
        c.trips += 1;
        spent.set(cost.currency, c);
      }
    }
    vehicles.set(key, v);
  }
  out.cost.byCurrency = [...spent.values()].sort((a, b) => b.amount - a.amount);
  out.days = days.size;
  out.countries = [...countries].sort((a, b) => a.localeCompare(b));
  out.towns = towns.size;
  out.months = [...months.values()].sort((a, b) => a.key.localeCompare(b.key));
  out.vehicles = [...vehicles.values()]
    .map(({ usedDist, ...v }) => ({ ...v, per100: usedDist > 0 && v.used > 0 ? v.used / (usedDist / 100000) : null }))
    .sort((a, b) => b.dist - a.dist);
  return out;
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

/** The height to show a map at, given the height the person chose (`wanted`) and how tall the window is now: the chosen height, kept
 * to what the window has room for. `null` while the window has no real height yet (it is still opening), or when nothing was chosen:
 * the stylesheet's own height then stands. The chosen height is never changed by this: a window made short and tall again gives the
 * map back the height that was chosen. */
export function mapHeightFor(wanted, windowHeight) {
  if (!Number.isFinite(wanted) || !(wanted > 0) || !Number.isFinite(windowHeight) || windowHeight < MAP_MIN_PX + MAP_ROOM_PX / 2) return null;
  return clampMapHeight(wanted, windowHeight);
}

/** A map height kept sensible: no shorter than MAP_MIN_PX, no taller than the window less room for the replay bar and the title. */
export function clampMapHeight(px, windowHeight) {
  const most = Math.max(MAP_MIN_PX, (Number.isFinite(windowHeight) ? windowHeight : 800) - MAP_ROOM_PX);
  return Math.round(Math.min(most, Math.max(MAP_MIN_PX, Number.isFinite(px) ? px : MAP_MIN_PX)));
}

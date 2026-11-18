// Comparing two trips (usually two drives of the same route): their figures side by side, and how far ahead or behind one was of the
// other along the way. Pure functions: the Trips page draws what they return (see trips.js), and Node tests them (tests/js/compare.test.js).
//
// "Along the way" is measured by distance driven: where the first trip had gone 3 km, how long had each taken? That is exact when both
// followed the same road; a detour in one makes the later part of the comparison approximate, and the page says so.

import { distanceText, sampleAtDistance, speedText } from "./model.js";
import { formatDuration, formatEnergy, resolveEconomy } from "../economy.js";

const finite = (v) => typeof v === "number" && Number.isFinite(v);
const sign = (diff) => (diff === 0 ? "" : diff > 0 ? "+" : "-"); // (nothing in front of a difference of nothing)

/** Seconds into the trip at which `distance` metres had been driven. */
export function timeAtDistance(points, distance) {
  const p = sampleAtDistance(points, distance);
  return p ? p.t : 0;
}

/**
 * How the second trip stood against the first over the distance both drove: [{d, a, b, gap}], `a` and `b` the seconds each had taken to
 * reach `d` metres, `gap` = b - a (positive: the second was behind). `count` evenly spaced steps from 0 to the shorter trip's distance.
 * Empty when either has no route.
 */
export function gapSeries(a, b, count = 120) {
  const pa = a?.points ?? [];
  const pb = b?.points ?? [];
  if (pa.length < 2 || pb.length < 2) return [];
  const total = Math.min(pa[pa.length - 1].d ?? 0, pb[pb.length - 1].d ?? 0);
  if (!(total > 0)) return [];
  const out = [];
  for (let i = 0; i <= count; i++) {
    const d = (total * i) / count;
    const ta = timeAtDistance(pa, d);
    const tb = timeAtDistance(pb, d);
    out.push({ d, a: ta, b: tb, gap: tb - ta });
  }
  return out;
}

/** "+1:05" or "-0:12": a signed difference in seconds, for the table and the graph's end. */
export function signedDuration(seconds) {
  const s = Math.round(seconds);
  const total = Math.abs(s);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const ss = String(total % 60).padStart(2, "0");
  return `${s < 0 ? "-" : "+"}${h ? `${h}:${String(m).padStart(2, "0")}` : m}:${ss}`;
}

/**
 * The table: one row per figure both trips have, {key, label, a, b, diff, better} where `a` and `b` are the texts shown, `diff` is how
 * the second differs from the first, and `better` is "a" or "b" for the figure where less is better (time, fuel, time over the
 * limit, time stopped) and "" otherwise or on a tie.
 */
export function compareRows(a, b, prefs) {
  const sa = a?.stats;
  const sb = b?.stats;
  if (!sa || !sb) return [];
  const rows = [];
  const add = (key, label, ta, tb, diff, lowerIsBetter = null) => {
    let better = "";
    if (lowerIsBetter !== null && finite(lowerIsBetter.a) && finite(lowerIsBetter.b) && lowerIsBetter.a !== lowerIsBetter.b) better = lowerIsBetter.a < lowerIsBetter.b ? "a" : "b";
    rows.push({ key, label, a: ta, b: tb, diff, better });
  };
  const dist = (m) => {
    const d = distanceText(m, prefs);
    return `${d.value} ${d.label}`;
  };
  const speed = (ms) => {
    const s = speedText(ms, prefs);
    return `${s.value} ${s.label}`;
  };
  add("distance", "Distance", dist(sa.dist), dist(sb.dist), `${sign(sb.dist - sa.dist)}${dist(Math.abs(sb.dist - sa.dist))}`);
  add("time", "Time", formatDuration(sa.dur), formatDuration(sb.dur), signedDuration(sb.dur - sa.dur), { a: sa.dur, b: sb.dur });
  add("moving", "Time moving", formatDuration(sa.moving_s), formatDuration(sb.moving_s), signedDuration(sb.moving_s - sa.moving_s), { a: sa.moving_s, b: sb.moving_s });
  const stopped = (s) => (s.stops ?? []).reduce((sum, x) => sum + (x.dur || 0), 0);
  add("stops", "Stopped", `${(sa.stops ?? []).length} · ${formatDuration(stopped(sa))}`, `${(sb.stops ?? []).length} · ${formatDuration(stopped(sb))}`, signedDuration(stopped(sb) - stopped(sa)), { a: stopped(sa), b: stopped(sb) });
  add("average", "Average speed", speed(sa.avg_moving_ms), speed(sb.avg_moving_ms), `${sign(sb.avg_moving_ms - sa.avg_moving_ms)}${speed(Math.abs(sb.avg_moving_ms - sa.avg_moving_ms))}`);
  add("top", "Top speed", speed(sa.top_ms), speed(sb.top_ms), `${sign(sb.top_ms - sa.top_ms)}${speed(Math.abs(sb.top_ms - sa.top_ms))}`);
  if (sa.fuel && sb.fuel && sa.fuel.unit === sb.fuel.unit) {
    const economy = resolveEconomy(prefs);
    const kind = sa.fuel.unit === "kWh" ? "electric" : "liquid";
    const energy = (x) => {
      const e = formatEnergy(x, kind, economy);
      return e ? `${e.value.toFixed(e.value >= 10 ? 1 : 2)} ${e.unit}` : "";
    };
    const delta = formatEnergy(Math.abs(sb.fuel.used - sa.fuel.used), kind, economy);
    add("fuel", kind === "electric" ? "Energy used" : "Fuel used", energy(sa.fuel.used), energy(sb.fuel.used),
      delta ? `${sign(sb.fuel.used - sa.fuel.used)}${delta.value.toFixed(delta.value >= 10 ? 1 : 2)} ${delta.unit}` : "", { a: sa.fuel.used, b: sb.fuel.used });
  }
  const over = (s) => (s.limits ? s.limits.over_s || 0 : null);
  if (over(sa) !== null && over(sb) !== null) add("over", "Over the limit", formatDuration(over(sa)), formatDuration(over(sb)), signedDuration(over(sb) - over(sa)), { a: over(sa), b: over(sb) });
  return rows;
}

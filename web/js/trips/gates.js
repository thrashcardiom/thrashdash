// The lines of a lap-timing circuit, as the Trips page draws them. Pure functions (unit-tested in Node); the page is circuits.js.
//
// A line is two points, a and b, [latitude, longitude]. The relay (relay/thrashdash/laps.py) counts a crossing only one way: from the
// right of the line, looking from a to b, to the left of it. A line is made here from the direction a car was driving where you clicked
// on its route, so the right way is always "the way it went", and you never have to think about it (there is a button to turn it round).

const R = 6371008.8; // metres: the same Earth the relay uses, so a line means the same to both
const rad = (d) => (d * Math.PI) / 180;
const deg = (r) => (r * 180) / Math.PI;

export const DEFAULT_GATE_WIDTH_M = 30;
export const MIN_GATE_WIDTH_M = 6; // the relay refuses a line shorter than 3 m or longer than 200 m; this keeps the page well inside
export const MAX_GATE_WIDTH_M = 120;
export const MAX_SECTOR_LINES = 8;
export const PICK_TOLERANCE_PX = 28; // a click this near the route (on the screen) places a line on it

/** Metres east and north of `origin`, both [lat, lon]. */
function xy(point, origin) {
  return [rad(point[1] - origin[1]) * R * Math.cos(rad(origin[0])), rad(point[0] - origin[0]) * R];
}

function fromXY(vec, origin) {
  return [origin[0] + deg(vec[1] / R), origin[1] + deg(vec[0] / (R * Math.cos(rad(origin[0]))))];
}

/** The direction from one point to another, in degrees clockwise from north (0 to 360). */
export function bearingOf(from, to) {
  const [x, y] = xy(to, from);
  return (deg(Math.atan2(x, y)) + 360) % 360;
}

/** Metres between two [lat, lon] points. */
export function distanceM(a, b) {
  const [x, y] = xy(b, a);
  return Math.hypot(x, y);
}

/**
 * A line of `widthM` metres across the road at `point`, for a car going `bearing` degrees: it counts when crossed in that direction.
 * @returns {{a: number[], b: number[]}}
 */
export function gateAt(point, bearing, widthM = DEFAULT_GATE_WIDTH_M) {
  const half = Math.min(MAX_GATE_WIDTH_M, Math.max(MIN_GATE_WIDTH_M, widthM)) / 2;
  const dx = Math.sin(rad(bearing));
  const dy = Math.cos(rad(bearing));
  // s is the direction travelled, turned a quarter the clockwise way: then cross(s, d) > 0, which is what the relay accepts.
  const s = [dy * half, -dx * half];
  return { a: fromXY([-s[0], -s[1]], point), b: fromXY(s, point) };
}

/** The same line, counting the other way. */
export const flipGate = (gate) => ({ a: gate.b, b: gate.a });

export const gateLengthM = (gate) => distanceM(gate.a, gate.b);
export const gateCentre = (gate) => [(gate.a[0] + gate.b[0]) / 2, (gate.a[1] + gate.b[1]) / 2];

/** The direction a car must cross the line in, in degrees clockwise from north. */
export function travelBearing(gate) {
  const [sx, sy] = xy(gate.b, gate.a);
  // the line's direction turned a quarter the counter-clockwise way (the opposite turn to the one gateAt used)
  return (deg(Math.atan2(-sy, sx)) + 360) % 360;
}

/** Where along the step p0 -> p1 (0 up to, not including, 1) it crosses the line the right way, or null. The same rule as the relay's. */
export function crossesGate(p0, p1, gate) {
  const [rx, ry] = xy(p1, p0);
  const [ax, ay] = xy(gate.a, p0);
  const [bx, by] = xy(gate.b, p0);
  const sx = bx - ax;
  const sy = by - ay;
  const denom = rx * sy - ry * sx;
  if (denom >= 0) return null;
  const t = (ax * sy - ay * sx) / denom;
  const u = (ax * ry - ay * rx) / denom;
  return t >= 0 && t < 1 && u >= 0 && u <= 1 ? t : null;
}

/** A point part of the way from one [lat, lon] to another (they are metres apart, so a straight blend is exact enough). */
export const pointAlong = (a, b, t) => [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t];

/**
 * The place on a route nearest to a click, in screen pixels (so "near" means near on the screen at any zoom).
 * @param {number[][]} pixels the route's vertices as [x, y] on the screen
 * @param {number[]} click [x, y]
 * @returns {{i: number, t: number, dist: number} | null} the segment (from vertex i to i + 1), how far along it, and how many pixels away; null if none is within `tolerance`
 */
export function nearestOnRoute(pixels, click, tolerance = PICK_TOLERANCE_PX) {
  let best = null;
  for (let i = 0; i + 1 < pixels.length; i++) {
    const [x0, y0] = pixels[i];
    const dx = pixels[i + 1][0] - x0;
    const dy = pixels[i + 1][1] - y0;
    const len2 = dx * dx + dy * dy;
    const t = len2 === 0 ? 0 : Math.min(1, Math.max(0, ((click[0] - x0) * dx + (click[1] - y0) * dy) / len2));
    const dist = Math.hypot(x0 + dx * t - click[0], y0 + dy * t - click[1]);
    if (dist <= tolerance && (best === null || dist < best.dist)) best = { i, t, dist };
  }
  return best;
}

/**
 * A line across a route where it was clicked.
 * @param {number[][]} route the route's vertices as [lat, lon]
 * @param {{i: number, t: number}} hit from nearestOnRoute
 */
export function gateOnRoute(route, hit, widthM = DEFAULT_GATE_WIDTH_M) {
  const a = route[hit.i];
  const b = route[hit.i + 1];
  return gateAt(pointAlong(a, b, hit.t), bearingOf(a, b), widthM);
}

/** What is still needed before a circuit can be saved, as plain sentences (an empty list: it can). */
export function circuitProblems(draft) {
  const problems = [];
  if (!draft.name || !draft.name.trim()) problems.push("Give the circuit a name.");
  if (!draft.start) problems.push(draft.kind === "stage" ? "Place the start line." : "Place the start/finish line.");
  if (draft.kind === "stage" && !draft.finish) problems.push("Place the finish line.");
  return problems;
}

/** A one-line description of a circuit for the list: "Closed circuit, 2 sectors" / "Stage, no sectors". */
export function circuitSummary(circuit) {
  const lines = circuit.sectors?.length ?? 0;
  const sectors = lines === 0 ? "no sectors" : `${lines + 1} sectors`;
  return `${circuit.kind === "stage" ? "Stage" : "Closed circuit"}, ${sectors}`;
}

/** The lines of a circuit as drawn: [{role: "start"|"finish"|"sector", label, gate}...], in the order a lap meets them. */
export function drawnGates(circuit) {
  const out = [];
  if (circuit.start) out.push({ role: "start", label: circuit.kind === "stage" ? "Start" : "Start / finish", gate: circuit.start });
  (circuit.sectors ?? []).forEach((gate, i) => out.push({ role: "sector", label: `S${i + 1}`, gate, index: i }));
  if (circuit.kind === "stage" && circuit.finish) out.push({ role: "finish", label: "Finish", gate: circuit.finish });
  return out;
}

/** Whether two circuits' lines are the same (what the relay compares to decide that the laps no longer mean anything). */
export function sameLines(a, b) {
  const key = (c) => JSON.stringify([c.kind, c.start, c.kind === "stage" ? c.finish : null, c.sectors ?? []]);
  return key(a) === key(b);
}

/** Move the sector line at `index` up (-1) or down (+1) in the order; returns a new list. */
export function moveSector(sectors, index, step) {
  const to = index + step;
  if (to < 0 || to >= sectors.length) return [...sectors];
  const out = [...sectors];
  [out[index], out[to]] = [out[to], out[index]];
  return out;
}

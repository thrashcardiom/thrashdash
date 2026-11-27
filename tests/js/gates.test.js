import assert from "node:assert/strict";
import test from "node:test";

import {
  DEFAULT_GATE_WIDTH_M, MAX_GATE_WIDTH_M, MIN_GATE_WIDTH_M, bearingOf, circuitProblems, circuitSummary, crossesGate, distanceM, drawnGates, flipGate, gateAt,
  gateCentre, gateLengthM, gateOnRoute, moveSector, nearestOnRoute, pointAlong, sameLines, travelBearing,
} from "../../web/js/trips/gates.js";

const HERE = [-41.29, 174.78];
// a point `east` and `north` metres from HERE
const at = (east, north) => [HERE[0] + (north / 6371008.8) * (180 / Math.PI), HERE[1] + (east / (6371008.8 * Math.cos((HERE[0] * Math.PI) / 180))) * (180 / Math.PI)];
const near = (a, b, places = 1) => assert.ok(Math.abs(a - b) < 10 ** -places, `${a} is not within ${10 ** -places} of ${b}`);

test("distances and bearings agree with the compass", () => {
  near(distanceM(at(0, 0), at(30, 40)), 50, 2);
  near(bearingOf(HERE, at(0, 100)), 0, 3);
  near(bearingOf(HERE, at(100, 0)), 90, 3);
  near(bearingOf(HERE, at(0, -100)), 180, 3);
  near(bearingOf(HERE, at(-100, 0)), 270, 3);
});

test("a line made for a direction counts that way across it and not the other", () => {
  for (const bearing of [0, 37, 90, 133, 180, 250, 315]) {
    const gate = gateAt(HERE, bearing, 30);
    const d = [Math.sin((bearing * Math.PI) / 180), Math.cos((bearing * Math.PI) / 180)];
    const before = at(-5 * d[0], -5 * d[1]);
    const after = at(5 * d[0], 5 * d[1]);
    const t = crossesGate(before, after, gate);
    assert.ok(t !== null && Math.abs(t - 0.5) < 1e-3, `bearing ${bearing}: ${t}`);
    assert.equal(crossesGate(after, before, gate), null, `bearing ${bearing} the wrong way`);
    assert.ok(crossesGate(after, before, flipGate(gate)) !== null, `bearing ${bearing} flipped`);
    assert.equal(crossesGate(before, after, flipGate(gate)), null);
  }
});

test("the line is as wide as asked, centred on the point, and keeps inside the limits the relay sets", () => {
  const gate = gateAt(HERE, 77, 40);
  near(gateLengthM(gate), 40, 2);
  near(distanceM(gateCentre(gate), HERE), 0, 3);
  near(gateLengthM(gateAt(HERE, 0, 1)), MIN_GATE_WIDTH_M, 2);
  near(gateLengthM(gateAt(HERE, 0, 5000)), MAX_GATE_WIDTH_M, 2);
  near(gateLengthM(gateAt(HERE, 0)), DEFAULT_GATE_WIDTH_M, 2);
  assert.ok(MIN_GATE_WIDTH_M >= 3 && MAX_GATE_WIDTH_M <= 200);
});

test("a step beside the line, short of it, or along it does not cross", () => {
  const gate = gateAt(HERE, 90, 30); // across an eastbound road
  assert.equal(crossesGate(at(-5, 40), at(5, 40), gate), null); // passes well to the side of the line
  assert.equal(crossesGate(at(-5, 0), at(-1, 0), gate), null); // stops short
  assert.equal(crossesGate(at(1, 0), at(5, 0), gate), null); // already across
  assert.equal(crossesGate(at(0, -5), at(0, 5), gate), null); // runs along it
});

test("the direction a line counts is reported back", () => {
  for (const bearing of [0, 45, 90, 180, 271, 359]) near(travelBearing(gateAt(HERE, bearing)), bearing, 3);
  near(travelBearing(flipGate(gateAt(HERE, 90))), 270, 3);
});

test("a click near a route finds the place on it, and a click far from it finds nothing", () => {
  const route = [[0, 0], [100, 0], [100, 100]];
  const hit = nearestOnRoute(route, [50, 10]);
  assert.deepEqual([hit.i, hit.t, hit.dist], [0, 0.5, 10]);
  assert.equal(nearestOnRoute(route, [50, 40]), null);
  assert.equal(nearestOnRoute(route, [50, 40], 50).i, 0);
  const corner = nearestOnRoute(route, [104, 4]);
  assert.deepEqual([corner.i, corner.dist], [1, 4]); // (nearer the second segment, which runs up from the corner, than the first)
  assert.equal(nearestOnRoute([[5, 5]], [5, 5]), null); // one point is not a route
  assert.equal(nearestOnRoute([], [0, 0]), null);
  assert.equal(nearestOnRoute([[0, 0], [0, 0]], [3, 4]).dist, 5); // a zero-length segment is a point
});

test("a line placed on a route faces the way the route goes there", () => {
  const route = [at(0, 0), at(0, 100), at(100, 100)]; // north, then east
  const first = gateOnRoute(route, { i: 0, t: 0.5 }, 30);
  near(travelBearing(first), 0, 2);
  near(distanceM(gateCentre(first), at(0, 50)), 0, 2);
  const second = gateOnRoute(route, { i: 1, t: 0.25 }, 30);
  near(travelBearing(second), 90, 2);
  assert.ok(crossesGate(at(0, 40), at(0, 60), first) !== null); // a car going north crosses the first
  assert.ok(crossesGate(at(15, 100), at(35, 100), second) !== null);
});

test("pointAlong blends two points", () => {
  assert.deepEqual(pointAlong([0, 0], [10, 20], 0.5), [5, 10]);
});

test("what is missing before a circuit can be saved", () => {
  const gate = gateAt(HERE, 0);
  assert.deepEqual(circuitProblems({ name: "A", kind: "circuit", start: gate }), []);
  assert.deepEqual(circuitProblems({ name: " ", kind: "circuit" }), ["Give the circuit a name.", "Place the start/finish line."]);
  assert.deepEqual(circuitProblems({ name: "H", kind: "stage", start: gate }), ["Place the finish line."]);
  assert.deepEqual(circuitProblems({ name: "H", kind: "stage" }), ["Place the start line.", "Place the finish line."]);
  assert.deepEqual(circuitProblems({ name: "H", kind: "stage", start: gate, finish: gate }), []);
});

test("circuits are described and their lines listed in the order a lap meets them", () => {
  const g = (b) => gateAt(HERE, b);
  assert.equal(circuitSummary({ kind: "circuit", sectors: [] }), "Closed circuit, no sectors");
  assert.equal(circuitSummary({ kind: "circuit", sectors: [g(0), g(90)] }), "Closed circuit, 3 sectors");
  assert.equal(circuitSummary({ kind: "stage", sectors: [g(0)] }), "Stage, 2 sectors");
  const lines = drawnGates({ kind: "stage", start: g(0), finish: g(180), sectors: [g(30), g(60)] });
  assert.deepEqual(lines.map((l) => [l.role, l.label]), [["start", "Start"], ["sector", "S1"], ["sector", "S2"], ["finish", "Finish"]]);
  assert.deepEqual(drawnGates({ kind: "circuit", start: g(0), sectors: [] }).map((l) => l.label), ["Start / finish"]);
  assert.deepEqual(drawnGates({ kind: "circuit", finish: g(0) }), []); // (a closed circuit has no separate finish)
});

test("two circuits have the same lines if the relay would keep the laps", () => {
  const g = (b) => gateAt(HERE, b);
  const base = { name: "A", kind: "circuit", enabled: true, start: g(0), sectors: [g(10)] };
  assert.ok(sameLines(base, { ...base, name: "B", enabled: false }));
  assert.ok(!sameLines(base, { ...base, sectors: [] }));
  assert.ok(!sameLines(base, { ...base, start: g(1) }));
  assert.ok(!sameLines(base, { ...base, kind: "stage", finish: g(5) }));
  assert.ok(sameLines({ ...base, finish: g(5) }, base)); // a finish a closed circuit does not use is not a difference
});

test("sector lines can be put in order", () => {
  assert.deepEqual(moveSector(["a", "b", "c"], 2, -1), ["a", "c", "b"]);
  assert.deepEqual(moveSector(["a", "b", "c"], 0, 1), ["b", "a", "c"]);
  assert.deepEqual(moveSector(["a", "b", "c"], 0, -1), ["a", "b", "c"]);
  assert.deepEqual(moveSector(["a", "b", "c"], 2, 1), ["a", "b", "c"]);
});

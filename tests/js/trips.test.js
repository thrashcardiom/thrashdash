import test from "node:test";
import assert from "node:assert/strict";
import { interpolateFeed, liveReadout, advanceLive, nextAutoOpen, turnToward, WHEN_OPTIONS, filterOptions, filterRoutes, overviewTiles, routeTotals, routesBounds, REPLAY_RATES, advanceReplay, clockText, isOver, limitAt, replayReadout, RAMP, colourScaleTop, distanceAtTime, graphModel, niceTicks, routeRuns, sameTrip, sampleAtDistance, sampleAtTime, speedBucket, statTiles, tripTitle } from "../../web/js/trips/model.js";
import { buildPrefs } from "../../web/js/units.js";

const prefs = buildPrefs({}, "");
// A trip driven north: a point every 10 s, 100 m apart (10 m/s).
const trip = (extra = {}) => ({
  header: { id: "20260101T000000Z", ts: 1, energy: "liquid" },
  points: [0, 1, 2, 3, 4].map((i) => ({ t: i * 10, la: -41 + i * 0.001, lo: 174, s: 10, d: i * 100, al: 5 + i })),
  stats: { dist: 400, dur: 40, moving_s: 40, avg_moving_ms: 10, top_ms: 10 },
  ...extra,
});

test("speed colours: slow is the first colour, the top of the scale the last, in between steps up", () => {
  assert.equal(speedBucket(0, 30), 0);
  assert.equal(speedBucket(30, 30), RAMP.length - 1);
  assert.equal(speedBucket(60, 30), RAMP.length - 1, "faster than the scale is still the last colour");
  assert.ok(speedBucket(15, 30) > 0 && speedBucket(15, 30) < RAMP.length - 1);
  assert.equal(speedBucket(NaN, 30), 0);
  assert.equal(speedBucket(5, 0), 0);
});

test("the colour scale tops out at the trip's top speed, but never below 10 m/s", () => {
  assert.equal(colourScaleTop([{ s: 3 }, { s: 4 }]), 10);
  assert.equal(colourScaleTop([{ s: 3 }, { s: 41 }, {}]), 41);
  assert.equal(colourScaleTop([]), 10);
});

test("the route is cut into runs of one colour with no gap between them", () => {
  const points = [
    { la: 0, lo: 0, s: 0 }, { la: 1, lo: 0, s: 0 }, { la: 2, lo: 0, s: 30 }, { la: 3, lo: 0, s: 30 }, { la: 4, lo: 0, s: 30 },
  ];
  const runs = routeRuns(points, 30);
  assert.ok(runs.length >= 2);
  for (let i = 1; i < runs.length; i++) assert.deepEqual(runs[i].coords[0], runs[i - 1].coords[runs[i - 1].coords.length - 1], "each run starts where the last ended");
  assert.deepEqual(runs.flatMap((r) => r.coords).filter((c, i, all) => i === 0 || c !== all[i - 1]).length > 0, true);
  assert.equal(runs[0].coords[0][0], 0);
  assert.equal(runs[runs.length - 1].coords.at(-1)[0], 4);
});

test("a route with bad points skips them and does not throw", () => {
  assert.deepEqual(routeRuns([{ la: 0, lo: 0 }]), []);
  assert.deepEqual(routeRuns([]), []);
  assert.doesNotThrow(() => routeRuns([{ la: 0, lo: 0, s: 1 }, { la: null, lo: 1 }, { la: 1, lo: 1, s: 1 }]));
});

test("the position at a time or a distance is interpolated, and clamped at the ends", () => {
  const p = trip().points;
  assert.equal(sampleAtTime(p, 15).d, 150);
  assert.equal(sampleAtTime(p, -5).d, 0);
  assert.equal(sampleAtTime(p, 999).d, 400);
  assert.ok(Math.abs(sampleAtDistance(p, 250).la - (-41 + 0.0025)) < 1e-9);
  assert.equal(sampleAtDistance(p, 250).t, 25);
  assert.equal(distanceAtTime(p, 30), 300);
  assert.equal(sampleAtTime([], 5), null);
  assert.equal(distanceAtTime([], 5), 0);
});

test("tick marks land on round numbers", () => {
  assert.deepEqual(niceTicks(100, 4), [0, 50, 100]);
  assert.deepEqual(niceTicks(87, 4), [0, 50, 100], "the last tick is at or past the largest value, so nothing is cut off");
  assert.deepEqual(niceTicks(2.3, 4), [0, 1, 2, 3]);
  assert.deepEqual(niceTicks(0), [0]);
  for (const t of niceTicks(0.7, 3)) assert.equal(String(t).length < 8, true, "no floating point noise like 0.30000000000000004");
});

test("the graph's scale always reaches the top speed", () => {
  const t = trip();
  t.points[2].s = 48; // 173 km/h: not a round number, and past the 150 tick
  const g = graphModel(t, { width: 400, height: 150 });
  assert.ok(g.y(48) >= g.pad.t, "the fastest point is inside the graph");
  assert.ok(Number(g.yTicks.at(-1).label) >= 48 * 3.6);
  assert.equal(g.y(48) > g.pad.t + 0.5, true, "and not pinned to the top edge");
});

test("the graph puts the start at the left edge, the end at the right, and faster higher up", () => {
  const g = graphModel(trip(), { width: 400, height: 150 });
  assert.equal(g.x(0), g.pad.l);
  assert.equal(g.x(400), 400 - g.pad.r);
  assert.ok(g.y(10) < g.y(2), "a higher speed is a smaller y");
  assert.ok(g.speedLine.startsWith("M"));
  assert.ok(g.altitude && g.altitude.area.endsWith("Z"));
  assert.equal(g.dFromX(g.pad.l), 0);
  assert.equal(g.dFromX(10000), 400, "past the right edge is the end");
  assert.ok(g.xTicks.length >= 2 && g.yTicks.length >= 2);
});

test("a flat trip is drawn flat, not as a mountain range", () => {
  const t = trip();
  t.points.forEach((p) => (p.al = 5.2));
  const g = graphModel(t, { width: 400, height: 150 });
  assert.equal(g.altitude.max - g.altitude.min, 10);
});

test("a trip with no altitude has no altitude area, and a one-point trip does not break the graph", () => {
  const t = trip();
  t.points.forEach((p) => delete p.al);
  assert.equal(graphModel(t, { width: 400, height: 150 }).altitude, null);
  assert.doesNotThrow(() => graphModel({ points: [{ t: 0, d: 0, s: 1, la: 0, lo: 0 }] }, { width: 400, height: 150 }));
  assert.doesNotThrow(() => graphModel({ points: [] }, { width: 400, height: 150 }));
});

test("posted limits and the stretches over them are placed by distance", () => {
  const t = trip({ stats: { ...trip().stats, limits: { posted: [[0, 20, 8]], spans: [[10, 20]] } } });
  const g = graphModel(t, { width: 400, height: 150 });
  assert.equal(g.limits.length, 1);
  assert.equal(g.limits[0].x0, g.x(0));
  assert.equal(g.limits[0].x1, g.x(200));
  assert.equal(g.over[0].x0, g.x(100));
  assert.equal(g.over[0].x1, g.x(200));
});

test("the figures: distance, time, speeds, and tiles only where the trip has the data", () => {
  const keys = statTiles(trip(), prefs).map((t) => t.key);
  assert.deepEqual(keys, ["distance", "time", "average", "top", "limits"]);
  const t = statTiles(trip(), prefs);
  assert.equal(t[0].value, "0.40 km");
  assert.equal(t[1].value, "0:00:40");
  assert.equal(t[2].value, "36 km/h");
  assert.equal(t[4].value, "Not checked", "no posted limit: says so, does not claim the driver was fine");
});

test("fuel, climb, damage, weather and places appear when there is data", () => {
  const stats = {
    ...trip().stats, fuel: { unit: "L", used: 2.5, per_100km: 8 }, alt: { min: 1, max: 9, gain: 8, loss: 0 }, damage: { start: 0, end: 3, max: 5 },
    wx: { temp_min: 12, temp_max: 16, temp_avg: 14, wind_max_ms: 10, rain_s: 30, snow_s: 0 }, places: ["A", "B", "C", "D"], countries: ["New Zealand"],
    limits: { checked_s: 100, over_s: 12, over_m: 100, max_over_ms: 3 },
  };
  const tiles = Object.fromEntries(statTiles(trip({ stats }), prefs).map((x) => [x.key, x]));
  assert.equal(tiles.fuel.value, "2.50 L");
  assert.equal(tiles.fuel.sub, "8.0 L/100 km");
  assert.equal(tiles.climb.value, "+8 m");
  assert.equal(tiles.climb.sub, "", "no 'down' when there was none");
  assert.equal(tiles.damage.value, "3.0%");
  assert.match(tiles.weather.value, /12 to 16/);
  assert.match(tiles.weather.sub, /rain 0:00:30/);
  assert.equal(tiles.limits.tone, "warn");
  assert.equal(tiles.limits.value, "0:00:12");
  assert.match(tiles.limits.sub, /0:01:40/);
  assert.equal(tiles.places.value, "A, B, C and 1 more");
});

test("units follow the user's choice", () => {
  const mph = buildPrefs({ speed: "mph", distance: "mi" }, "");
  const t = Object.fromEntries(statTiles(trip(), mph).map((x) => [x.key, x]));
  assert.equal(t.distance.value, "0.25 mi");
  assert.equal(t.top.value, "22 mph");
});

test("a trip with no summary has no tiles; the title prefers the user's own name", () => {
  assert.deepEqual(statTiles({ header: {}, points: [] }, prefs), []);
  assert.equal(tripTitle({ title: "Coast run", stats: { name: "A to B" } }), "Coast run");
  assert.equal(tripTitle({ title: null, stats: { name: "A to B" } }), "A to B");
  assert.equal(tripTitle({}), "Trip");
});

test("a trip read again is 'the same' only when nothing has been added or changed", () => {
  const a = { ...trip(), info: [], events: [], end: null, title: null };
  const copy = () => JSON.parse(JSON.stringify(a));
  assert.equal(sameTrip(a, copy()), true);
  const more = copy();
  more.points.push({ t: 50, d: 500, la: 0, lo: 0 });
  assert.equal(sameTrip(a, more), false, "a new point");
  const info = copy();
  info.info.push({ t: 1 });
  assert.equal(sameTrip(a, info), false, "a new place or limit reading");
  const ended = copy();
  ended.end = { reason: "idle" };
  assert.equal(sameTrip(a, ended), false, "the trip ended");
  const named = copy();
  named.title = "Coast";
  assert.equal(sameTrip(a, named), false, "renamed");
  const other = copy();
  other.header.id = "20260202T000000Z";
  assert.equal(sameTrip(a, other), false, "another trip");
  assert.equal(sameTrip(a, null), false);
  assert.equal(sameTrip(null, null), false);
});

test("the replay clock advances by real time times the rate, and stops at the end", () => {
  assert.deepEqual(advanceReplay(10, 0.1, 4, 100), { t: 10.4, ended: false });
  assert.deepEqual(advanceReplay(99.9, 0.2, 4, 100), { t: 100, ended: true });
  assert.deepEqual(advanceReplay(100, 0.1, 1, 100), { t: 100, ended: true });
  assert.deepEqual(REPLAY_RATES, [1, 4, 16, 64]);
});

test("a long pause between frames (a hidden window) is not time the replay skips", () => {
  assert.equal(advanceReplay(10, 30, 1, 1000).t, 10.25, "at most a quarter of a second of real time per frame");
  assert.equal(advanceReplay(10, -5, 4, 1000).t, 10, "and never backwards");
});

test("the replay shows the speed, limit and distance at a time, and flags speeding", () => {
  const t = trip({ stats: { ...trip().stats, limits: { posted: [[0, 20, 8]], spans: [[10, 20]] } } });
  const early = replayReadout(t, 5, prefs);
  assert.equal(early.text, "36 km/h · limit 29 · 0.05 km");
  assert.equal(early.over, false);
  const late = replayReadout(t, 15, prefs);
  assert.equal(late.over, true);
  assert.equal(late.here.d, 150);
  const none = replayReadout(t, 35, prefs);
  assert.equal(none.text, "36 km/h · 0.35 km", "no limit read there: none shown");
  assert.deepEqual(replayReadout({ points: [] }, 5, prefs), { text: "", over: false, here: null });
  assert.equal(limitAt(t, 25), null);
  assert.equal(limitAt(t, 20), 8);
  assert.equal(isOver(t, 5), false);
});

test("the replay clock reads as elapsed over total", () => {
  assert.equal(clockText(65, 146), "0:01:05 / 0:02:26");
});

const NOW = Date.UTC(2026, 10, 14, 12, 0, 0); // 14 Nov 2026
const route = (id, daysAgo, extra = {}) => ({ id, ts: NOW / 1000 - daysAgo * 86400, vehicle: "etkc", countries: ["New Zealand"], dist: 1000, dur: 100, top: 20, line: [[-41, 174], [-41.1, 174.1]], ...extra });

test("the all-trips filters: by when, vehicle and country", () => {
  const routes = [route("a", 1), route("b", 20, { vehicle: "pickup" }), route("c", 100, { countries: ["Australia"] }), route("d", 400, { countries: [] })];
  const ids = (f) => filterRoutes(routes, f, NOW).map((r) => r.id).join("");
  assert.equal(ids({}), "abcd");
  assert.equal(ids({ when: "7" }), "a");
  assert.equal(ids({ when: "30" }), "ab");
  assert.equal(ids({ when: "year" }), "abc", "this year runs from 1 January");
  assert.equal(ids({ vehicle: "pickup" }), "b");
  assert.equal(ids({ country: "Australia" }), "c");
  assert.equal(ids({ when: "30", vehicle: "etkc" }), "a");
  assert.equal(ids({ when: "all", vehicle: "", country: "" }), "abcd");
  assert.equal(ids({ country: "Nowhere" }), "");
});

test("the pickers offer what occurs, sorted, and nothing that is missing", () => {
  const o = filterOptions([route("a", 1, { vehicle: "pickup" }), route("b", 2), route("c", 3, { vehicle: null, countries: ["Australia", "New Zealand"] }), route("d", 4, { countries: undefined })]);
  assert.deepEqual(o.vehicles, ["etkc", "pickup"]);
  assert.deepEqual(o.countries, ["Australia", "New Zealand"]);
  assert.deepEqual(filterOptions([]), { vehicles: [], countries: [] });
});

test("totals over the trips shown", () => {
  const t = routeTotals([route("a", 1, { dist: 2000, dur: 60, top: 30 }), route("b", 2, { dist: 500, dur: 90, top: 41 })]);
  assert.deepEqual(t, { trips: 2, dist: 2500, dur: 150, top: 41, longest: 2000 });
  assert.deepEqual(routeTotals([]), { trips: 0, dist: 0, dur: 0, top: 0, longest: 0 });
  const tiles = Object.fromEntries(overviewTiles([route("a", 1, { dist: 2000, dur: 60, top: 30 })], prefs).map((x) => [x.key, x.value]));
  assert.equal(tiles.trips, "1");
  assert.equal(tiles.distance, "2.00 km");
  assert.equal(tiles.time, "0:01:00");
  assert.equal(tiles.top, "108 km/h");
});

test("the bounds enclose every route, and there are none for no routes", () => {
  const b = routesBounds([route("a", 1, { line: [[-41.2, 174.7], [-41.1, 174.9]] }), route("b", 1, { line: [[-41.5, 174.8]] }), route("c", 1, { line: undefined })]);
  assert.deepEqual(b, [[-41.5, 174.7], [-41.1, 174.9]]);
  assert.equal(routesBounds([]), null);
  assert.equal(routesBounds([route("x", 1, { line: [] })]), null);
  assert.equal(WHEN_OPTIONS[0].id, "all");
});

test("the map's turn follows the heading the short way round and eases in", () => {
  assert.equal(turnToward(10, 200, undefined), 200, "a jump snaps");
  assert.equal(turnToward(null, 45, 0.1), 45, "no turn yet: start at the heading");
  const slightly = turnToward(0, 90, 0.05);
  assert.ok(slightly > 0 && slightly < 30, `one short step is a small turn (${slightly})`);
  assert.ok(Math.abs(turnToward(0, 90, 10) - 90) < 0.01, "given time it gets there");
  const across = turnToward(350, 10, 0.2);
  assert.ok(across > 350 || across < 10, `350 to 10 goes through north, not the long way (${across})`);
  assert.ok(turnToward(10, 350, 0.2) < 10 || turnToward(10, 350, 0.2) > 350, "and back the other way");
  assert.equal(turnToward(123, NaN, 0.1), 123, "no heading: stays");
  assert.equal(turnToward(123, undefined, 0.1), 123);
  assert.ok(turnToward(720 + 5, 8, 10) >= 0 && turnToward(-30, 8, 10) < 360, "angles are kept between 0 and 360");
});

test("a new trip being recorded is moved to once, and not again after the user looks elsewhere", () => {
  const trips = [{ id: "new", recording: true }, { id: "old", recording: false }];
  assert.equal(nextAutoOpen(trips, null), "new");
  assert.equal(nextAutoOpen(trips, "new"), null, "already moved to it");
  assert.equal(nextAutoOpen([{ id: "newer", recording: true }, ...trips.slice(1)], "new"), "newer", "a later trip is another move");
  assert.equal(nextAutoOpen([{ id: "old", recording: false }], null), null, "nothing being recorded");
  assert.equal(nextAutoOpen([], null), null);
  assert.equal(nextAutoOpen(undefined, null), null);
});

test("the live clock runs a couple of seconds behind the newest point, smoothly, and never backwards", () => {
  assert.equal(advanceLive(NaN, 0, 100), 98, "the first time it starts at the delayed target");
  assert.equal(advanceLive(50, 0.016, 100), 98, "far behind (a burst of points): it catches up at once");
  assert.ok(Math.abs(advanceLive(97, 0.016, 100) - 97.016) < 1e-9, "close behind: it moves at real speed");
  assert.equal(advanceLive(97.99, 0.2, 100), 98, "and stops at the target");
  assert.equal(advanceLive(98, 0.016, 100), 98, "no new points: it holds");
  assert.equal(advanceLive(98, 0.016, 99), 98, "the target moving back never moves the car back");
  assert.equal(advanceLive(97, 10, 100), 97.25, "a long gap between frames (a hidden window) moves it only a quarter of a second");
  assert.equal(advanceLive(NaN, 0, 1), 0, "a trip only a second old starts at its beginning");
  assert.equal(advanceLive(97, -1, 100), 97, "time never runs backwards");
});


const S = (rt, lat, lon, hdg, spd) => ({ rt, lat, lon, hdg, spd });

test("the live position is drawn between the two positions either side of the moment, heading the short way round", () => {
  const samples = [S(10.0, -41.0, 174.0, 350, 10), S(10.1, -41.001, 174.002, 10, 20), S(10.2, -41.002, 174.004, 30, 20)];
  const mid = interpolateFeed(samples, 10.05);
  assert.ok(Math.abs(mid.lat - -41.0005) < 1e-9 && Math.abs(mid.lon - 174.001) < 1e-9);
  assert.ok(Math.abs(mid.spd - 15) < 1e-9);
  assert.ok(mid.hdg > 355 || mid.hdg < 5, `350 to 10 passes through north (${mid.hdg})`);
  assert.equal(interpolateFeed(samples, 9).lat, -41.0, "before the first: the first");
  assert.equal(interpolateFeed(samples, 99).lat, -41.002, "after the last: held where the car was last seen");
  assert.equal(interpolateFeed([], 5), null);
  assert.equal(interpolateFeed([S(1, 2, 3)], 5).lat, 2, "one position is all there is");
  assert.equal(interpolateFeed([S(1, 2, 3, undefined, undefined), S(2, 4, 5, 90, 7)], 1.5).hdg, 90, "a heading only one side has is used");
});

test("the live readout shows what the car is doing now, and the limit and distance of the newest recorded point", () => {
  const t = trip({ stats: { ...trip().stats, limits: { posted: [[0, 100, 8]], spans: [[30, 100]] } } });
  const r = liveReadout(t, { lat: -41, lon: 174, hdg: 123, spd: 20 }, prefs);
  assert.equal(r.text, "72 km/h · limit 29 · 0.40 km");
  assert.equal(r.over, true);
  assert.deepEqual([r.here.la, r.here.lo, r.here.h, r.here.s], [-41, 174, 123, 20]);
  assert.equal(liveReadout(t, null, prefs).here, null);
  assert.equal(liveReadout({ points: [] }, { lat: 1, lon: 2 }, prefs).here, null);
  assert.equal(liveReadout(t, { lat: 1, lon: 2 }, prefs).here.h, t.points[4].h, "no heading from the feed: the recorded one");
});

import test from "node:test";
import assert from "node:assert/strict";
import { compareRows, gapSeries, signedDuration, timeAtDistance } from "../../web/js/trips/compare.js";
import { buildPrefs } from "../../web/js/units.js";

// a trip that drives `metres` at `speed` m/s, a point every 10 s
const trip = (speed, seconds, stats = {}) => {
  const points = [];
  for (let t = 0; t <= seconds; t += 10) points.push({ t, d: t * speed, s: speed, la: -41.2 + t * 1e-5, lo: 174.8 });
  const dist = seconds * speed;
  return { header: { energy: "liquid" }, points, stats: { dist, dur: seconds, moving_s: seconds, avg_moving_ms: speed, top_ms: speed, ...stats } };
};
const prefs = buildPrefs({}, "");

test("timeAtDistance reads the time a distance was reached", () => {
  const a = trip(10, 100);
  assert.equal(timeAtDistance(a.points, 500), 50);
  assert.equal(timeAtDistance(a.points, 0), 0);
  assert.equal(timeAtDistance(a.points, 5000), 100, "past the end it stays at the end");
});

test("gapSeries: a slower second trip falls behind steadily", () => {
  const fast = trip(20, 50); // 1000 m in 50 s
  const slow = trip(10, 100); // 1000 m in 100 s
  const series = gapSeries(fast, slow, 10);
  assert.equal(series.length, 11);
  assert.deepEqual([series[0].gap, series[10].gap], [0, 50]);
  assert.ok(series.every((p, i) => i === 0 || p.gap >= series[i - 1].gap), "the gap only grows");
  assert.equal(series[10].d, 1000);
});

test("gapSeries compares over the distance both drove, and is empty without a route", () => {
  const short = trip(10, 50); // 500 m
  const long = trip(10, 100); // 1000 m
  assert.equal(gapSeries(short, long).at(-1).d, 500);
  assert.deepEqual(gapSeries(null, long), []);
  assert.deepEqual(gapSeries({ points: [{ t: 0, d: 0 }] }, long), []);
  assert.deepEqual(gapSeries({ points: [{ t: 0, d: 0 }, { t: 5, d: 0 }] }, long), [], "a trip that went nowhere has nothing to compare");
});

test("signedDuration shows the sign and drops a leading zero hour", () => {
  assert.equal(signedDuration(65), "+1:05");
  assert.equal(signedDuration(-12), "-0:12");
  assert.equal(signedDuration(0), "+0:00");
  assert.equal(signedDuration(3725), "+1:02:05");
});

test("compareRows lines the figures up, marks the better of the 'less is better' ones, and says how the second differs", () => {
  const a = trip(20, 50, { fuel: { unit: "L", used: 1.0 }, limits: { over_s: 5 }, stops: [] });
  const b = trip(10, 100, { fuel: { unit: "L", used: 1.5 }, limits: { over_s: 0 }, stops: [{ dur: 60 }] });
  const rows = Object.fromEntries(compareRows(a, b, prefs).map((r) => [r.key, r]));
  assert.equal(rows.time.better, "a");
  assert.equal(rows.time.diff, "+0:50");
  assert.equal(rows.fuel.better, "a");
  assert.equal(rows.over.better, "b");
  assert.equal(rows.stops.better, "a");
  assert.equal(rows.average.better, "", "speed is not marked: faster is not 'better'");
  assert.equal(rows.distance.diff.startsWith("+"), false, "the same distance: no plus for nothing");
});

test("compareRows leaves out figures that one of the trips does not have, and needs both summaries", () => {
  const a = trip(10, 100, { fuel: { unit: "L", used: 1 } });
  const b = trip(10, 100); // no fuel reading
  assert.ok(!compareRows(a, b, prefs).some((r) => r.key === "fuel"));
  assert.ok(!compareRows(a, b, prefs).some((r) => r.key === "over"), "no posted limit on either: not listed");
  assert.deepEqual(compareRows({ points: [] }, b, prefs), []);
  const mixed = compareRows(trip(10, 100, { fuel: { unit: "L", used: 1 } }), trip(10, 100, { fuel: { unit: "kWh", used: 1 } }), prefs);
  assert.ok(!mixed.some((r) => r.key === "fuel"), "litres and kWh are not compared");
});

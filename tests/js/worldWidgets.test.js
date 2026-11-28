import assert from "node:assert/strict";
import test from "node:test";

import { buildPrefs } from "../../web/js/units.js";
import { specs } from "../../web/js/widgets/index.js";
import { OFF_ROUTE_M, formatArrival, formatTimeLeft, routeView } from "../../web/js/widgets/route.js";
import { OVER_TOLERANCE, limitView } from "../../web/js/widgets/speedlimit.js";
import { sanitizeLayout } from "../../web/js/layoutSchema.js";

const KMH = (v) => v / 3.6;
const prefs = { speed: "kmh", distance: "km" };

test("the sign shows the limit in the user's speed unit", () => {
  const values = { "geo.limit": KMH(50), "geo.limitPosted": true, wheelspeed: KMH(42) };
  assert.deepEqual(limitView(values, "wheelspeed", prefs), { limit: "50", known: true, posted: true, over: false, now: "42 km/h" });
  const mph = limitView({ "geo.limit": 22.352, "geo.limitPosted": true, wheelspeed: 20 }, "wheelspeed", { speed: "mph" });
  assert.equal(mph.limit, "50");
  assert.equal(mph.now, "45 mph");
});

test("the sign turns red only against a posted limit, past a one-metre allowance", () => {
  const at = (extra, speed) => limitView({ "geo.limit": 20, "geo.limitPosted": true, wheelspeed: speed, ...extra }, "wheelspeed", prefs);
  assert.equal(at({}, 20 + OVER_TOLERANCE).over, false);
  assert.equal(at({}, 20 + OVER_TOLERANCE + 0.1).over, true);
  assert.equal(at({}, -(21.5)).over, true); // reversing fast is still fast
  const guess = at({ "geo.limitPosted": false }, 40);
  assert.deepEqual([guess.known, guess.posted, guess.over], [true, false, false]); // an estimate never scolds
});

test("the sign copes with no limit, no speed and odd values", () => {
  assert.deepEqual(limitView({}, "wheelspeed", prefs), { limit: "--", known: false, posted: false, over: false, now: "--" });
  assert.equal(limitView({ "geo.limit": "50", wheelspeed: "fast" }, "wheelspeed", prefs).known, false);
  assert.equal(limitView({ "geo.limit": NaN }, "wheelspeed", prefs).known, false);
  assert.equal(limitView({ "geo.limit": 10, "geo.limitPosted": true, speed2: 30 }, "speed2", prefs).over, true); // another speed channel
  assert.equal(limitView({ "geo.limit": 10 }, "wheelspeed", undefined).limit, "36"); // no prefs: km/h
});

test("time left is hours and minutes, and arrival is a clock time", () => {
  assert.equal(formatTimeLeft(5400), "1 h 30 min");
  assert.equal(formatTimeLeft(3600), "1 h 00 min");
  assert.equal(formatTimeLeft(540), "9 min");
  assert.equal(formatTimeLeft(40), "<1 min");
  assert.equal(formatTimeLeft(0), "<1 min");
  for (const bad of [undefined, null, -5, NaN, "9", Infinity]) assert.equal(formatTimeLeft(bad), "--", String(bad));
  const when = new Date(2026, 10, 21, 9, 0, 0).getTime();
  assert.equal(formatArrival(1800, when), new Date(2026, 10, 21, 9, 30, 0).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }));
  assert.equal(formatArrival(undefined, when), "--");
});

test("the route panel shows progress, and 'no route' when none is set", () => {
  const now = new Date(2026, 10, 21, 9, 0, 0).getTime();
  const values = { "geo.route.active": true, "geo.route.remaining": 12345, "geo.route.eta": 900, "geo.route.off": 3, "geo.route.name": "Home" };
  const v = routeView(values, prefs, now);
  assert.deepEqual([v.active, v.name, v.timeLeft, v.distanceLeft, v.offRoute], [true, "Home", "15 min", "12 km", false]);
  assert.equal(v.arrival, formatArrival(900, now));
  assert.equal(routeView({ ...values, "geo.route.remaining": 860 }, prefs, now).distanceLeft, "0.9 km"); // small distances keep a decimal
  assert.equal(routeView({ ...values, "geo.route.remaining": 12345 }, { distance: "mi" }, now).distanceLeft, "7.7 mi");
  const none = routeView({ "geo.route.active": false }, prefs, now);
  assert.deepEqual(none, { active: false, name: "", arrival: "--", timeLeft: "--", distanceLeft: "--", offRoute: false, planning: "", next: null, stop: null });
  assert.equal(routeView({}, prefs, now).active, false); // the game is not sending at all
});

test("off the route is flagged only past the allowance, and only on a route", () => {
  const base = { "geo.route.active": true, "geo.route.remaining": 1000, "geo.route.eta": 60 };
  assert.equal(routeView({ ...base, "geo.route.off": OFF_ROUTE_M }, prefs, 0).offRoute, false);
  assert.equal(routeView({ ...base, "geo.route.off": OFF_ROUTE_M + 1 }, prefs, 0).offRoute, true);
  assert.equal(routeView({ "geo.route.active": false, "geo.route.off": 500 }, prefs, 0).offRoute, false);
  assert.equal(routeView({ ...base, "geo.route.name": 5 }, prefs, 0).name, ""); // a name that is not text is ignored
});

test("both widgets are registered and a layout using them is accepted as it is", () => {
  for (const type of ["speedlimit", "route"]) assert.ok(specs[type], type);
  const widgets = [
    { type: "speedlimit", x: 10, y: 10, w: 200, h: 240, speedChannel: "wheelspeed", style: "box", showSpeed: false },
    { type: "route", x: 300, y: 10, w: 300, h: 260, showName: false, showOff: true },
  ];
  const raw = { format: 1, name: "t", width: 800, height: 400, widgets };
  const { layout: result, warnings } = sanitizeLayout(raw, specs);
  assert.deepEqual(warnings, []);
  assert.deepEqual(result.widgets.map((w) => w.type), ["speedlimit", "route"]);
  assert.equal(result.widgets[0].style, "box");
  assert.equal(result.widgets[0].showSpeed, false);
  assert.equal(result.widgets[1].showName, false);
  const bad = sanitizeLayout({ ...raw, widgets: [{ ...widgets[0], style: "triangle" }] }, specs);
  assert.notEqual(bad.layout.widgets[0]?.style, "triangle"); // an unknown sign style is not kept
  assert.ok(bad.warnings.length > 0);
});

// ---- the lap timer -------------------------------------------------------------------------------------------------------------
import { formatDelta, formatLapTime, lapView } from "../../web/js/widgets/laptimer.js";

test("lap times are shown to a tenth of a second, minutes first", () => {
  assert.equal(formatLapTime(83.46), "1:23.5");
  assert.equal(formatLapTime(9.04), "0:09.0");
  assert.equal(formatLapTime(59.96), "1:00.0"); // rounds up into the next minute
  assert.equal(formatLapTime(0), "0:00.0");
  assert.equal(formatLapTime(3725), "62:05.0");
  for (const bad of [undefined, null, -1, NaN, "83", Infinity]) assert.equal(formatLapTime(bad), "--", String(bad));
});

test("the gap to the best lap has a sign, and nothing under a twentieth of a second is a lead", () => {
  assert.equal(formatDelta(0.43), "+0.4");
  assert.equal(formatDelta(-1.25), "-1.3");
  assert.equal(formatDelta(0.02), "0.0");
  assert.equal(formatDelta(-0.04), "0.0");
  assert.equal(formatDelta(12), "+12.0");
  assert.equal(formatDelta(undefined), "--");
});

test("the lap timer says 'not timing' until a circuit has been started", () => {
  const v = lapView({});
  assert.deepEqual([v.timing, v.active, v.time, v.sectors.length], [false, false, "--", 0]);
});

test("a lap in progress shows its clock, number, gap and sectors", () => {
  const v = lapView({
    "lap.active": true, "lap.circuit": "Round", "lap.number": 3, "lap.time": 14.26, "lap.delta": -0.34, "lap.sector": 2, "lap.sectors": 3,
    "lap.last": 31.4, "lap.lastValid": true, "lap.lastDelta": 0.8, "lap.lastIsBest": false, "lap.best": 30.6,
    "lap.s1.last": 10.1, "lap.s1.isBest": true, "lap.s2.last": 10.6, "lap.s2.isBest": false, "lap.s3.last": 10.7, "lap.s3.isBest": false,
  });
  assert.deepEqual([v.timing, v.active, v.circuit, v.lap, v.time, v.delta, v.deltaSign], [true, true, "Round", "Lap 3", "0:14.3", "-0.3", -1]);
  assert.deepEqual([v.last, v.lastNote, v.best, v.lastValid, v.lastIsBest], ["0:31.4", "+0.8", "0:30.6", true, false]);
  assert.deepEqual(v.sectors.map((s) => [s.n, s.last, s.isBest, s.current]), [[1, "0:10.1", true, false], [2, "0:10.6", false, true], [3, "0:10.7", false, false]]);
});

test("between runs (a stage finished) the last and best stay and the clock does not", () => {
  const v = lapView({ "lap.active": false, "lap.circuit": "Hill", "lap.last": 41.2, "lap.best": 40.1, "lap.lastValid": false });
  assert.deepEqual([v.timing, v.active, v.time, v.delta, v.lap, v.last, v.best, v.lastValid], [true, false, "--", "", "", "0:41.2", "0:40.1", false]);
});

test("odd values do not break the lap timer", () => {
  const v = lapView({ "lap.circuit": 5, "lap.sectors": 40, "lap.time": "x", "lap.active": true });
  assert.equal(v.timing, false);
  assert.equal(v.sectors.length, 9); // never more than nine sectors
  assert.equal(lapView({ "lap.circuit": "A", "lap.sectors": -3 }).sectors.length, 0);
});

test("the lap timer is registered and its channels are named and grouped", async () => {
  assert.ok(specs.laptimer);
  const { classifyChannel, describeChannel } = await import("../../web/js/editor/channels.js");
  for (const name of ["lap.active", "lap.time", "lap.delta", "lap.best", "lap.s1.last", "lap.s9.isBest", "lap.s2.best"]) {
    assert.ok(describeChannel(name), name);
    assert.equal(classifyChannel(name), "lap", name);
  }
  assert.equal(describeChannel("lap.s2.best"), "Lap timing: sector 2 best time (seconds)");
  assert.equal(describeChannel("lap.s10.best"), null); // (nine sectors at most)
});

// ---- the next turn ---------------------------------------------------------------------------------------------------------------
import { THEN_WITHIN_M, formatManeuverDistance, maneuverArrow, maneuverText, nextTurn, ordinal } from "../../web/js/widgets/route.js";

const km = buildPrefs({}, "");
const mi = buildPrefs({ distance: "mi" }, "");

test("ordinals", () => {
  assert.deepEqual([1, 2, 3, 4, 11, 12, 13, 21, 22, 23, 101, 111].map(ordinal), ["1st", "2nd", "3rd", "4th", "11th", "12th", "13th", "21st", "22nd", "23rd", "101st", "111th"]);
});

test("the distance to a turn is in the person's unit, rounded the way a person would say it", () => {
  assert.deepEqual([0, 12, 19.9].map((m) => formatManeuverDistance(m, km)), ["Now", "Now", "Now"]);
  assert.deepEqual([20, 266, 304, 949].map((m) => formatManeuverDistance(m, km)), ["20 m", "270 m", "300 m", "950 m"]);
  assert.deepEqual([950, 1234, 9990, 12400].map((m) => formatManeuverDistance(m, km)), ["1.0 km", "1.2 km", "10 km", "12 km"]);
  assert.deepEqual([30, 100, 150].map((m) => formatManeuverDistance(m, mi)), ["100 ft", "350 ft", "500 ft"]);
  assert.deepEqual([161, 500, 1609, 16093].map((m) => formatManeuverDistance(m, mi)), ["0.1 mi", "0.3 mi", "1.0 mi", "10 mi"]);
  for (const bad of [undefined, null, "far", NaN]) assert.equal(formatManeuverDistance(bad, km), "");
  assert.equal(formatManeuverDistance(500, undefined), "500 m"); // no prefs: kilometres
});

test("each kind of maneuver has an arrow", () => {
  assert.equal(maneuverArrow("turn", "left"), "↰");
  assert.equal(maneuverArrow("turn", "right"), "↱");
  assert.equal(maneuverArrow("fork", "slight left"), "↖");
  assert.equal(maneuverArrow("turn", "slight right"), "↗");
  assert.equal(maneuverArrow("turn", "uturn"), "↶");
  assert.equal(maneuverArrow("roundabout"), "⟳");
  assert.equal(maneuverArrow("exit roundabout"), "⟳");
  assert.equal(maneuverArrow("arrive", "left"), "⚑");
  assert.equal(maneuverArrow("turn", "something new"), "↑"); // an unknown direction is straight on
  assert.equal(maneuverArrow(undefined, undefined), "↑");
});

test("the words for the kinds of step seen in a real route", () => {
  assert.equal(maneuverText({ type: "fork", modifier: "slight left", street: "Aotea Quay" }), "Keep left at the fork onto Aotea Quay");
  assert.equal(maneuverText({ type: "turn", modifier: "slight left", street: "Oriental Parade" }), "Bear left onto Oriental Parade");
  assert.equal(maneuverText({ type: "turn", modifier: "left", street: "Evans Bay Parade" }), "Turn left onto Evans Bay Parade");
  assert.equal(maneuverText({ type: "turn", modifier: "sharp right" }), "Turn sharp right");
  assert.equal(maneuverText({ type: "turn", modifier: "uturn" }), "Make a U-turn");
  assert.equal(maneuverText({ type: "turn", modifier: "straight", street: "SH 1" }), "Continue on SH 1");
  assert.equal(maneuverText({ type: "exit roundabout", street: "SH 1" }), "Leave the roundabout onto SH 1");
  assert.equal(maneuverText({ type: "arrive", modifier: "left" }), "Arrive: your destination is on the left");
  assert.equal(maneuverText({ type: "arrive" }), "Arrive at your destination");
  assert.equal(maneuverText({ type: "depart", street: "Hutt Road" }), "Head out on Hutt Road");
});

test("a roundabout says its exit, and names the street from the step after it", () => {
  assert.equal(maneuverText({ type: "roundabout", exit: 2 }), "At the roundabout take the 2nd exit");
  assert.equal(maneuverText({ type: "roundabout", exit: 1 }, { type: "exit roundabout", street: "Aotea Quay" }), "At the roundabout take the 1st exit onto Aotea Quay");
  assert.equal(maneuverText({ type: "roundabout", exit: 3 }, { type: "turn", street: "Elsewhere" }), "At the roundabout take the 3rd exit"); // (only an exit step names it)
  assert.equal(maneuverText({ type: "roundabout" }), "At the roundabout go round");
});

test("the other kinds a routing service uses are worded too, and an unknown one still says something", () => {
  assert.equal(maneuverText({ type: "end of road", modifier: "right", street: "Cuba Street" }), "At the end of the road turn right onto Cuba Street");
  assert.equal(maneuverText({ type: "merge", modifier: "slight right", street: "SH 2" }), "Merge right onto SH 2");
  assert.equal(maneuverText({ type: "on ramp", modifier: "right" }), "Take the ramp on the right");
  assert.equal(maneuverText({ type: "off ramp", modifier: "left", street: "SH 1" }), "Take the exit on the left onto SH 1");
  assert.equal(maneuverText({ type: "new name", street: "Quay Street" }), "Continue on Quay Street");
  assert.equal(maneuverText({ type: "continue" }), "Continue");
  assert.equal(maneuverText({ type: "notification", modifier: "left", street: "X" }), "Notification left onto X");
  assert.equal(maneuverText({}), "Continue");
});

test("the next-turn block is made from the relay's channels", () => {
  const values = {
    "geo.route.next.distance": 266, "geo.route.next.type": "fork", "geo.route.next.modifier": "slight left", "geo.route.next.street": "Aotea Quay",
    "geo.route.then.gap": 810, "geo.route.then.type": "roundabout", "geo.route.then.exit": 1,
  };
  assert.deepEqual(nextTurn(values, km), { arrow: "↖", distance: "270 m", text: "Keep left at the fork onto Aotea Quay", then: "" }); // (810 m behind it: not worth a mention)
  assert.equal(nextTurn({}, km), null);
  assert.equal(nextTurn({ "geo.route.next.type": 5 }, km), null);
});

test("a close second maneuver is mentioned, unless it is the exit of the roundabout already named", () => {
  const close = { "geo.route.next.distance": 100, "geo.route.next.type": "turn", "geo.route.next.modifier": "left", "geo.route.then.gap": THEN_WITHIN_M, "geo.route.then.type": "turn", "geo.route.then.modifier": "right", "geo.route.then.street": "Cuba Street" };
  assert.equal(nextTurn(close, km).then, "then turn right onto Cuba Street");
  assert.equal(nextTurn({ ...close, "geo.route.then.gap": THEN_WITHIN_M + 1 }, km).then, "");
  const round = { "geo.route.next.distance": 50, "geo.route.next.type": "roundabout", "geo.route.next.exit": 1, "geo.route.then.gap": 31, "geo.route.then.type": "exit roundabout", "geo.route.then.street": "Aotea Quay" };
  const got = nextTurn(round, km);
  assert.equal(got.text, "At the roundabout take the 1st exit onto Aotea Quay");
  assert.equal(got.then, ""); // (it would say the same again)
  assert.equal(nextTurn({ "geo.route.next.type": "arrive", "geo.route.next.distance": 80 }, km).then, "");
});

test("the route panel shows the next turn only while there is a route", () => {
  const values = { "geo.route.active": true, "geo.route.remaining": 5000, "geo.route.eta": 300, "geo.route.next.distance": 120, "geo.route.next.type": "turn", "geo.route.next.modifier": "left" };
  assert.equal(routeView(values, km, 0).next.text, "Turn left");
  assert.equal(routeView({ ...values, "geo.route.active": false }, km, 0).next, null);
  assert.equal(routeView({ "geo.route.active": true }, km, 0).next, null); // (a route with no steps: the older Earth app)
});

test("the route widget has the option to show or hide the next turn", () => {
  assert.equal(specs.route.props.showNext.type, "bool");
  assert.equal(specs.route.defaults.showNext, true);
});

test("the route panel says when a route is being planned, and which kind of plan it is", () => {
  const view = (extra) => routeView({ "geo.route.active": true, ...extra }, prefs, 0).planning;
  assert.equal(view({}), "");
  assert.equal(view({ "geo.route.planning": false, "geo.route.replanning": false }), "");
  assert.equal(view({ "geo.route.planning": true, "geo.route.replanning": false }), "Planning route\u2026");
  assert.equal(view({ "geo.route.planning": true, "geo.route.replanning": true }), "Re-routing\u2026");
  assert.equal(routeView({ "geo.route.active": false, "geo.route.planning": true }, prefs, 0).planning, "");
});

test("the route panel shows the next stop on the way: its name, how far and when", () => {
  const now = new Date(2026, 10, 21, 9, 0, 0).getTime();
  const base = { "geo.route.active": true, "geo.route.stop.distance": 3200, "geo.route.stop.eta": 300, "geo.route.stop.number": 2 };
  assert.deepEqual(routeView({ ...base, "geo.route.stop.name": "Cafe" }, prefs, now).stop, { name: "Cafe", text: `3.2 km \u00b7 ${formatArrival(300, now)}` });
  assert.equal(routeView(base, prefs, now).stop.name, "Stop 2");
  assert.equal(routeView({ ...base, "geo.route.stop.distance": 24000 }, { distance: "mi" }, now).stop.text.startsWith("15 mi"), true);
  assert.equal(routeView({ ...base, "geo.route.stop.eta": undefined }, prefs, now).stop.text, "3.2 km");
  assert.equal(routeView({ "geo.route.active": true }, prefs, now).stop, null);
  assert.equal(routeView({ ...base, "geo.route.active": false }, prefs, now).stop, null);
  assert.equal(specs.route.defaults.showStop, true);
});


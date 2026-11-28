import assert from "node:assert/strict";
import test from "node:test";

import { MAX_STOPS, addStop, distanceText, formatMinutes, insertFirst, metresBetween, moveStop, placesFromRoutes, removeStop, routeSummary, routeToExtend, stopLabel, stopsSummary } from "../../web/js/trips/stops.js";
import { buildPrefs } from "../../web/js/units.js";

const km = buildPrefs({}, "");
const mi = buildPrefs({ distance: "mi" }, "");
const at = (east, north) => ({ lat: -41.29 + north / 111195, lon: 174.78 + east / (111195 * Math.cos((41.29 * Math.PI) / 180)) });

test("distances between places", () => {
  assert.ok(Math.abs(metresBetween(at(0, 0), at(300, 400)) - 500) < 1);
  assert.equal(metresBetween(at(0, 0), at(0, 0)), 0);
});

test("stops are added at the end, with their name tidied and their place to a decimetre", () => {
  const { stops, error } = addStop([], { lat: -41.2865432198, lon: 174.7762, name: "  Home \n sweet  home " });
  assert.equal(error, "");
  assert.deepEqual(stops, [{ lat: -41.286543, lon: 174.7762, name: "Home sweet home" }]);
  assert.deepEqual(addStop(stops, { lat: -41.3, lon: 174.8 }).stops.map((s) => s.name), ["Home sweet home", undefined]);
  assert.equal("name" in addStop([], { lat: 1, lon: 2, name: "   " }).stops[0], false);
});

test("a route has at most eleven stops, and says so", () => {
  let stops = [];
  for (let i = 0; i < MAX_STOPS; i++) stops = addStop(stops, at(i * 500, 0)).stops;
  assert.equal(stops.length, MAX_STOPS);
  const full = addStop(stops, at(99999, 0));
  assert.equal(full.stops.length, MAX_STOPS);
  assert.match(full.error, /11 stops/);
});

test("things that are not places are refused", () => {
  for (const bad of [null, undefined, {}, { lat: 1 }, { lat: "1", lon: 2 }, { lat: 95, lon: 0 }, { lat: 0, lon: 200 }, { lat: NaN, lon: 0 }]) {
    const got = addStop([], bad);
    assert.deepEqual(got.stops, []);
    assert.match(got.error, /not a place/, JSON.stringify(bad));
  }
});

test("the same place twice in a row is ignored without a complaint", () => {
  const one = addStop([], at(0, 0)).stops;
  const again = addStop(one, at(10, 5)); // (twelve metres away: a double click)
  assert.deepEqual(again, { stops: one, error: "" });
  assert.equal(addStop(one, at(100, 0)).stops.length, 2);
  assert.equal(addStop(addStop(one, at(100, 0)).stops, at(0, 0)).stops.length, 3); // (going back to a place is fine: only the last stop counts)
});

test("stops can be moved and removed", () => {
  const stops = [{ lat: 1, lon: 1, name: "a" }, { lat: 2, lon: 2, name: "b" }, { lat: 3, lon: 3, name: "c" }];
  assert.deepEqual(moveStop(stops, 2, -1).map((s) => s.name), ["a", "c", "b"]);
  assert.deepEqual(moveStop(stops, 0, 1).map((s) => s.name), ["b", "a", "c"]);
  assert.deepEqual(moveStop(stops, 0, -1).map((s) => s.name), ["a", "b", "c"]);
  assert.deepEqual(moveStop(stops, 2, 1).map((s) => s.name), ["a", "b", "c"]);
  assert.deepEqual(moveStop(stops, 9, 1), stops);
  assert.deepEqual(removeStop(stops, 1).map((s) => s.name), ["a", "c"]);
  assert.deepEqual(removeStop(stops, 7), stops);
  assert.equal(stops.length, 3); // (the list given is never changed)
});

test("stops are labelled by name, else by position, the last being the destination", () => {
  const stops = [{ lat: 1, lon: 1 }, { lat: 2, lon: 2, name: "Cafe" }, { lat: 3, lon: 3 }];
  assert.deepEqual([0, 1, 2].map((i) => stopLabel(stops, i)), ["Stop 1", "Cafe", "Destination"]);
  assert.equal(stopLabel([{ lat: 1, lon: 1 }], 0), "Destination");
});

test("the stops are summed up in the person's unit", () => {
  assert.equal(stopsSummary([], km), "No stops yet");
  assert.equal(stopsSummary([at(0, 0)], km), "1 stop");
  assert.equal(stopsSummary([at(0, 0), at(0, 5000)], km), "2 stops, 5.0 km in a straight line between them");
  assert.equal(stopsSummary([at(0, 0), at(0, 5000), at(0, 25000)], km), "3 stops, 25 km in a straight line between them");
  assert.match(stopsSummary([at(0, 0), at(0, 16093)], mi), /^2 stops, 10 mi in a straight line/);
});

test("places from earlier trips: where each began and ended, newest first, each name once", () => {
  const routes = [
    { ts: 100, from: "Oriental Parade", to: "Courtenay Place", line: [[-41.29, 174.79], [-41.3, 174.78]] },
    { ts: 300, from: "Courtenay Place", to: "Kelburn", line: [[-41.3, 174.78], [-41.28, 174.76]] },
    { ts: 200, from: null, to: "Petone", line: [[-41.2, 174.9], [-41.22, 174.88]] },
    { ts: 400, from: "No Line", to: "Nowhere", line: [[-41, 174]] },
    { ts: 50, from: "  ", to: "", line: [[1, 2], [3, 4]] },
  ];
  const got = placesFromRoutes(routes);
  assert.deepEqual(got.map((p) => p.name), ["Kelburn", "Courtenay Place", "Petone", "Oriental Parade"]);
  assert.deepEqual([got[0].lat, got[0].lon], [-41.28, 174.76]); // (a trip's end is its last point)
  assert.deepEqual([got[1].lat, got[1].lon], [-41.3, 174.78]); // (the newest trip's start: the same name as an older trip's end, kept once)
  assert.deepEqual(placesFromRoutes(routes, 2).map((p) => p.name), ["Kelburn", "Courtenay Place"]);
  assert.deepEqual(placesFromRoutes([]), []);
  assert.deepEqual(placesFromRoutes([{ ts: 1, from: "A", to: "B", line: [[95, 0], [0, 200]] }]), []);
});

test("minutes", () => {
  assert.equal(formatMinutes(540), "9 min");
  assert.equal(formatMinutes(5400), "1 h 30 min");
  assert.equal(formatMinutes(3600), "1 h 00 min");
});

test("the planned route in a line", () => {
  assert.equal(routeSummary({ active: false }, km), "");
  assert.equal(routeSummary(null, km), "");
  assert.equal(routeSummary({ active: true, name: "Home", remaining: 1100, eta: 300 }, km), "Home: 1.1 km, 5 min left");
  assert.equal(routeSummary({ active: true, name: "Home", remaining: 16093, eta: 5400 }, mi), "Home: 10 mi, 1 h 30 min left");
  assert.equal(routeSummary({ active: true, dest: { name: "Cafe" }, remaining: 800, eta: 30 }, km), "Cafe: 0.8 km, under a minute left");
  assert.equal(routeSummary({ active: true }, km), "Route set");
  assert.equal(routeSummary({ active: true, remaining: 5000 }, km), "5.0 km");
});

test("a place can be put first, to be driven to next", () => {
  const stops = [at(1000, 0), at(2000, 0)];
  const got = insertFirst(stops, { ...at(500, 0), name: "Z" });
  assert.equal(got.error, "");
  assert.deepEqual(got.stops.map((s) => s.name), ["Z", undefined, undefined]);
  assert.equal(stops.length, 2, "the list given is not changed");
  assert.match(insertFirst(got.stops, at(500, 5)).error, /already the next stop/);
  assert.match(insertFirst(stops, { lat: 99, lon: 0 }).error, /not a place/);
  const full = Array.from({ length: MAX_STOPS }, (_, i) => at(i * 500 + 5000, 0));
  assert.match(insertFirst(full, at(0, 0)).error, /11 stops/);
  assert.equal(insertFirst(full, at(0, 0)).stops.length, MAX_STOPS);
  assert.equal(insertFirst([], at(0, 0)).stops.length, 1);
});

test("a stop goes into the draft, or into what is left of the game's route when there is no draft or it was just sent", () => {
  const plan = { active: true, name: "Home", waypoints: [{ ...at(100, 0), name: "Cafe" }, at(200, 0)], dest: at(300, 0) };
  const draft = [at(900, 0)];
  assert.equal(routeToExtend(draft, false, plan), draft);
  for (const [given, sent] of [[[], false], [draft, true]]) {
    assert.deepEqual(routeToExtend(given, sent, plan).map((s) => s.name), ["Cafe", undefined, "Home"]);
  }
  assert.equal(routeToExtend(draft, true, { active: false }), draft);
  assert.equal(routeToExtend(draft, true, null), draft);
  assert.deepEqual(routeToExtend([], false, { active: true }), []); // (a route whose line has not come yet has no destination to keep)
});

test("distances to places nearby, in the person's unit", () => {
  assert.equal(distanceText(820, km), "0.8 km");
  assert.equal(distanceText(12400, km), "12 km");
  assert.equal(distanceText(1609.344, mi), "1.0 mi");
  assert.equal(distanceText(null, km), "");
});

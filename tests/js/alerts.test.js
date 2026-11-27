import test from "node:test";
import assert from "node:assert/strict";
import { ALERTS, ALERT_IDS, DEFAULT_LIMIT_MARGIN, HOLD_MS, alertSettings, createAlertTracker, evaluateAlerts } from "../../web/js/alerts.js";

const ids = (alerts) => alerts.map((a) => a.id);

import { OFF_ROUTE_M } from "../../web/js/widgets/route.js";

test("a healthy car raises no alerts, and nor does missing data", () => {
  assert.deepEqual(evaluateAlerts({ rpm: 900, lowfuel: false, "tire.FL.deflated": false, "state.engine.coolantOverheating": false }), []);
  assert.deepEqual(evaluateAlerts({}), []);
  assert.deepEqual(evaluateAlerts(null), []);
});

test("each rule follows the game's own flag", () => {
  const cases = [
    [{ lowfuel: true }, "fuel", "warn"],
    [{ "state.engine.coolantOverheating": true }, "overheat", "crit"],
    [{ "state.engine.oilOverheating": true }, "oilHot", "crit"],
    [{ "state.engine.starvedOfOil": true }, "oilLow", "crit"],
    [{ "state.engine.oilLevelCritical": true }, "oilLow", "crit"],
    [{ "state.engine.engineLockedUp": true }, "engineDamage", "crit"],
    [{ "state.engine.overRevDanger": true }, "overRev", "warn"],
    [{ highBraketemp: 1 }, "brakes", "warn"],
    [{ lowpressure: 1 }, "pressure", "warn"],
    [{ "tire.RR.deflated": true }, "flatTyre", "crit"],
  ];
  for (const [values, id, level] of cases) {
    const found = evaluateAlerts(values);
    assert.deepEqual(ids(found), [id], JSON.stringify(values));
    assert.equal(found[0].level, level, id);
  }
});

test("only a real true counts: strings, numbers and zero do not trip a flag", () => {
  assert.deepEqual(evaluateAlerts({ lowfuel: "true", "state.engine.coolantOverheating": 1, highBraketemp: 0, lowpressure: 0, "tire.FL.deflated": "yes" }), []);
});

test("flat tyres name the wheels, in wheel order, for cars with more than four", () => {
  const found = evaluateAlerts({ "tire.RR.deflated": true, "tire.FL.deflated": true, "tire.A1.deflated": true });
  assert.equal(found[0].text, "Flat tyre FL, RR, A1");
  assert.equal(evaluateAlerts({ "tire.FL.deflated": true, lowpressure: 1 }).filter((a) => a.id === "pressure").length, 0, "low pressure is not reported on top of a flat");
});

test("heavy and wrecked damage use the damage word, wrecked being critical", () => {
  assert.deepEqual(evaluateAlerts({ "damage.brokenPct": 2, "damage.deformedPct": 9 }), []); // moderate: no alert
  assert.deepEqual(evaluateAlerts({ "damage.brokenPct": 6 }).map((a) => [a.text, a.level]), [["Heavy damage", "warn"]]);
  assert.deepEqual(evaluateAlerts({ "damage.brokenPct": 23.6 }).map((a) => [a.text, a.level]), [["Car wrecked", "crit"]]);
});

test("critical alerts come first", () => {
  const found = evaluateAlerts({ lowfuel: true, "state.engine.coolantOverheating": true, highBraketemp: 1 });
  assert.deepEqual(ids(found), ["overheat", "fuel", "brakes"]);
});

test("settings: switching one off or all off", () => {
  const values = { lowfuel: true, "state.engine.coolantOverheating": true };
  assert.deepEqual(ids(evaluateAlerts(values, { enabled: true, off: ["fuel"] })), ["overheat"]);
  assert.deepEqual(evaluateAlerts(values, { enabled: false, off: [] }), []);
});

test("stored settings are cleaned: unknown ids dropped, defaults filled in", () => {
  const margin = DEFAULT_LIMIT_MARGIN;
  assert.deepEqual(alertSettings(null), { enabled: true, off: [], on: [], limitMargin: margin });
  assert.deepEqual(alertSettings({ enabled: false, off: ["fuel", "nonsense", 5] }), { enabled: false, off: ["fuel"], on: [], limitMargin: margin });
  assert.deepEqual(alertSettings({ off: "fuel" }), { enabled: true, off: [], on: [], limitMargin: margin });
  assert.deepEqual(alertSettings({ on: ["offRoute", "nonsense"] }).on, ["offRoute"]);
  assert.deepEqual(alertSettings({ on: "offRoute" }).on, []);
});

test("alert ids are unique and well formed (they are stored in settings)", () => {
  assert.equal(new Set(ALERT_IDS).size, ALERTS.length);
  for (const id of ALERT_IDS) assert.match(id, /^[A-Za-z][A-Za-z0-9_]{0,39}$/);
});

test("a rule that throws does not silence the others", () => {
  const original = ALERTS[0].test;
  ALERTS[0].test = () => { throw new Error("boom"); };
  try {
    assert.deepEqual(ids(evaluateAlerts({ lowfuel: true })), ["fuel"]);
  } finally {
    ALERTS[0].test = original;
  }
});

test("the tracker shows alerts at once and holds them briefly after they clear", () => {
  const tracker = createAlertTracker();
  const settings = { enabled: true, off: [] };
  assert.deepEqual(ids(tracker.update({ lowfuel: true }, settings, 0)), ["fuel"]);
  assert.deepEqual(ids(tracker.update({ lowfuel: false }, settings, 1000)), ["fuel"], "still held after the flag flickers off");
  assert.deepEqual(ids(tracker.update({ lowfuel: false }, settings, HOLD_MS + 1)), [], "gone once the hold has passed");
  tracker.update({ lowfuel: true }, settings, 5000);
  assert.deepEqual(ids(tracker.update({ lowfuel: true }, settings, 5000 + HOLD_MS + 10)), ["fuel"], "still true keeps it");
  assert.deepEqual(ids(tracker.update(null, settings, 5000 + HOLD_MS + 20)), ["fuel"], "no data: held, then it clears");
  assert.deepEqual(ids(tracker.update(null, settings, 5000 + 2 * HOLD_MS + 100)), []);
});

test("switching alerts off clears them at once, even ones being held over", () => {
  const tracker = createAlertTracker();
  const on = { enabled: true, off: [] };
  const values = { lowfuel: true, highBraketemp: 1 };
  assert.deepEqual(ids(tracker.update(values, on, 0)), ["fuel", "brakes"]);
  assert.deepEqual(ids(tracker.update(values, { enabled: true, off: ["fuel"] }, 100)), ["brakes"]);
  assert.deepEqual(ids(tracker.update(values, { enabled: false, off: [] }, 200)), []);
  assert.deepEqual(ids(tracker.update(values, on, 300)), ["fuel", "brakes"], "and they come back when switched on");
});

// ---- over the posted speed limit ------------------------------------------------------------------------------------------------
const road = (speed, limit, posted = true) => ({ wheelspeed: speed, "geo.limit": limit, "geo.limitPosted": posted });
const KMH = (v) => v / 3.6;

test("the limit alert needs a posted limit: a guess from the kind of road never alerts", () => {
  assert.deepEqual(ids(evaluateAlerts(road(KMH(80), KMH(50)))), ["overLimit"]);
  assert.deepEqual(evaluateAlerts(road(KMH(80), KMH(50), false)), []);
  assert.deepEqual(evaluateAlerts({ wheelspeed: KMH(80) }), []); // no limit known
  assert.deepEqual(evaluateAlerts({ "geo.limit": KMH(50), "geo.limitPosted": true }), []); // no speed
});

test("the limit alert waits for the margin: 5 km/h unless the user chose another", () => {
  assert.deepEqual(evaluateAlerts(road(KMH(54.9), KMH(50))), []);
  assert.deepEqual(ids(evaluateAlerts(road(KMH(55.5), KMH(50)))), ["overLimit"]);
  const strict = { enabled: true, off: [], limitMargin: 0 };
  assert.deepEqual(ids(evaluateAlerts(road(KMH(50.5), KMH(50)), strict)), ["overLimit"]);
  const lax = { enabled: true, off: [], limitMargin: KMH(20) };
  assert.deepEqual(evaluateAlerts(road(KMH(65), KMH(50)), lax), []);
  assert.deepEqual(ids(evaluateAlerts(road(KMH(75), KMH(50)), lax)), ["overLimit"]);
});

test("the limit alert can be switched off, on its own or with all alerts", () => {
  const values = road(KMH(80), KMH(50));
  assert.deepEqual(evaluateAlerts(values, { enabled: true, off: ["overLimit"], limitMargin: 0 }), []);
  assert.deepEqual(evaluateAlerts(values, { enabled: false, off: [], limitMargin: 0 }), []);
  // ...without touching the others
  assert.deepEqual(ids(evaluateAlerts({ ...values, lowfuel: true }, { enabled: true, off: ["overLimit"], limitMargin: 0 })), ["fuel"]);
});

test("the limit alert names the limit in the user's own speed unit, and reversing counts as speed too", () => {
  const found = evaluateAlerts(road(KMH(80), KMH(50)));
  assert.equal(found[0].text, "Over the speed limit (50 km/h)");
  assert.equal(found[0].level, "warn");
  const mph = evaluateAlerts(road(40, 22.352), DEFAULT_ALERT_SETTINGS_FOR_TEST, { speed: "mph" });
  assert.equal(mph[0].text, "Over the speed limit (50 mph)");
  assert.deepEqual(ids(evaluateAlerts(road(-KMH(80), KMH(50)))), ["overLimit"]);
});
const DEFAULT_ALERT_SETTINGS_FOR_TEST = alertSettings(null);

test("the limit alert shows only after the excess lasts two seconds, and starts again if it lapses", () => {
  const tracker = createAlertTracker();
  const settings = alertSettings(null);
  const over = road(KMH(80), KMH(50));
  const under = road(KMH(48), KMH(50));
  assert.deepEqual(tracker.update(over, settings, 0), []);
  assert.deepEqual(tracker.update(over, settings, 1500), []);
  assert.deepEqual(ids(tracker.update(over, settings, 2000)), ["overLimit"]);
  // a lapse then a new excess: held over, then gone, and the wait starts again
  assert.deepEqual(ids(tracker.update(under, settings, 3000)), ["overLimit"]); // held over briefly
  assert.deepEqual(tracker.update(under, settings, 3000 + HOLD_MS + 1), []);
  assert.deepEqual(tracker.update(over, settings, 10000), []);
  assert.deepEqual(tracker.update(under, settings, 11000), []); // dipped under: the wait is forgotten
  assert.deepEqual(tracker.update(over, settings, 11500), []);
  assert.deepEqual(tracker.update(over, settings, 13000), []); // only 1.5 s since the new excess began
  assert.deepEqual(ids(tracker.update(over, settings, 13500)), ["overLimit"]);
});

test("other alerts still show at once, and switching the limit alert off clears it at once", () => {
  const tracker = createAlertTracker();
  const settings = alertSettings(null);
  assert.deepEqual(ids(tracker.update({ lowfuel: true }, settings, 0)), ["fuel"]);
  const over = { ...road(KMH(80), KMH(50)), lowfuel: true };
  tracker.update(over, settings, 100);
  assert.deepEqual(ids(tracker.update(over, settings, 2200)), ["fuel", "overLimit"]); // (alphabetical by rule order: fuel is earlier)
  assert.deepEqual(ids(tracker.update(over, { ...settings, off: ["overLimit"] }, 2300)), ["fuel"]);
});

test("stale or missing data can never leave the limit alert showing", () => {
  const tracker = createAlertTracker();
  const settings = alertSettings(null);
  const over = road(KMH(80), KMH(50));
  tracker.update(over, settings, 0);
  tracker.update(over, settings, 2100);
  assert.deepEqual(tracker.update(null, settings, 2200 + HOLD_MS + 1), []);
});

const away = (off, extra = {}) => ({ "geo.route.active": true, "geo.route.off": off, ...extra });
const asked = alertSettings({ on: ["offRoute"] });

test("the off-route alert is off until it is switched on", () => {
  assert.deepEqual(evaluateAlerts(away(500), alertSettings(null)), []);
  assert.deepEqual(evaluateAlerts(away(500), alertSettings({ off: [] })), []);
  assert.deepEqual(ids(evaluateAlerts(away(500), asked)), ["offRoute"]);
  assert.deepEqual(evaluateAlerts(away(500), { ...asked, enabled: false }), []);
});

test("it needs a route and more than the route panel's distance", () => {
  assert.deepEqual(evaluateAlerts(away(OFF_ROUTE_M), asked), []);
  assert.equal(evaluateAlerts(away(OFF_ROUTE_M + 1), asked)[0].text, "Off the route");
  assert.deepEqual(evaluateAlerts({ "geo.route.active": false, "geo.route.off": 500 }, asked), []);
  assert.deepEqual(evaluateAlerts({ "geo.route.active": true }, asked), []);
});

test("it waits three seconds, stays while a new route is planned, and clears when back on one", () => {
  const tracker = createAlertTracker();
  assert.deepEqual(tracker.update(away(90), asked, 0), []);
  assert.deepEqual(tracker.update(away(90), asked, 2900), []);
  assert.deepEqual(ids(tracker.update(away(90), asked, 3000)), ["offRoute"]);
  assert.deepEqual(ids(tracker.update(away(130, { "geo.route.planning": true, "geo.route.replanning": true }), asked, 4000)), ["offRoute"]);
  assert.deepEqual(tracker.update(away(2), asked, 4000 + HOLD_MS + 1), []);
});

test("switching it off clears it at once", () => {
  const tracker = createAlertTracker();
  tracker.update(away(90), asked, 0);
  assert.deepEqual(ids(tracker.update(away(90), asked, 3000)), ["offRoute"]);
  assert.deepEqual(tracker.update(away(90), alertSettings(null), 3100), []);
});

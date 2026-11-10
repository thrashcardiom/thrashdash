import test from "node:test";
import assert from "node:assert/strict";
import { ALERTS, ALERT_IDS, HOLD_MS, alertSettings, createAlertTracker, evaluateAlerts } from "../../web/js/alerts.js";

const ids = (alerts) => alerts.map((a) => a.id);

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
  assert.deepEqual(alertSettings(null), { enabled: true, off: [] });
  assert.deepEqual(alertSettings({ enabled: false, off: ["fuel", "nonsense", 5] }), { enabled: false, off: ["fuel"] });
  assert.deepEqual(alertSettings({ off: "fuel" }), { enabled: true, off: [] });
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

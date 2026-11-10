import assert from "node:assert/strict";
import test from "node:test";

import { damageColor, severity, worstZone, ZONES } from "../../web/js/widgets/damage.js";
import { tyreLevel } from "../../web/js/widgets/tyres.js";
import { buildPrefs, resolveUnit } from "../../web/js/units.js";

const T = { cold: 50, warn: 100, crit: 120 };

test("tyre temperature bands: cold, ok, hot, too hot, and no data", () => {
  assert.equal(tyreLevel(20, T), "cold");
  assert.equal(tyreLevel(49.9, T), "cold");
  assert.equal(tyreLevel(50, T), "ok"); // the boundary belongs to the warmer band
  assert.equal(tyreLevel(99.9, T), "ok");
  assert.equal(tyreLevel(100, T), "warn");
  assert.equal(tyreLevel(119.9, T), "warn");
  assert.equal(tyreLevel(120, T), "crit");
  assert.equal(tyreLevel(400, T), "crit");
  for (const v of [null, undefined, NaN, Infinity, "80", {}]) assert.equal(tyreLevel(v, T), "missing", String(v));
});

test("tyre bands cope with missing thresholds", () => {
  assert.equal(tyreLevel(500, { cold: undefined, warn: undefined, crit: undefined }), "ok");
  assert.equal(tyreLevel(10, { cold: 50, warn: undefined, crit: undefined }), "cold");
  assert.equal(tyreLevel(110, { warn: 100 }), "warn");
});

test("damage colours: grey when undamaged, green to red, none without data", () => {
  assert.equal(damageColor(0), "var(--track)");
  assert.equal(damageColor(0.001), "var(--track)");
  assert.equal(damageColor(null), null);
  assert.equal(damageColor(NaN), null);
  assert.equal(damageColor("0.5"), null);
  const hue = (c) => Number(/hsl\((\d+)/.exec(c)[1]);
  assert.ok(hue(damageColor(0.02)) > 100, "slight damage is green");
  assert.ok(hue(damageColor(0.075)) > 40 && hue(damageColor(0.075)) < 80, "moderate damage is yellow");
  assert.ok(hue(damageColor(0.12)) < 30, "12% (a wrecked car in the real game) is nearly red");
  assert.equal(hue(damageColor(0.15)), 0); // fully red at the default threshold
  assert.ok(hue(damageColor(0.5, 0.5)) === 0 && hue(damageColor(0.25, 0.5)) > 40, "a custom threshold still works");
  assert.equal(hue(damageColor(1)), 0); // and stays red beyond it
  assert.ok(hue(damageColor(0.1)) > hue(damageColor(0.3)), "hue falls as damage grows");
  assert.equal(hue(damageColor(0.25, 1)) > hue(damageColor(0.25, 0.5)), true, "a higher full-red threshold is more forgiving");
});

test("worst zone is the maximum over the six zones that have data", () => {
  assert.equal(worstZone({}), null);
  assert.equal(worstZone({ "damage.zone.FL": 0.2, "damage.zone.RR": 0.7, "damage.zone.MR": 0.1 }), 0.7);
  assert.equal(worstZone({ "damage.zone.FL": 0, "damage.zone.FR": 0 }), 0);
  assert.equal(worstZone({ "damage.zone.FL": NaN, "damage.zone.FR": 0.3, "damage.zone.XX": 9 }), 0.3); // junk and unknown zones ignored
  assert.deepEqual(ZONES, ["FL", "FR", "ML", "MR", "RL", "RR"]);
});

test("pressure units: the mod sends gauge kPa, shown as psi by default", () => {
  const psi = resolveUnit("pressure", buildPrefs({}, ""));
  assert.equal(psi.label, "psi");
  assert.ok(Math.abs(psi.convert(220) - 31.908) < 0.01, "220 kPa is about 31.9 psi");
  const bar = resolveUnit("pressure", buildPrefs({ pressure: "bar" }, ""));
  assert.equal(bar.convert(220), 2.2);
  const kpa = resolveUnit("pressure", buildPrefs({ pressure: "kpa" }, ""));
  assert.equal(kpa.convert(220), 220);
  assert.equal(buildPrefs({ pressure: "furlongs" }, "").pressure, "psi"); // junk falls back
});

test("the damage word follows the share of broken beams, using readings from the real game", () => {
  const car = (broken, deformed) => ({ "damage.brokenPct": broken, "damage.deformedPct": deformed });
  assert.equal(severity({}), null);
  assert.equal(severity({ "damage.brokenPct": NaN }), null);
  assert.deepEqual(severity(car(0, 0)), { word: "Undamaged", detail: "" });
  assert.deepEqual(severity(car(0, 5.725)), { word: "Light", detail: "5.7% dented" }); // a scrape: nothing broken, a few dents
  assert.equal(severity(car(0, 0.4)).word, "Undamaged"); // measurement noise is not damage
  assert.equal(severity(car(0.5, 6)).word, "Light");
  assert.equal(severity(car(2, 8)).word, "Moderate");
  assert.equal(severity(car(6, 12)).word, "Heavy");
  assert.deepEqual(severity(car(23.644, 30)), { word: "Wrecked", detail: "24% broken" }); // the wreck
  assert.equal(severity(car(23.644, 30), 30).word, "Heavy"); // the threshold is adjustable
  assert.equal(severity({ "damage.brokenPct": 3 }).word, "Moderate"); // no deformed channel is fine
});

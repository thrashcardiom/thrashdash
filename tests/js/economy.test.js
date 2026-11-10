import test from "node:test";
import assert from "node:assert/strict";
import { formatConsumption, formatDuration, formatEnergy, resolveEconomy } from "../../web/js/economy.js";
import { sizeFormat } from "../../web/js/widgets/trip.js";
import { buildPrefs, UNIT_OPTIONS } from "../../web/js/units.js";

const near = (a, b, eps = 0.01) => Math.abs(a - b) < eps;

test("auto follows the distance unit: kilometres give L/100 km, miles give mpg", () => {
  assert.equal(resolveEconomy({ economy: "auto", distance: "km" }), "l100");
  assert.equal(resolveEconomy({ economy: "auto", distance: "mi" }), "mpgus");
  assert.equal(resolveEconomy({ economy: "kmpl", distance: "mi" }), "kmpl", "an explicit choice wins");
  assert.equal(resolveEconomy({}), "l100");
});

test("petrol consumption in each unit (8 L/100 km)", () => {
  assert.deepEqual(formatConsumption(8, "liquid", "l100"), { value: 8, unit: "L/100 km" });
  assert.ok(near(formatConsumption(8, "liquid", "kmpl").value, 12.5));
  assert.ok(near(formatConsumption(8, "liquid", "mpgus").value, 29.40, 0.02), "8 L/100 km is about 29.4 US mpg");
  assert.ok(near(formatConsumption(8, "liquid", "mpguk").value, 35.31, 0.02), "and about 35.3 UK mpg");
  assert.equal(formatConsumption(8, "liquid", "mpgus").unit, "mpg");
});

test("electric consumption in each unit (20 kWh/100 km)", () => {
  assert.deepEqual(formatConsumption(20, "electric", "l100"), { value: 20, unit: "kWh/100 km" });
  assert.ok(near(formatConsumption(20, "electric", "kmpl").value, 5));
  assert.ok(near(formatConsumption(20, "electric", "mpgus").value, 3.11, 0.01), "20 kWh/100 km is about 3.1 mi/kWh");
  assert.equal(formatConsumption(20, "electric", "mpguk").unit, "mi/kWh");
});

test("no consumption figure for a hybrid, an unknown car, or nonsense", () => {
  assert.equal(formatConsumption(8, "mixed", "l100"), null);
  assert.equal(formatConsumption(8, "unknown", "l100"), null);
  for (const bad of [0, -1, NaN, Infinity, undefined, null, "8"]) assert.equal(formatConsumption(bad, "liquid", "l100"), null);
});

test("energy used: litres, gallons for mpg, kWh for a battery", () => {
  assert.deepEqual(formatEnergy(3.5, "liquid", "l100"), { value: 3.5, unit: "L" });
  assert.deepEqual(formatEnergy(3.5, "liquid", "kmpl"), { value: 3.5, unit: "L" });
  assert.ok(near(formatEnergy(3.785411784, "liquid", "mpgus").value, 1, 1e-6));
  assert.equal(formatEnergy(3.5, "liquid", "mpguk").unit, "gal UK");
  assert.deepEqual(formatEnergy(12, "electric", "mpgus"), { value: 12, unit: "kWh" });
  assert.equal(formatEnergy(3, "mixed", "l100"), null);
  assert.equal(formatEnergy(NaN, "liquid", "l100"), null);
});

test("durations read as h:mm:ss", () => {
  assert.equal(formatDuration(0), "0:00:00");
  assert.equal(formatDuration(65.9), "0:01:05");
  assert.equal(formatDuration(3725), "1:02:05");
  assert.equal(formatDuration(36000), "10:00:00");
  for (const bad of [undefined, null, NaN, -1, "5"]) assert.equal(formatDuration(bad), "--");
});

test("the economy unit is a saved setting like the others", () => {
  assert.equal(buildPrefs({}).economy, "auto");
  assert.equal(buildPrefs({ economy: "kmpl" }).economy, "kmpl");
  assert.equal(buildPrefs({ economy: "furlongs" }).economy, "auto", "unknown choices are ignored");
  assert.deepEqual(UNIT_OPTIONS.economy.map((u) => u.key), ["auto", "l100", "kmpl", "mpgus", "mpguk"]);
});

test("trip figures get a sensible number of decimals for their size", () => {
  assert.equal(sizeFormat(0.4213), "0.42");
  assert.equal(sizeFormat(4.2), "4.20");
  assert.equal(sizeFormat(42.37), "42.4");
  assert.equal(sizeFormat(420.6), "421");
  assert.equal(sizeFormat(NaN), "--");
  assert.equal(sizeFormat(undefined), "--");
});

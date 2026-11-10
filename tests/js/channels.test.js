import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { classifyChannel, describeChannel, formatLive, groupChannels, humanize, suggestKind } from "../../web/js/editor/channels.js";

const captured = Object.keys(JSON.parse(readFileSync("docs/phase0-capture-truck.json", "utf8")).flat_first_frame);

test("curated channels get friendly names and unit hints", () => {
  assert.equal(describeChannel("rpm"), "Engine RPM");
  assert.equal(suggestKind("wheelspeed"), "speed");
  assert.equal(suggestKind("watertemp"), "temp");
  assert.equal(suggestKind("fuel"), "fraction");
  assert.equal(suggestKind("odometer"), "distance");
  assert.equal(describeChannel("wheelThermals.FL.brakeCoreTemperature"), "Brake core temp FL");
  assert.equal(suggestKind("wheelThermals.RR.brakeThermalEfficiency"), "fraction");
  assert.equal(suggestKind("wheelThermals.RR.brakeSurfaceTemperature"), "temp");
});

test("unknown channels have no label or hint, and prototype names are not treated as known", () => {
  for (const n of ["mystery", "constructor", "__proto__", "toString", "hasOwnProperty"]) {
    assert.equal(describeChannel(n), null, n);
    assert.equal(suggestKind(n), null, n);
    assert.notEqual(classifyChannel(n), "common", n);
  }
});

test("noise channels are classified as minor, real ones are not", () => {
  for (const n of ["hoodLatchCoupler_attached", "door_FL_coupler_notAttached", "sunvisor_L", "signal_L_filament", "dualAxisX", "nop", "brakeGlow_FL", "dseWarningPulse"]) {
    assert.equal(classifyChannel(n), "minor", n);
  }
  for (const n of ["axle_FL", "mode4WD", "exhaustFlow", "propshaft"]) assert.equal(classifyChannel(n), "car", n);
  assert.equal(classifyChannel("rpm"), "common");
});

test("grouping the real captured truck channels: every core dashboard channel is in 'common'", () => {
  const channels = Object.fromEntries(captured.map((n) => [n, "number"]));
  const g = groupChannels(channels);
  const names = (list) => list.map((e) => e.name);
  for (const must of ["rpm", "wheelspeed", "gear", "fuel", "watertemp", "oiltemp", "signal_L", "parkingbrake", "throttle"]) {
    assert.ok(names(g.common).includes(must), `${must} should be common`);
  }
  assert.equal(Object.values(g).reduce((n, list) => n + list.length, 0), captured.length); // nothing lost
  assert.ok(g.minor.length > 10 && g.minor.length < captured.length / 2, `minor group size ${g.minor.length}`);
  assert.ok(names(g.car).includes("axle_FL")); // truck-specific drivetrain channels are offered
});

test("search matches raw names and friendly labels, case-insensitively", () => {
  const channels = { rpm: "number", watertemp: "number", fuel: "number", oiltemp: "number" };
  assert.deepEqual(groupChannels(channels, "COOLANT").common.map((e) => e.name), ["watertemp"]);
  assert.deepEqual(groupChannels(channels, "temp").common.map((e) => e.name).sort(), ["oiltemp", "watertemp"]);
  assert.deepEqual(groupChannels(channels, "  rpm ").common.map((e) => e.name), ["rpm"]);
  assert.equal(groupChannels(channels, "zzz").common.length, 0);
});

test("live value formatting", () => {
  assert.equal(formatLive(3000), "3000");
  assert.equal(formatLive(0.123456), "0.123");
  assert.equal(formatLive(true), "true");
  assert.equal(formatLive("N"), '"N"');
  assert.equal(formatLive(null), "n/a");
  assert.equal(formatLive(undefined), "");
  assert.equal(formatLive("x".repeat(100)).length, 26);
});

test("an exact channel-name match ranks first, so Enter picks what you typed", () => {
  const channels = { maxrpm: "number", idlerpm: "number", rpm: "number", rpmTacho: "number" };
  assert.deepEqual(groupChannels(channels, "rpm").common.map((e) => e.name)[0], "rpm");
  assert.equal(groupChannels(channels, "RPM").common[0].name, "rpm"); // case-insensitive
  // With no query, plain alphabetical by friendly label: "Engine max RPM", "Engine RPM", "Idle RPM".
  assert.deepEqual(groupChannels(channels, "").common.map((e) => e.name), ["maxrpm", "rpm", "idlerpm"]);
});

test("tyre channels get friendly names, unit hints and their own group", () => {
  assert.equal(describeChannel("tire.FL.temp"), "Tyre temperature FL");
  assert.equal(describeChannel("tire.RR.coreTemp"), "Tyre core temperature RR");
  assert.equal(describeChannel("tire.FR.pressure"), "Tyre pressure FR");
  assert.equal(describeChannel("tire.FR.deflated"), "Tyre flat FR");
  assert.equal(describeChannel("tire.W7.load"), "Tyre load W7"); // wheels on trucks and trailers have other names
  assert.equal(suggestKind("tire.FL.temp"), "temp");
  assert.equal(suggestKind("tire.FL.coreTemp"), "temp");
  assert.equal(suggestKind("tire.FL.pressure"), "pressure");
  assert.equal(suggestKind("tire.FL.deflated"), "none");
  assert.equal(classifyChannel("tire.FL.temp"), "tires");
  assert.equal(describeChannel("tire.FL.bogus"), null);
});

test("damage channels: zone names, percentages and unit hints", () => {
  assert.equal(describeChannel("damage.zone.FL"), "Body damage: front left");
  assert.equal(describeChannel("damage.zone.MR"), "Body damage: middle right");
  assert.equal(describeChannel("damage.zone.RR"), "Body damage: rear right");
  assert.equal(describeChannel("damage.brokenPct"), "Broken beams (%)");
  assert.equal(describeChannel("damage.total"), "Damage (total)");
  assert.equal(suggestKind("damage.zone.FL"), "fraction");
  assert.equal(suggestKind("damage.brokenPct"), null);
  assert.equal(classifyChannel("damage.zone.FL"), "damage");
  assert.equal(describeChannel("damage.zone.XX"), null);
});

test("component states get readable names", () => {
  assert.equal(humanize("oilOverheating"), "oil overheating");
  assert.equal(humanize("brakeFL"), "brake FL");
  assert.equal(humanize("engineIsHydrolocking"), "engine is hydrolocking");
  assert.equal(humanize("some_snake_name"), "some snake name");
  assert.equal(describeChannel("state.engine.oilOverheating"), "Engine: oil overheating");
  assert.equal(describeChannel("state.wheels.brakeOverHeatFL"), "Wheels: brake over heat FL");
  assert.equal(describeChannel("state.energyStorage.leak"), "Fuel/energy: leak");
  assert.equal(describeChannel("state.someNewGroup.thing"), "some new group: thing"); // an unknown group still reads fine
  assert.equal(classifyChannel("state.engine.oilOverheating"), "states");
});

test("the new groups sort and filter like the others", () => {
  const channels = {
    "tire.FL.temp": "number", "tire.FR.temp": "number", "tire.FL.pressure": "number", "damage.zone.FL": "number",
    "state.engine.oilOverheating": "boolean", rpm: "number", axle_FL: "number",
  };
  const g = groupChannels(channels);
  assert.deepEqual(g.tires.map((e) => e.name), ["tire.FL.pressure", "tire.FL.temp", "tire.FR.temp"]); // by label: pressure before temperature
  assert.deepEqual([g.damage.length, g.states.length, g.common.length, g.car.length], [1, 1, 1, 1]);
  assert.equal(groupChannels(channels, "tyre temp").tires.length, 2); // a substring of "Tyre temperature ..." matches
  assert.deepEqual(groupChannels(channels, "temperature").tires.map((e) => e.name), ["tire.FL.temp", "tire.FR.temp"]);
  assert.deepEqual(groupChannels(channels, "oil").states.map((e) => e.name), ["state.engine.oilOverheating"]);
});

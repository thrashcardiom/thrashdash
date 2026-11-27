import assert from "node:assert/strict";
import test from "node:test";

import { STATS_COLUMNS, csvField, csvText, statsCsv, statsCsvName, statsRow } from "../../web/js/trips/csv.js";
import { costPer100, lifetimeStats, moneyText, statTiles, tripCost } from "../../web/js/trips/model.js";
import { buildPrefs } from "../../web/js/units.js";

const PRICE = { liquid: 2.5, electric: 0.3, currency: "$" };
const trip = (extra = {}, fuel = { unit: "L", used: 10, per_100km: 8 }, dist = 125000) => ({
  id: "20261101T000000Z", ts: 1793500000, vehicle: "etkc", title: null, tags: [], starred: false, price: PRICE, header: { energy: "liquid" },
  stats: { name: "A to B", dist, dur: 5000, moving_s: 4800, stopped_s: 200, avg_ms: 25, avg_moving_ms: 26, top_ms: 40, fuel, countries: ["New Zealand"], places: ["Wellington"] }, ...extra,
});

test("money is written with a symbol in front or a code after", () => {
  assert.equal(moneyText(12.5, "$"), "$12.50");
  assert.equal(moneyText(12.5, "EUR"), "12.50 EUR");
  assert.equal(moneyText(12.5, "NZ$"), "NZ$12.50");
  assert.equal(moneyText(12.5, ""), "12.50");
  assert.equal(moneyText(0.005, "$"), "$0.01");
  assert.equal(moneyText(1234.5, "£"), "£1234.50");
});

test("a trip costs what it used times the price saved with it", () => {
  assert.deepEqual(tripCost(trip()), { amount: 25, currency: "$", rate: 2.5, unit: "L" });
  const electric = trip({}, { unit: "kWh", used: 20 });
  assert.deepEqual(tripCost(electric), { amount: 6, currency: "$", rate: 0.3, unit: "kWh" });
});

test("a trip with no price, no fuel, or no price for its kind of energy has no cost: nothing is guessed", () => {
  assert.equal(tripCost(trip({ price: null })), null);
  assert.equal(tripCost(trip({ price: undefined })), null);
  assert.equal(tripCost(trip({}, null)), null);
  assert.equal(tripCost(trip({}, { unit: "L", used: 0 })), null);
  assert.equal(tripCost(trip({ price: { liquid: 0, electric: 0.3, currency: "$" } })), null); // a petrol trip, only an electricity price
  assert.equal(tripCost(trip({}, { unit: "kWh", used: 5 }, 1000), null) === null, false); // (sanity: the helper takes the price from the trip)
  assert.equal(tripCost(null), null);
});

test("the cost per 100 km (or miles), for a trip long enough to mean something", () => {
  const cost = tripCost(trip());
  assert.deepEqual(costPer100(cost, 125000, buildPrefs({}, "")), { amount: 20, per: "100 km" });
  const miles = costPer100(cost, 160934.4, buildPrefs({ distance: "mi" }, ""));
  assert.equal(miles.per, "100 mi");
  assert.ok(Math.abs(miles.amount - 25) < 1e-9);
  assert.equal(costPer100(cost, 400, buildPrefs({}, "")), null);
  assert.equal(costPer100(null, 125000, buildPrefs({}, "")), null);
});

test("a trip's tiles include its cost, only when it has one", () => {
  const prefs = buildPrefs({}, "");
  const tile = statTiles(trip(), prefs, { damage: 1, g: 4 }).find((t) => t.key === "cost");
  assert.deepEqual([tile.label, tile.value, tile.sub], ["Fuel cost", "$25.00", "$20.00 per 100 km"]);
  assert.equal(statTiles(trip({ price: null }), prefs, { damage: 1, g: 4 }).some((t) => t.key === "cost"), false);
  const ev = statTiles(trip({ header: { energy: "electric" } }, { unit: "kWh", used: 20 }), prefs, { damage: 1, g: 4 }).find((t) => t.key === "cost");
  assert.equal(ev.label, "Energy cost");
});

test("the Stats totals add up what the priced trips cost, by currency", () => {
  const a = trip({ id: "a" });
  const b = trip({ id: "b", price: { liquid: 3, electric: 0, currency: "$" } });
  const c = trip({ id: "c", price: null });
  const d = trip({ id: "d", price: { liquid: 2, electric: 0, currency: "EUR" } });
  const e = trip({ id: "e" }, { unit: "L", used: 1 }, 100); // too short: left out, as it is from the fuel total
  const s = lifetimeStats([a, b, c, d, e], { damage: 1, g: 4 });
  assert.deepEqual(s.cost.byCurrency.map((x) => [x.currency, x.amount, x.trips]), [["$", 55, 2], ["EUR", 20, 1]]);
  assert.deepEqual([s.cost.priced, s.cost.fuelTrips], [3, 4]);
  assert.deepEqual(lifetimeStats([c], { damage: 1, g: 4 }).cost.byCurrency, []);
  assert.deepEqual(lifetimeStats([], { damage: 1, g: 4 }).cost, { byCurrency: [], priced: 0, fuelTrips: 0 });
});

// ---- the CSV --------------------------------------------------------------------------------------------------------------------

const RULE = { damage: 1, g: 4 };
const cells = (t) => Object.fromEntries(STATS_COLUMNS.map((c, i) => [c, statsRow(t, RULE)[i]]));

test("text cells cannot run as formulas, and fields are quoted only when they must be", () => {
  assert.equal(csvText("=HYPERLINK(1)"), "'=HYPERLINK(1)");
  for (const lead of ["+1", "-1", "@SUM(A1)"]) assert.ok(csvText(lead).startsWith("'"), lead);
  assert.equal(csvText("Coast run"), "Coast run");
  assert.equal(csvText("a\u0000b\nc"), "a b c");
  assert.equal(csvText(undefined), "");
  assert.equal(csvField("plain"), "plain");
  assert.equal(csvField("a,b"), '"a,b"');
  assert.equal(csvField('say "hi"'), '"say ""hi"""');
  assert.equal(csvField("two\nlines"), '"two\nlines"');
});

test("a trip becomes one row, in SI units, with what it cost", () => {
  const row = cells(trip({ title: "Coast run", tags: ["Track", "Hill climb"], starred: true, reason: "idle" }));
  assert.equal(row.id, "20261101T000000Z");
  assert.equal(row.start_utc, "2026-11-01T02:26:40Z"); // (1793500000 seconds after 1970, in UTC, whole seconds)
  assert.deepEqual([row.name, row.vehicle, row.tags, row.starred, row.imported, row.ended], ["Coast run", "etkc", "Track; Hill climb", "true", "false", "idle"]);
  assert.deepEqual([row.distance_m, row.duration_s, row.moving_s, row.stopped_s, row.top_speed_ms], ["125000.0", "5000.0", "4800.0", "200.0", "40.00"]);
  assert.deepEqual([row.energy_used, row.energy_unit, row.energy_per_100km], ["10.000", "L", "8.00"]);
  assert.deepEqual([row.price_per_unit, row.cost, row.currency], ["2.5", "25.00", "$"]);
  assert.deepEqual([row.countries, row.places], ["New Zealand", "Wellington"]);
});

test("a figure a trip does not have is an empty cell", () => {
  const row = cells(trip({ price: null }, null));
  for (const key of ["energy_used", "energy_unit", "energy_per_100km", "price_per_unit", "cost", "currency", "peak_g", "impacts", "body_damage_end_pct", "refuels", "altitude_gain_m"]) assert.equal(row[key], "", key);
  assert.equal(statsRow({ id: "x", ts: 0 }, RULE).length, STATS_COLUMNS.length); // (even a bare item gives a whole row)
});

test("impacts are counted by the rule in force", () => {
  const t = trip();
  t.stats.knocks = [{ rise: 3 }, { rise: 0.2, g: 5 }, { rise: 0.2, g: 1 }];
  assert.equal(cells(t).impacts, "2");
});

test("the file is every finished trip, oldest first, with a header row, UTF-8 marked and CRLF", () => {
  const text = statsCsv([trip({ id: "b", ts: 200, title: "Taupō, loop" }), trip({ id: "a", ts: 100 }), trip({ id: "live", ts: 300, stats: undefined }), trip({ id: "rec", ts: 400, recording: true })], RULE);
  assert.ok(text.startsWith("﻿id,start_utc,name,"));
  const lines = text.split("\r\n");
  assert.equal(lines.length, 4); // header, two trips, and the empty piece after the last line break
  assert.equal(lines[3], "");
  assert.ok(lines[1].startsWith("a,"));
  assert.ok(lines[2].startsWith("b,"));
  assert.ok(lines[2].includes('"Taupō, loop"'));
});

test("a file with no trips is just the header, and the file name carries the date", () => {
  assert.equal(statsCsv([], RULE), `﻿${STATS_COLUMNS.join(",")}\r\n`);
  assert.equal(statsCsvName(new Date(2026, 10, 5)), "thrashdash-trips-2026-11-05.csv");
});

// Run from the repo root:  node --test tests/js
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { LayoutError, MAX_WIDGETS, newWidget, sanitizeLayout, sanitizeValue } from "../../web/js/layoutSchema.js";
import { buildPrefs, pick, resolveUnit } from "../../web/js/units.js";
import { formatGear } from "../../web/js/widgets/gear.js";
import { specs } from "../../web/js/widgets/index.js";
import { isOn } from "../../web/js/widgets/light.js";

const base = (widgets) => ({ format: 1, name: "t", width: 1000, height: 400, widgets });
const gauge = (extra = {}) => ({ type: "roundGauge", x: 10, y: 10, w: 200, h: 200, channel: "rpm", ...extra });

test("the shipped default layout is accepted without a single warning", () => {
  const raw = JSON.parse(readFileSync("web/layouts/default.json", "utf8"));
  const { layout, warnings } = sanitizeLayout(raw, specs);
  assert.deepEqual(warnings, []);
  assert.equal(layout.widgets.length, raw.widgets.length);
  assert.deepEqual(layout.widgets[0], raw.widgets[0]); // nothing silently rewritten
});

test("unusable layouts are rejected as a whole", () => {
  for (const bad of [null, "x", 5, [], { format: 99, widgets: [] }, { widgets: [] }]) {
    assert.throws(() => sanitizeLayout(bad, specs), LayoutError);
  }
  assert.throws(() => sanitizeLayout({ format: 2 }, specs), /newer ThrashDash/);
});

test("unknown and prototype-ish widget types are dropped", () => {
  const types = ["nope", "__proto__", "constructor", "toString", "hasOwnProperty", 5, null];
  const { layout, warnings } = sanitizeLayout(base(types.map((t) => ({ type: t, x: 0, y: 0, w: 50, h: 50 }))), specs);
  assert.equal(layout.widgets.length, 0);
  assert.equal(warnings.length, types.length);
});

test("unknown properties are dropped and __proto__ cannot pollute", () => {
  const raw = JSON.parse('{"format":1,"width":900,"height":300,"widgets":[{"type":"gear","x":1,"y":1,"w":100,"h":100,"channel":"gear","evil":"x","__proto__":{"polluted":true},"constructor":"x"}]}');
  const { layout, warnings } = sanitizeLayout(raw, specs);
  const w = layout.widgets[0];
  assert.equal(w.evil, undefined);
  assert.equal(Object.hasOwn(w, "__proto__"), false);
  assert.equal({}.polluted, undefined);
  assert.equal(w.constructor, Object); // untouched inherited constructor, not the string "x"
  assert.ok(warnings.some((m) => m.includes("evil")));
});

test("colours: hex and theme variables only", () => {
  const ok = ["#fff", "#ffff", "#aabbcc", "#aabbccdd", "var(--ok)", "var(--accent)"];
  const bad = ["red", "rgb(1,2,3)", "url(http://x)", "#12345", "var(--evil)", "var(--ok);x", "javascript:alert(1)", "", 5];
  for (const c of ok) assert.equal(sanitizeValue(c, { type: "color" }).ok, true, c);
  for (const c of bad) assert.equal(sanitizeValue(c, { type: "color" }).ok, false, String(c));
});

test("numbers: clamped, rounded, and non-finite rejected", () => {
  const ps = { type: "number", min: 0, max: 4, int: true };
  assert.deepEqual(sanitizeValue(2.6, ps), { ok: true, value: 3 });
  assert.equal(sanitizeValue(99, ps).value, 4);
  assert.ok(sanitizeValue(99, ps).note);
  for (const v of [NaN, Infinity, "3", null, undefined, {}, true]) assert.equal(sanitizeValue(v, ps).ok, false, String(v));
});

test("per-unit numbers", () => {
  const ps = { type: "unitNumber" };
  assert.deepEqual(sanitizeValue({ kmh: 240, mph: 150 }, ps).value, { kmh: 240, mph: 150 });
  assert.equal(sanitizeValue(5, ps).value, 5);
  for (const v of [{ furlongs: 1 }, { kmh: "x" }, {}, [], "5", { kmh: NaN }]) assert.equal(sanitizeValue(v, ps).ok, false, JSON.stringify(v));
});

test("text is stripped of control characters and truncated", () => {
  assert.equal(sanitizeValue("a\u0000b\nc", { type: "text", max: 10 }).value, "abc");
  assert.equal(sanitizeValue("x".repeat(500), { type: "text", max: 10 }).value.length, 10);
  assert.equal(sanitizeValue("<img src=x onerror=1>", { type: "text", max: 50 }).value, "<img src=x onerror=1>"); // kept as inert text; rendered via textContent
});

test("channel names and channel lists", () => {
  assert.equal(sanitizeValue("wheelThermals.FL.brakeCoreTemperature", { type: "channel" }).ok, true);
  for (const c of ["has space", "a/b", "<script>", "", "x".repeat(101), 5]) assert.equal(sanitizeValue(c, { type: "channel" }).ok, false, String(c));
  assert.equal(sanitizeValue(["a", "b"], { type: "channels" }).ok, true);
  assert.equal(sanitizeValue(["a", "b c"], { type: "channels" }).ok, false);
  assert.equal(sanitizeValue(Array(13).fill("a"), { type: "channels" }).ok, false);
});

test("select rejects values outside options", () => {
  const ps = { type: "select", options: ["a", "b"] };
  assert.equal(sanitizeValue("a", ps).ok, true);
  assert.equal(sanitizeValue("c", ps).ok, false);
  assert.equal(sanitizeValue("__proto__", ps).ok, false);
});

test("widget ids: duplicates and invalid ids are replaced with unique ones", () => {
  const { layout } = sanitizeLayout(base([gauge({ id: "a" }), gauge({ id: "a" }), gauge({ id: "bad id!" }), gauge()]), specs);
  const ids = layout.widgets.map((w) => w.id);
  assert.equal(new Set(ids).size, 4);
  assert.equal(ids[0], "a");
  for (const id of ids) assert.match(id, /^[A-Za-z0-9_-]{1,40}$/);
});

test("geometry is clamped and missing geometry falls back to the widget's default size", () => {
  const { layout, warnings } = sanitizeLayout(base([{ type: "gear", x: 99999, y: -99999, w: 0, h: "big" }]), specs);
  const w = layout.widgets[0];
  assert.equal(w.x, 8000);
  assert.equal(w.y, -1000);
  assert.equal(w.w, 8); // present but tiny: clamped to the minimum
  assert.equal(w.h, specs.gear.size.h); // present but not a number: default
  assert.ok(warnings.length >= 1);
});

test("widget count is capped", () => {
  const many = Array.from({ length: MAX_WIDGETS + 50 }, () => gauge());
  const { layout, warnings } = sanitizeLayout(base(many), specs);
  assert.equal(layout.widgets.length, MAX_WIDGETS);
  assert.ok(warnings.some((m) => m.includes("extra")));
});

test("canvas size defaults and clamps", () => {
  const { layout, warnings } = sanitizeLayout({ format: 1, widgets: [] }, specs);
  assert.deepEqual([layout.width, layout.height, layout.name], [1200, 420, "Untitled"]);
  assert.ok(warnings.length);
  assert.equal(sanitizeLayout({ format: 1, width: 99999, height: 1, widgets: [] }, specs).layout.width, 4000);
});

test("newWidget: defaults, unique ids, and defaults are copies", () => {
  const a = newWidget("roundGauge", specs, []);
  const b = newWidget("roundGauge", specs, [a]);
  assert.notEqual(a.id, b.id);
  assert.equal(a.w, specs.roundGauge.size.w);
  a.max = 999;
  assert.equal(specs.roundGauge.defaults.max, 100); // spec untouched
  assert.throws(() => newWidget("nope", specs, []), LayoutError);
});

test("every spec describes only property types the sanitiser understands", () => {
  const known = new Set(["channel", "channels", "number", "unitNumber", "text", "textList", "color", "select", "bool"]);
  for (const [type, spec] of Object.entries(specs)) {
    assert.ok(spec.label && spec.size?.w && spec.size?.h, type);
    for (const [name, ps] of Object.entries(spec.props)) {
      assert.ok(known.has(ps.type), `${type}.${name} has unknown type ${ps.type}`);
      assert.ok(ps.label, `${type}.${name} needs a label for the editor`);
    }
    // defaults must themselves be valid, or a freshly added widget would be "repaired" on reload
    const w = newWidget(type, specs, []);
    const { warnings } = sanitizeLayout(base([w]), specs);
    assert.deepEqual(warnings, [], `${type} defaults produce warnings`);
  }
});

test("gear formatting handles strings (automatic) and numbers (manual)", () => {
  assert.equal(formatGear("N", 0), "N");
  assert.equal(formatGear("D", 3), "D");
  assert.equal(formatGear(1, 1), "1");
  assert.equal(formatGear(0), "N");
  assert.equal(formatGear(-1), "R");
  assert.equal(formatGear(undefined, 4), "4");
  assert.equal(formatGear(undefined, undefined), null);
  assert.equal(formatGear("", 2), null);
});

test("indicator truthiness", () => {
  for (const v of [true, 1, 0.9, "1", "on"]) assert.equal(isOn(v), true, String(v));
  for (const v of [false, 0, 0.5, "", "0", null, undefined, {}]) assert.equal(isOn(v), false, String(v));
});

test("unit preferences: saved settings, then URL, invalid values ignored", () => {
  assert.equal(buildPrefs({}, "").speed, "kmh");
  assert.equal(buildPrefs({ speed: "mph" }, "").speed, "mph");
  assert.equal(buildPrefs({ speed: "mph" }, "?speed=kmh").speed, "kmh");
  assert.equal(buildPrefs({ speed: "furlongs" }, "?temp=kelvin").speed, "kmh");
  assert.equal(buildPrefs({ speed: "__proto__" }, "").speed, "kmh");
  const u = resolveUnit("speed", buildPrefs({ speed: "mph" }));
  assert.equal(u.label, "mph");
  assert.ok(Math.abs(u.convert(10) - 22.369) < 0.01);
  assert.equal(pick({ kmh: 240, mph: 150 }, "mph", 0), 150);
  assert.equal(pick(undefined, "mph", 7), 7);
});

test("textList: short labels, gaps allowed, limits enforced, control characters stripped", () => {
  const ps = { type: "textList", maxItems: 4, max: 3 };
  assert.deepEqual(sanitizeValue(["E", "", "F"], ps).value, ["E", "", "F"]);
  assert.deepEqual(sanitizeValue(["abcdef", "a\u0000b"], ps).value, ["abc", "ab"]); // truncated, control chars removed
  for (const v of ["E,F", 5, null, [1, 2], ["a", {}], ["a", "b", "c", "d", "e"]]) assert.equal(sanitizeValue(v, ps).ok, false, JSON.stringify(v));
});

test("a skin is not part of a layout: a stray skin key in a layout file is ignored", () => {
  const base = { format: 1, name: "x", width: 1000, height: 400, widgets: [] };
  for (const skin of ["backlit", "banana", "__proto__", 5]) {
    const { layout, warnings } = sanitizeLayout({ ...base, skin }, specs);
    assert.equal("skin" in layout, false);
    assert.deepEqual(warnings, []);
  }
});

test("every skin-only / clean-only flag sits on a real property, and no property is both", () => {
  for (const [type, spec] of Object.entries(specs)) {
    for (const [name, ps] of Object.entries(spec.props)) {
      assert.ok(!(ps.skinOnly && ps.cleanOnly), `${type}.${name} is flagged both ways`);
    }
  }
  // The properties that only mean something on skinned dials really are flagged (so the panel can hide them on clean ones).
  for (const name of ["startAngle", "endAngle", "labels", "icon", "sub", "subChannel", "shiftFrom", "compact"]) assert.ok(specs.roundGauge.props[name].skinOnly, `roundGauge.${name}`);
  assert.ok(specs.light.props.color.cleanOnly && specs.light.props.lampColor.skinOnly);
});

test("skinned property values are validated like any other", () => {
  const w = { id: "g", type: "roundGauge", x: 0, y: 0, w: 200, h: 200, channel: "rpm", startAngle: -105, endAngle: 105, labels: ["E", "\u00bd", "F"], icon: "fuel", sub: "lcd", compact: true, lowTo: 0.12 };
  const ok = sanitizeLayout({ format: 1, name: "x", width: 800, height: 400, widgets: [w] }, specs);
  assert.deepEqual(ok.warnings, []);
  assert.deepEqual(ok.layout.widgets[0].labels, ["E", "\u00bd", "F"]);
  const bad = sanitizeLayout({ format: 1, name: "x", width: 800, height: 400, widgets: [{ ...w, icon: "<img>", sub: "banana", startAngle: 999, labels: "E,F" }] }, specs);
  const got = bad.layout.widgets[0];
  assert.equal(got.icon, undefined);
  assert.equal(got.sub, undefined);
  assert.equal(got.labels, undefined);
  assert.equal(got.startAngle, 180); // clamped
});

test("every built-in layout is accepted without a single warning", async () => {
  const { readdirSync } = await import("node:fs");
  const files = readdirSync("web/layouts").filter((f) => f.endsWith(".json"));
  assert.ok(files.includes("default.json") && files.includes("cluster.json"));
  for (const f of files) {
    const raw = JSON.parse(readFileSync(`web/layouts/${f}`, "utf8"));
    const { layout, warnings } = sanitizeLayout(raw, specs);
    assert.deepEqual(warnings, [], f);
    assert.equal(layout.widgets.length, raw.widgets.length, f);
  }
});

import assert from "node:assert/strict";
import test from "node:test";

import { computeScale, scaleSetting, stepPercent } from "../../web/js/scaling.js";
import { sanitizeLayout } from "../../web/js/layoutSchema.js";
import { EditorState } from "../../web/js/editor/state.js";
import { specs } from "../../web/js/widgets/index.js";

const near = (a, b) => assert.ok(Math.abs(a - b) < 1e-9, `${a} !~ ${b}`);

test("max mode (the default): shrinks to fit a small window, never grows past the percentage", () => {
  near(computeScale({ mode: "max", percent: 100 }, 1200, 420, 600, 600), 0.5); // small window: fit wins
  near(computeScale({ mode: "max", percent: 100 }, 1200, 420, 3840, 2160), 1); // 4K fullscreen: capped at design size
  near(computeScale({ mode: "max", percent: 60 }, 1200, 420, 3840, 2160), 0.6);
  near(computeScale({ mode: "max", percent: 150 }, 1200, 420, 1500, 1000), 1.25); // fit (1.25) below the 150% cap
});

test("fit mode always fills the window (the original behaviour)", () => {
  near(computeScale({ mode: "fit" }, 1200, 420, 2400, 2000), 2);
  near(computeScale({ mode: "fit", percent: 50 }, 1200, 420, 2400, 2000), 2); // percent ignored
});

test("fixed mode ignores the window", () => {
  near(computeScale({ mode: "fixed", percent: 50 }, 1200, 420, 3840, 2160), 0.5);
  near(computeScale({ mode: "fixed", percent: 200 }, 1200, 420, 300, 300), 2);
});

test("the limiting axis decides fit: a tall window is limited by width, a wide one by height", () => {
  near(computeScale({ mode: "fit" }, 1000, 500, 500, 2000), 0.5);
  near(computeScale({ mode: "fit" }, 1000, 500, 4000, 250), 0.5);
});

test("settings are normalised: unknown modes and bad percents fall back safely", () => {
  assert.deepEqual(scaleSetting({}), { mode: "max", percent: 100 });
  assert.deepEqual(scaleSetting(undefined), { mode: "max", percent: 100 });
  assert.deepEqual(scaleSetting({ scaleMode: "banana", scalePercent: "x" }), { mode: "max", percent: 100 });
  assert.deepEqual(scaleSetting({ scaleMode: "fixed", scalePercent: 5 }), { mode: "fixed", percent: 10 });
  assert.deepEqual(scaleSetting({ scaleMode: "fixed", scalePercent: 9999 }), { mode: "fixed", percent: 400 });
  assert.deepEqual(scaleSetting({ scaleMode: "__proto__" }), { mode: "max", percent: 100 });
  assert.deepEqual(scaleSetting({ mode: "fit", percent: 73.6 }), { mode: "fit", percent: 74 }); // override shape
});

test("zoom steps move about 10% but always at least one point, and stay in range", () => {
  assert.equal(stepPercent(1, 1), 110);
  assert.equal(stepPercent(1, -1), 91);
  assert.equal(stepPercent(0.12, 1), 13); // 12 * 1.1 rounds to 13
  assert.equal(stepPercent(0.1, 1), 11); // would round to 11 anyway; never stuck
  assert.ok(stepPercent(0.101, -1) >= 10);
  assert.equal(stepPercent(0.1, -1), 10); // floor
  assert.equal(stepPercent(4, 1), 400); // ceiling
  let p = 100;
  for (let i = 0; i < 100; i++) p = stepPercent(p / 100, 1);
  assert.equal(p, 400);
});

test("layouts without a scale setting load as 'fit, no bigger than 100%'; valid settings survive", () => {
  const base = { format: 1, name: "x", width: 1000, height: 400, widgets: [] };
  const a = sanitizeLayout(base, specs);
  assert.deepEqual([a.layout.scaleMode, a.layout.scalePercent], ["max", 100]);
  assert.deepEqual(a.warnings, []);
  const b = sanitizeLayout({ ...base, scaleMode: "fixed", scalePercent: 60 }, specs);
  assert.deepEqual([b.layout.scaleMode, b.layout.scalePercent], ["fixed", 60]);
  const c = sanitizeLayout({ ...base, scaleMode: "banana", scalePercent: 1e9 }, specs);
  assert.deepEqual([c.layout.scaleMode, c.layout.scalePercent], ["max", 400]);
  assert.ok(c.warnings.some((w) => w.includes("scale mode")));
});

test("editor: setScale is undoable, no-ops record nothing, and typed percents coalesce", () => {
  const { layout } = sanitizeLayout({ format: 1, name: "x", width: 1000, height: 400, widgets: [] }, specs);
  const s = new EditorState(layout, specs);
  s.setScale("max", 100); // already that
  assert.equal(s.canUndo, false);
  s.setScale("fixed", 50);
  assert.deepEqual([s.layout.scaleMode, s.layout.scalePercent], ["fixed", 50]);
  s.setScale("fixed", 55);
  s.setScale("fixed", 60);
  assert.equal(s.undoStack.length, 1); // consecutive scale edits are one undo step
  s.setScale("fixed", 5); // clamped
  assert.equal(s.layout.scalePercent, 10);
  s.undo();
  assert.deepEqual([s.layout.scaleMode, s.layout.scalePercent], ["max", 100]);
  assert.equal(s.dirty, false);
});

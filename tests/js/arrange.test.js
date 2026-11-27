import assert from "node:assert/strict";
import test from "node:test";

import { ALIGN_MODES, alignRects, distributeRects, rectBetween, rectsOverlap, widgetsInRect } from "../../web/js/editor/geometry.js";
import { EditorState } from "../../web/js/editor/state.js";
import { specs } from "../../web/js/widgets/index.js";

const R = (x, y, w, h) => ({ x, y, w, h });
const rects = () => new Map([["a", R(10, 20, 100, 50)], ["b", R(200, 100, 40, 80)], ["c", R(80, 300, 60, 30)]]);

test("a marquee is the rectangle between two corners, whichever way it was dragged", () => {
  assert.deepEqual(rectBetween({ x: 50, y: 60 }, { x: 10, y: 20 }), R(10, 20, 40, 40));
  assert.deepEqual(rectBetween({ x: 5, y: 5 }, { x: 5, y: 5 }), R(5, 5, 0, 0));
});

test("rectangles overlap when they share area, not when they only touch", () => {
  assert.ok(rectsOverlap(R(0, 0, 10, 10), R(5, 5, 10, 10)));
  assert.ok(!rectsOverlap(R(0, 0, 10, 10), R(10, 0, 10, 10)));
  assert.ok(!rectsOverlap(R(0, 0, 10, 10), R(0, 10, 10, 10)));
  assert.ok(rectsOverlap(R(0, 0, 100, 100), R(40, 40, 5, 5))); // one inside the other
});

test("a marquee takes the widgets it touches", () => {
  const widgets = [{ id: "a", ...R(0, 0, 100, 100) }, { id: "b", ...R(150, 0, 100, 100) }, { id: "c", ...R(0, 150, 100, 100) }];
  assert.deepEqual(widgetsInRect(widgets, R(90, 90, 70, 70)), ["a", "b", "c"]); // (it reaches a corner of each)
  assert.deepEqual(widgetsInRect(widgets, R(90, 20, 70, 20)), ["a", "b"]);
  assert.deepEqual(widgetsInRect(widgets, R(0, 0, 400, 400)), ["a", "b", "c"]);
  assert.deepEqual(widgetsInRect(widgets, R(120, 120, 10, 10)), []);
});

test("aligning moves widgets to the edge or middle of the box around them, and never resizes", () => {
  const box = { x: 10, y: 20, x2: 240, y2: 330 };
  const left = alignRects(rects(), "left");
  assert.deepEqual([...left.values()].map((r) => r.x), [10, 10, 10]);
  const right = alignRects(rects(), "right");
  assert.deepEqual([...right.values()].map((r) => r.x + r.w), [box.x2, box.x2, box.x2]);
  const centre = alignRects(rects(), "centre");
  assert.deepEqual([...centre.values()].map((r) => r.x + r.w / 2), [125, 125, 125]);
  const top = alignRects(rects(), "top");
  assert.deepEqual([...top.values()].map((r) => r.y), [20, 20, 20]);
  const bottom = alignRects(rects(), "bottom");
  assert.deepEqual([...bottom.values()].map((r) => r.y + r.h), [box.y2, box.y2, box.y2]);
  const middle = alignRects(rects(), "middle");
  assert.deepEqual([...middle.values()].map((r) => r.y + r.h / 2), [175, 175, 175]);
  for (const mode of ALIGN_MODES) for (const [id, r] of alignRects(rects(), mode)) assert.deepEqual([r.w, r.h], [rects().get(id).w, rects().get(id).h], mode);
});

test("aligning keeps everything inside the box it started in", () => {
  for (const mode of ALIGN_MODES) {
    for (const r of alignRects(rects(), mode).values()) assert.ok(r.x >= 10 && r.y >= 20 && r.x + r.w <= 240 && r.y + r.h <= 330, mode);
  }
});

test("a mode that does not exist, or nothing to align, changes nothing", () => {
  assert.deepEqual([...alignRects(rects(), "diagonal")], [...rects()]);
  assert.equal(alignRects(new Map(), "left").size, 0);
});

test("distributing makes the gaps equal and leaves the two outer widgets where they are", () => {
  const across = new Map([["a", R(0, 0, 50, 10)], ["b", R(70, 0, 30, 10)], ["c", R(300, 0, 50, 10)], ["d", R(150, 0, 20, 10)]]);
  const out = distributeRects(across, "h");
  const order = [...out].sort((p, q) => p[1].x - q[1].x);
  assert.deepEqual(order.map(([id]) => id), ["a", "b", "d", "c"]);
  assert.equal(order[0][1].x, 0); // the first and last stay
  assert.equal(order[3][1].x, 300);
  const gaps = order.slice(1).map(([, r], i) => r.x - (order[i][1].x + order[i][1].w));
  assert.ok(Math.max(...gaps) - Math.min(...gaps) <= 1, String(gaps));
  assert.deepEqual([...out.values()].map((r) => r.w).sort(), [20, 30, 50, 50]); // sizes unchanged
});

test("distributing down works the same way, and needs three", () => {
  const down = new Map([["a", R(0, 0, 10, 40)], ["b", R(0, 50, 10, 40)], ["c", R(0, 400, 10, 40)]]);
  const out = distributeRects(down, "v");
  assert.deepEqual([out.get("a").y, out.get("b").y, out.get("c").y], [0, 200, 400]);
  const two = new Map([["a", R(0, 0, 10, 10)], ["b", R(5, 5, 10, 10)]]);
  assert.deepEqual([...distributeRects(two, "h")], [...two]);
});

test("distributing widgets that already overlap does not push the last one past its place", () => {
  const out = distributeRects(new Map([["a", R(0, 0, 100, 10)], ["b", R(10, 0, 100, 10)], ["c", R(20, 0, 100, 10)]]), "h");
  assert.equal(out.get("c").x, 20); // (nothing to spread: the span is smaller than the widgets, so the gaps go negative and the middle one slides)
  assert.equal(out.get("a").x, 0);
});

// ---- on the editor state -------------------------------------------------------------------------------------------------------

const layout = () => ({
  format: 1, name: "T", width: 1000, height: 400,
  widgets: [
    { id: "a", type: "gear", x: 10, y: 20, w: 100, h: 50, channel: "gear" },
    { id: "b", type: "gear", x: 200, y: 100, w: 40, h: 80, channel: "gear" },
    { id: "c", type: "gear", x: 80, y: 300, w: 60, h: 30, channel: "gear" },
    { id: "d", type: "gear", x: 500, y: 0, w: 60, h: 30, channel: "gear" },
  ],
});
const make = () => new EditorState(layout(), specs, {});
const pos = (s, id) => [s.widget(id).x, s.widget(id).y];

test("aligning the selection is one undo step", () => {
  const s = make();
  s.select(["a", "b", "c"]);
  assert.equal(s.alignSelection("left"), true);
  assert.deepEqual([pos(s, "a")[0], pos(s, "b")[0], pos(s, "c")[0]], [10, 10, 10]);
  assert.deepEqual(pos(s, "d"), [500, 0]); // (not selected: not moved)
  s.undo();
  assert.deepEqual([pos(s, "a")[0], pos(s, "b")[0], pos(s, "c")[0]], [10, 200, 80]);
  assert.equal(s.undoStack.length, 0);
});

test("aligning needs two widgets, and does nothing (and makes no undo step) when they are already aligned", () => {
  const s = make();
  s.select(["a"]);
  assert.equal(s.alignSelection("left"), false);
  s.select(["a", "b"]);
  assert.equal(s.alignSelection("top"), true);
  assert.equal(s.alignSelection("top"), false);
  assert.equal(s.undoStack.length, 1);
});

test("distributing needs three, moves only the middle ones, and is one undo step", () => {
  const s = make();
  s.select(["a", "b"]);
  assert.equal(s.distributeSelection("h"), false);
  s.select(["a", "b", "d"]);
  assert.equal(s.distributeSelection("h"), true);
  assert.deepEqual([pos(s, "a")[0], pos(s, "d")[0]], [10, 500]);
  assert.ok(pos(s, "b")[0] > 200);
  s.undo();
  assert.equal(pos(s, "b")[0], 200);
});

test("arranging keeps widgets within the canvas rules and records the new rectangles for the editor to redraw", () => {
  const s = make();
  const seen = [];
  s.subscribe((e) => seen.push(e));
  s.select(["a", "c"]);
  s.alignSelection("centre");
  const rectEvents = seen.filter((e) => e.kind === "rect");
  assert.equal(rectEvents.length, 1);
  assert.deepEqual(rectEvents[0].ids.sort(), ["a", "c"]); // (only the widgets that moved are redrawn)
});

// Run from the repo root:  node --test tests/js
import assert from "node:assert/strict";
import test from "node:test";

import { MAX_WIDGETS } from "../../web/js/layoutSchema.js";
import { boundsOf, clampRect, dragRects, MIN_SIZE, resizeRect, snap } from "../../web/js/editor/geometry.js";
import { EditorState } from "../../web/js/editor/state.js";
import { specs } from "../../web/js/widgets/index.js";

const layout = () => ({
  format: 1, name: "T", width: 1000, height: 400,
  widgets: [
    { id: "a", type: "gear", x: 0, y: 0, w: 100, h: 100, channel: "gear" },
    { id: "b", type: "bar", x: 200, y: 0, w: 300, h: 30, channel: "fuel", kind: "fraction" },
    { id: "c", type: "light", x: 600, y: 0, w: 48, h: 40, channel: "signal_L" },
  ],
});
const make = (meta) => new EditorState(layout(), specs, meta);
const ids = (s) => s.widgets.map((w) => w.id);

// ---------- geometry ----------

test("snap rounds to the grid and to whole pixels without one", () => {
  assert.equal(snap(14, 10), 10);
  assert.equal(snap(15, 10), 20);
  assert.equal(snap(14.6, 0), 15);
});

test("dragging a group is rigid: offsets between widgets survive snapping", () => {
  const start = new Map([["a", { x: 3, y: 3, w: 50, h: 50 }], ["b", { x: 13, y: 8, w: 50, h: 50 }]]);
  const moved = dragRects(start, 7, 7, 10);
  assert.deepEqual(moved.get("a"), { x: 10, y: 10, w: 50, h: 50 });
  assert.equal(moved.get("b").x - moved.get("a").x, 10); // original offset (13-3) preserved
  assert.equal(moved.get("b").y - moved.get("a").y, 5);
  assert.equal(dragRects(new Map(), 5, 5, 10).size, 0);
});

test("resize: dragged edge snaps, opposite edge stays put", () => {
  const r = { x: 100, y: 100, w: 200, h: 100 };
  assert.deepEqual(resizeRect(r, "e", 33, 0, { grid: 10 }), { x: 100, y: 100, w: 230, h: 100 });
  assert.deepEqual(resizeRect(r, "w", -33, 0, { grid: 10 }), { x: 70, y: 100, w: 230, h: 100 });
  assert.deepEqual(resizeRect(r, "s", 0, 24, { grid: 10 }), { x: 100, y: 100, w: 200, h: 120 });
  assert.deepEqual(resizeRect(r, "nw", -20, -20, { grid: 10 }), { x: 80, y: 80, w: 220, h: 120 });
});

test("resize cannot go below the minimum and keeps the opposite edge fixed", () => {
  const r = { x: 100, y: 100, w: 200, h: 100 };
  const a = resizeRect(r, "e", -500, 0);
  assert.deepEqual([a.x, a.w], [100, MIN_SIZE]);
  const b = resizeRect(r, "w", 500, 0); // dragging the left edge past the right edge
  assert.equal(b.x + b.w, 300);
  assert.equal(b.w, MIN_SIZE);
  const c = resizeRect(r, "n", 0, 500);
  assert.equal(c.y + c.h, 200);
});

test("aspect-locked corner resize keeps the ratio and the opposite corner", () => {
  const r = { x: 100, y: 100, w: 200, h: 100 };
  const se = resizeRect(r, "se", 100, 0, { keepAspect: true });
  assert.deepEqual(se, { x: 100, y: 100, w: 300, h: 150 });
  const nw = resizeRect(r, "nw", -100, -10, { keepAspect: true });
  assert.equal(nw.x + nw.w, 300);
  assert.equal(nw.y + nw.h, 200);
  assert.ok(Math.abs(nw.w / nw.h - 2) < 0.02);
  const tiny = resizeRect(r, "se", -9999, -9999, { keepAspect: true });
  assert.ok(tiny.w >= MIN_SIZE && tiny.h >= MIN_SIZE);
});

test("clampRect and boundsOf", () => {
  assert.deepEqual(clampRect({ x: 99999, y: -99999, w: 0, h: 99999 }, 1000), { x: 8000, y: -1000, w: MIN_SIZE, h: 8000 });
  assert.deepEqual(boundsOf([{ x: 0, y: 10, w: 50, h: 50 }, { x: 100, y: 0, w: 10, h: 10 }]), { x: 0, y: 0, w: 110, h: 60 });
  assert.equal(boundsOf([]), null);
});

// ---------- selection ----------

test("selection: replace, toggle, additive, unknown ids, selectAll; events only on real changes", () => {
  const s = make();
  const seen = [];
  s.subscribe((e) => e.kind === "selection" && seen.push([...s.selection]));
  s.select(["a"]);
  s.select(["b"], { additive: true });
  s.select(["a"], { toggle: true }); // a was selected, so toggling removes it
  s.select(["nope"]); // selecting only unknown ids selects nothing
  s.selectAll();
  s.select(["a", "b", "c"]); // identical selection: no event
  assert.deepEqual(seen, [["a"], ["a", "b"], ["b"], [], ["a", "b", "c"]]);
});

// ---------- add / remove / undo ----------

test("add selects the new widget with its defaults, remove deletes selection, and both undo", () => {
  const s = make();
  const id = s.add("readout", 40, 50);
  assert.equal(s.widget(id).x, 40);
  assert.equal(s.widget(id).w, specs.readout.size.w);
  assert.deepEqual(s.selection, [id]);
  s.remove();
  assert.equal(s.widget(id), null);
  assert.deepEqual(s.selection, []);
  s.undo();
  assert.ok(s.widget(id));
  s.undo();
  assert.equal(s.widget(id), null);
  assert.equal(s.canUndo, false);
  s.redo();
  s.redo();
  assert.equal(s.widget(id), null);
  assert.equal(s.canRedo, false);
});

test("a new edit after undo discards the redo history", () => {
  const s = make();
  s.add("text");
  s.undo();
  assert.equal(s.canRedo, true);
  s.add("gear");
  assert.equal(s.canRedo, false);
});

test("add refuses beyond the widget limit", () => {
  const s = make();
  for (let i = 0; i < MAX_WIDGETS + 5; i++) s.add("light");
  assert.equal(s.widgets.length, MAX_WIDGETS);
  assert.equal(s.add("light"), null);
});

test("remove ignores unknown ids and records nothing", () => {
  const s = make();
  s.remove(["zzz"]);
  assert.equal(s.canUndo, false);
});

// ---------- gestures and coalescing ----------

test("a drag gesture is ONE undo step however many moves it contains", () => {
  const s = make();
  s.beginGesture();
  for (let i = 1; i <= 30; i++) s.setRects(new Map([["a", { x: i * 3, y: 0, w: 100, h: 100 }]]));
  s.endGesture();
  assert.equal(s.widget("a").x, 90);
  assert.equal(s.undoStack.length, 1);
  s.undo();
  assert.equal(s.widget("a").x, 0);
});

test("a gesture that changes nothing leaves no history and stays clean", () => {
  const s = make();
  s.beginGesture();
  s.setRects(new Map([["a", { x: 0, y: 0, w: 100, h: 100 }]]));
  s.endGesture();
  assert.equal(s.canUndo, false);
  assert.equal(s.dirty, false);
});

test("holding an arrow key (many nudges) is one undo step; a different target starts a new one", () => {
  const s = make();
  s.select(["a"]);
  for (let i = 0; i < 10; i++) s.moveBy(1, 0);
  assert.equal(s.widget("a").x, 10);
  assert.equal(s.undoStack.length, 1);
  s.select(["b"]);
  s.moveBy(0, 5);
  assert.equal(s.undoStack.length, 2);
  s.undo();
  assert.equal(s.widget("b").y, 0);
  assert.equal(s.widget("a").x, 10);
  s.undo();
  assert.equal(s.widget("a").x, 0);
});

test("typing in one property field coalesces; switching field starts a new step", () => {
  const s = make();
  for (const v of ["R", "RP", "RPM"]) assert.equal(s.setProp("a", "label", v).ok, true);
  assert.equal(s.undoStack.length, 1);
  s.setProp("a", "channel", "rpm");
  assert.equal(s.undoStack.length, 2);
  s.undo();
  assert.equal(s.widget("a").channel, "gear");
  assert.equal(s.widget("a").label, "RPM");
});

// ---------- properties ----------

test("setProp validates against the widget spec and never changes anything on failure", () => {
  const s = make();
  const before = JSON.stringify(s.layout);
  assert.equal(s.setProp("a", "channel", "has space").ok, false);
  assert.equal(s.setProp("a", "color", "red").ok, false);
  assert.equal(s.setProp("b", "decimals", "x").ok, false);
  assert.equal(s.setProp("a", "evil", 1).ok, false);
  assert.equal(s.setProp("a", "__proto__", {}).ok, false);
  assert.equal(s.setProp("nope", "channel", "x").ok, false);
  assert.equal(JSON.stringify(s.layout), before);
  assert.equal(s.canUndo, false);
});

test("setProp clamps numbers per spec, and undefined removes the property", () => {
  const s = make();
  s.setProp("b", "decimals", 99);
  assert.equal(s.widget("b").decimals, 4);
  s.setProp("b", "decimals", undefined);
  assert.equal("decimals" in s.widget("b"), false);
  assert.equal(s.setProp("b", "decimals", undefined).ok, true); // removing something absent is a no-op
});

test("setGeometry clamps and rejects non-numbers", () => {
  const s = make();
  assert.equal(s.setGeometry("a", "w", 0).ok, true);
  assert.equal(s.widget("a").w, MIN_SIZE);
  assert.equal(s.setGeometry("a", "x", NaN).ok, false);
  assert.equal(s.setGeometry("a", "color", 5).ok, false);
});

test("name and canvas size are clamped and undoable", () => {
  const s = make();
  s.setName("x".repeat(100) + "\n");
  assert.equal(s.layout.name.length, 60);
  s.setCanvasSize(99999, 1);
  assert.deepEqual([s.layout.width, s.layout.height], [4000, 100]);
  assert.equal(s.setCanvasSize(NaN, 5).ok, false);
  s.undo();
  assert.deepEqual([s.layout.width, s.layout.height], [1000, 400]);
});

// ---------- z-order ----------

test("reorder: front, back, forward, backward", () => {
  const s = make();
  s.reorder("front", ["a"]);
  assert.deepEqual(ids(s), ["b", "c", "a"]);
  s.reorder("back", ["c"]);
  assert.deepEqual(ids(s), ["c", "b", "a"]);
  s.reorder("forward", ["c"]);
  assert.deepEqual(ids(s), ["b", "c", "a"]);
  s.reorder("backward", ["a"]);
  assert.deepEqual(ids(s), ["b", "a", "c"]);
  const depth = s.undoStack.length;
  s.reorder("front", ["c"]); // already on top: nothing changes, nothing recorded
  assert.equal(s.undoStack.length, depth);
});

test("reorder moves multi-selections together", () => {
  const s = make();
  s.reorder("front", ["a", "b"]);
  assert.deepEqual(ids(s), ["c", "a", "b"]);
  s.reorder("backward", ["a", "b"]);
  assert.deepEqual(ids(s), ["a", "b", "c"]);
});

// ---------- clipboard ----------

test("copy/paste: new unique ids, growing offset, pasted copies selected, deep-copied", () => {
  const s = make();
  s.select(["b"]);
  assert.equal(s.copy(), 1);
  const [p1] = s.paste();
  const [p2] = s.paste();
  assert.notEqual(p1, p2);
  assert.equal(new Set(ids(s)).size, ids(s).length);
  assert.equal(s.widget(p1).x, 220);
  assert.equal(s.widget(p2).x, 240);
  assert.deepEqual(s.selection, [p2]);
  s.widget(p1).channel = "changed";
  assert.equal(s.widget("b").channel, "fuel"); // independent copies
  assert.equal(s.clipboard[0].channel, "fuel");
});

test("duplicate keeps z-order of the originals and selects the copies", () => {
  const s = make();
  const created = s.duplicate(["a", "c"]);
  assert.equal(created.length, 2);
  assert.deepEqual(s.selection, created);
  assert.equal(s.widget(created[0]).type, "gear");
  assert.equal(s.widget(created[1]).type, "light");
});

test("paste with an empty clipboard does nothing", () => {
  const s = make();
  assert.deepEqual(s.paste(), []);
  assert.equal(s.canUndo, false);
});

// ---------- dirty tracking and replace ----------

test("dirty reflects content: edits set it, undoing back to the saved state clears it, save resets it", () => {
  const s = make();
  assert.equal(s.dirty, false);
  s.setProp("a", "label", "X");
  assert.equal(s.dirty, true);
  s.undo();
  assert.equal(s.dirty, false);
  s.setProp("a", "label", "Y");
  s.markSaved({ id: "u-123456789abc", builtin: false });
  assert.equal(s.dirty, false);
  assert.equal(s.meta.id, "u-123456789abc");
  s.undo();
  assert.equal(s.dirty, true); // undoing past the saved point makes it differ from what is stored
});

test("replace swaps the layout and resets history, selection and clipboard", () => {
  const s = make({ id: "default", builtin: true });
  s.select(["a"]);
  s.copy();
  s.setProp("a", "label", "x");
  s.replace({ format: 1, name: "Other", width: 500, height: 200, widgets: [] }, { id: "u-aaaaaaaaaaaa", builtin: false });
  assert.deepEqual([s.layout.name, s.widgets.length, s.canUndo, s.dirty, s.selection.length, s.clipboard.length], ["Other", 0, false, false, 0, 0]);
  assert.equal(s.meta.builtin, false);
});

test("the working copy is detached from the layout it was built from", () => {
  const src = layout();
  const s = new EditorState(src, specs);
  s.setProp("a", "label", "changed");
  assert.equal(src.widgets[0].label, undefined);
  const out = s.toLayout();
  out.widgets.length = 0;
  assert.equal(s.widgets.length, 3);
});

test("undo after a selection change drops ids that no longer exist", () => {
  const s = make();
  const id = s.add("text");
  assert.deepEqual(s.selection, [id]);
  s.undo();
  assert.deepEqual(s.selection, []);
});

test("a reset (property removal) is its own undo step and does not merge with typing that follows", () => {
  const s = make();
  s.setProp("a", "label", "Speedo");
  s.setProp("a", "label", undefined); // reset
  for (const v of ["A", "AB", "ABC"]) s.setProp("a", "label", v); // typing again right away
  s.undo(); // removes the typed run only
  assert.equal(s.widget("a").label, undefined);
  s.undo(); // undoes the reset
  assert.equal(s.widget("a").label, "Speedo");
});

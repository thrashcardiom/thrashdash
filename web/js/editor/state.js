// Editor state: the working copy of a layout plus selection, undo/redo, clipboard and z-order.
// No DOM, so it is unit-tested under Node. The UI (editor.js) only calls these methods and reacts to events.
//
// Undo model: a snapshot of the whole layout (a few KB) is pushed BEFORE each change. Continuous
// actions are collapsed into ONE undo step so Ctrl-Z behaves the way people expect:
//   * a mouse drag/resize is a "gesture" (beginGesture/endGesture),
//   * typing in a field, or holding an arrow key, is "coalesced": repeated edits with the same key
//     within COALESCE_MS share a single snapshot.

import { MAX_WIDGETS, freshId, newWidget, sanitizeValue } from "../layoutSchema.js";
import { scaleSetting } from "../scaling.js";
import { normaliseColour } from "../theme.js";
import { alignRects, clampRect, distributeRects } from "./geometry.js";

const HISTORY_LIMIT = 100;
const COALESCE_MS = 1000;
const PASTE_STEP = 20;

export class EditorState {
  /**
   * @param {object} layout a sanitised layout
   * @param {object} specs  widget type -> spec
   * @param {{id?: string|null, builtin?: boolean}} meta where the layout is stored
   */
  constructor(layout, specs, meta = {}) {
    this.specs = specs;
    this.listeners = new Set();
    this._load(layout, meta);
  }

  _load(layout, meta) {
    this.layout = structuredClone(layout);
    this.meta = { id: meta.id ?? null, builtin: Boolean(meta.builtin) };
    this.selection = [];
    this.undoStack = [];
    this.redoStack = [];
    this.clipboard = [];
    this._pasteCount = 0;
    this._gesture = null;
    this._lastKey = null;
    this._lastTime = 0;
    this._savedJson = JSON.stringify(this.layout);
  }

  // ---- events -------------------------------------------------------------------------------

  subscribe(fn) {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  }

  /** kind: "structure" (widgets added/removed/reordered/replaced), "rect" (geometry), "props" (one widget's
   *  properties), "selection", "meta" (name, canvas size, saved state), "history". */
  _emit(kind, detail = {}) {
    for (const fn of this.listeners) fn({ kind, ...detail });
  }

  // ---- queries ------------------------------------------------------------------------------

  get widgets() {
    return this.layout.widgets;
  }

  widget(id) {
    return this.layout.widgets.find((w) => w.id === id) ?? null;
  }

  get selectedWidgets() {
    return this.selection.map((id) => this.widget(id)).filter(Boolean);
  }

  /** True while a drag/resize gesture is in progress (the UI skips expensive refreshes then). */
  get gesturing() {
    return this._gesture !== null;
  }

  get canUndo() {
    return this.undoStack.length > 0;
  }

  get canRedo() {
    return this.redoStack.length > 0;
  }

  get dirty() {
    return JSON.stringify(this.layout) !== this._savedJson;
  }

  toLayout() {
    return structuredClone(this.layout);
  }

  // ---- history ------------------------------------------------------------------------------

  _pushUndo(snapshot) {
    this.undoStack.push(snapshot);
    if (this.undoStack.length > HISTORY_LIMIT) this.undoStack.shift();
    this.redoStack = [];
  }

  /** Call BEFORE mutating. With a `key`, repeated calls with the same key inside COALESCE_MS share one snapshot. */
  _record(key = null) {
    if (this._gesture) return; // the gesture records its own single snapshot
    const now = Date.now();
    if (key && key === this._lastKey && now - this._lastTime < COALESCE_MS) {
      this._lastTime = now;
      return;
    }
    this._pushUndo(structuredClone(this.layout));
    this._lastKey = key;
    this._lastTime = now;
  }

  beginGesture() {
    if (this._gesture) return;
    this._gesture = { before: structuredClone(this.layout) };
    this._lastKey = null;
  }

  endGesture() {
    const g = this._gesture;
    this._gesture = null;
    if (g && JSON.stringify(g.before) !== JSON.stringify(this.layout)) {
      this._pushUndo(g.before);
      this._emit("history");
    }
  }

  /** Abandon an in-progress gesture (Esc while dragging) and restore the layout as it was. */
  cancelGesture() {
    const g = this._gesture;
    if (!g) return false;
    this._gesture = null;
    this.layout = g.before;
    this._emit("structure");
    return true;
  }

  undo() {
    if (!this.canUndo) return false;
    this.redoStack.push(structuredClone(this.layout));
    this.layout = this.undoStack.pop();
    this._afterTimeTravel();
    return true;
  }

  redo() {
    if (!this.canRedo) return false;
    this.undoStack.push(structuredClone(this.layout));
    this.layout = this.redoStack.pop();
    this._afterTimeTravel();
    return true;
  }

  _afterTimeTravel() {
    this._lastKey = null;
    this.selection = this.selection.filter((id) => this.widget(id));
    this._emit("structure");
    this._emit("selection");
    this._emit("meta");
    this._emit("history");
  }

  // ---- selection ----------------------------------------------------------------------------

  select(ids, { toggle = false, additive = false } = {}) {
    const wanted = ids.filter((id) => this.widget(id));
    let next;
    if (toggle) {
      next = [...this.selection];
      for (const id of wanted) next = next.includes(id) ? next.filter((x) => x !== id) : [...next, id];
    } else if (additive) {
      next = [...new Set([...this.selection, ...wanted])];
    } else {
      next = [...new Set(wanted)];
    }
    if (next.length !== this.selection.length || next.some((id, i) => id !== this.selection[i])) {
      this.selection = next;
      this._emit("selection");
    }
  }

  selectAll() {
    this.select(this.widgets.map((w) => w.id));
  }

  clearSelection() {
    this.select([]);
  }

  // ---- widgets ------------------------------------------------------------------------------

  /** Add a widget of `type` and select it. Returns its id, or null if the layout is full. */
  add(type, x = 20, y = 20) {
    if (this.widgets.length >= MAX_WIDGETS) return null;
    this._record();
    const w = newWidget(type, this.specs, this.widgets, x, y);
    this.widgets.push(w);
    this.selection = [w.id];
    this._emit("structure");
    this._emit("selection");
    return w.id;
  }

  remove(ids = this.selection) {
    const doomed = new Set(ids.filter((id) => this.widget(id)));
    if (!doomed.size) return;
    this._record();
    this.layout.widgets = this.widgets.filter((w) => !doomed.has(w.id));
    this.selection = this.selection.filter((id) => !doomed.has(id));
    this._emit("structure");
    this._emit("selection");
  }

  /** Set geometry for several widgets at once (used while dragging; wrap in begin/endGesture). */
  setRects(rects) {
    this._record();
    for (const [id, r] of rects) {
      const w = this.widget(id);
      if (!w) continue;
      Object.assign(w, clampRect(r, this.layout.width));
    }
    this._emit("rect", { ids: [...rects.keys()] });
  }

  /** The selected widgets' rectangles, by id. */
  _selectedRects() {
    return new Map(this.selection.map((id) => this.widget(id)).filter(Boolean).map((w) => [w.id, { x: w.x, y: w.y, w: w.w, h: w.h }]));
  }

  /** Apply new rectangles as ONE undo step, and only if something actually moves. Returns whether it did. */
  _arrange(next, before) {
    const moved = new Map([...next].filter(([id, r]) => ["x", "y", "w", "h"].some((k) => r[k] !== before.get(id)[k])));
    if (!moved.size) return false;
    this.setRects(moved);
    return true;
  }

  /** Line the selected widgets up with each other: mode "left", "centre", "right", "top", "middle" or "bottom". Needs two or more. */
  alignSelection(mode) {
    const before = this._selectedRects();
    return before.size >= 2 && this._arrange(alignRects(before, mode), before);
  }

  /** Space the selected widgets evenly across ("h") or down ("v"). Needs three or more. */
  distributeSelection(axis) {
    const before = this._selectedRects();
    return before.size >= 3 && this._arrange(distributeRects(before, axis), before);
  }

  /** Nudge the selection. Holding an arrow key coalesces into one undo step. */
  moveBy(dx, dy, ids = this.selection) {
    const targets = ids.map((id) => this.widget(id)).filter(Boolean);
    if (!targets.length) return;
    this._record(`move:${targets.map((w) => w.id).join(",")}`);
    const rects = new Map();
    for (const w of targets) {
      Object.assign(w, clampRect({ x: w.x + dx, y: w.y + dy, w: w.w, h: w.h }, this.layout.width));
      rects.set(w.id, w);
    }
    this._emit("rect", { ids: [...rects.keys()] });
  }

  /** Set one geometry field (x, y, w or h) from a typed number. */
  setGeometry(id, key, value) {
    const w = this.widget(id);
    if (!w || !["x", "y", "w", "h"].includes(key)) return { ok: false, reason: "no such field" };
    if (typeof value !== "number" || !Number.isFinite(value)) return { ok: false, reason: "not a number" };
    this._record(`geo:${id}:${key}`);
    Object.assign(w, clampRect({ ...w, [key]: value }, this.layout.width));
    this._emit("rect", { ids: [id] });
    return { ok: true };
  }

  /**
   * Set (or, with `undefined`, remove) one widget property, validated against the widget's spec.
   * @returns {{ok: true} | {ok: false, reason: string}}
   */
  setProp(id, key, value) {
    const w = this.widget(id);
    const ps = w && Object.hasOwn(this.specs[w.type].props, key) ? this.specs[w.type].props[key] : null;
    if (!ps) return { ok: false, reason: "unknown property" };
    if (value === undefined) {
      if (!(key in w)) return { ok: true };
      this._record(); // a reset/clear is its own undo step and must not merge with typing that follows it
      delete w[key];
    } else {
      const res = sanitizeValue(value, ps);
      if (!res.ok) return res;
      this._record(`prop:${id}:${key}`);
      w[key] = res.value;
    }
    this._emit("props", { id, key });
    return { ok: true };
  }

  // ---- layout-level fields ------------------------------------------------------------------

  setName(name) {
    const clean = String(name).replace(/[\u0000-\u001f\u007f]/g, "").slice(0, 60);
    if (clean === this.layout.name) return;
    this._record("name");
    this.layout.name = clean;
    this._emit("meta");
  }

  setCanvasSize(width, height) {
    if (![width, height].every((n) => Number.isFinite(n))) return { ok: false, reason: "not a number" };
    const w = Math.round(Math.min(4000, Math.max(200, width)));
    const h = Math.round(Math.min(4000, Math.max(100, height)));
    if (w === this.layout.width && h === this.layout.height) return { ok: true };
    this._record("canvas");
    this.layout.width = w;
    this.layout.height = h;
    this._emit("meta");
    return { ok: true };
  }

  /** Set how the canvas scales to the window. `percent` applies to the "max" and "fixed" modes. */
  setScale(mode, percent) {
    const next = scaleSetting({ mode, percent });
    const current = scaleSetting(this.layout);
    if (next.mode === current.mode && next.percent === current.percent) return;
    this._record("scale");
    this.layout.scaleMode = next.mode;
    this.layout.scalePercent = next.percent;
    this._emit("meta");
  }

  /**
   * Set (or, with `undefined`, take away) one colour of the theme. Typing or dragging in a colour picker makes many changes in a row:
   * they coalesce into one undo step.
   */
  setThemeColour(key, value) {
    const colour = value === undefined ? undefined : normaliseColour(value);
    if (value !== undefined && !colour) return { ok: false, reason: "not a colour" };
    const theme = { ...(this.layout.theme ?? {}) };
    if ((theme[key] ?? undefined) === colour) return { ok: true };
    this._record(`theme:${key}`);
    if (colour === undefined) delete theme[key];
    else theme[key] = colour;
    if (Object.keys(theme).length) this.layout.theme = theme;
    else delete this.layout.theme;
    this._emit("meta");
    return { ok: true };
  }

  /** Take the whole theme away. */
  clearTheme() {
    if (!this.layout.theme) return;
    this._record();
    delete this.layout.theme;
    this._emit("meta");
  }

  // ---- z-order ------------------------------------------------------------------------------

  /** mode: "front" | "back" | "forward" | "backward". Later in the list draws on top. */
  reorder(mode, ids = this.selection) {
    const chosen = new Set(ids.filter((id) => this.widget(id)));
    if (!chosen.size) return;
    const before = this.widgets.map((w) => w.id).join();
    const list = [...this.widgets];
    const picked = list.filter((w) => chosen.has(w.id));
    const rest = list.filter((w) => !chosen.has(w.id));
    let result;
    if (mode === "front") result = [...rest, ...picked];
    else if (mode === "back") result = [...picked, ...rest];
    else {
      result = [...list];
      const order = mode === "forward" ? [...result.keys()].reverse() : [...result.keys()];
      for (const i of order) {
        const j = mode === "forward" ? i + 1 : i - 1;
        if (chosen.has(result[i].id) && result[j] && !chosen.has(result[j].id)) [result[i], result[j]] = [result[j], result[i]];
      }
    }
    if (result.map((w) => w.id).join() === before) return;
    this._record();
    this.layout.widgets = result;
    this._emit("structure");
  }

  // ---- clipboard ----------------------------------------------------------------------------

  copy(ids = this.selection) {
    const chosen = new Set(ids);
    this.clipboard = this.widgets.filter((w) => chosen.has(w.id)).map((w) => structuredClone(w));
    this._pasteCount = 0;
    return this.clipboard.length;
  }

  /** Paste the clipboard, offset a little more each time so copies do not stack invisibly. */
  paste() {
    if (!this.clipboard.length) return [];
    const room = MAX_WIDGETS - this.widgets.length;
    const items = this.clipboard.slice(0, Math.max(0, room));
    if (!items.length) return [];
    this._record();
    this._pasteCount += 1;
    const offset = PASTE_STEP * this._pasteCount;
    const taken = new Set(this.widgets.map((w) => w.id));
    const created = [];
    for (const src of items) {
      const w = structuredClone(src);
      w.id = freshId(w.type, taken);
      taken.add(w.id);
      w.x += offset;
      w.y += offset;
      this.widgets.push(w);
      created.push(w.id);
    }
    this.selection = created;
    this._emit("structure");
    this._emit("selection");
    return created;
  }

  duplicate(ids = this.selection) {
    if (!this.copy(ids)) return [];
    return this.paste();
  }

  // ---- persistence bookkeeping --------------------------------------------------------------

  /** Record that the current layout is what is stored (after a save). */
  markSaved(meta = {}) {
    this._savedJson = JSON.stringify(this.layout);
    this.meta = { ...this.meta, ...meta };
    this._emit("meta");
  }

  /** Replace everything with another layout (switching layouts). History starts fresh. */
  replace(layout, meta) {
    this._load(layout, meta);
    this._emit("structure");
    this._emit("selection");
    this._emit("meta");
    this._emit("history");
  }
}

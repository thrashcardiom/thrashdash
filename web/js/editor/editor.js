// Edit mode: a frame over every widget (move / resize / select), keyboard shortcuts, and the panel.
// The dashboard underneath keeps running on live data; the frames sit in a separate layer so the
// widgets themselves never need to know they are being edited.
import { h } from "../dom.js";
import { dialogOpen } from "./dialog.js";
import { dragRects, resizeRect, snap } from "./geometry.js";
import { LayoutManager } from "./manager.js";
import { Panel } from "./panel.js";
import { pickChannel, pickerOpen } from "./picker.js";
import { EditorState } from "./state.js";

const HANDLES = ["nw", "n", "ne", "e", "se", "s", "sw", "w"];
const DRAG_THRESHOLD_PX = 3; // below this a press is a click, not a drag
const GRID_KEY = "thrashdash.grid";

function loadGrid() {
  const v = Number(localStorage.getItem(GRID_KEY));
  return [0, 5, 10, 20, 50].includes(v) && localStorage.getItem(GRID_KEY) !== null ? v : 10;
}

const typing = (el) => el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.tagName === "SELECT" || el.isContentEditable);

/**
 * Start editing. `host` is the page controller (app.js); see the list of members used below.
 * @returns {{close: () => void, state: EditorState}}
 */
export function openEditor(host) {
  const { canvas, specs, api } = host;
  const info = host.getInfo();
  const state = new EditorState(info.layout, specs, { id: info.id, builtin: info.builtin });
  let grid = loadGrid();
  let addCount = 0;
  let drag = null;
  let panel = null;
  let statusTimer = null;

  document.body.classList.add("editing");
  const PANEL_WIDTH = 340;
  host.setInsets(PANEL_WIDTH);

  const layer = h("div", "edit-layer", canvas);
  const frames = new Map();

  const applyGrid = () => {
    canvas.style.setProperty("--grid", `${grid}px`);
    canvas.classList.toggle("no-grid", grid === 0);
  };
  applyGrid();

  // ---- frames -----------------------------------------------------------------------------

  const place = (el, r) => Object.assign(el.style, { left: `${r.x}px`, top: `${r.y}px`, width: `${r.w}px`, height: `${r.h}px` });

  function rebuildFrames() {
    layer.replaceChildren();
    frames.clear();
    for (const w of state.widgets) {
      const frame = h("div", "ef", layer);
      frame.dataset.id = w.id;
      place(frame, w);
      h("span", "ef-tag", frame, `${specs[w.type].label} · ${w.id}`);
      frames.set(w.id, frame);
    }
    updateSelection();
  }

  function updateSelection() {
    for (const [id, frame] of frames) {
      const on = state.selection.includes(id);
      frame.classList.toggle("sel", on);
      frame.querySelectorAll(".eh").forEach((el) => el.remove());
    }
    if (state.selection.length === 1) {
      const frame = frames.get(state.selection[0]);
      if (frame) for (const handle of HANDLES) h("span", `eh eh-${handle}`, frame).dataset.handle = handle;
    }
  }

  function applyRects(ids) {
    const elements = host.getElements();
    for (const id of ids) {
      const w = state.widget(id);
      if (!w) continue;
      if (frames.has(id)) place(frames.get(id), w);
      if (elements.has(id)) place(elements.get(id), w);
    }
  }

  // ---- state events -----------------------------------------------------------------------

  state.subscribe((e) => {
    switch (e.kind) {
      case "rect":
        applyRects(e.ids);
        panel?.refreshGeometry();
        break;
      case "props":
        host.render(state.layout);
        break;
      case "structure":
        host.render(state.layout);
        rebuildFrames();
        panel?.refreshSelection();
        break;
      case "selection":
        updateSelection();
        panel?.refreshSelection();
        break;
      case "meta":
        host.fit(state.layout);
        panel?.refreshMeta();
        break;
      case "history":
        panel?.refreshMeta();
        break;
    }
    // Undo/Redo buttons and the "unsaved changes" note depend on every kind of edit, not just meta/history
    // events. Skipped for selection changes and per-pixel drag updates, which cannot change them.
    if (e.kind !== "selection" && e.kind !== "meta" && e.kind !== "history" && !(e.kind === "rect" && state.gesturing)) panel?.refreshMeta();
  });

  // ---- pointer interactions ---------------------------------------------------------------

  const scale = () => host.getScale() || 1;

  layer.addEventListener("pointerdown", (e) => {
    if (e.button !== 0) return;
    const handleEl = e.target.closest(".eh");
    const frame = e.target.closest(".ef");
    if (!frame) {
      if (!e.shiftKey) state.clearSelection();
      return;
    }
    const id = frame.dataset.id;
    let collapseOnClick = false;
    if (e.shiftKey && !handleEl) {
      state.select([id], { toggle: true });
      if (!state.selection.includes(id)) return; // shift-click removed it: nothing to drag
    } else if (!state.selection.includes(id)) {
      state.select([id]);
    } else if (!handleEl && state.selection.length > 1) {
      collapseOnClick = true; // pressing a member of a group: if it turns out to be a click, select just that one
    }
    const startRects = new Map(state.selection.map((sid) => [sid, { ...rectOf(state.widget(sid)) }]));
    drag = { kind: handleEl ? "resize" : "move", handle: handleEl?.dataset.handle, id, startX: e.clientX, startY: e.clientY, startRects, moved: false, collapseOnClick };
    layer.setPointerCapture(e.pointerId);
    e.preventDefault();
  });

  layer.addEventListener("pointermove", (e) => {
    if (!drag) return;
    const dxScreen = e.clientX - drag.startX;
    const dyScreen = e.clientY - drag.startY;
    if (!drag.moved) {
      if (Math.hypot(dxScreen, dyScreen) < DRAG_THRESHOLD_PX) return;
      drag.moved = true;
      state.beginGesture();
    }
    const dx = dxScreen / scale();
    const dy = dyScreen / scale();
    const g = e.altKey ? 0 : grid; // holding Alt turns snapping off for fine placement
    if (drag.kind === "move") state.setRects(dragRects(drag.startRects, dx, dy, g));
    else state.setRects(new Map([[drag.id, resizeRect(drag.startRects.get(drag.id), drag.handle, dx, dy, { grid: g, keepAspect: e.shiftKey })]]));
  });

  const endDrag = (cancel = false) => {
    if (!drag) return;
    const d = drag;
    drag = null;
    if (d.moved) {
      if (cancel) state.cancelGesture();
      else state.endGesture();
    } else if (d.collapseOnClick && !cancel) state.select([d.id]);
  };
  layer.addEventListener("pointerup", () => endDrag());
  layer.addEventListener("pointercancel", () => endDrag(true));

  // Clicking the dark area around the canvas deselects too (the frames only cover the canvas itself).
  const onStagePointerDown = (e) => {
    if (e.button !== 0 || e.target.closest(".ed-panel, .edit-layer, .dlg-backdrop")) return;
    if (!e.shiftKey) state.clearSelection();
  };
  document.addEventListener("pointerdown", onStagePointerDown);

  // ---- keyboard ---------------------------------------------------------------------------

  const onKey = (e) => {
    if (dialogOpen() || pickerOpen()) return; // dialogs own the keyboard while open
    const mod = e.ctrlKey || e.metaKey;
    const key = e.key.toLowerCase();

    if (key === "escape") {
      if (drag) endDrag(true);
      else if (typing(e.target)) e.target.blur();
      else state.clearSelection();
      return;
    }
    if (mod && key === "s") {
      e.preventDefault();
      manager.save();
      return;
    }
    if (typing(e.target)) return; // let fields keep their own editing keys (including native undo)

    if (mod && key === "z") {
      e.preventDefault();
      if (e.shiftKey) state.redo();
      else state.undo();
    } else if (mod && key === "y") {
      e.preventDefault();
      state.redo();
    } else if (mod && key === "a") {
      e.preventDefault();
      state.selectAll();
    } else if (mod && key === "c") {
      state.copy();
    } else if (mod && key === "v") {
      e.preventDefault();
      state.paste();
    } else if (mod && key === "d") {
      e.preventDefault();
      state.duplicate();
    } else if (key === "delete" || key === "backspace") {
      e.preventDefault();
      state.remove();
    } else if (key === "[") {
      state.reorder("backward");
    } else if (key === "]") {
      state.reorder("forward");
    } else if (key.startsWith("arrow") && state.selection.length) {
      e.preventDefault();
      const step = e.shiftKey ? 10 : 1;
      state.moveBy(key === "arrowleft" ? -step : key === "arrowright" ? step : 0, key === "arrowup" ? -step : key === "arrowdown" ? step : 0);
    } else if (key === "e" && !mod) {
      e.preventDefault();
      done();
    }
  };
  document.addEventListener("keydown", onKey);

  const onBeforeUnload = (e) => {
    if (state.dirty) {
      e.preventDefault();
      e.returnValue = "";
    }
  };
  window.addEventListener("beforeunload", onBeforeUnload);

  // ---- wiring: manager and panel ----------------------------------------------------------

  const manager = new LayoutManager({
    state,
    host,
    api,
    specs,
    notify: (msg, kind) => panel?.notify(msg, kind),
    changed: () => panel?.refreshLayoutSection(),
  });

  function addWidget(type) {
    const spec = specs[type];
    const stagger = (addCount++ % 6) * 20;
    const x = snap((state.layout.width - spec.size.w) / 2, grid) + stagger;
    const y = snap((state.layout.height - spec.size.h) / 2, grid) + stagger;
    if (state.add(type, x, y) === null) panel.notify("This dashboard has reached the widget limit", "warn");
  }

  async function done() {
    if (!(await manager.resolveUnsaved("leave edit mode"))) return;
    close();
  }

  function close() {
    clearInterval(statusTimer);
    document.removeEventListener("keydown", onKey);
    document.removeEventListener("pointerdown", onStagePointerDown);
    window.removeEventListener("beforeunload", onBeforeUnload);
    panel?.destroy();
    layer.remove();
    document.body.classList.remove("editing");
    canvas.classList.remove("no-grid");
    host.setInsets(0);
    host.editorClosed(); // reloads what is stored, so the window shows exactly what a new window would
  }

  panel = new Panel({
    state,
    manager,
    host,
    specs,
    pick: (opts) => pickChannel({ channels: host.getCatalog().channels, getValues: host.getValues, connected: host.getStatus().connected, ...opts }),
    addWidget,
    done,
    getGrid: () => grid,
    setGrid: (g) => {
      grid = g;
      localStorage.setItem(GRID_KEY, String(g));
      applyGrid();
    },
  });
  statusTimer = setInterval(() => panel.refreshStatus(), 500);
  rebuildFrames();
  manager.refresh();
  return { close, state };
}

const rectOf = (w) => ({ x: w.x, y: w.y, w: w.w, h: w.h });

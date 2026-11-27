// The editor's right-hand panel. Sections: header, Layout, Add widget, Selection, Display, footer.
// Built with createElement/textContent only. The panel is redrawn per section: typing in a property
// field must not rebuild that field (it would lose focus), so property changes do NOT redraw the
// selection section; only selection/structure changes (and unit-type changes) do.
import { h } from "../dom.js";
import { MAX_PERCENT, MIN_PERCENT, scaleSetting } from "../scaling.js";
import { ALERTS, MAX_LIMIT_MARGIN, isAlertOn } from "../alerts.js";
import { THEME_KEYS, themeColour } from "../theme.js";
import { UNIT_OPTIONS, resolveUnit } from "../units.js";
import { buildControl } from "./controls.js";

export const GRID_CHOICES = [0, 5, 10, 20, 50];

function button(parent, text, onClick, { cls = "", title = "", disabled = false } = {}) {
  const b = h("button", `btn ${cls}`.trim(), parent, text);
  b.type = "button";
  if (title) b.title = title;
  b.disabled = disabled;
  b.addEventListener("click", onClick);
  return b;
}

function numberField(parent, label, value, onChange, { min, max, step = "any", live = false } = {}) {
  const wrap = h("label", "gf", parent);
  h("span", "gf-label", wrap, label);
  const input = h("input", "pr-input", wrap);
  input.type = "number";
  input.step = step;
  if (min !== undefined) input.min = min;
  if (max !== undefined) input.max = max;
  input.value = value;
  const apply = () => {
    const n = Number(input.value);
    if (input.value.trim() !== "" && Number.isFinite(n)) onChange(n);
  };
  input.addEventListener("change", apply);
  if (live) input.addEventListener("input", apply);
  return input;
}

export class Panel {
  /**
   * @param {{state, manager, host, specs, pick, addWidget, done, getGrid, setGrid}} deps
   */
  constructor(deps) {
    Object.assign(this, deps);
    this.toastTimer = null;
    this.root = h("aside", "ed-panel", document.body);
    this.root.setAttribute("aria-label", "Dashboard editor");
    this.build();
    this.refreshAll();
  }

  destroy() {
    clearTimeout(this.toastTimer);
    this.root.remove();
  }

  // ---- construction -----------------------------------------------------------------------

  build() {
    const head = h("header", "ed-head", this.root);
    const titleRow = h("div", "ed-title-row", head);
    h("h1", "ed-title", titleRow, "Edit dashboard");
    this.chip = h("span", "ed-chip", titleRow);
    const bar = h("div", "ed-bar", head);
    this.undoBtn = button(bar, "Undo", () => this.state.undo(), { title: "Ctrl+Z" });
    this.redoBtn = button(bar, "Redo", () => this.state.redo(), { title: "Ctrl+Y" });
    button(bar, "Done", () => this.done(), { cls: "btn-primary", title: "E" });

    const scroll = h("div", "ed-scroll", this.root);
    this.scroller = scroll;

    // Selection
    this.selectionSection = this.section(scroll, "Selection");
    this.selectionBody = h("div", "sel-body", this.selectionSection);

    // Add widget
    const add = this.section(scroll, "Add widget");
    const grid = h("div", "palette", add);
    for (const [type, spec] of Object.entries(this.specs)) {
      button(grid, spec.label, () => this.addWidget(type), { cls: "palette-btn" });
    }

    // Layout
    const lay = this.section(scroll, "Dashboard");
    this.layoutSelect = h("select", "pr-input", lay);
    this.layoutSelect.setAttribute("aria-label", "Dashboard");
    this.layoutSelect.addEventListener("change", async () => {
      const ok = await this.manager.switchTo(this.layoutSelect.value);
      if (!ok) this.refreshLayoutSection(); // put the select back on the layout that is still open
    });
    this.nameInput = h("input", "pr-input", lay);
    this.nameInput.type = "text";
    this.nameInput.maxLength = 60;
    this.nameInput.setAttribute("aria-label", "Dashboard name");
    this.nameInput.addEventListener("input", () => this.state.setName(this.nameInput.value));
    this.sizeRow = h("div", "gf-row", lay);
    // The skin is how this dashboard LOOKS (a display setting, like units), not part of what it contains: changing it
    // never marks the layout unsaved and is not undone by Undo.
    const skinRow = h("label", "pr", lay);
    h("span", "pr-label", skinRow, "Look (skin)");
    this.skinSelect = h("select", "pr-input", h("div", "pr-body", skinRow));
    for (const { id, label } of this.host.skinChoices) {
      const o = h("option", "", this.skinSelect, label);
      o.value = id;
    }
    this.skinSelect.addEventListener("change", async () => {
      try {
        await this.host.setSkin(this.skinSelect.value);
      } catch (e) {
        this.notify(`Could not change the look: ${e.message}`, "error");
      }
      this.refreshMeta();
      this.refreshSelection(); // which properties apply depends on the skin
    });
    const matchRow = h("div", "btn-row", lay);
    button(matchRow, "Match window size", () => this.matchCanvas(window.innerWidth, window.innerHeight), { title: "Set the canvas to this window's current size, so the layout is designed 1:1 for it" });
    button(matchRow, "Match screen size", () => this.matchCanvas(window.screen.width, window.screen.height), { title: "Set the canvas to this monitor's resolution (use this for a fullscreen dashboard)" });
    const scaleRow = h("label", "pr", lay);
    h("span", "pr-label", scaleRow, "Scale in window");
    this.scaleSelect = h("select", "pr-input", h("div", "pr-body", scaleRow));
    for (const [value, label] of [["max", "Fit, but no bigger than..."], ["fit", "Always fill the window"], ["fixed", "Fixed size"]]) {
      const o = h("option", "", this.scaleSelect, label);
      o.value = value;
    }
    this.scaleSelect.addEventListener("change", () => {
      this.host.clearZoom(); // an older +/- zoom would otherwise override the setting being chosen
      this.state.setScale(this.scaleSelect.value, scaleSetting(this.state.layout).percent);
    });
    this.percentRow = h("label", "pr", lay);
    h("span", "pr-label", this.percentRow, "Size (%)");
    this.percentInput = h("input", "pr-input", h("div", "pr-body", this.percentRow));
    this.percentInput.type = "number";
    this.percentInput.min = MIN_PERCENT;
    this.percentInput.max = MAX_PERCENT;
    this.percentInput.step = "5";
    this.percentInput.addEventListener("change", () => {
      const n = Number(this.percentInput.value);
      this.host.clearZoom();
      if (Number.isFinite(n)) this.state.setScale(this.state.layout.scaleMode, n);
      this.refreshScale();
    });
    h("p", "ed-note", lay, "Editing always shows the whole canvas. Leave edit mode to see the scale. Outside edit mode, + / - or Ctrl+scroll zoom this window and 0 resets.");
    this.builtinNote = h("p", "ed-note", lay);
    const actions = h("div", "btn-row", lay);
    this.saveAsBtn = button(actions, "Save as...", () => this.manager.saveAs());
    button(actions, "New", () => this.manager.newLayout());
    this.deleteBtn = button(actions, "Delete", () => this.manager.deleteCurrent(), { cls: "btn-danger-text" });
    const actions2 = h("div", "btn-row", lay);
    const file = h("input", "visually-hidden", lay);
    file.type = "file";
    file.accept = ".json,application/json";
    file.addEventListener("change", async () => {
      await this.manager.importFile(file.files[0]);
      file.value = "";
    });
    button(actions2, "Import...", () => file.click());
    button(actions2, "Export", () => this.manager.exportCurrent());
    this.windowBtn = button(actions2, "New window", () => this.manager.openInNewWindow(), { title: "Open another window the same size as this one (less this panel), with the Dashboards menu open to pick what it shows" });
    this.defaultBtn = button(actions2, "Use as default", () => this.manager.setAsDefault(), { title: "Open this dashboard in new windows" });

    // Colours: the theme saved with this dashboard (the flat look). Not a display setting like the skin: it is part of the dashboard, undoable, and saved with it.
    const colours = this.section(scroll, "Colours");
    h("p", "ed-note", colours, "The colours of the flat look, saved with this dashboard. The other looks have their own colours.");
    this.themeRows = {};
    for (const [key, label] of THEME_KEYS) {
      const row = h("label", "pr", colours);
      h("span", "pr-label", row, label);
      const body = h("div", "pr-body pr-colour", row);
      const input = h("input", "pr-color", body);
      input.type = "color";
      input.dataset.theme = key;
      input.setAttribute("aria-label", `${label} colour`);
      input.addEventListener("input", () => this.state.setThemeColour(key, input.value));
      const reset = h("button", "btn small", body, "Default");
      reset.type = "button";
      reset.dataset.themeReset = key;
      reset.setAttribute("aria-label", `${label}: back to the default colour`);
      reset.addEventListener("click", (e) => {
        e.preventDefault();
        this.state.setThemeColour(key, undefined);
      });
      this.themeRows[key] = { input, reset };
    }
    this.themeClear = button(h("div", "btn-row", colours), "Reset all colours", () => this.state.clearTheme());

    // Display
    const disp = this.section(scroll, "Display");
    this.unitsBox = h("div", "unit-grid", disp);
    const gridRow = h("label", "pr", disp);
    h("span", "pr-label", gridRow, "Snap to grid");
    this.gridSelect = h("select", "pr-input", h("div", "pr-body", gridRow));
    for (const g of GRID_CHOICES) {
      const o = h("option", "", this.gridSelect, g === 0 ? "Off" : `${g} px`);
      o.value = String(g);
    }
    this.gridSelect.value = String(this.getGrid());
    this.gridSelect.addEventListener("change", () => this.setGrid(Number(this.gridSelect.value)));

    // Vehicle dashboards: which dashboard goes with which vehicle (assigned from the Dashboards menu while driving).
    const veh = this.section(scroll, "Vehicle dashboards");
    h("p", "ed-note", veh, "A window that has not been set to one dashboard switches to the dashboard assigned to the vehicle you are driving. Assign one from the Dashboards menu.");
    this.autoRow = h("label", "pr-check-row", veh);
    this.autoBox = h("input", "pr-check", this.autoRow);
    this.autoBox.type = "checkbox";
    h("span", "", this.autoRow, "Switch automatically when the vehicle changes");
    this.autoBox.addEventListener("change", async () => {
      try {
        await this.manager.api.saveSettings({ autoSwitch: this.autoBox.checked });
      } catch (e) {
        this.notify(`Could not save: ${e.message}`, "error");
      }
      this.refreshRules();
    });
    this.rulesList = h("div", "rules-list", veh);

    // Windows: reopening the dashboard windows that were open when ThrashDash last ran.
    const win = this.section(scroll, "Windows");
    h("p", "ed-note", win, "ThrashDash remembers which dashboard windows are open, and how big. Next time it starts it opens them again.");
    this.restoreRow = h("label", "pr-check-row", win);
    this.restoreBox = h("input", "pr-check", this.restoreRow);
    this.restoreBox.type = "checkbox";
    h("span", "", this.restoreRow, "Reopen my windows when ThrashDash starts");
    this.closeRow = h("label", "pr-check-row", win);
    this.closeBox = h("input", "pr-check", this.closeRow);
    this.closeBox.type = "checkbox";
    h("span", "", this.closeRow, "Close my windows when ThrashDash closes");
    h("p", "ed-note", win, "The dashboard windows and the map window ThrashDash opened are closed with it (and come back next time if the box above is ticked). Other devices on your network are not affected.");
    this.exactRow = h("label", "pr-check-row", win);
    this.exactBox = h("input", "pr-check", this.exactRow);
    this.exactBox.type = "checkbox";
    h("span", "", this.exactRow, "Place windows exactly");
    h("p", "ed-note", win, "Where a window goes is normally decided by the desktop (on Linux with Wayland, programs cannot choose). This option starts the dashboard windows in X11 mode so they can be put back exactly where they were. It applies the next time the dashboard windows are opened from scratch, after all of them have been closed.");
    for (const [box, key] of [[this.restoreBox, "restoreWindows"], [this.closeBox, "closeWindowsOnExit"], [this.exactBox, "exactWindowPlacement"]]) {
      box.addEventListener("change", async () => {
        try {
          await this.manager.api.saveSettings({ [key]: box.checked });
        } catch (e) {
          this.notify(`Could not save: ${e.message}`, "error");
        }
        this.refreshWindows();
      });
    }

    // Alerts: the banner shown over every dashboard (and on phones). Display settings, like units.
    const al = this.section(scroll, "Alerts");
    h("p", "ed-note", al, "A banner over the dashboard when the game reports a problem. Choose which ones you want.");
    this.alertBoxes = [];
    const master = h("label", "pr-check-row", al);
    this.alertMaster = h("input", "pr-check", master);
    this.alertMaster.type = "checkbox";
    h("span", "", master, "Show alerts");
    this.alertMaster.addEventListener("change", () => this.saveAlerts());
    for (const alert of ALERTS) {
      const row = h("label", "pr-check-row al-opt", al);
      const box = h("input", "pr-check", row);
      box.type = "checkbox";
      box.dataset.alert = alert.id;
      h("span", `al-dot al-dot-${alert.level}`, row);
      h("span", "", row, alert.label);
      box.addEventListener("change", () => this.saveAlerts());
      this.alertBoxes.push(box);
    }
    // A chime when an alert appears: a setting of THIS WINDOW (kept in this browser), off until switched on.
    const sound = this.host.alertSound;
    const soundRow = h("label", "pr-check-row", al);
    this.soundBox = h("input", "pr-check", soundRow);
    this.soundBox.type = "checkbox";
    this.soundBox.dataset.soundOn = "1";
    this.soundBox.checked = sound.settings.on;
    h("span", "", soundRow, "Play a sound when an alert appears (this window only)");
    this.soundBox.addEventListener("change", () => {
      sound.set({ on: this.soundBox.checked });
      if (this.soundBox.checked) sound.test(); // (the click that switches it on is also the click a browser wants before it lets sound through)
      this.refreshSound();
    });
    const volumeRow = h("label", "pr", al);
    h("span", "pr-label", volumeRow, "Volume");
    this.soundVolume = h("input", "pr-input", h("div", "pr-body pr-colour", volumeRow));
    this.soundVolume.type = "range";
    this.soundVolume.min = "0";
    this.soundVolume.max = "1";
    this.soundVolume.step = "0.05";
    this.soundVolume.value = String(sound.settings.volume);
    this.soundVolume.dataset.soundVolume = "1";
    this.soundVolume.addEventListener("input", () => sound.set({ volume: Number(this.soundVolume.value) }));
    const tests = h("div", "btn-row", al);
    button(tests, "Test: warning", () => sound.test("warn"));
    button(tests, "Test: critical", () => sound.test("crit"));
    this.soundNote = h("p", "ed-note", al, "");
    this.soundNote.hidden = true;
    // How far over a posted limit before that alert. Kept by the relay in m/s, shown in the unit chosen for speed.
    this.marginInput = numberField(al, "Over-limit margin", 0, (shown) => this.saveLimitMargin(shown), { min: 0, step: "1" });
    this.marginLabel = this.marginInput.previousSibling;
    h("p", "ed-note", al, "The speed limit alert only uses limits read from a real sign, never a guess from the kind of road, and waits two seconds before it shows.");

    const help = h("details", "ed-help", scroll);
    h("summary", "", help, "Keyboard shortcuts");
    const keys = [
      ["Click / Shift+Click", "select / add to selection"], ["Drag", "move (Alt = no snapping)"], ["Drag a handle", "resize (Shift = keep shape)"],
      ["Arrows", "nudge 1 px (Shift = 10 px)"], ["Ctrl+A", "select all"], ["Ctrl+C / Ctrl+V", "copy / paste"], ["Ctrl+D", "duplicate"],
      ["Delete", "delete"], ["[ and ]", "send backward / bring forward"], ["Ctrl+Z / Ctrl+Y", "undo / redo"], ["Ctrl+S", "save"], ["Esc", "cancel drag / deselect"], ["E", "leave edit mode"],
    ];
    const dl = h("dl", "keys", help);
    for (const [k, d] of keys) {
      h("dt", "", dl, k);
      h("dd", "", dl, d);
    }

    const foot = h("footer", "ed-foot", this.root);
    this.toast = h("div", "ed-toast", foot);
    this.saveBtn = button(foot, "Save", () => this.manager.save(), { cls: "btn-primary btn-wide", title: "Ctrl+S" });
    this.dirtyNote = h("div", "ed-dirty", foot);
  }

  section(parent, title) {
    const sec = h("section", "ed-section", parent);
    h("h2", "ed-h2", sec, title);
    return sec;
  }

  // ---- refreshers -------------------------------------------------------------------------

  refreshAll() {
    this.refreshRules();
    this.refreshMeta();
    this.refreshLayoutSection();
    this.refreshSelection();
    this.refreshDisplay();
    this.refreshAlerts();
    this.refreshWindows();
    this.refreshStatus();
  }

  /** Header buttons, name, canvas size, save state. Called on every "meta"/"history" event. */
  refreshMeta() {
    const { state } = this;
    this.undoBtn.disabled = !state.canUndo;
    this.redoBtn.disabled = !state.canRedo;
    if (document.activeElement !== this.nameInput) this.nameInput.value = state.layout.name;
    this.refreshSize();
    this.refreshScale();
    this.refreshTheme();
    if (document.activeElement !== this.skinSelect) this.skinSelect.value = this.host.getSkin();
    const dirty = state.dirty;
    const builtin = state.meta.builtin;
    this.saveBtn.textContent = builtin || !state.meta.id ? "Save as my dashboard..." : "Save";
    this.saveBtn.disabled = !dirty && !builtin && !!state.meta.id;
    this.dirtyNote.textContent = dirty ? "Unsaved changes" : state.meta.id && !builtin ? "All changes saved" : "";
    this.dirtyNote.classList.toggle("is-dirty", dirty);
    this.deleteBtn.disabled = builtin || !state.meta.id;
    this.windowBtn.disabled = !state.meta.id;
    this.defaultBtn.disabled = !state.meta.id || this.manager.defaultId === state.meta.id;
    this.builtinNote.textContent = builtin ? "Built-in and read-only: your changes are saved as a new dashboard." : !state.meta.id ? "Not saved yet." : "";
    this.builtinNote.hidden = !this.builtinNote.textContent;
  }

  refreshSize() {
    const { layout } = this.state;
    if (!this.sizeRow.firstChild) {
      this.wField = numberField(this.sizeRow, "Canvas width", layout.width, (n) => this.state.setCanvasSize(n, this.state.layout.height), { min: 200, max: 4000, step: "1" });
      this.hField = numberField(this.sizeRow, "Canvas height", layout.height, (n) => this.state.setCanvasSize(this.state.layout.width, n), { min: 100, max: 4000, step: "1" });
    }
    if (document.activeElement !== this.wField) this.wField.value = layout.width;
    if (document.activeElement !== this.hField) this.hField.value = layout.height;
  }

  refreshTheme() {
    const { theme } = this.state.layout;
    for (const [key, { input, reset }] of Object.entries(this.themeRows)) {
      if (document.activeElement !== input) input.value = themeColour(theme, key);
      reset.disabled = !theme?.[key];
    }
    this.themeClear.disabled = !theme;
  }

  refreshScale() {
    const { mode, percent } = scaleSetting(this.state.layout);
    if (document.activeElement !== this.scaleSelect) this.scaleSelect.value = mode;
    if (document.activeElement !== this.percentInput) this.percentInput.value = percent;
    this.percentRow.hidden = mode === "fit";
  }

  /** Set the canvas to a measured size (window or screen). Widgets keep their positions. */
  matchCanvas(width, height) {
    this.state.setCanvasSize(Math.round(width), Math.round(height));
    this.notify(`Canvas set to ${this.state.layout.width} \u00d7 ${this.state.layout.height}`);
  }

  refreshLayoutSection() {
    const { state, manager } = this;
    const select = this.layoutSelect;
    select.replaceChildren();
    if (!state.meta.id) {
      const o = h("option", "", select, "(new, unsaved)");
      o.value = "";
    }
    for (const item of manager.list) {
      const star = item.id === manager.defaultId ? " ★" : "";
      const o = h("option", "", select, `${item.name}${item.builtin ? " (built-in)" : ""}${star}`);
      o.value = item.id;
    }
    select.value = state.meta.id ?? "";
    this.refreshMeta();
    this.refreshRules(); // rules name dashboards, so redraw them once the dashboard list is known
  }

  refreshStatus() {
    const s = this.host.getStatus();
    const name = String(s.vehicle ?? "").split("/").filter(Boolean).pop();
    this.chip.textContent = s.connected ? (s.simulated ? "Simulated data" : `Live: ${name || "vehicle"}`) : "No data";
    this.chip.classList.toggle("ok", s.connected);
    this.chip.title = s.connected ? "" : "Widgets show -- until the game (or --simulate) sends data. You can still edit.";
  }

  async refreshWindows() {
    let settings;
    try {
      settings = await this.manager.api.getSettings();
    } catch {
      return;
    }
    this.restoreBox.checked = settings.restoreWindows !== false;
    this.closeBox.checked = settings.closeWindowsOnExit !== false;
    this.exactBox.checked = settings.exactWindowPlacement === true;
  }

  async saveAlerts() {
    const optIn = new Set(ALERTS.filter((a) => a.optIn).map((a) => a.id));
    const off = this.alertBoxes.filter((b) => !b.checked && !optIn.has(b.dataset.alert)).map((b) => b.dataset.alert);
    const on = this.alertBoxes.filter((b) => b.checked && optIn.has(b.dataset.alert)).map((b) => b.dataset.alert);
    try {
      await this.host.saveAlerts({ enabled: this.alertMaster.checked, off, on });
    } catch (e) {
      this.notify(`Could not save: ${e.message}`, "error");
    }
    this.refreshAlerts();
  }

  /** The margin the user typed, in their speed unit, saved as metres a second (what the relay and the alert rule use). */
  async saveLimitMargin(shown) {
    const unit = resolveUnit("speed", this.host.getPrefs());
    const perMs = unit.convert(1); // how many of the shown unit one m/s is
    const ms = Math.min(MAX_LIMIT_MARGIN, Math.max(0, shown / perMs));
    try {
      await this.host.saveAlerts({ limitMargin: ms });
    } catch (e) {
      this.notify(`Could not save: ${e.message}`, "error");
    }
    this.refreshAlerts();
  }

  refreshSound() {
    const sound = this.host.alertSound;
    this.soundVolume.disabled = !this.soundBox.checked;
    const wanted = sound.settings.on && sound.blocked;
    this.soundNote.textContent = wanted ? "This browser has not allowed sound yet: click anywhere in this window once (the Test buttons will do) and it will." : "";
    this.soundNote.hidden = !wanted;
  }

  refreshAlerts() {
    this.refreshSound();
    const settings = this.host.getAlerts();
    this.alertMaster.checked = settings.enabled;
    for (const box of this.alertBoxes) {
      box.checked = isAlertOn(ALERTS.find((a) => a.id === box.dataset.alert), settings);
      box.disabled = !settings.enabled;
    }
    const unit = resolveUnit("speed", this.host.getPrefs());
    this.marginLabel.textContent = `Over-limit margin (${unit.label})`;
    if (document.activeElement !== this.marginInput) this.marginInput.value = String(Math.round(unit.convert(settings.limitMargin)));
    this.marginInput.disabled = !settings.enabled || settings.off.includes("overLimit");
  }

  refreshDisplay() {
    this.unitsBox.replaceChildren();
    const prefs = this.host.getPrefs();
    for (const [kind, title] of [["speed", "Speed"], ["temp", "Temperature"], ["distance", "Distance"], ["pressure", "Pressure"], ["economy", "Fuel economy"]]) {
      const row = h("label", "pr", this.unitsBox);
      h("span", "pr-label", row, title);
      const select = h("select", "pr-input", h("div", "pr-body", row));
      for (const u of UNIT_OPTIONS[kind]) {
        const o = h("option", "", select, u.label);
        o.value = u.key;
      }
      select.value = prefs[kind];
      select.addEventListener("change", () => this.host.saveUnit(kind, select.value));
    }
  }

  /** Redraw the Selection section (selection or structure changed, or a unit type changed). */
  refreshSelection() {
    const { state } = this;
    const body = this.selectionBody;
    body.replaceChildren();
    const sel = state.selectedWidgets;

    if (!sel.length) {
      this.lastSelectedId = null;
      h("p", "ed-note", body, "Click a widget to edit it. Shift-click to select several, or drag a box round them. Drag to move, drag a handle to resize.");
      return;
    }

    const actions = h("div", "btn-row", body);
    button(actions, "Duplicate", () => state.duplicate(), { title: "Ctrl+D" });
    button(actions, "Delete", () => state.remove(), { cls: "btn-danger-text", title: "Delete" });
    const order = h("div", "btn-row", body);
    button(order, "To back", () => state.reorder("back"));
    button(order, "Backward", () => state.reorder("backward"), { title: "[" });
    button(order, "Forward", () => state.reorder("forward"), { title: "]" });
    button(order, "To front", () => state.reorder("front"));

    if (sel.length > 1) {
      // Line them up with each other, or space them evenly: each is one undo step.
      h("div", "sel-title", body, "Align");
      const align = h("div", "btn-row", body);
      for (const [mode, label, title] of [["left", "Left", "Line up their left edges"], ["centre", "Centre", "Line up their middles, side to side"], ["right", "Right", "Line up their right edges"],
                                          ["top", "Top", "Line up their top edges"], ["middle", "Middle", "Line up their middles, top to bottom"], ["bottom", "Bottom", "Line up their bottom edges"]]) {
        const b = button(align, label, () => state.alignSelection(mode), { title });
        b.dataset.align = mode;
      }
      h("div", "sel-title", body, "Space evenly");
      const spread = h("div", "btn-row", body);
      const across = button(spread, "Across", () => state.distributeSelection("h"), { title: "Equal gaps from side to side, the outer two staying where they are (three or more)", disabled: sel.length < 3 });
      across.dataset.distribute = "h";
      const down = button(spread, "Down", () => state.distributeSelection("v"), { title: "Equal gaps from top to bottom, the outer two staying where they are (three or more)", disabled: sel.length < 3 });
      down.dataset.distribute = "v";
      h("p", "ed-note", body, `${sel.length} widgets selected. Select just one to edit its properties.`);
      return;
    }

    const w = sel[0];
    if (this.lastSelectedId !== w.id) this.scroller.scrollTop = 0; // a newly selected widget's properties start at the top
    this.lastSelectedId = w.id;
    const spec = this.specs[w.type];
    h("div", "sel-title", body, `${spec.label}  ·  ${w.id}`);
    this.geoRow = h("div", "gf-row gf-4", body);
    this.geoInputs = {};
    for (const key of ["x", "y", "w", "h"]) {
      this.geoInputs[key] = numberField(this.geoRow, key.toUpperCase(), w[key], (n) => state.setGeometry(w.id, key, n), { step: "1", live: true });
    }
    const ctx = {
      state,
      catalog: () => this.host.getCatalog(),
      pick: (opts) => this.pick(opts),
      rebuild: () => this.refreshSelection(),
    };
    // Properties that only mean something with (or without) a skin are hidden on the other kind of dashboard.
    const clean = this.host.getSkin() === "clean";
    for (const [key, ps] of Object.entries(spec.props)) {
      if ((ps.skinOnly && clean) || (ps.cleanOnly && !clean)) continue;
      body.appendChild(buildControl(ctx, w, key, ps));
    }
  }

  /** Redraw the list of vehicle -> dashboard rules from the relay's settings. */
  async refreshRules() {
    let settings;
    try {
      settings = await this.manager.api.getSettings();
    } catch {
      return;
    }
    this.autoBox.checked = settings.autoSwitch !== false;
    this.rulesList.replaceChildren();
    const names = new Map(this.manager.list.map((l) => [l.id, l.name]));
    const rules = Object.entries(settings.vehicleDashboards ?? {});
    if (!rules.length) h("p", "ed-note", this.rulesList, "No vehicle rules yet.");
    for (const [vehicle, id] of rules) {
      const row = h("div", "rule-row", this.rulesList);
      h("span", "rule-vehicle", row, vehicle);
      h("span", "rule-arrow", row, "\u2192");
      h("span", "rule-target", row, names.get(id) ?? id);
      const del = h("button", "pr-reset rule-x", row, "\u00d7");
      del.type = "button";
      del.title = `Stop using it for ${vehicle}`;
      del.setAttribute("aria-label", `Remove the rule for ${vehicle}`);
      del.addEventListener("click", async () => {
        try {
          await this.manager.api.saveSettings({ vehicleDashboards: { [vehicle]: null } });
        } catch (e) {
          this.notify(`Could not save: ${e.message}`, "error");
        }
        this.refreshRules();
      });
    }
  }

  /** Keep the X/Y/W/H fields in step with drags, without stealing focus from a field being typed in. */
  refreshGeometry() {
    const [w] = this.state.selectedWidgets;
    if (!w || this.state.selection.length !== 1 || !this.geoInputs) return;
    for (const [key, input] of Object.entries(this.geoInputs)) if (document.activeElement !== input) input.value = w[key];
  }

  notify(message, kind = "info") {
    clearTimeout(this.toastTimer);
    this.toast.textContent = message;
    this.toast.dataset.kind = kind;
    this.toastTimer = setTimeout(() => (this.toast.textContent = ""), 4000);
  }
}

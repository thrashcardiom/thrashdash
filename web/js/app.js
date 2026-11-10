import { alertSettings, createAlertBanner, createAlertTracker } from "./alerts.js";
import { api } from "./api.js";
import { buildDashboard, fitCanvas } from "./dashboard.js";
import { sanitizeLayout } from "./layoutSchema.js";
import { scaleSetting, stepPercent } from "./scaling.js";
import { SKIN_CHOICES, SKIN_LABELS } from "./skins/themes.js";
import { confirmDialog, infoDialog } from "./editor/dialog.js";
import { createSwitcher } from "./switcher.js";
import { buildPrefs } from "./units.js";
import { createWindowReporter, ensureWindowId } from "./windowState.js";
import { openDashboardWindow } from "./windows.js";
import { specs } from "./widgets/index.js";

const canvas = document.getElementById("canvas");
const widgetsEl = document.getElementById("widgets");
const overlayTitle = document.getElementById("overlay-title");
const overlayDetail = document.getElementById("overlay-detail");
const editButton = document.getElementById("edit-button");
const controls = document.getElementById("top-controls");

/** Everything this page knows: which layout is shown, units, and the latest data from the relay. */
const app = {
  id: null,
  builtin: true,
  layout: null,
  prefs: buildPrefs({}, location.search),
  values: {}, // newest frame (flattened channels)
  catalog: { vehicle: null, channels: {} },
  status: { connected: false, vehicle: null, simulated: false },
  client: { local: true, lan: { enabled: false, urls: [] } }, // what THIS device may do: only the computer running ThrashDash can change things
  alerts: alertSettings(null), // which alerts are on (a display setting kept by the relay)
  skins: {}, // dashboard id -> skin chosen for it (a display setting kept by the relay, like units)
  unsavedSkin: "clean", // the skin of a brand-new dashboard that has no id yet
  insetRight: 0, // pixels the editor panel takes from the right edge
  scale: 1, // scale currently applied to the canvas
};
let dashboard = null;
let editor = null;

// This window's id lives in its address (?w=...), so a reload is the same window; the relay uses it to remember which
// windows are open (dashboard, size, position) and reopen them at the next start. Only the computer running ThrashDash reports.
const windowId = ensureWindowId();
const windowReporter = createWindowReporter({
  id: windowId,
  state: () => ({ id: app.id, pinned: new URLSearchParams(location.search).has("d") }),
  send: (report) => api.putWindow(windowId, report),
  forget: (stamp) => api.forgetWindow(windowId, stamp).catch(() => {}),
});

// ---- loading and drawing ----------------------------------------------------------------

/** Load the layout this window should show: ?d=<id>, else the saved default, else the built-in one. */
async function loadLayout() {
  let settings = { units: {}, defaultLayout: "default", skins: {}, vehicleDashboards: {}, autoSwitch: true };
  try {
    settings = await api.getSettings();
  } catch (e) {
    console.warn("ThrashDash: could not read settings, using defaults", e);
  }
  // Which dashboard: this window's own choice (?d=), else the rule for the vehicle being driven, else the default.
  const ruled = settings.autoSwitch !== false ? settings.vehicleDashboards?.[vehicleKey(app.status.vehicle)] : null;
  const wanted = new URLSearchParams(location.search).get("d") || ruled || settings.defaultLayout;
  let loaded;
  try {
    loaded = await api.getLayout(wanted);
  } catch (e) {
    if (wanted === "default") throw e;
    console.warn(`ThrashDash: layout "${wanted}" not available (${e.message}); showing the default`);
    loaded = await api.getLayout("default");
  }
  let client = app.client;
  try {
    client = await api.getClient();
  } catch (e) {
    console.warn("ThrashDash: could not tell whether this device may make changes; assuming it may not", e);
    client = { local: false, lan: { enabled: false, urls: [] } }; // the safe assumption: hide the controls that would fail
  }
  const { layout, warnings } = sanitizeLayout(loaded.layout, specs);
  for (const w of warnings) console.warn("ThrashDash layout:", w);
  return { id: loaded.id, builtin: loaded.builtin, layout, prefs: buildPrefs(settings.units, location.search), skins: settings.skins ?? {}, alerts: alertSettings(settings.alerts), client };
}

// ---- scale: the layout's own setting, optionally overridden per dashboard by this window's zoom ----

const zoomKey = () => `thrashdash.zoom.${app.id}`;

/** The zoom the user set with +/- or Ctrl+scroll for this dashboard on this computer, if any. */
function loadZoomOverride() {
  try {
    const raw = JSON.parse(localStorage.getItem(zoomKey()) ?? "null");
    return raw && typeof raw === "object" ? scaleSetting(raw) : null;
  } catch {
    return null;
  }
}

const editing = () => document.body.classList.contains("editing");

function fit(layout = app.layout) {
  app.layout = layout;
  // While editing, the whole canvas is always shown so it can be edited; the scale setting applies in normal view.
  app.scale = fitCanvas(canvas, layout, { insetRight: app.insetRight, setting: loadZoomOverride() ?? layout, fitAll: editing() });
}

// ---- skin: how the open dashboard LOOKS. Separate from what it contains, so it never touches the layout ----

const deviceSkinKey = (id) => `thrashdash.skin.${id}`;

/**
 * The skin for the open dashboard ("clean" unless one was chosen for it). A phone or tablet on the network cannot change
 * the relay's settings, so its choice is remembered on that device only (and does not change the look on the PC).
 */
function skinFor() {
  if (!app.id) return app.unsavedSkin ?? "clean";
  if (!app.client.local) {
    const own = localStorage.getItem(deviceSkinKey(app.id));
    if (own && SKIN_CHOICES.includes(own)) return own;
  }
  return app.skins[app.id] ?? "clean";
}

/** Choose the skin for the open dashboard and keep it (so it is still applied next time, and in other windows). */
async function setSkin(id) {
  if (!SKIN_CHOICES.includes(id)) return;
  if (app.id && !app.client.local) {
    localStorage.setItem(deviceSkinKey(app.id), id);
  } else if (app.id) {
    const settings = await api.saveSettings({ skins: { [app.id]: id } });
    app.skins = settings.skins ?? {};
  } else {
    app.unsavedSkin = id; // not saved yet: remembered with the dashboard once it is
  }
  if (app.layout) render(app.layout);
}

/** (Re)build the widgets for `layout` and feed them the latest data so there is no blank flash. */
function render(layout) {
  app.layout = layout;
  dashboard = buildDashboard(widgetsEl, layout, app.prefs, skinFor(), { canWrite: app.client.local });
  fit(layout);
  dashboard.update(app.values);
}

function showLoaded(result) {
  Object.assign(app, { id: result.id, builtin: result.builtin, prefs: result.prefs, skins: result.skins ?? app.skins, alerts: result.alerts ?? app.alerts, client: result.client ?? app.client }); // keep what we know if the caller did not reload settings
  render(result.layout);
  controls.hidden = false;
  editButton.hidden = !app.client.local; // a phone or tablet on the network can look, not edit
  if (app.client.local) windowReporter.start(); // (does nothing if already started)
  windowReporter.notify();
}

window.addEventListener("resize", () => app.layout && fit());

// ---- zoom in normal view: + / - / 0 and Ctrl+scroll ----

const zoomToast = document.getElementById("zoom-toast");
let zoomToastTimer = null;
function showZoomToast(text) { // a small status line at the bottom, used for zoom and skin changes
  zoomToast.textContent = text;
  zoomToast.classList.add("show");
  clearTimeout(zoomToastTimer);
  zoomToastTimer = setTimeout(() => zoomToast.classList.remove("show"), 1200);
}

/** direction: +1 / -1 for one step, 0 to reset to the dashboard's own scale setting. */
function zoom(direction) {
  if (!app.layout || !app.id || editing()) return;
  if (direction === 0) {
    localStorage.removeItem(zoomKey());
    fit();
    showZoomToast("Zoom reset");
    return;
  }
  const percent = stepPercent(app.scale, direction);
  localStorage.setItem(zoomKey(), JSON.stringify({ mode: "fixed", percent }));
  fit();
  showZoomToast(`Zoom ${percent}%`);
}

let wheelAccum = 0;
window.addEventListener(
  "wheel",
  (e) => {
    if (!e.ctrlKey) return;
    e.preventDefault(); // otherwise the browser zooms the whole page instead
    if (editing()) return;
    wheelAccum += e.deltaY;
    if (Math.abs(wheelAccum) >= 40) {
      zoom(wheelAccum < 0 ? 1 : -1);
      wheelAccum = 0;
    }
  },
  { passive: false },
);

// Double-click toggles fullscreen (F11 also works in an app window). Not while editing: double-clicks
// there are for the editor.
document.addEventListener("dblclick", () => {
  if (document.body.classList.contains("editing")) return;
  if (document.fullscreenElement) document.exitFullscreen();
  else document.documentElement.requestFullscreen().catch(() => {});
});

// ---- editing ----------------------------------------------------------------------------

/** What the editor needs from the page. Kept as one object so the editor never reaches into globals. */
const host = {
  canvas,
  specs,
  api,
  getInfo: () => ({ layout: app.layout, id: app.id, builtin: app.builtin }),
  render,
  fit,
  getElements: () => dashboard?.elements ?? new Map(),
  getScale: () => (app.layout ? canvas.getBoundingClientRect().width / app.layout.width : 1),
  setInsets(px) {
    app.insetRight = px;
    fit();
  },
  getSkin: skinFor,
  setSkin,
  skinChoices: SKIN_CHOICES.map((id) => ({ id, label: SKIN_LABELS[id] })),
  getInsetRight: () => app.insetRight,
  getAlerts: () => app.alerts,
  /** Persist which alerts are on ({enabled, off}) and apply it at once. */
  async saveAlerts(patch) {
    const settings = await api.saveSettings({ alerts: patch });
    app.alerts = alertSettings(settings.alerts);
  },
  getValues: () => app.values,
  getCatalog: () => app.catalog,
  getStatus: () => app.status,
  getPrefs: () => app.prefs,
  /** Persist a unit choice and apply it immediately. A ?speed= style URL override would mask it, so drop that. */
  async saveUnit(kind, key) {
    const settings = await api.saveSettings({ units: { [kind]: key } });
    const url = new URL(location.href);
    url.searchParams.delete(kind);
    history.replaceState(null, "", url);
    app.prefs = buildPrefs(settings.units, location.search);
    render(app.layout);
  },
  /**
   * Reflect which stored layout is open in the address (so a reload or "new window" gets the same one).
   * With `inheritLook`, the dashboard keeps the skin it had (used by Save As, where the copy must not change appearance).
   */
  async setUrl(id, { inheritLook = false } = {}) {
    const look = skinFor();
    const url = new URL(location.href);
    if (id) url.searchParams.set("d", id);
    else url.searchParams.delete("d");
    history.replaceState(null, "", url);
    app.id = id;
    app.builtin = id === null ? false : app.builtin;
    if (inheritLook && look !== "clean") await setSkin(look);
    else if (id === null) app.unsavedSkin = look; // "New" starts from the look you were using
  },
  /** Forget this window's +/- zoom for the open dashboard (called when its Scale setting is changed in the editor). */
  clearZoom() {
    if (app.id) localStorage.removeItem(zoomKey());
  },
  /** Called by the editor when it closes: show exactly what is stored. */
  editorClosed() {
    editor = null;
    loadLayout().then(showLoaded, (e) => setState("error", "Could not load the dashboard layout", String(e.message || e)));
  },
};

function ensureEditorCss() {
  if (document.getElementById("editor-css")) return;
  const link = document.createElement("link");
  link.id = "editor-css";
  link.rel = "stylesheet";
  link.href = "/css/editor.css";
  document.head.appendChild(link);
}

async function startEditing() {
  if (editor || !app.layout || !app.client.local) return;
  ensureEditorCss();
  // Loaded on demand: the editor is ~10 files that a dashboard left running on a second monitor never needs.
  const { openEditor } = await import("./editor/editor.js");
  editor = openEditor(host);
}

editButton.addEventListener("click", startEditing);

/** True if this window was set to one dashboard (its address has ?d=), so it does not follow vehicle rules. */
const isPinned = () => new URLSearchParams(location.search).has("d");

/**
 * Show a stored dashboard in this window. `pin: true` records it in the address (?d=<id>), so a reload shows the same
 * one and the window stops following vehicle rules; `pin: false` leaves the address bare, so it keeps following them.
 */
async function showDashboard(id, { pin }) {
  if (editor) return;
  let loaded;
  try {
    loaded = await api.getLayout(id);
  } catch (e) {
    if (id === "default") throw e;
    console.warn(`ThrashDash: dashboard "${id}" not available (${e.message}); showing the default`);
    loaded = await api.getLayout("default");
  }
  const { layout, warnings } = sanitizeLayout(loaded.layout, specs);
  for (const w of warnings) console.warn("ThrashDash layout:", w);
  let skins = app.skins;
  let alerts = app.alerts;
  try {
    const settings = await api.getSettings();
    skins = settings.skins ?? skins; // another window may have changed a look
    alerts = alertSettings(settings.alerts);
  } catch {
    /* keep what we have */
  }
  showLoaded({ id: loaded.id, builtin: loaded.builtin, layout, prefs: app.prefs, skins, alerts });
  const url = new URL(location.href);
  if (pin) url.searchParams.set("d", loaded.id);
  else url.searchParams.delete("d");
  history.replaceState(null, "", url);
}

const switchDashboard = (id) => showDashboard(id, { pin: true });

/** Which dashboard the vehicle rules (or, failing that, the default) call for right now. */
async function ruledTarget() {
  const settings = await api.getSettings();
  const key = vehicleKey(app.status.vehicle);
  const rule = settings.autoSwitch !== false && key ? settings.vehicleDashboards?.[key] : null;
  return { target: rule || settings.defaultLayout, auto: settings.autoSwitch !== false };
}

/** A window that has not been set to one dashboard switches with the vehicle: its rule, or the default. */
async function followVehicle() {
  if (editing() || isPinned() || !app.layout) return;
  try {
    const { target, auto } = await ruledTarget();
    if (auto && target && target !== app.id) await showDashboard(target, { pin: false });
  } catch (e) {
    console.warn("ThrashDash: could not follow the vehicle rules", e);
  }
}

/** Let a pinned window follow the vehicle rules again. */
async function unpinAndFollow() {
  const url = new URL(location.href);
  url.searchParams.delete("d");
  history.replaceState(null, "", url);
  try {
    const { target } = await ruledTarget();
    await showDashboard(target, { pin: false });
  } catch (e) {
    console.warn("ThrashDash: could not follow the vehicle rules", e);
  }
}

/** Delete a saved dashboard (from the menu), after asking. Resolves true if it was deleted. */
async function removeDashboard(layout) {
  ensureEditorCss(); // the confirmation dialog's styles live with the editor's
  const ok = await confirmDialog({ title: "Delete dashboard", message: `Delete "${layout.name}" permanently? This cannot be undone.`, confirm: "Delete", danger: true });
  if (!ok) return false;
  try {
    await api.deleteLayout(layout.id); // the relay also forgets its look, vehicle rules and default status
  } catch (e) {
    await infoDialog({ title: "Could not delete", message: e.message });
    return false;
  }
  if (layout.id === app.id) await showDashboard("default", { pin: isPinned() });
  showZoomToast(`Deleted "${layout.name}"`);
  return true;
}

const switcher = createSwitcher({
  button: document.getElementById("dash-button"),
  menu: document.getElementById("dash-menu"),
  api,
  currentId: () => app.id,
  load: switchDashboard,
  remove: removeDashboard,
  skins: () => SKIN_CHOICES.map((id) => ({ id, label: SKIN_LABELS[id] })),
  currentSkin: skinFor,
  setSkin,
  vehicle: () => vehicleKey(app.status.vehicle),
  pinned: isPinned,
  follow: unpinAndFollow,
  openWindow: (id) => openDashboardWindow(id, { menu: true }), // the same size as this window, with the menu open to pick what it should show
  onToggle: (open) => {
    if (open) wakeControls();
  },
});
document.addEventListener("keydown", (e) => {
  const t = e.target;
  const typing = t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.tagName === "SELECT" || t.isContentEditable);
  if (typing || editor || e.altKey || switcher.isOpen) return;
  const key = e.key.toLowerCase();
  if (key === "e" && !e.ctrlKey && !e.metaKey) {
    if (!app.client.local) return; // editing is for the computer running ThrashDash
    e.preventDefault();
    startEditing();
  } else if (key === "d" && !e.ctrlKey && !e.metaKey) {
    e.preventDefault();
    switcher.toggle();
  } else if (key === "n" && !e.ctrlKey && !e.metaKey && app.id) {
    if (!app.client.local) return; // opening windows is for the computer running ThrashDash
    e.preventDefault();
    openDashboardWindow(app.id, { menu: true });
  } else if (key === "s" && !e.ctrlKey && !e.metaKey && app.layout) {
    e.preventDefault();
    const next = SKIN_CHOICES[(SKIN_CHOICES.indexOf(skinFor()) + 1) % SKIN_CHOICES.length];
    setSkin(next).then(() => showZoomToast(`Skin: ${SKIN_LABELS[next]}`), (err) => console.warn("ThrashDash: could not change the skin", err));
  } else if (key === "+" || key === "=") {
    e.preventDefault(); // also swallows Ctrl+= so the browser's own page zoom does not fight ours
    zoom(1);
  } else if (key === "-" || key === "_") {
    e.preventDefault();
    zoom(-1);
  } else if (key === "0") {
    e.preventDefault();
    zoom(0);
  }
});

// The Edit button fades out when the mouse has been idle, so a dashboard on a second monitor stays clean.
let idleTimer = null;
function wakeControls() {
  controls.classList.add("awake");
  clearTimeout(idleTimer);
  idleTimer = setTimeout(() => {
    if (!switcher.isOpen) controls.classList.remove("awake"); // stay visible while the menu is open
  }, 2500);
}
document.addEventListener("pointermove", wakeControls);
document.addEventListener("pointerdown", wakeControls);

// ---- connection -------------------------------------------------------------------------

function setState(state, title = "", detail = "") {
  document.body.dataset.state = state;
  overlayTitle.textContent = title;
  overlayDetail.textContent = detail;
}

function vehicleName(path) {
  // "/vehicles/etkc/" -> "etkc"
  const parts = String(path || "").split("/").filter(Boolean);
  return parts.length ? parts[parts.length - 1] : "";
}

let latest = null; // newest frame not yet drawn

const vehicleKey = (path) => vehicleName(path) || null;
let lastVehicle = null;

function applyStatus() {
  const name = vehicleName(app.status.vehicle);
  document.title = name ? `ThrashDash - ${name}` : "ThrashDash";
  if (name && name !== lastVehicle) {
    lastVehicle = name;
    followVehicle(); // a different vehicle: switch to its dashboard if there is a rule (and this window is not pinned)
  }
  if (app.status.connected) {
    setState("live");
  } else {
    setState("waiting", "Waiting for BeamNG...", "Start the game, load a map and sit in a vehicle. This window will update by itself.");
  }
}

let retryMs = 500;
function connect() {
  const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`);
  ws.onopen = () => {
    retryMs = 500;
    windowReporter.notify({ now: true }); // a relay that has just (re)started learns at once that this window is open
  };
  ws.onmessage = (event) => {
    let msg;
    try {
      msg = JSON.parse(event.data);
    } catch {
      return;
    }
    if (msg.type === "frame") latest = msg.d;
    else if (msg.type === "status") {
      app.status = msg;
      applyStatus();
    } else if (msg.type === "catalog") {
      app.catalog = { vehicle: msg.vehicle, channels: msg.channels || {} };
    }
  };
  ws.onclose = () => {
    latest = null;
    app.status = { ...app.status, connected: false };
    setState("offline", "ThrashDash relay not running", "Start the ThrashDash program. This window reconnects automatically.");
    setTimeout(connect, retryMs);
    retryMs = Math.min(retryMs * 2, 5000);
  };
  ws.onerror = () => ws.close();
}

// ---- alerts: the banner over the dashboard ----------------------------------------------

const alertTracker = createAlertTracker();
const alertBanner = createAlertBanner(document.getElementById("alerts"));
let lastAlertCheck = 0;

/** Re-evaluate the alerts about 5 times a second (the data behind them changes at 4 Hz at most), keeping their hold-over. */
function updateAlerts(now) {
  if (now - lastAlertCheck < 200) return;
  lastAlertCheck = now;
  alertBanner.show(alertTracker.update(app.status.connected ? app.values : null, app.alerts, now));
}

// Another window (or the editor) may have changed which alerts are on: look again when this window is used.
window.addEventListener("focus", () => {
  api.getSettings().then((s) => (app.alerts = alertSettings(s.alerts)), () => {});
});

// Draw at most once per display frame, using only the newest data: if frames arrive faster than the
// screen refreshes, older ones are simply skipped.
function tick() {
  if (latest && dashboard) {
    app.values = latest;
    dashboard.update(latest);
    latest = null;
  }
  updateAlerts(performance.now());
  requestAnimationFrame(tick);
}

controls.hidden = true;
setState("connecting", "Connecting...", "");
loadLayout().then(
  (result) => {
    showLoaded(result);
    followVehicle(); // the vehicle may have been reported while the layout was still loading, when there was nothing to switch yet
    const params = new URLSearchParams(location.search);
    if (params.has("menu")) {
      // This window was opened to show something else: offer the choice at once, and forget the request so a reload does not repeat it.
      params.delete("menu");
      history.replaceState(null, "", `${location.pathname}${params.size ? `?${params}` : ""}${location.hash}`);
      if (app.client.local) switcher.toggle();
    }
  },
  (e) => setState("error", "Could not load the dashboard layout", String(e.message || e)),
);
connect();
requestAnimationFrame(tick);

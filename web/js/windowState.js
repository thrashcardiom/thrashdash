// Tells the relay which dashboard this window shows and how big (and, where the system reports it, where) it is, so
// ThrashDash can reopen the same windows next time it starts. Only the computer running ThrashDash takes part.
// The window's id lives in its own address (?w=...) so a reload is the same window, and a new window gets a new one.

const ID_RE = /^[a-z0-9]{6,24}$/;
export const HEARTBEAT_MS = 10000; // also lets the relay tell the window is still open
export const POLL_MS = 1500; // windows have no "moved" event, so look
export const DEBOUNCE_MS = 600;

/** A fresh window id: 12 random lowercase letters and digits. */
export function randomWindowId(crypto = globalThis.crypto) {
  const bytes = crypto.getRandomValues(new Uint8Array(12));
  return [...bytes].map((b) => (b % 36).toString(36)).join("");
}

/** This window's id from its address, made (and written into the address, without reloading) if it has none yet. */
export function ensureWindowId(loc = location, hist = history, random = randomWindowId) {
  const params = new URLSearchParams(loc.search);
  const existing = params.get("w");
  if (existing && ID_RE.test(existing)) return existing;
  const id = random();
  params.set("w", id);
  hist.replaceState(null, "", `${loc.pathname}?${params}${loc.hash}`);
  return id;
}

/** The window's size and position now. (Under Wayland the position is always 0,0: the relay only uses it if asked to.) */
export function readGeometry(win = window, doc = document) {
  return {
    width: Math.round(win.outerWidth),
    height: Math.round(win.outerHeight),
    x: Math.round(win.screenX ?? 0),
    y: Math.round(win.screenY ?? 0),
    fullscreen: Boolean(doc.fullscreenElement),
  };
}

export const sameReport = (a, b) => a !== null && b !== null && JSON.stringify(a) === JSON.stringify(b);

/**
 * Reports this window to the relay when it opens, whenever its dashboard, size, position or fullscreen state changes
 * (after a short pause so dragging does not flood the relay), and every HEARTBEAT_MS; says so when it closes.
 * Everything it touches is passed in, so it can be tested without a browser.
 * @param {object} o
 *   id          this window's id
 *   state()     { id: dashboard id or null, pinned: bool, kind?: "trips" for the map window (a dashboard window needs none) }
 *   send(body)  deliver a report (body includes the stamp); failures are ignored
 *   forget(stamp)  say the window closed
 *   geometry()  readGeometry
 *   now()       milliseconds
 *   timers      { setTimeout, clearTimeout, setInterval, clearInterval }
 *   events      the window and document: addEventListener
 */
export function createWindowReporter({ id, state, send, forget, geometry = readGeometry, now = Date.now, timers = globalThis, events = { window, document } }) {
  let started = false;
  let debounce = null;
  let last = null;
  let heartbeat = null;
  let poll = null;

  const body = () => ({ ...(state().kind ? { kind: state().kind } : {}), dashboard: state().id ?? null, pinned: state().pinned === true, ...geometry() });

  function report(force = false) {
    const current = body();
    if (!force && sameReport(current, last)) return;
    last = current;
    try {
      const result = send({ ...current, stamp: now() });
      if (result && typeof result.catch === "function") result.catch(() => {});
    } catch {
      /* a failed report is retried by the next heartbeat */
    }
  }

  function notify({ now: immediately = false } = {}) {
    if (!started) return;
    timers.clearTimeout(debounce);
    debounce = null;
    if (immediately) report(true);
    else debounce = timers.setTimeout(() => report(), DEBOUNCE_MS);
  }

  return {
    id,
    start() {
      if (started) return;
      started = true;
      report(true);
      heartbeat = timers.setInterval(() => report(true), HEARTBEAT_MS);
      poll = timers.setInterval(() => {
        if (!sameReport(body(), last)) notify();
      }, POLL_MS);
      events.window.addEventListener("resize", () => notify());
      events.document.addEventListener("fullscreenchange", () => notify());
      events.window.addEventListener("pagehide", () => {
        try {
          forget(now());
        } catch {
          /* closing: nothing more to do */
        }
      });
    },
    /** The dashboard (or anything else the report holds) may have changed. `{now: true}` reports straight away. */
    notify,
    /** Stop reporting (tests). */
    stop() {
      started = false;
      timers.clearTimeout(debounce);
      timers.clearInterval(heartbeat);
      timers.clearInterval(poll);
    },
  };
}

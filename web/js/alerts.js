// Alerts: a banner across every dashboard when something needs the driver's attention (low fuel, overheating, a flat
// tyre...). The rules are plain functions of the current channel values, so they can be tested without a browser.
// Which alerts are switched on is a display setting kept by the relay (settings.alerts), like units.
import { severity } from "./widgets/damage.js";
import { OFF_ROUTE_M } from "./widgets/route.js";
import { resolveUnit } from "./units.js";

const WHEELS = ["FL", "FR", "RL", "RR"];

/** The wheels (of any count: trucks have more than four) whose flag `name` is true, in a stable order. */
function wheelsWith(values, name) {
  const found = new Set();
  const re = new RegExp(`^tire\\.([A-Za-z0-9]+)\\.${name}$`);
  for (const [key, value] of Object.entries(values)) {
    const m = value === true && re.exec(key);
    if (m) found.add(m[1]);
  }
  return [...found].sort((a, b) => (WHEELS.indexOf(a) + 1 || 99) - (WHEELS.indexOf(b) + 1 || 99) || a.localeCompare(b));
}

const anyTrue = (values, ...names) => names.some((n) => values[n] === true);

/** How far over a posted limit (metres a second) before the "over the limit" alert, unless the user chose another: 5 km/h. */
export const DEFAULT_LIMIT_MARGIN = 5 / 3.6;
/** The most the setting may be (the relay refuses more): 14 m/s, about 50 km/h. */
export const MAX_LIMIT_MARGIN = 14;
/** The car's own speed in m/s, from the wheels (the same channel the default dashboard's speedometer reads), or null. */
const carSpeed = (v) => (typeof v.wheelspeed === "number" && Number.isFinite(v.wheelspeed) ? Math.abs(v.wheelspeed) : null);

/**
 * Every alert ThrashDash knows. `test(values)` returns the text to show, or null when the alert is not active.
 * level: "crit" (red, needs action now) or "warn" (amber). `id`s are stored in settings, so never rename one.
 * `delayMs` (optional): the condition must hold this long before the alert shows, so a brief excess does not flash the banner.
 * `test(values, settings, prefs)`: `settings` are the alert settings (the over-limit margin), `prefs` the user's units.
 * `optIn` (optional): the alert is off until the user switches it on (kept in settings.on); the others are on until switched off (settings.off).
 * Most of these simply follow the game's own flags (state.* comes from the game's damage tracker), so they
 * trip when the game says so rather than at a threshold of ours.
 */
export const ALERTS = [
  { id: "flatTyre", label: "Flat tyre", level: "crit", test: (v) => { const w = wheelsWith(v, "deflated"); return w.length ? `Flat tyre ${w.join(", ")}` : null; } },
  { id: "overheat", label: "Engine overheating", level: "crit", test: (v) => (anyTrue(v, "state.engine.coolantOverheating") ? "Engine overheating" : null) },
  { id: "oilHot", label: "Oil overheating", level: "crit", test: (v) => (anyTrue(v, "state.engine.oilOverheating") ? "Oil overheating" : null) },
  { id: "oilLow", label: "Low oil", level: "crit", test: (v) => (anyTrue(v, "state.engine.oilLevelCritical", "state.engine.starvedOfOil") ? "Low oil" : null) },
  { id: "engineDamage", label: "Engine damaged", level: "crit", test: (v) => (anyTrue(v, "state.engine.headGasketDamaged", "state.engine.engineHydrolocked", "state.engine.engineLockedUp") ? "Engine damaged" : null) },
  {
    id: "damage",
    label: "Body damage (heavy or wrecked)",
    level: "warn",
    test(v) {
      const found = severity(v);
      if (found?.word === "Wrecked") return { text: "Car wrecked", level: "crit" };
      return found?.word === "Heavy" ? "Heavy damage" : null;
    },
  },
  { id: "fuel", label: "Low fuel", level: "warn", test: (v) => (v.lowfuel === true ? "Low fuel" : null) },
  { id: "pressure", label: "Low tyre pressure", level: "warn", test: (v) => (typeof v.lowpressure === "number" && v.lowpressure > 0 && !wheelsWith(v, "deflated").length ? "Low tyre pressure" : null) },
  { id: "brakes", label: "Brakes overheating", level: "warn", test: (v) => (typeof v.highBraketemp === "number" && v.highBraketemp > 0 ? "Brakes overheating" : null) },
  { id: "overRev", label: "Over-revving", level: "warn", test: (v) => (anyTrue(v, "state.engine.overRevDanger") ? "Over-revving" : null) },
  {
    // BeamNG.Earth's speed limits are only sometimes read from a real sign: the rest is a guess from the kind of road, and an alert
    // that scolds you over a guess would be wrong too often to leave on. So, like the trip log's speeding figures, only a POSTED limit
    // counts. The text names the limit, not your speed, so it does not change (and the banner does not redraw) as you speed up.
    id: "overLimit",
    label: "Over the posted speed limit",
    level: "warn",
    delayMs: 2000,
    test(v, settings, prefs) {
      const limit = v["geo.limit"];
      const speed = carSpeed(v);
      if (v["geo.limitPosted"] !== true || typeof limit !== "number" || speed === null) return null;
      const margin = alertSettings(settings).limitMargin;
      if (speed <= limit + margin) return null;
      const unit = resolveUnit("speed", prefs ?? {});
      return `Over the speed limit (${Math.round(unit.convert(limit))} ${unit.label})`;
    },
  },
  {
    // Farther from BeamNG.Earth's GPS route than the route panel's own warning. Earth plans a new route itself, but only from much
    // farther off, so this says so earlier. Some leave the route on purpose, so it is off unless asked for.
    id: "offRoute",
    label: "Off the GPS route",
    level: "warn",
    delayMs: 3000,
    optIn: true,
    test: (v) => (v["geo.route.active"] === true && typeof v["geo.route.off"] === "number" && v["geo.route.off"] > OFF_ROUTE_M ? "Off the route" : null),
  },
];

export const ALERT_IDS = ALERTS.map((a) => a.id);
export const DEFAULT_ALERT_SETTINGS = { enabled: true, off: [], on: [], limitMargin: DEFAULT_LIMIT_MARGIN };

/** Whether the user's settings have this alert switched on (all of them can still be silenced by `enabled`). */
export function isAlertOn(alert, settings) {
  return alert.optIn ? Boolean(settings.on?.includes(alert.id)) : !settings.off?.includes(alert.id);
}

/** Clean up whatever the relay stored: known ids only, defaults when missing. */
export function alertSettings(raw) {
  const known = (list) => (Array.isArray(list) ? list.filter((id) => ALERT_IDS.includes(id)) : []);
  const m = raw?.limitMargin;
  const limitMargin = typeof m === "number" && Number.isFinite(m) && m >= 0 && m <= MAX_LIMIT_MARGIN ? m : DEFAULT_LIMIT_MARGIN;
  return { enabled: raw?.enabled !== false, off: known(raw?.off), on: known(raw?.on), limitMargin };
}

/**
 * Which alerts are active for these values, most urgent first. `values` null (no data) means none.
 * @returns {{id: string, text: string, level: "crit"|"warn"}[]}
 */
export function evaluateAlerts(values, settings = DEFAULT_ALERT_SETTINGS, prefs = null) {
  if (!values || settings.enabled === false) return [];
  const active = [];
  for (const alert of ALERTS) {
    if (!isAlertOn(alert, settings)) continue;
    let result = null;
    try {
      result = alert.test(values, settings, prefs);
    } catch {
      continue; // one broken rule must not silence the others
    }
    if (!result) continue;
    const { text, level } = typeof result === "string" ? { text: result, level: alert.level } : { level: alert.level, ...result };
    active.push({ id: alert.id, text, level });
  }
  return active.sort((a, b) => (a.level === b.level ? 0 : a.level === "crit" ? -1 : 1));
}

/** An alert stays up this long after its condition clears, so a flickering flag does not make the banner blink. */
export const HOLD_MS = 2500;

/**
 * Smooths alerts over time: a new alert shows at once, one that has cleared lingers for HOLD_MS.
 * `now` is a number of milliseconds from any monotonic clock (injected so tests need no timers).
 */
export function createAlertTracker() {
  const shown = new Map(); // id -> { text, level, lastSeen }
  const pending = new Map(); // id -> when its condition began, for alerts that wait (delayMs) before showing
  return {
    update(values, settings, now, prefs = null) {
      // Switching alerts off (all, or one) takes effect at once: only a condition that merely stopped is held over.
      for (const id of [...shown.keys()]) if (settings.enabled === false || !isAlertOn(ALERTS.find((def) => def.id === id), settings)) shown.delete(id);
      const holding = new Set();
      for (const a of evaluateAlerts(values, settings, prefs)) {
        const wait = ALERTS.find((def) => def.id === a.id)?.delayMs ?? 0;
        if (wait > 0) {
          holding.add(a.id);
          if (!pending.has(a.id)) pending.set(a.id, now);
          if (now - pending.get(a.id) < wait) continue; // not yet: it must last
        }
        shown.set(a.id, { id: a.id, text: a.text, level: a.level, lastSeen: now });
      }
      for (const id of [...pending.keys()]) if (!holding.has(id)) pending.delete(id); // the condition lapsed: start the wait again next time
      for (const [id, entry] of shown) if (now - entry.lastSeen > HOLD_MS) shown.delete(id);
      return [...shown.values()]
        .sort((a, b) => (a.level === b.level ? ALERT_IDS.indexOf(a.id) - ALERT_IDS.indexOf(b.id) : a.level === "crit" ? -1 : 1))
        .map(({ id, text, level }) => ({ id, text, level }));
    },
    clear() {
      shown.clear();
      pending.clear();
    },
  };
}

/** Draws the active alerts into `el` (only touching the page when they change). */
export function createAlertBanner(el, { max = 3 } = {}) {
  let signature = "";
  return {
    show(alerts) {
      const list = alerts.slice(0, max);
      const sig = list.map((a) => `${a.level}:${a.text}`).join("|") + (alerts.length > max ? `+${alerts.length - max}` : "");
      if (sig === signature) return;
      signature = sig;
      el.replaceChildren();
      for (const a of list) {
        const item = document.createElement("div");
        item.className = `al-item al-${a.level}`;
        item.textContent = a.text;
        el.appendChild(item);
      }
      if (alerts.length > max) {
        const more = document.createElement("div");
        more.className = "al-more";
        more.textContent = `+${alerts.length - max} more`;
        el.appendChild(more);
      }
      el.hidden = list.length === 0;
    },
  };
}

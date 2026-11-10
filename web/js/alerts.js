// Alerts: a banner across every dashboard when something needs the driver's attention (low fuel, overheating, a flat
// tyre...). The rules are plain functions of the current channel values, so they can be tested without a browser.
// Which alerts are switched on is a display setting kept by the relay (settings.alerts), like units.
import { severity } from "./widgets/damage.js";

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

/**
 * Every alert ThrashDash knows. `test(values)` returns the text to show, or null when the alert is not active.
 * level: "crit" (red, needs action now) or "warn" (amber). `id`s are stored in settings, so never rename one.
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
];

export const ALERT_IDS = ALERTS.map((a) => a.id);
export const DEFAULT_ALERT_SETTINGS = { enabled: true, off: [] };

/** Clean up whatever the relay stored: known ids only, defaults when missing. */
export function alertSettings(raw) {
  const off = Array.isArray(raw?.off) ? raw.off.filter((id) => ALERT_IDS.includes(id)) : [];
  return { enabled: raw?.enabled !== false, off };
}

/**
 * Which alerts are active for these values, most urgent first. `values` null (no data) means none.
 * @returns {{id: string, text: string, level: "crit"|"warn"}[]}
 */
export function evaluateAlerts(values, settings = DEFAULT_ALERT_SETTINGS) {
  if (!values || settings.enabled === false) return [];
  const off = new Set(settings.off ?? []);
  const active = [];
  for (const alert of ALERTS) {
    if (off.has(alert.id)) continue;
    let result = null;
    try {
      result = alert.test(values);
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
  return {
    update(values, settings, now) {
      // Switching alerts off (all, or one) takes effect at once: only a condition that merely stopped is held over.
      for (const id of [...shown.keys()]) if (settings.enabled === false || settings.off?.includes(id)) shown.delete(id);
      for (const a of evaluateAlerts(values, settings)) shown.set(a.id, { id: a.id, text: a.text, level: a.level, lastSeen: now });
      for (const [id, entry] of shown) if (now - entry.lastSeen > HOLD_MS) shown.delete(id);
      return [...shown.values()]
        .sort((a, b) => (a.level === b.level ? ALERT_IDS.indexOf(a.id) - ALERT_IDS.indexOf(b.id) : a.level === "crit" ? -1 : 1))
        .map(({ id, text, level }) => ({ id, text, level }));
    },
    clear() {
      shown.clear();
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

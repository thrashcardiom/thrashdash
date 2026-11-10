// Unit conversion lives on the display side: the game reports SI-ish values (m/s, deg C, 0..1)
// and each user picks what they want to see. A widget names a `kind`; the user's preference
// for that kind decides the conversion.

const KINDS = {
  speed: { kmh: { label: "km/h", f: (v) => v * 3.6 }, mph: { label: "mph", f: (v) => v * 2.2369363 } },
  temp: { c: { label: "°C", f: (v) => v }, f: { label: "°F", f: (v) => (v * 9) / 5 + 32 } },
  // ASSUMPTION (unverified): odometer/trip arrive in metres.
  distance: { km: { label: "km", f: (v) => v / 1000 }, mi: { label: "mi", f: (v) => v / 1609.344 } },
  // Tyre pressure arrives as GAUGE kPa (the mod subtracts the atmosphere from the game's absolute reading).
  pressure: { kpa: { label: "kPa", f: (v) => v }, psi: { label: "psi", f: (v) => v * 0.1450377 }, bar: { label: "bar", f: (v) => v / 100 } },
  fraction: { pct: { label: "%", f: (v) => v * 100 } },
  // Fuel economy is only used by the trip computer, which converts it itself (see economy.js: litres and kWh need
  // different maths), so these entries exist for the unit picker and the saved setting. "auto" follows the distance unit.
  economy: { auto: { label: "Follow distance unit", f: (v) => v }, l100: { label: "L/100 km", f: (v) => v }, kmpl: { label: "km/L", f: (v) => v }, mpgus: { label: "mpg (US)", f: (v) => v }, mpguk: { label: "mpg (UK)", f: (v) => v } },
  none: { none: { label: "", f: (v) => v } },
};

const DEFAULT_PREFS = { speed: "kmh", temp: "c", distance: "km", pressure: "psi", economy: "auto", fraction: "pct", none: "none" };

/** Unit types a widget may declare, in picker order. */
export const KIND_OPTIONS = ["none", "speed", "temp", "distance", "pressure", "fraction"];

/** Units each kind offers, in display order (the editor builds its unit pickers from this). */
export const UNIT_OPTIONS = Object.fromEntries(Object.entries(KINDS).map(([kind, units]) => [kind, Object.entries(units).map(([key, u]) => ({ key, label: u.label }))]));

/**
 * Effective unit preferences: defaults, then the user's saved settings, then the page URL
 * (?speed=mph&temp=f), which wins so a single window can be switched without touching settings.
 */
export function buildPrefs(saved = {}, search = "") {
  const q = new URLSearchParams(search);
  const prefs = { ...DEFAULT_PREFS };
  for (const kind of Object.keys(KINDS)) {
    for (const want of [saved[kind], q.get(kind)]) {
      if (want && Object.hasOwn(KINDS[kind], want)) prefs[kind] = want;
    }
  }
  return prefs;
}

/** @returns {{unit: string, label: string, convert: (v:number)=>number}} */
export function resolveUnit(kind, prefs) {
  const group = KINDS[kind || "none"] || KINDS.none;
  const unit = prefs[kind || "none"] in group ? prefs[kind || "none"] : Object.keys(group)[0];
  return { unit, label: group[unit].label, convert: group[unit].f };
}

/** A layout value may be a plain number or an object keyed by unit ({kmh: 240, mph: 150}). */
export function pick(value, unit, fallback) {
  if (value === undefined || value === null) return fallback;
  if (typeof value === "object") return unit in value ? value[unit] : fallback;
  return value;
}

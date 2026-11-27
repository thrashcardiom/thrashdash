// Layout validation and normalisation. Pure functions, no DOM, so they are unit-tested under Node.
//
// This is the "meaning" layer (the relay's validate.py only checks structure and size). A layout is
// untrusted input - it may come from a file the user imported - so every property is checked against
// the spec its widget declares, anything unknown or invalid is dropped, and the result is always a
// layout the renderer can draw safely. Nothing here (or anywhere) ever evaluates layout content.

import { cleanTheme } from "./theme.js";
import { scaleSetting } from "./scaling.js";

export const LAYOUT_FORMAT = 1;
export const MAX_WIDGETS = 200;

const GEOMETRY = ["x", "y", "w", "h"];
const RESERVED = new Set(["id", "type", ...GEOMETRY]);

/** Unit keys a {unit: value} property may use. Must stay in step with units.js. */
export const UNIT_KEYS = ["kmh", "mph", "c", "f", "km", "mi", "kpa", "psi", "bar", "pct", "none"];

export const CHANNEL_RE = /^[A-Za-z0-9_.]{1,100}$/;
const ID_RE = /^[A-Za-z0-9_-]{1,40}$/;
// Colours: hex, or one of the theme variables. Never rgb()/url()/free text: those can carry
// surprises, and themes (phase 3) should be able to restyle everything through the variables.
const THEME_VARS = ["accent", "ok", "warn", "crit", "text", "muted", "panel", "track"];
const COLOR_RE = new RegExp(`^(#(?:[0-9a-fA-F]{3,4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})|var\\(--(?:${THEME_VARS.join("|")})\\))$`);
const CONTROL_RE = /[\u0000-\u001f\u007f]/g;

export class LayoutError extends Error {}

const isNum = (v) => typeof v === "number" && Number.isFinite(v);
const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
const isPlainObject = (v) => v !== null && typeof v === "object" && !Array.isArray(v);

/**
 * Check one property value against its spec.
 * @returns {{ok: true, value: any, note?: string} | {ok: false, reason: string}}
 */
export function sanitizeValue(value, ps) {
  switch (ps.type) {
    case "channel":
      return typeof value === "string" && CHANNEL_RE.test(value) ? { ok: true, value } : { ok: false, reason: "not a channel name" };

    case "channels": {
      if (!Array.isArray(value) || value.length > 12) return { ok: false, reason: "not a list of up to 12 channels" };
      if (!value.every((c) => typeof c === "string" && CHANNEL_RE.test(c))) return { ok: false, reason: "contains an invalid channel name" };
      return { ok: true, value: [...value] };
    }

    case "number": {
      if (!isNum(value)) return { ok: false, reason: "not a number" };
      let v = ps.int ? Math.round(value) : value;
      const lo = ps.min ?? -1e9;
      const hi = ps.max ?? 1e9;
      const clamped = clamp(v, lo, hi);
      return clamped === v ? { ok: true, value: v } : { ok: true, value: clamped, note: `clamped to ${clamped}` };
    }

    case "unitNumber": {
      if (isNum(value)) return { ok: true, value: clamp(value, -1e9, 1e9) };
      if (isPlainObject(value)) {
        const out = {};
        for (const [unit, v] of Object.entries(value)) {
          if (!UNIT_KEYS.includes(unit) || !isNum(v)) return { ok: false, reason: `bad per-unit value for "${unit}"` };
          out[unit] = clamp(v, -1e9, 1e9);
        }
        return Object.keys(out).length ? { ok: true, value: out } : { ok: false, reason: "empty unit map" };
      }
      return { ok: false, reason: "not a number or per-unit numbers" };
    }

    case "textList": {
      // Short labels such as E / 1/2 / F, or gear letters. Empty entries are allowed (a gap in the sequence).
      if (!Array.isArray(value) || value.length > (ps.maxItems ?? 12)) return { ok: false, reason: `not a list of up to ${ps.maxItems ?? 12} short texts` };
      if (!value.every((v) => typeof v === "string")) return { ok: false, reason: "contains something that is not text" };
      return { ok: true, value: value.map((v) => v.replace(CONTROL_RE, "").slice(0, ps.max ?? 8)) };
    }

    case "text": {
      if (typeof value !== "string") return { ok: false, reason: "not text" };
      const cleaned = value.replace(CONTROL_RE, "").slice(0, ps.max ?? 100);
      return { ok: true, value: cleaned };
    }

    case "color":
      return typeof value === "string" && COLOR_RE.test(value) ? { ok: true, value } : { ok: false, reason: "colour must be #hex or a theme variable like var(--ok)" };

    case "select":
      return ps.options.includes(value) ? { ok: true, value } : { ok: false, reason: `must be one of: ${ps.options.map((o) => `"${o}"`).join(", ")}` };

    case "bool":
      return typeof value === "boolean" ? { ok: true, value } : { ok: false, reason: "not true/false" };

    default:
      return { ok: false, reason: `unknown property type ${ps.type}` };
  }
}

/** Pick an id for a widget of `type` that is not in `taken`: "bar-1", "bar-2"... */
export function freshId(type, taken) {
  let n = 1;
  while (taken.has(`${type}-${n}`)) n++;
  return `${type}-${n}`;
}

/**
 * Clean an untrusted layout. Throws LayoutError only when the layout is unusable as a whole
 * (not an object, wrong format version). Everything else is repaired or dropped and reported.
 * @returns {{layout: object, warnings: string[]}}
 */
export function sanitizeLayout(raw, specs) {
  if (!isPlainObject(raw)) throw new LayoutError("layout is not an object");
  if (raw.format !== LAYOUT_FORMAT) {
    throw new LayoutError(raw.format > LAYOUT_FORMAT ? "this layout was made by a newer ThrashDash; please update" : `unsupported layout format ${raw.format}`);
  }
  const warnings = [];
  const name = typeof raw.name === "string" ? raw.name.replace(CONTROL_RE, "").trim().slice(0, 60) : "";
  const width = isNum(raw.width) ? Math.round(clamp(raw.width, 200, 4000)) : 1200;
  const height = isNum(raw.height) ? Math.round(clamp(raw.height, 100, 4000)) : 420;
  if (!isNum(raw.width) || !isNum(raw.height)) warnings.push("canvas size missing or invalid; using 1200x420");

  const input = Array.isArray(raw.widgets) ? raw.widgets : [];
  if (!Array.isArray(raw.widgets)) warnings.push("widgets is not a list; layout is empty");
  if (input.length > MAX_WIDGETS) warnings.push(`more than ${MAX_WIDGETS} widgets; extra ones ignored`);

  const widgets = [];
  const taken = new Set();
  for (const [i, w] of input.slice(0, MAX_WIDGETS).entries()) {
    const where = `widget ${i + 1}`;
    if (!isPlainObject(w)) {
      warnings.push(`${where}: not an object, dropped`);
      continue;
    }
    const spec = typeof w.type === "string" && Object.hasOwn(specs, w.type) ? specs[w.type] : null;
    if (!spec) {
      warnings.push(`${where}: unknown widget type ${JSON.stringify(String(w.type).slice(0, 40))}, dropped`);
      continue;
    }
    const clean = { id: typeof w.id === "string" && ID_RE.test(w.id) && !taken.has(w.id) ? w.id : freshId(w.type, taken), type: w.type };
    taken.add(clean.id);

    for (const key of GEOMETRY) {
      const [lo, hi] = key === "x" || key === "y" ? [-width, 8000] : [8, 8000];
      if (isNum(w[key])) clean[key] = Math.round(clamp(w[key], lo, hi));
      else {
        clean[key] = key === "w" ? spec.size.w : key === "h" ? spec.size.h : 0;
        warnings.push(`${where} (${w.type}): ${key} missing or invalid, using ${clean[key]}`);
      }
    }
    for (const [key, value] of Object.entries(w)) {
      if (RESERVED.has(key)) continue;
      const ps = Object.hasOwn(spec.props, key) ? spec.props[key] : null;
      if (!ps) {
        warnings.push(`${where} (${w.type}): unknown property "${key}", dropped`);
        continue;
      }
      const res = sanitizeValue(value, ps);
      if (res.ok) {
        clean[key] = res.value;
        if (res.note) warnings.push(`${where} (${w.type}): ${key} ${res.note}`);
      } else {
        warnings.push(`${where} (${w.type}): ${key} ${res.reason}, dropped`);
      }
    }
    widgets.push(clean);
  }
  const { mode: scaleMode, percent: scalePercent } = scaleSetting(raw);
  if (raw.scaleMode !== undefined && raw.scaleMode !== scaleMode) warnings.push(`scale mode ${JSON.stringify(String(raw.scaleMode).slice(0, 20))} not understood; using "${scaleMode}"`);
  const { theme, warnings: themeWarnings } = cleanTheme(raw.theme);
  warnings.push(...themeWarnings);
  return { layout: { format: LAYOUT_FORMAT, name: name || "Untitled", width, height, scaleMode, scalePercent, ...(theme ? { theme } : {}), widgets }, warnings };
}

/** A new widget of `type` with its default size and properties, placed at (x, y). */
export function newWidget(type, specs, existingWidgets, x = 20, y = 20) {
  const spec = specs[type];
  if (!spec) throw new LayoutError(`unknown widget type ${type}`);
  const taken = new Set(existingWidgets.map((w) => w.id));
  return { id: freshId(type, taken), type, x, y, w: spec.size.w, h: spec.size.h, ...structuredClone(spec.defaults) };
}

// A dashboard's theme: the colours of the flat look (the page behind, the panels, text, accent and the warning and critical colours), saved
// in the dashboard itself so they travel with it. Pure functions (unit-tested in Node); app.js applies one to the canvas.
//
// A theme only holds the colours that were changed: everything else stays the way the stylesheet has it. It is applied as CSS variables on
// the canvas (so the editor panel, the alert banner and the menus keep their own colours and stay readable whatever is chosen here), and
// the page behind the canvas takes the background colour. The other looks (skins) have their own colours and are not affected.

/** [key, label, CSS variable, default]. The defaults are the stylesheet's (dash.css :root); keep them in step. */
export const THEME_KEYS = [
  ["bg", "Background", "--bg", "#0b0e13"],
  ["panel", "Panels", "--panel", "#141922"],
  ["text", "Text", "--text", "#e8edf4"],
  ["muted", "Quiet text", "--muted", "#7d8a9c"],
  ["accent", "Accent", "--accent", "#35c2ff"],
  ["warn", "Warning", "--warn", "#ffb020"],
  ["crit", "Critical", "--crit", "#ff4d4d"],
];

const KEYS = new Set(THEME_KEYS.map(([key]) => key));

/** "#abc" or "#AABBCC" -> "#aabbcc"; null for anything else (a name, rgb(), a url...: nothing that is not a plain colour gets into a style). */
export function normaliseColour(value) {
  if (typeof value !== "string") return null;
  const m = /^#([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(value.trim());
  if (!m) return null;
  const hex = m[1].toLowerCase();
  return `#${hex.length === 3 ? [...hex].map((c) => c + c).join("") : hex}`;
}

/**
 * Clean whatever a layout file holds as its theme.
 * @returns {{theme: object | null, warnings: string[]}} `theme` is null when nothing usable is left (a layout then has no theme at all)
 */
export function cleanTheme(raw) {
  const warnings = [];
  if (raw === undefined) return { theme: null, warnings };
  if (raw === null || typeof raw !== "object" || Array.isArray(raw)) return { theme: null, warnings: ["theme is not an object; ignored"] };
  const theme = {};
  for (const [key, value] of Object.entries(raw)) {
    if (!KEYS.has(key)) {
      warnings.push(`theme: unknown colour "${String(key).slice(0, 20)}", dropped`);
      continue;
    }
    const colour = normaliseColour(value);
    if (colour) theme[key] = colour;
    else warnings.push(`theme: ${key} is not a colour like #1a2b3c, dropped`);
  }
  return { theme: Object.keys(theme).length ? theme : null, warnings };
}

/** The colour of one theme key: the theme's, or the stylesheet's default. */
export const themeColour = (theme, key) => theme?.[key] ?? THEME_KEYS.find(([k]) => k === key)?.[3];

/** The CSS variables a theme sets: {"--panel": "#fff", ...}. */
export function themeVars(theme) {
  const out = {};
  for (const [key, , variable] of THEME_KEYS) if (theme?.[key]) out[variable] = theme[key];
  return out;
}

/** Put a theme on the canvas (and its background behind it), or take every trace of one off. */
export function applyTheme(canvasEl, bodyEl, theme) {
  for (const [, , variable] of THEME_KEYS) canvasEl.style.removeProperty(variable);
  for (const [variable, value] of Object.entries(themeVars(theme))) canvasEl.style.setProperty(variable, value);
  if (theme?.bg) bodyEl.style.background = theme.bg;
  else bodyEl.style.removeProperty("background");
}

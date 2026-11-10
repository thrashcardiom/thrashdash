// How a layout's fixed-size canvas is scaled into the window. Pure functions (unit-tested under Node).
//
//   fit    always fill the window (the original behaviour: bigger window = bigger gauges)
//   max    fill the window but never exceed `percent` (default; fullscreen stays at design size)
//   fixed  exactly `percent`, whatever the window size
//
// New and existing layouts without the setting get "max" at 100%.

export const SCALE_MODES = ["max", "fit", "fixed"];
export const DEFAULT_SCALE = { mode: "max", percent: 100 };
export const MIN_PERCENT = 10;
export const MAX_PERCENT = 400;

const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));

/** Normalise whatever a layout (or a saved override) says into a valid {mode, percent}. */
export function scaleSetting(source) {
  const mode = SCALE_MODES.includes(source?.scaleMode ?? source?.mode) ? (source.scaleMode ?? source.mode) : DEFAULT_SCALE.mode;
  const raw = source?.scalePercent ?? source?.percent;
  const percent = Number.isFinite(raw) ? clamp(Math.round(raw), MIN_PERCENT, MAX_PERCENT) : DEFAULT_SCALE.percent;
  return { mode, percent };
}

/** The scale factor (1 = design size) for a canvas of width x height in an available area. */
export function computeScale(setting, width, height, availW, availH) {
  const { mode, percent } = scaleSetting(setting);
  const fit = Math.min(availW / width, availH / height);
  const wanted = percent / 100;
  if (mode === "fit") return fit;
  if (mode === "fixed") return wanted;
  return Math.min(fit, wanted);
}

/** One zoom step (+1 / -1): about 10%, but always at least 1 point so it never gets stuck on rounding. */
export function stepPercent(currentScale, direction) {
  const now = currentScale * 100;
  const next = Math.round(direction > 0 ? now * 1.1 : now / 1.1);
  const moved = direction > 0 ? Math.max(next, Math.round(now) + 1) : Math.min(next, Math.round(now) - 1);
  return clamp(moved, MIN_PERCENT, MAX_PERCENT);
}

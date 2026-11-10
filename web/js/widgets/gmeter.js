import { h, s } from "../dom.js";

// A G-force meter: a dot that moves with the force on the car, left/right in corners and up/down under braking and
// acceleration, with a short fading trail and a held peak. Uses the game's smoothed accelerometer channels, which are
// in metres per second squared.
export const spec = {
  label: "G-force meter",
  size: { w: 240, h: 270 },
  defaults: { range: 1.5, showTrail: true },
  props: {
    label: { type: "text", label: "Title", max: 20 },
    range: { type: "number", label: "Outer ring (g)", min: 0.5, max: 6 },
    showTrail: { type: "bool", label: "Fading trail" },
    flipLateral: { type: "bool", label: "Reverse left and right" },
    flipLongitudinal: { type: "bool", label: "Reverse up and down" },
  },
};

export const STANDARD_GRAVITY = 9.80665;
// Which way the dot goes. Measured in the real game (BeamNG 0.40.5, a recorded drive): the accelerometer channels are the
// car's own acceleration in the game's vehicle frame, where +x is the car's LEFT and +y is its REAR (the game's body-zone
// code puts low x on the right and low y at the front). So accYSmooth is positive when braking and negative when
// accelerating, and accXSmooth is negative in a right-hand corner. The dot follows the force you FEEL (like a ball hanging
// in the car): braking moves it up, accelerating down, a right-hand corner to the left. That is +1 for both axes here;
// the "reverse" properties flip them for anyone who prefers the opposite convention.
export const LATERAL_SIGN = 1;
export const LONGITUDINAL_SIGN = 1;

/**
 * Where the dot goes, from the two acceleration channels (m/s^2). x is -1..1 across the meter (right positive) and y is
 * -1..1 up the meter (up positive), both as a fraction of the outer ring and limited to the ring. `g` is the combined
 * force in g, unlimited. Returns null when either channel has no value.
 */
export function gPoint(ax, ay, { range = 1.5, flipLateral = false, flipLongitudinal = false } = {}) {
  if (![ax, ay].every((v) => typeof v === "number" && Number.isFinite(v))) return null;
  const lateral = (ax / STANDARD_GRAVITY) * LATERAL_SIGN * (flipLateral ? -1 : 1);
  const longitudinal = (ay / STANDARD_GRAVITY) * LONGITUDINAL_SIGN * (flipLongitudinal ? -1 : 1);
  const g = Math.hypot(lateral, longitudinal);
  const r = Math.max(0.1, range);
  const scale = g > r ? r / g : 1; // beyond the outer ring the dot stays on it, in the right direction
  return { x: (lateral * scale) / r, y: (longitudinal * scale) / r, g };
}

/** How long the peak reading is held before it follows the live value down again (ms). */
export const PEAK_HOLD_MS = 4000;
const TRAIL_POINTS = 18;
const TRAIL_EVERY_MS = 60;
const R = 90; // radius of the outer ring in the drawing

export function create(def, ctx) {
  const root = h("div", "w w-gmeter");
  if (ctx.skin) root.classList.add("w-skinned", `sk-${ctx.skin.id}`);
  if (def.label) h("div", "gm-label", root, def.label);
  const svg = s("svg", { viewBox: "-100 -100 200 200", preserveAspectRatio: "xMidYMid meet" }, root);
  s("circle", { class: "gm-face", r: R + 4 }, svg);
  const range = def.range ?? 1.5;
  for (const f of [1 / 3, 2 / 3, 1]) s("circle", { class: f === 1 ? "gm-ring gm-outer" : "gm-ring", r: R * f }, svg);
  s("line", { class: "gm-axis", x1: -R, y1: 0, x2: R, y2: 0 }, svg);
  s("line", { class: "gm-axis", x1: 0, y1: -R, x2: 0, y2: R }, svg);
  s("text", { class: "gm-scale", x: R * (2 / 3) + 2, y: -3 }, svg, `${Math.round((range * 2 * 100) / 3) / 100}g`);
  s("text", { class: "gm-scale", x: R + 2, y: -3, "text-anchor": "end" }, svg, `${range}g`);

  const trail = [];
  if (def.showTrail !== false) {
    for (let i = 0; i < TRAIL_POINTS; i++) trail.push(s("circle", { class: "gm-trail", r: 2 + (i / TRAIL_POINTS) * 3, cx: 0, cy: 0, opacity: 0 }, svg));
  }
  const dot = s("circle", { class: "gm-dot", r: 7, cx: 0, cy: 0 }, svg);
  const readout = h("div", "gm-readout", root);
  const now = h("span", "gm-now", readout, "--");
  h("span", "gm-unit", readout, "g");
  const peakEl = h("span", "gm-peak", readout, "");

  const history = []; // newest last
  let lastTrail = -Infinity;
  let peak = 0;
  let peakAt = -Infinity;
  let lastText = "";
  let lastPeakText = "";
  const options = { range, flipLateral: def.flipLateral === true, flipLongitudinal: def.flipLongitudinal === true };

  return {
    el: root,
    update(values, time = performance.now()) {
      const p = gPoint(values.accXSmooth, values.accYSmooth, options);
      root.classList.toggle("missing", p === null);
      if (p === null) {
        if (lastText !== "--") {
          now.textContent = lastText = "--";
          peakEl.textContent = lastPeakText = "";
          dot.setAttribute("cx", "0");
          dot.setAttribute("cy", "0");
        }
        return;
      }
      dot.setAttribute("cx", (p.x * R).toFixed(1));
      dot.setAttribute("cy", (-p.y * R).toFixed(1));

      if (trail.length && time - lastTrail >= TRAIL_EVERY_MS) {
        lastTrail = time;
        history.push({ x: p.x * R, y: -p.y * R });
        if (history.length > TRAIL_POINTS) history.shift();
        const offset = TRAIL_POINTS - history.length;
        for (let i = 0; i < TRAIL_POINTS; i++) {
          const point = history[i - offset];
          if (point) {
            trail[i].setAttribute("cx", point.x.toFixed(1));
            trail[i].setAttribute("cy", point.y.toFixed(1));
            trail[i].setAttribute("opacity", String(((i + 1) / TRAIL_POINTS) * 0.5));
          } else trail[i].setAttribute("opacity", "0");
        }
      }

      if (p.g >= peak || time - peakAt > PEAK_HOLD_MS) {
        peak = p.g;
        peakAt = time;
      }
      const text = p.g.toFixed(2);
      if (text !== lastText) now.textContent = lastText = text;
      const peakText = peak >= 0.3 ? `peak ${peak.toFixed(2)}` : "";
      if (peakText !== lastPeakText) peakEl.textContent = lastPeakText = peakText;
    },
  };
}

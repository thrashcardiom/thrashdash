import { h, s } from "../dom.js";

// What the driver is doing with the controls: a steering wheel that turns, and clutch, brake and throttle bars.
// These are the game's *input* channels (what the controller or wheel is asking for), not what the car does with it.
export const spec = {
  label: "Driver inputs",
  size: { w: 300, h: 150 },
  defaults: { showWheel: true, showClutch: false, wheelLock: 180 },
  props: {
    label: { type: "text", label: "Title", max: 20 },
    showWheel: { type: "bool", label: "Show the steering wheel" },
    showClutch: { type: "bool", label: "Show the clutch (manual cars)" },
    wheelLock: { type: "number", label: "Wheel turns at full lock (degrees)", min: 30, max: 900 },
    flipSteering: { type: "bool", label: "Reverse the steering direction" },
  },
};

const clamp01 = (x) => (typeof x === "number" && Number.isFinite(x) ? Math.min(1, Math.max(0, x)) : null);

/** Steering wheel rotation in degrees for a steering input of -1..1 (clockwise is positive), or null with no data. */
export function wheelAngle(input, lockDegrees = 180, flip = false) {
  if (typeof input !== "number" || !Number.isFinite(input)) return null;
  const limited = Math.min(1, Math.max(-1, input));
  return (flip ? -limited : limited) * lockDegrees;
}

/** A pedal's fill (0..1), or null with no data. Pedals never go below 0 or above 1 even if a channel misbehaves. */
export const pedalFill = clamp01;

const PEDALS = [
  { key: "clutch", channel: "clutch_input", letter: "C", colour: "var(--accent)" },
  { key: "brake", channel: "brake_input", letter: "B", colour: "var(--crit)" },
  { key: "throttle", channel: "throttle_input", letter: "T", colour: "var(--ok)" },
];
const PEDAL_TOP = 22;
const PEDAL_HEIGHT = 96;

export function create(def, ctx) {
  const root = h("div", "w w-inputs");
  if (ctx.skin) root.classList.add("w-skinned", `sk-${ctx.skin.id}`);
  if (def.label) h("div", "in-label", root, def.label);
  const showWheel = def.showWheel !== false;
  const pedals = PEDALS.filter((p) => p.key !== "clutch" || def.showClutch === true);
  const width = (showWheel ? 130 : 0) + pedals.length * 44 + 8;
  const svg = s("svg", { viewBox: `0 0 ${width} 140`, preserveAspectRatio: "xMidYMid meet" }, root);

  let wheel = null;
  if (showWheel) {
    wheel = s("g", { class: "in-wheel", transform: "translate(66 70)" }, svg);
    const turn = s("g", {}, wheel);
    s("circle", { class: "in-rim", r: 52, cx: 0, cy: 0 }, turn);
    s("circle", { class: "in-hub", r: 11, cx: 0, cy: 0 }, turn);
    s("line", { class: "in-spoke", x1: -52, y1: 0, x2: -11, y2: 0 }, turn);
    s("line", { class: "in-spoke", x1: 11, y1: 0, x2: 52, y2: 0 }, turn);
    s("line", { class: "in-spoke", x1: 0, y1: 11, x2: 0, y2: 52 }, turn);
    s("rect", { class: "in-top", x: -4, y: -56, width: 8, height: 10, rx: 2 }, turn); // the top-centre marker shows how far it has turned
    wheel.turn = turn;
  }

  const left = showWheel ? 132 : 6;
  const bars = pedals.map((p, i) => {
    const x = left + i * 44;
    s("rect", { class: "in-track", x, y: PEDAL_TOP, width: 34, height: PEDAL_HEIGHT, rx: 4 }, svg);
    const fill = s("rect", { class: "in-fill", x, y: PEDAL_TOP + PEDAL_HEIGHT, width: 34, height: 0, rx: 4 }, svg);
    fill.style.fill = p.colour;
    s("text", { class: "in-letter", x: x + 17, y: 134, "text-anchor": "middle" }, svg, p.letter);
    const pct = s("text", { class: "in-pct", x: x + 17, y: 15, "text-anchor": "middle" }, svg, "");
    return { ...p, fill, pct, last: -1 };
  });

  let lastAngle = null;
  return {
    el: root,
    update(values) {
      if (wheel) {
        const angle = wheelAngle(values.steering_input, def.wheelLock ?? 180, def.flipSteering === true);
        const shown = angle === null ? null : Math.round(angle * 2) / 2;
        if (shown !== lastAngle) {
          lastAngle = shown;
          wheel.turn.setAttribute("transform", `rotate(${shown ?? 0})`);
          wheel.classList.toggle("missing", shown === null);
        }
      }
      for (const bar of bars) {
        const fill = pedalFill(values[bar.channel]);
        const key = fill === null ? -2 : Math.round(fill * 100);
        if (key === bar.last) continue;
        bar.last = key;
        const height = (fill ?? 0) * PEDAL_HEIGHT;
        bar.fill.setAttribute("y", String(PEDAL_TOP + PEDAL_HEIGHT - height));
        bar.fill.setAttribute("height", String(height));
        bar.pct.textContent = fill === null ? "" : String(key);
      }
    },
  };
}

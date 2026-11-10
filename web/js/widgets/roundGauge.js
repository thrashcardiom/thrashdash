import { h, s, clamp } from "../dom.js";
import { ICON_NAMES } from "../skins/icons.js";
import { createSkinnedGauge } from "../skins/widgets.js";
import { KIND_OPTIONS, resolveUnit, pick } from "../units.js";

// Dial geometry: 0 degrees is straight up; the sweep runs from -135 to +135 degrees clockwise.
const START = -135;
const END = 135;
const CX = 100;
const CY = 100;

const point = (deg, r) => {
  const a = (deg * Math.PI) / 180;
  return [CX + r * Math.sin(a), CY - r * Math.cos(a)];
};

const arc = (a0, a1, r) => {
  const [x0, y0] = point(a0, r);
  const [x1, y1] = point(a1, r);
  return `M ${x0.toFixed(2)} ${y0.toFixed(2)} A ${r} ${r} 0 ${a1 - a0 > 180 ? 1 : 0} 1 ${x1.toFixed(2)} ${y1.toFixed(2)}`;
};

export const spec = {
  label: "Round gauge",
  size: { w: 300, h: 300 },
  defaults: { kind: "none", min: 0, max: 100, majorStep: 10, minorDiv: 2, decimals: 0 },
  props: {
    channel: { type: "channel", label: "Channel" },
    kind: { type: "select", label: "Unit type", options: KIND_OPTIONS },
    label: { type: "text", label: "Title", max: 24 },
    showUnit: { type: "text", label: "Unit text", max: 12 },
    decimals: { type: "number", label: "Decimals", min: 0, max: 4, int: true },
    min: { type: "unitNumber", label: "Scale minimum" },
    max: { type: "unitNumber", label: "Scale maximum" },
    maxChannel: { type: "channel", label: "Scale max from channel" },
    maxRound: { type: "number", label: "Round scale up to", min: 1, max: 100000 },
    majorStep: { type: "unitNumber", label: "Major tick every" },
    minorDiv: { type: "number", label: "Minor ticks per major", min: 1, max: 10, int: true },
    labelDiv: { type: "number", label: "Divide tick labels by", min: 0.001, max: 100000 },
    redFrom: { type: "number", label: "Red zone from (0-1)", min: 0, max: 1 },
    // ---- skinned dials only (shown when the dashboard has a skin) ----
    startAngle: { type: "number", label: "Sweep start (deg)", min: -180, max: 180, int: true, skinOnly: true },
    endAngle: { type: "number", label: "Sweep end (deg)", min: -180, max: 180, int: true, skinOnly: true },
    labels: { type: "textList", label: "Labels (comma separated)", maxItems: 12, max: 8, skinOnly: true },
    icon: { type: "select", label: "Face icon", options: ["", ...ICON_NAMES], skinOnly: true },
    amberFrom: { type: "number", label: "Amber zone from (0-1)", min: 0, max: 1, skinOnly: true },
    coldTo: { type: "number", label: "Blue zone up to (0-1)", min: 0, max: 1, skinOnly: true },
    lowTo: { type: "number", label: "Red low zone up to (0-1)", min: 0, max: 1, skinOnly: true },
    sub: { type: "select", label: "Display window", options: ["", "odometer", "lcd"], skinOnly: true },
    subChannel: { type: "channel", label: "Window channel", skinOnly: true },
    subKind: { type: "select", label: "Window unit type", options: KIND_OPTIONS, skinOnly: true },
    subLabel: { type: "text", label: "Window caption", max: 8, skinOnly: true },
    shiftFrom: { type: "number", label: "Shift light from (0-1)", min: 0, max: 1, skinOnly: true },
    compact: { type: "bool", label: "Compact (small dial)", skinOnly: true },
    showValue: { type: "bool", label: "Show the value on the face", skinOnly: true },
  },
};

/**
 * Round gauge. Layout properties:
 *   channel, kind ("speed"|"temp"|...), label, decimals
 *   min, max            number or {unit: number}
 *   maxChannel, maxRound   take the scale top from a channel (e.g. maxrpm) rounded UP to maxRound
 *   majorStep, minorDiv    tick spacing (number or {unit: number}); minorDiv minor ticks per major
 *   labelDiv               divide tick labels (e.g. 1000 shows rpm as 0..8)
 *   redFrom                where the red zone starts, as a fraction of the scale (0..1)
 *   showUnit               unit text under the value ("km/h"); defaults to the converted unit label
 */
export function create(def, ctx) {
  if (ctx.skin) return createSkinnedGauge(def, ctx);
  const { unit, label: unitLabel, convert } = resolveUnit(def.kind, ctx.prefs);
  const min = pick(def.min, unit, 0);
  let max = pick(def.max, unit, 100);

  const root = h("div", "w w-gauge");
  const svg = s("svg", { viewBox: "0 0 200 200" }, root);
  const scaleGroup = s("g", {}, svg); // ticks/labels/track: rebuilt when the scale top changes
  const needleGroup = s("g", { class: "g-needle-group", style: `transform-origin: ${CX}px ${CY}px` }, svg);
  s("polygon", { class: "g-needle", points: `${CX - 3.5},${CY + 12} ${CX + 3.5},${CY + 12} ${CX + 1.2},${CY - 78} ${CX - 1.2},${CY - 78}` }, needleGroup);
  s("circle", { class: "g-hub", cx: CX, cy: CY, r: 9 }, svg);
  if (def.label) s("text", { class: "g-label", x: CX, y: 138 }, svg, def.label);
  const valueEl = s("text", { class: "g-value", x: CX, y: 172 }, svg, "--");
  s("text", { class: "g-unit", x: CX, y: 188 }, svg, def.showUnit ?? unitLabel);

  const angleFor = (v) => START + clamp((v - min) / (max - min), 0, 1) * (END - START);

  function buildScale() {
    scaleGroup.replaceChildren();
    s("path", { class: "g-track", d: arc(START, END, 86) }, scaleGroup);
    if (def.redFrom !== undefined) {
      s("path", { class: "g-red", d: arc(angleFor(min + (max - min) * def.redFrom), END, 86) }, scaleGroup);
    }
    let step = pick(def.majorStep, unit, (max - min) / 10);
    step *= Math.max(1, Math.ceil((max - min) / step / 10)); // never more than ~10 labels, whatever the scale
    const minorDiv = def.minorDiv ?? 2;
    const labelDiv = def.labelDiv ?? 1;
    for (let v = min; v <= max + 1e-6; v += step) {
      const a = angleFor(v);
      const [x0, y0] = point(a, 74);
      const [x1, y1] = point(a, 86);
      s("line", { class: "g-major", x1: x0, y1: y0, x2: x1, y2: y1 }, scaleGroup);
      const [tx, ty] = point(a, 60);
      s("text", { class: "g-num", x: tx, y: ty }, scaleGroup, String(Math.round((v / labelDiv) * 100) / 100));
      for (let m = 1; m < minorDiv && v + (step * m) / minorDiv <= max + 1e-6; m++) {
        const am = angleFor(v + (step * m) / minorDiv);
        const [a0, b0] = point(am, 79);
        const [a1, b1] = point(am, 86);
        s("line", { class: "g-minor", x1: a0, y1: b0, x2: a1, y2: b1 }, scaleGroup);
      }
    }
  }
  buildScale();

  let lastText = "";
  return {
    el: root,
    update(values) {
      if (def.maxChannel) {
        const top = values[def.maxChannel];
        if (typeof top === "number" && top > 0) {
          const round = def.maxRound ?? 1000;
          const wanted = Math.ceil(top / round) * round;
          if (wanted !== max) {
            max = wanted;
            buildScale();
          }
        }
      }
      const raw = values[def.channel];
      const missing = typeof raw !== "number";
      root.classList.toggle("missing", missing);
      const v = missing ? min : convert(raw);
      needleGroup.style.transform = `rotate(${angleFor(v).toFixed(1)}deg)`;
      const text = missing ? "--" : v.toFixed(def.decimals ?? 0);
      if (text !== lastText) {
        valueEl.textContent = text;
        lastText = text;
      }
    },
  };
}

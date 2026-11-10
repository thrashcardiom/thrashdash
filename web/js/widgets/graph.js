import { h, s } from "../dom.js";
import { KIND_OPTIONS, pick, resolveUnit } from "../units.js";

export const spec = {
  label: "Graph (history)",
  size: { w: 360, h: 140 },
  defaults: { kind: "none", min: 0, max: 100, seconds: 30, decimals: 0 },
  props: {
    channel: { type: "channel", label: "Channel" },
    kind: { type: "select", label: "Unit type", options: KIND_OPTIONS },
    label: { type: "text", label: "Title", max: 24 },
    min: { type: "unitNumber", label: "Scale minimum" },
    max: { type: "unitNumber", label: "Scale maximum" },
    seconds: { type: "number", label: "History (seconds)", min: 5, max: 120 },
    decimals: { type: "number", label: "Decimals", min: 0, max: 4, int: true },
    color: { type: "color", label: "Line colour" },
  },
};

const SAMPLE_MS = 50; // 20 samples/s is plenty for a line; it also bounds the work per frame

/** Scrolling line graph of one channel over the last N seconds. */
export function create(def, ctx) {
  const { unit, label: unitLabel, convert } = resolveUnit(def.kind, ctx.prefs);
  const min = pick(def.min, unit, 0);
  const max = pick(def.max, unit, 100);
  const windowMs = (def.seconds ?? 30) * 1000;
  const W = 200;
  const H = 100;

  const root = h("div", "w w-graph");
  if (def.color) root.style.setProperty("--c", def.color);
  const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, preserveAspectRatio: "none" }, root);
  for (const frac of [0.25, 0.5, 0.75]) s("line", { class: "gr-grid", x1: 0, x2: W, y1: H * frac, y2: H * frac }, svg);
  const line = s("polyline", { class: "gr-line", points: "" }, svg);
  if (def.label) h("div", "gr-label", root, def.label);
  const valueEl = h("div", "gr-value", root, "--");
  h("div", "gr-max", root, String(Math.round(max)));
  h("div", "gr-min", root, String(Math.round(min)));

  const samples = []; // [timestamp ms, value in display units]
  let lastSample = 0;
  let lastText = "";

  function redraw(now) {
    const pts = [];
    for (const [t, v] of samples) {
      const x = W * (1 - (now - t) / windowMs);
      const y = H * (1 - Math.min(1, Math.max(0, (v - min) / (max - min || 1))));
      pts.push(`${x.toFixed(1)},${y.toFixed(1)}`);
    }
    line.setAttribute("points", pts.join(" "));
  }

  return {
    el: root,
    update(values) {
      const raw = values[def.channel];
      const missing = typeof raw !== "number";
      root.classList.toggle("missing", missing);
      if (missing) {
        if (lastText !== "--") valueEl.textContent = lastText = "--";
        return;
      }
      const now = performance.now();
      const v = convert(raw);
      const text = `${v.toFixed(def.decimals ?? 0)} ${def.showUnit ?? unitLabel}`.trim();
      if (text !== lastText) {
        valueEl.textContent = text;
        lastText = text;
      }
      if (now - lastSample < SAMPLE_MS) return;
      lastSample = now;
      samples.push([now, v]);
      while (samples.length && now - samples[0][0] > windowMs) samples.shift();
      redraw(now);
    },
  };
}

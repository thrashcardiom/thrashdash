// Skinned versions of the non-gauge widgets: gear indicators, telltale lamps, LED bars and the LCD readout.
// Same idea as gauge.js: static art built once, tiny updates at runtime.
import { h, s } from "../dom.js";
import { drawIcon } from "./icons.js";

let counter = 0;
const nextId = (p) => `${p}${++counter}`;

function gradient(defs, tag, attrs, stops) {
  const id = nextId("pg");
  const g = s(tag, { id, ...attrs }, defs);
  for (const [offset, color, opacity] of stops) s("stop", { offset, "stop-color": color, "stop-opacity": opacity ?? 1 }, g);
  return `url(#${id})`;
}

/** Shift-lever style strip: P R N D 2 1 (or any letters/digits), the active one lit. */
export function buildGearStrip(skin, { letters = ["P", "R", "N", "D", "2", "1"] } = {}) {
  const root = h("div", `sk-strip sk-${skin.id}`);
  const cells = letters.map((l) => h("span", "sk-strip-cell", root, l));
  return {
    el: root,
    /** active: the gear text ("N", "D", "3"...) or null. */
    set(active) {
      cells.forEach((c, i) => c.classList.toggle("on", letters[i] === String(active)));
    },
  };
}

/** Large 7-segment digit(s), for the current gear number. */
export function buildGearDigit(skin) {
  const root = h("div", `sk-digit sk-${skin.id}`);
  h("span", "sk-digit-ghost", root, "8"); // the unlit segments, as on a real LCD
  const lit = h("span", "sk-digit-lit", root, "-");
  return {
    el: root,
    set(text) {
      lit.textContent = text === null || text === undefined ? "-" : String(text);
    },
  };
}

const LAMP_COLORS = ["red", "amber", "green", "blue"];

/** Round telltale with an icon. color: red | amber | green | blue (the colour it lights up in). */
export function buildLamp(skin, { icon, color = "amber", text } = {}) {
  const on = skin.lamp.on[LAMP_COLORS.includes(color) ? color : "amber"];
  const svg = s("svg", { viewBox: "0 0 48 48", class: `sk-lamp sk-${skin.id}` });
  const defs = s("defs", {}, svg);
  const lens = skin.lamp.style === "jewel" ? drawJewel(svg, defs, on) : drawGlowPlate(svg, defs, on);
  const iconHolder = s("g", {}, svg);
  if (text) s("text", { x: 24, y: 24, dy: "0.35em", "text-anchor": "middle", "font-family": "'Barlow Condensed', sans-serif", "font-weight": 600, "font-size": 13, fill: "currentColor" }, iconHolder, text);
  else drawIcon(iconHolder, icon, 24, 24, 24, { strokeWidth: 1.9 });
  return {
    el: svg,
    set(lit) {
      lens(lit);
      iconHolder.style.color = lit ? (skin.lamp.style === "jewel" ? "#fff" : on) : skin.lamp.style === "jewel" ? "rgba(255,255,255,.28)" : "#3b4656";
      iconHolder.style.filter = lit && skin.lamp.style !== "jewel" ? `drop-shadow(0 0 2.2px ${on}) drop-shadow(0 0 5px ${on})` : "none";
    },
  };
}

function drawJewel(svg, defs, on) {
  const chrome = gradient(defs, "linearGradient", { x1: 0.15, y1: 0, x2: 0.85, y2: 1 }, [[0, "#fff"], [0.3, "#a8aeb6"], [0.55, "#eef0f2"], [0.8, "#737982"], [1, "#f4f5f6"]]);
  s("circle", { cx: 24, cy: 24, r: 23, fill: chrome }, svg);
  s("circle", { cx: 24, cy: 24, r: 20, fill: "#0e0f11" }, svg);
  const offFill = gradient(defs, "radialGradient", { cx: 0.38, cy: 0.32, r: 0.9 }, [[0, "#3a3d42"], [1, "#101113"]]);
  const onFill = gradient(defs, "radialGradient", { cx: 0.4, cy: 0.35, r: 0.85 }, [[0, on], [0.55, on, 0.95], [1, "#000", 0.35]]);
  const lens = s("circle", { cx: 24, cy: 24, r: 18, fill: offFill }, svg);
  const halo = s("circle", { cx: 24, cy: 24, r: 22, fill: on, opacity: 0 }, svg);
  halo.style.filter = "blur(4px)";
  s("ellipse", { cx: 18.5, cy: 15.5, rx: 8, ry: 4.6, fill: "#fff", opacity: 0.28, transform: "rotate(-28 18.5 15.5)" }, svg);
  return (lit) => {
    lens.setAttribute("fill", lit ? onFill : offFill);
    halo.setAttribute("opacity", lit ? "0.55" : "0");
  };
}

function drawGlowPlate(svg, defs, on) {
  const plate = s("rect", { x: 2, y: 2, width: 44, height: 44, rx: 9, fill: "#0a0d12", stroke: "#26313f", "stroke-width": 1.2 }, svg);
  const wash = s("rect", { x: 2, y: 2, width: 44, height: 44, rx: 9, fill: on, opacity: 0 }, svg);
  void plate;
  return (lit) => wash.setAttribute("opacity", lit ? "0.14" : "0");
}

/** Row of segments that fill up like an LED bar graph. */
export function buildLedBar(skin, { segments = 20, amberFrom = 0.7, redFrom = 0.9, invert = false } = {}) {
  const root = h("div", `sk-ledbar sk-${skin.id}`);
  const cells = [];
  for (let i = 0; i < segments; i++) {
    const f = (i + 1) / segments;
    const level = invert ? (f <= 1 - redFrom ? "red" : f <= 1 - amberFrom ? "amber" : "ok") : f > redFrom ? "red" : f > amberFrom ? "amber" : "ok";
    const c = h("span", "sk-led", root);
    c.dataset.level = level;
    cells.push(c);
  }
  return {
    el: root,
    set(frac) {
      const lit = frac === null ? 0 : Math.round(Math.min(1, Math.max(0, frac)) * segments);
      cells.forEach((c, i) => c.classList.toggle("on", i < lit));
    },
  };
}

/** Dark LCD panel with glowing 7-segment digits: odometer, trip, anything numeric. */
export function buildLcdReadout(skin, { label = "", unit = "", digits = 6 } = {}) {
  const root = h("div", `sk-lcd sk-${skin.id}`);
  root.style.setProperty("--digits", String(digits));
  if (label) h("div", "sk-lcd-label", root, label);
  const body = h("div", "sk-lcd-body", root);
  const row = h("div", "sk-lcd-row", body);
  const ghost = h("span", "sk-lcd-ghost", row, "8".repeat(digits));
  const lit = h("span", "sk-lcd-lit", row, "0");
  if (unit) h("span", "sk-lcd-unit", body, unit);
  return {
    el: root,
    set(text) {
      const shown = text === null || text === undefined ? "" : String(text);
      lit.textContent = shown;
      // The unlit "8" segments mirror the lit text (so a decimal point lines up) and pad out to the full digit count.
      const used = (shown.match(/[0-9]/g) ?? []).length;
      ghost.textContent = "8".repeat(Math.max(0, digits - used)) + shown.replace(/[0-9]/g, "8");
    },
  };
}

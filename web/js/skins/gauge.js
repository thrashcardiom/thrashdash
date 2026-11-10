// Skinned round gauge: builds an SVG dial (viewBox 0 0 200 200) from a skin (see themes.js) and geometry options.
//
// Static art (bezel, face, ticks, numerals, glass) is built once; only the needle's rotation, the sub-display
// text and the shift LED change at runtime, so animating at 30 Hz is cheap. Everything is vector, so it stays
// sharp at any window size or scale.
import { s } from "../dom.js";
import { drawIcon } from "./icons.js";

let counter = 0;
const nextId = (prefix) => `${prefix}${++counter}`;
const CX = 100;
const CY = 100;
const rad = (d) => (d * Math.PI) / 180;
const pt = (deg, r) => [CX + r * Math.sin(rad(deg)), CY - r * Math.cos(rad(deg))];
const r2 = (v) => Math.round(v * 100) / 100;
const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));

function arcPath(a0, a1, r) {
  const [x0, y0] = pt(a0, r);
  const [x1, y1] = pt(a1, r);
  return `M ${r2(x0)} ${r2(y0)} A ${r} ${r} 0 ${Math.abs(a1 - a0) > 180 ? 1 : 0} ${a1 > a0 ? 1 : 0} ${r2(x1)} ${r2(y1)}`;
}

/** Add a gradient to <defs> and return its url() reference. */
function gradient(defs, tag, attrs, stops) {
  const id = nextId("g");
  const g = s(tag, { id, ...attrs }, defs);
  for (const [offset, color, opacity] of stops) s("stop", { offset, "stop-color": color, "stop-opacity": opacity ?? 1 }, g);
  return `url(#${id})`;
}

const CHROME_STOPS = [[0, "#ffffff"], [0.18, "#a7adb5"], [0.34, "#f1f3f5"], [0.5, "#6d737b"], [0.66, "#e7eaed"], [0.82, "#8b9199"], [1, "#f8f9fa"]];

function drawBezel(svg, defs, skin) {
  if (skin.glow) {
    // Light leaking out around the bezel from the backlighting.
    const amb = gradient(defs, "radialGradient", { cx: 0.5, cy: 0.5, r: 0.5 }, [[0.86, skin.glow, 0], [0.9, skin.glow, 0.5], [1, skin.glow, 0]]);
    s("circle", { cx: CX, cy: CY, r: 114, fill: amb }, svg);
  }
  if (skin.bezel === "chrome") {
    s("circle", { cx: CX, cy: CY, r: 99, fill: gradient(defs, "linearGradient", { x1: 0.15, y1: 0, x2: 0.85, y2: 1 }, CHROME_STOPS) }, svg);
    s("circle", { cx: CX, cy: CY, r: 93.5, fill: gradient(defs, "linearGradient", { x1: 0.85, y1: 1, x2: 0.15, y2: 0 }, CHROME_STOPS) }, svg);
    s("circle", { cx: CX, cy: CY, r: 88.5, fill: "#0f1012" }, svg);
  } else {
    s("circle", { cx: CX, cy: CY, r: 99, fill: gradient(defs, "linearGradient", { x1: 0.2, y1: 0, x2: 0.8, y2: 1 }, [[0, "#454c55"], [0.5, "#14171b"], [1, "#050607"]]) }, svg);
    s("circle", { cx: CX, cy: CY, r: 98, fill: "none", stroke: "#aab3be", "stroke-width": 0.9, "stroke-opacity": 0.7 }, svg);
    s("circle", { cx: CX, cy: CY, r: 91, fill: "#06070a" }, svg);
    s("circle", { cx: CX, cy: CY, r: 88.5, fill: "none", stroke: skin.glow ?? "#ffffff", "stroke-width": 0.8, "stroke-opacity": 0.35 }, svg);
  }
}

const FACES = {
  white: [[0, "#ffffff"], [0.7, "#f3f4f5"], [1, "#d9dcdf"]],
  black: [[0, "#383b41"], [0.65, "#16171a"], [1, "#070708"]],
  frost: [[0, "#f7fbff"], [0.6, "#e0ebfc"], [1, "#b2cbef"]],
};

function drawFace(svg, defs, skin) {
  s("circle", { cx: CX, cy: CY, r: 86, fill: gradient(defs, "radialGradient", { cx: 0.5, cy: 0.42, r: 0.62 }, FACES[skin.face]) }, svg);
  // The face sits recessed under the bezel: darken its rim a little.
  s("circle", { cx: CX, cy: CY, r: 86, fill: gradient(defs, "radialGradient", { cx: 0.5, cy: 0.5, r: 0.5 }, [[0.8, "#000", 0], [1, "#000", skin.face === "white" ? 0.3 : 0.55]]) }, svg);
}

function drawGlass(svg, defs, skin) {
  const g = skin.glass;
  if (!g) return;
  const none = { "pointer-events": "none" };
  const clip = nextId("glassclip");
  s("circle", { cx: CX, cy: CY, r: 85.5 }, s("clipPath", { id: clip }, defs));
  const layer = s("g", { "clip-path": `url(#${clip})`, ...none }, svg);

  // 0. Light faces need something for the reflection to show against: shade the dome toward the lower right,
  //    so the glare reads as a bright patch on a slightly darker face (as on a real white dial).
  const shade = skin.face === "white" ? 0.25 : skin.face === "frost" ? 0.13 : 0;
  if (shade) s("circle", { cx: CX, cy: CY, r: 86, fill: gradient(defs, "radialGradient", { cx: 0.3, cy: 0.2, r: 1.0 }, [[0.3, "#1b2230", 0], [1, "#1b2230", shade]]) }, layer);

  // 1. Soft dome light: the curved glass catches more light toward the upper left.
  s("circle", { cx: CX, cy: CY, r: 86, fill: gradient(defs, "radialGradient", { cx: 0.3, cy: 0.2, r: 0.85 }, [[0, "#fff", g * 0.5], [0.4, "#fff", g * 0.14], [1, "#fff", 0]]) }, layer);

  // 2. Crisp lens-shaped reflection along the upper-left rim, fading toward the lower right.
  const [ax, ay] = pt(-132, 85);
  const [bx, by] = pt(20, 85);
  const [c1x, c1y] = pt(2, 56);
  const [c2x, c2y] = pt(-92, 54);
  s("path", { d: `M ${r2(ax)} ${r2(ay)} A 85 85 0 0 1 ${r2(bx)} ${r2(by)} C ${r2(c1x)} ${r2(c1y)} ${r2(c2x)} ${r2(c2y)} ${r2(ax)} ${r2(ay)} Z`, stroke: shade ? "#1b2230" : "none", "stroke-opacity": 0.07, "stroke-width": 0.6, fill: gradient(defs, "linearGradient", { x1: 0.05, y1: 0.05, x2: 0.8, y2: 0.75 }, [[0, "#fff", g * 0.85], [0.55, "#fff", g * 0.28], [1, "#fff", g * 0.04]]) }, layer);

  // 3. Second, fainter streak below the main one: the double reflection you get from thick glass.
  const [dx, dy] = pt(-118, 78);
  const [ex, ey] = pt(-30, 78);
  const [f1x, f1y] = pt(-70, 66);
  s("path", { d: `M ${r2(dx)} ${r2(dy)} A 78 78 0 0 1 ${r2(ex)} ${r2(ey)} Q ${r2(f1x)} ${r2(f1y)} ${r2(dx)} ${r2(dy)} Z`, fill: "#fff", opacity: g * 0.1 }, layer);

  // 4. Bright edge where the glass meets the bezel (upper left) and a faint bounce light opposite it.
  s("path", { d: arcPath(-136, 22, 84.6), fill: "none", stroke: "#fff", "stroke-opacity": 0.5 * g + 0.1, "stroke-width": 1.2, "stroke-linecap": "round" }, layer);
  const blur = nextId("gblur");
  s("feGaussianBlur", { stdDeviation: 0.9 }, s("filter", { id: blur, x: "-20%", y: "-20%", width: "140%", height: "140%" }, defs));
  s("path", { d: arcPath(122, 218, 82), fill: "none", stroke: "#fff", "stroke-opacity": 0.24 * g + 0.04, "stroke-width": 3.4, "stroke-linecap": "round", filter: `url(#${blur})` }, layer);
}

const NEEDLES = {
  classic: { shape: "M100 19 L103.4 100 L102 128 L98 128 L96.6 100 Z", glow: "M100 19 L105.5 100 L103.5 128 L96.5 128 L94.5 100 Z" },
  slim: { shape: "M100 21 L101.9 100 L101.1 122 L98.9 122 L98.1 100 Z", glow: "M100 21 L104.6 100 L103 122 L97 122 L95.4 100 Z" },
};

const text = (parent, str, x, y, style, extra = {}) =>
  s("text", { x, y, "text-anchor": "middle", "font-family": style.family, "font-weight": style.weight, "font-size": style.size, fill: style.color, "letter-spacing": style.letterSpacing ?? 0, ...extra }, parent, str);

/**
 * @param {object} o geometry & content
 *   startAngle,endAngle   sweep in degrees from straight up, clockwise (default -135..135; small gauges use less)
 *   min,max,majorStep,minorDiv,labelDiv    scale
 *   labels      optional strings for the major ticks instead of numbers (e.g. ["E","1/2","F"])
 *   redFrom, amberFrom   where the amber/red zones start; coldTo / lowTo   where the blue (cold) / red (low) zones end
 *                        (all as fractions of the scale, 0..1)
 *   title, unit, titlePos ("top"|"bottom"), icon (name from icons.js)
 *   sub         "odometer" | "lcd" | null: the small display window on the lower face (see skin.sub)
 *   subLabel    caption above an LCD window (e.g. "TRIP")
 *   shiftLight  true to include the shift-light LED
 *   showValue   true to print the current value on the face (returned `setValue(text)` updates it)
 * @param {object} skin a skin from themes.js
 */
export function buildGauge(o, skin) {
  // Undefined options are dropped so a caller can pass optional layout properties straight through.
  const given = Object.fromEntries(Object.entries(o).filter(([, v]) => v !== undefined));
  const opts = { startAngle: -135, endAngle: 135, min: 0, max: 100, majorStep: 10, minorDiv: 2, labelDiv: 1, titlePos: "bottom", ...given };
  // Compact: for small dials. Bigger numerals and title (they are tiny otherwise), icon on top, title below, no unit line.
  const k = opts.compact ? 1.32 : 1;
  const numeralStyle = (extra) => ({ ...skin.numerals, size: skin.numerals.size * k, ...extra });
  const { startAngle: a0, endAngle: a1 } = opts;
  const frac = (v) => (v - opts.min) / (opts.max - opts.min);
  const angleFor = (f) => a0 + clamp(f, 0, 1) * (a1 - a0);

  const svg = s("svg", { viewBox: "0 0 200 200", class: `sk-gauge sk-${skin.id}`, overflow: "visible" });
  const defs = s("defs", {}, svg);
  drawBezel(svg, defs, skin);
  drawFace(svg, defs, skin);

  const zoneColor = (f) => {
    if (opts.lowTo !== undefined && f <= opts.lowTo + 1e-9) return skin.zones.red;
    if (opts.redFrom !== undefined && f >= opts.redFrom - 1e-9) return skin.zones.red;
    if (opts.amberFrom !== undefined && f >= opts.amberFrom - 1e-9) return skin.zones.amber;
    if (opts.coldTo !== undefined && f <= opts.coldTo + 1e-9) return skin.zones.blue;
    return null;
  };

  // Thin coloured bands just outside the ticks mark the zones.
  const bands = [[opts.lowTo !== undefined ? 0 : null, opts.lowTo, skin.zones.red], [opts.coldTo !== undefined ? 0 : null, opts.coldTo, skin.zones.blue], [opts.amberFrom, opts.redFrom ?? 1, skin.zones.amber], [opts.redFrom, 1, skin.zones.red]];
  for (const [from, to, color] of bands) {
    if (from === null || from === undefined || to === undefined || to <= from) continue;
    s("path", { d: arcPath(angleFor(from), angleFor(to), 83.5), fill: "none", stroke: color, "stroke-width": 2.6, "stroke-linecap": "butt", opacity: 0.95 }, svg);
  }

  // Ticks and numerals go in their own groups so a backlit skin can reuse them (<use>) as a blurred glow layer.
  const glowLayer = skin.glow ? s("g", {}, svg) : null;
  const ticks = s("g", { id: nextId("ticks") }, svg);
  const numerals = s("g", { id: nextId("nums") }, svg);

  const per = opts.majorStep / opts.minorDiv;
  const count = Math.round((opts.max - opts.min) / per);
  const majorCount = Math.floor(count / opts.minorDiv) + 1;
  const majors = [];
  for (let i = 0; i <= count; i++) {
    const v = opts.min + i * per;
    const f = frac(v);
    const major = i % opts.minorDiv === 0;
    const a = angleFor(f);
    const [x0, y0] = pt(a, 80);
    const [x1, y1] = pt(a, major ? 67 : 74);
    s("line", { x1: r2(x0), y1: r2(y0), x2: r2(x1), y2: r2(y1), stroke: zoneColor(f) ?? (major ? skin.ink : skin.inkSoft), "stroke-width": major ? 2.3 : 1.1, "stroke-linecap": "butt" }, ticks);
    if (major) majors.push({ v, f, a });
  }
  // Numerals: custom labels spread over the majors (or the full sweep), otherwise the scale values.
  const labelAt = (i) => {
    if (!opts.labels) return String(Math.round((majors[i].v / opts.labelDiv) * 100) / 100);
    if (opts.labels.length === majorCount) return opts.labels[i];
    return null;
  };
  if (opts.labels && opts.labels.length !== majorCount) {
    opts.labels.forEach((label, i) => {
      if (!label) return;
      const a = angleFor(opts.labels.length === 1 ? 0.5 : i / (opts.labels.length - 1));
      const [x, y] = pt(a, 56 - (k - 1) * 8);
      text(numerals, label, r2(x), r2(y), numeralStyle({ color: skin.ink }), { dy: "0.35em" });
    });
  } else {
    majors.forEach((m, i) => {
      const label = labelAt(i);
      if (!label) return;
      const [x, y] = pt(m.a, 56 - (k - 1) * 8);
      text(numerals, label, r2(x), r2(y), numeralStyle({ color: zoneColor(m.f) ?? skin.ink }), { dy: "0.35em" });
    });
  }
  if (glowLayer) {
    const blur = nextId("blur");
    const f = s("filter", { id: blur, x: "-30%", y: "-30%", width: "160%", height: "160%" }, defs);
    s("feGaussianBlur", { stdDeviation: 1.7 }, f);
    glowLayer.setAttribute("filter", `url(#${blur})`);
    glowLayer.setAttribute("opacity", "0.85");
    s("use", { href: `#${ticks.id}` }, glowLayer);
    s("use", { href: `#${numerals.id}` }, glowLayer);
  }

  // Face furniture: icon, title/unit, sub-display, shift light.
  const hasSub = Boolean(opts.sub);
  const lowerSlot = hasSub || Boolean(opts.showValue); // the lower face is taken by a window or a printed value
  if (opts.icon) {
    const holder = s("g", { color: skin.glow ? skin.ink : skin.inkSoft, opacity: 0.95 }, svg);
    drawIcon(holder, opts.icon, 100, opts.compact ? 72 : opts.titlePos === "top" ? 70 : 60, opts.compact ? 17 : 19);
  }
  if (opts.title) {
    const style = { ...skin.title, size: skin.title.size * k };
    const y = opts.compact ? 133 : opts.titlePos === "top" ? 84 : lowerSlot ? 118 : opts.shiftLight ? 121 : 128;
    text(svg, opts.title, 100, y, style);
  }
  if (opts.unit && !opts.compact) {
    const style = { ...skin.title, size: skin.title.size - 0.8 };
    const y = opts.titlePos === "top" ? 94 : lowerSlot ? 127 : opts.shiftLight ? 130 : 138;
    text(svg, opts.unit, 100, y, style);
  }

  let setSub = () => {};
  if (opts.sub === "odometer") setSub = drawOdometer(svg, skin);
  else if (opts.sub === "lcd") setSub = drawLcd(svg, defs, skin, opts.subLabel);

  let setValue = () => {};
  if (opts.showValue && !hasSub) {
    const valueEl = text(svg, "--", 100, 149, { family: skin.title.family, weight: 600, size: 16, color: skin.ink, letterSpacing: 0.3 });
    setValue = (str) => {
      if (valueEl.textContent !== str) valueEl.textContent = str;
    };
  }

  let setShift = () => {};
  if (opts.shiftLight) setShift = drawShiftLight(svg, defs, skin, opts.showValue && !hasSub ? 162 : 150);

  // Needle: a fixed-offset shadow (light comes from the top-left), then the needle, then the hub cap.
  const spec = NEEDLES[skin.needle.style];
  const shadowWrap = s("g", { transform: "translate(2.4 3.4)" }, svg);
  const shadowRot = s("g", { class: "sk-rot", style: "transform-origin: 100px 100px" }, shadowWrap);
  s("path", { d: spec.shape, fill: "#000", opacity: 0.28 }, shadowRot);
  s("circle", { cx: CX, cy: CY, r: 9.5, fill: "#000", opacity: 0.28 }, shadowWrap);

  const needleRot = s("g", { class: "sk-rot", style: "transform-origin: 100px 100px" }, svg);
  if (skin.needle.glow) s("path", { d: spec.glow, fill: skin.needle.glow, opacity: 0.28 }, needleRot);
  s("path", { d: spec.shape, fill: skin.needle.color }, needleRot);
  if (skin.needle.style === "slim") s("path", { d: "M100 24 L100.7 100", stroke: "#fff", "stroke-opacity": 0.35, "stroke-width": 0.5 }, needleRot);

  const dome = gradient(defs, "radialGradient", { cx: 0.35, cy: 0.3, r: 0.85 }, [[0, skin.hub.dome[0]], [1, skin.hub.dome[1]]]);
  s("circle", { cx: CX, cy: CY, r: 9.5, fill: dome, stroke: skin.hub.rim, "stroke-width": 1 }, svg);
  s("ellipse", { cx: 97.2, cy: 96.8, rx: 3.6, ry: 2.2, fill: "#fff", opacity: 0.4 }, svg);

  drawGlass(svg, defs, skin);

  const rots = svg.querySelectorAll(".sk-rot");
  const setAngle = (deg) => {
    const t = `rotate(${deg.toFixed(1)}deg)`;
    for (const r of rots) r.style.transform = t;
  };
  setAngle(a0);

  return {
    el: svg,
    /** f: 0..1 along the scale, or null for "no data" (needle parks at the start). */
    setFraction(f) {
      svg.classList.toggle("sk-missing", f === null);
      setAngle(angleFor(f === null ? 0 : f));
    },
    setSub,
    setShift,
    setValue,
  };
}

function drawOdometer(svg, skin) {
  const cells = 6;
  const w = 7.6;
  const h = 14;
  const x0 = CX - (cells * w) / 2;
  const y0 = 139;
  const sub = skin.sub;
  s("rect", { x: x0 - 1.6, y: y0 - 1.6, width: cells * w + 3.2, height: h + 3.2, rx: 2.4, fill: "#2a2c30" }, svg);
  const digits = [];
  for (let i = 0; i < cells; i++) {
    const last = i === cells - 1;
    s("rect", { x: x0 + i * w + 0.3, y: y0, width: w - 0.6, height: h, fill: last ? sub.lastCell : sub.cell }, svg);
    digits.push(s("text", { x: x0 + i * w + w / 2, y: y0 + h / 2, dy: "0.36em", "text-anchor": "middle", "font-family": sub.family, "font-weight": 600, "font-size": 12, fill: last ? sub.lastDigit : sub.digit }, svg, "0"));
  }
  s("rect", { x: x0, y: y0, width: cells * w, height: h * 0.45, fill: "#fff", opacity: 0.1 }, svg); // glass sheen over the window
  return (str) => {
    const padded = String(str).replace(/[^0-9]/g, "").padStart(cells, "0").slice(-cells);
    digits.forEach((d, i) => (d.textContent = padded[i]));
  };
}

function drawLcd(svg, defs, skin, label) {
  const sub = skin.sub;
  const x = 73;
  const y = 138;
  const w = 54;
  const h = 17;
  const back = gradient(defs, "linearGradient", { x1: 0, y1: 0, x2: 0, y2: 1 }, [[0, sub.back[0]], [1, sub.back[1]]]);
  s("rect", { x: x - 1.5, y: y - 1.5, width: w + 3, height: h + 3, rx: 4, fill: "#0b0d10" }, svg);
  s("rect", { x, y, width: w, height: h, rx: 3, fill: back, stroke: "#1d3d5c", "stroke-width": 0.8 }, svg);
  // The caption sits just above the window, not inside it (where it would fight the digits).
  if (label) s("text", { x: x + 2, y: y - 3, "font-family": "'Barlow Condensed', sans-serif", "font-weight": 600, "font-size": 5.4, fill: skin.ink, opacity: 0.85, "letter-spacing": 0.8 }, svg, label);
  const glow = s("text", { x: x + w - 4, y: y + h - 4, "text-anchor": "end", "font-family": sub.family, "font-weight": 400, "font-size": 11.5, fill: sub.color, opacity: 0.5, style: "filter: blur(1.3px)" }, svg, "0.0");
  const main = s("text", { x: x + w - 4, y: y + h - 4, "text-anchor": "end", "font-family": sub.family, "font-weight": 400, "font-size": 11.5, fill: sub.color }, svg, "0.0");
  return (str) => {
    glow.textContent = main.textContent = String(str);
  };
}

function drawShiftLight(svg, defs, skin, cy = 150) {
  const cx = 100;
  const ring = s("circle", { cx, cy, r: 5.4, fill: "none", stroke: skin.inkSoft, "stroke-width": 0.9, opacity: 0.8 }, svg);
  const lamp = s("circle", { cx, cy, r: 3.8, fill: skin.shift.off }, svg);
  const halo = s("circle", { cx, cy, r: 9, fill: skin.shift.on, opacity: 0 }, svg);
  halo.style.filter = "blur(2.5px)";
  void ring;
  return (on) => {
    lamp.setAttribute("fill", on ? skin.shift.on : skin.shift.off);
    halo.setAttribute("opacity", on ? "0.75" : "0");
  };
}

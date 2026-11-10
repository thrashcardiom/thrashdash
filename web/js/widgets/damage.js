import { h, s } from "../dom.js";

export const spec = {
  label: "Car damage",
  size: { w: 200, h: 300 },
  defaults: { showPercent: true, showTyres: true, wreckedFrom: 15 },
  props: {
    label: { type: "text", label: "Title", max: 20 },
    showPercent: { type: "bool", label: "Show the damage summary (e.g. Wrecked)" },
    wreckedFrom: { type: "number", label: "Wrecked from (% of beams broken)", min: 2, max: 100 },
    showTyres: { type: "bool", label: "Mark flat tyres" },
    critFrom: { type: "number", label: "Fully red at (0-1)", min: 0.05, max: 1 },
  },
};

// The game divides the body into six zones: front, middle and rear, left and right.
export const ZONES = ["FL", "FR", "ML", "MR", "RL", "RR"];
const WHEELS = ["FL", "FR", "RL", "RR"];

/**
 * The game's zone damage is (broken beams + deformation) / ALL beams in the zone, and most of those beams are
 * internal structure a crash never reaches, so a completely wrecked car's worst zone reads only about 12% (seen in
 * the real game). Full red therefore comes early, at 15%; it can be changed per widget ("Fully red at").
 */
export const DEFAULT_CRIT = 0.15;

/**
 * Colour for a zone's damage (0..1): grey when undamaged, then green -> yellow -> red, reaching full red at `crit`.
 * @returns {string|null} a CSS colour, or null for "no data"
 */
export function damageColor(d, crit = DEFAULT_CRIT) {
  if (typeof d !== "number" || !Number.isFinite(d)) return null;
  if (d <= 0.005) return "var(--track)";
  const frac = Math.min(1, d / Math.max(0.05, crit));
  return `hsl(${Math.round(125 * (1 - frac))} 78% ${Math.round(42 + 6 * (1 - frac))}%)`;
}

/**
 * One word for how badly the car is damaged, from the share of the car's beams that are broken (and, for cars that are
 * only dented, deformed). Seen in the real game: a light scrape read 0% broken but 5.7% deformed, a wreck 23.6% broken.
 * "Wrecked" starts at `wreckedFrom` percent broken; "Heavy" at a third of that, "Moderate" at a fifteenth (1% by default).
 * Returns { word, detail } or null when the car sends no damage data.
 */
export function severity(values, wreckedFrom = 15) {
  const broken = values["damage.brokenPct"];
  const deformed = values["damage.deformedPct"];
  const ok = (x) => typeof x === "number" && Number.isFinite(x);
  if (!ok(broken)) return null;
  const dented = ok(deformed) ? deformed : 0;
  const shown = (x) => (x < 10 ? Math.round(x * 10) / 10 : Math.round(x));
  if (broken >= wreckedFrom) return { word: "Wrecked", detail: `${shown(broken)}% broken` };
  if (broken >= wreckedFrom / 3) return { word: "Heavy", detail: `${shown(broken)}% broken` };
  if (broken >= wreckedFrom / 15) return { word: "Moderate", detail: `${shown(broken)}% broken` };
  if (broken > 0) return { word: "Light", detail: `${shown(broken)}% broken` };
  if (dented >= 1) return { word: "Light", detail: `${shown(dented)}% dented` };
  return { word: "Undamaged", detail: "" };
}

/** The worst zone's damage (0..1), or null if no zone has data. */
export function worstZone(values) {
  let worst = null;
  for (const z of ZONES) {
    const d = values[`damage.zone.${z}`];
    if (typeof d === "number" && Number.isFinite(d)) worst = worst === null ? d : Math.max(worst, d);
  }
  return worst;
}

// A car seen from above in a 160 x 260 drawing. The outline clips the six zone rectangles, so damage shows as colour on the body.
const BODY = "M34 40 Q34 8 80 8 Q126 8 126 40 L132 84 L132 180 L126 224 Q126 252 80 252 Q34 252 34 224 L28 180 L28 84 Z";
const ZONE_RECT = {
  FL: [0, 0, 80, 90], FR: [80, 0, 80, 90], ML: [0, 90, 80, 80], MR: [80, 90, 80, 80], RL: [0, 170, 80, 90], RR: [80, 170, 80, 90],
};
const WHEEL_RECT = { FL: [14, 44, 14, 38], FR: [132, 44, 14, 38], RL: [14, 180, 14, 38], RR: [132, 180, 14, 38] };

export function create(def, ctx) {
  const crit = def.critFrom ?? DEFAULT_CRIT;
  const root = h("div", "w w-damage");
  if (ctx.skin) root.classList.add("w-skinned", `sk-${ctx.skin.id}`);
  if (def.label) h("div", "dg-label", root, def.label);
  const svg = s("svg", { viewBox: "0 0 160 260", preserveAspectRatio: "xMidYMid meet" }, root);
  const defs = s("defs", {}, svg);
  const clipId = `dmg${Math.random().toString(36).slice(2, 8)}`;
  s("path", { d: BODY }, s("clipPath", { id: clipId }, defs));

  const wheelEls = {};
  for (const w of WHEELS) {
    const [x, y, width, height] = WHEEL_RECT[w];
    wheelEls[w] = s("rect", { class: "dg-wheel", x, y, width, height, rx: 4 }, svg);
  }
  const zones = s("g", { "clip-path": `url(#${clipId})` }, svg);
  const zoneEls = {};
  for (const z of ZONES) {
    const [x, y, width, height] = ZONE_RECT[z];
    zoneEls[z] = s("rect", { class: "dg-zone", x, y, width, height, "data-zone": z }, zones);
  }
  s("path", { class: "dg-outline", d: BODY }, svg);
  // windscreen and rear window, for orientation: the nose is at the top
  s("path", { class: "dg-glass", d: "M52 64 Q80 52 108 64 L104 96 Q80 90 56 96 Z" }, svg);
  s("path", { class: "dg-glass", d: "M56 176 Q80 182 104 176 L108 204 Q80 214 52 204 Z" }, svg);
  s("line", { class: "dg-split", x1: 28, x2: 132, y1: 90, y2: 90 }, svg);
  s("line", { class: "dg-split", x1: 28, x2: 132, y1: 170, y2: 170 }, svg);
  s("line", { class: "dg-split", x1: 80, x2: 80, y1: 8, y2: 252 }, svg);
  const pct = s("text", { class: "dg-pct", x: 80, y: 134, "text-anchor": "middle" }, svg, "");
  const sub = s("text", { class: "dg-sub", x: 80, y: 146, "text-anchor": "middle" }, svg, "");

  const lastFill = {};
  return {
    el: root,
    update(values) {
      for (const z of ZONES) {
        const d = values[`damage.zone.${z}`];
        const colour = damageColor(d, crit);
        const key = colour ?? "none";
        if (lastFill[z] !== key) {
          lastFill[z] = key;
          zoneEls[z].style.fill = colour ?? "";
          zoneEls[z].classList.toggle("missing", colour === null);
        }
      }
      for (const w of WHEELS) {
        const flat = def.showTyres !== false && values[`tire.${w}.deflated`] === true;
        wheelEls[w].classList.toggle("flat", flat);
      }
      const found = def.showPercent !== false ? severity(values, def.wreckedFrom ?? 15) : null;
      const text = found ? found.word : "";
      if (pct.textContent !== text) pct.textContent = text;
      const caption = found ? found.detail.toUpperCase() : "";
      if (sub.textContent !== caption) sub.textContent = caption;
    },
  };
}

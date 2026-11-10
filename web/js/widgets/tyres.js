import { h, s } from "../dom.js";
import { pick, resolveUnit } from "../units.js";

export const spec = {
  label: "Tyres",
  size: { w: 230, h: 290 },
  defaults: { source: "temp", showPressure: true },
  props: {
    source: { type: "select", label: "Temperature of", options: ["temp", "coreTemp"] },
    showPressure: { type: "bool", label: "Show pressure" },
    coldBelow: { type: "unitNumber", label: "Cold below", unitKind: "temp" },
    warnFrom: { type: "unitNumber", label: "Hot from", unitKind: "temp" },
    critFrom: { type: "unitNumber", label: "Too hot from", unitKind: "temp" },
    lowPressure: { type: "unitNumber", label: "Low pressure below", unitKind: "pressure" },
    wheels: { type: "textList", label: "Wheel names FL, FR, RL, RR", maxItems: 4, max: 6 },
  },
};

// Starting points for a road tyre in BeamNG; the layout can override each (they are properties of the widget).
const DEFAULT_TEMP = { coldBelow: { c: 50, f: 122 }, warnFrom: { c: 100, f: 212 }, critFrom: { c: 120, f: 248 } };
const DEFAULT_LOW_PRESSURE = { kpa: 150, psi: 22, bar: 1.5 };
const DEFAULT_WHEELS = ["FL", "FR", "RL", "RR"];

/** "missing" without data, otherwise cold / ok / warn / crit from the temperature (all in the same unit). */
export function tyreLevel(temp, { cold, warn, crit }) {
  if (typeof temp !== "number" || !Number.isFinite(temp)) return "missing";
  if (crit !== undefined && temp >= crit) return "crit";
  if (warn !== undefined && temp >= warn) return "warn";
  if (cold !== undefined && temp < cold) return "cold";
  return "ok";
}

// Card positions inside the 200 x 260 drawing: [x, y] of the top-left corner, for FL, FR, RL, RR.
const CARDS = [[2, 30], [128, 30], [2, 158], [128, 158]];
const CARD_W = 70;
const CARD_H = 72;

/** Top-down car with four tyre cards: temperature, pressure, and a warning when a tyre is flat. */
export function create(def, ctx) {
  const temp = resolveUnit("temp", ctx.prefs);
  const press = resolveUnit("pressure", ctx.prefs);
  const thresholds = {
    cold: pick(def.coldBelow ?? DEFAULT_TEMP.coldBelow, temp.unit, undefined),
    warn: pick(def.warnFrom ?? DEFAULT_TEMP.warnFrom, temp.unit, undefined),
    crit: pick(def.critFrom ?? DEFAULT_TEMP.critFrom, temp.unit, undefined),
  };
  const lowPressure = pick(def.lowPressure ?? DEFAULT_LOW_PRESSURE, press.unit, undefined);
  const names = def.wheels?.length === 4 ? def.wheels : DEFAULT_WHEELS;
  const source = def.source === "coreTemp" ? "coreTemp" : "temp";

  const root = h("div", "w w-tyres");
  if (ctx.skin) root.classList.add("w-skinned", `sk-${ctx.skin.id}`);
  const svg = s("svg", { viewBox: "0 0 200 260", preserveAspectRatio: "xMidYMid meet" }, root);
  // the car, seen from above
  s("rect", { class: "ty-body", x: 66, y: 12, width: 68, height: 236, rx: 30 }, svg);
  s("path", { class: "ty-glass", d: "M78 70 Q100 56 122 70 L118 98 Q100 92 82 98 Z" }, svg);
  s("path", { class: "ty-glass", d: "M82 190 Q100 196 118 190 L122 214 Q100 224 78 214 Z" }, svg);

  const cards = CARDS.map(([x, y], i) => {
    const g = s("g", { class: "ty-card", "data-level": "missing", "data-flat": "false" }, svg);
    s("rect", { class: "ty-rect", x, y, width: CARD_W, height: CARD_H, rx: 10 }, g);
    s("text", { class: "ty-name", x: x + CARD_W / 2, y: y + 13 }, g, names[i]);
    const t = s("text", { class: "ty-temp", x: x + CARD_W / 2, y: y + 40 }, g, "--");
    s("text", { class: "ty-unit", x: x + CARD_W / 2, y: y + 49 }, g, temp.label);
    const p = s("text", { class: "ty-press", x: x + CARD_W / 2, y: y + 64 }, g, "");
    return { g, t, p, name: names[i] };
  });

  const set = (el, text) => {
    if (el.textContent !== text) el.textContent = text;
  };

  return {
    el: root,
    update(values) {
      for (const c of cards) {
        const raw = values[`tire.${c.name}.${source}`];
        const value = typeof raw === "number" ? temp.convert(raw) : null;
        const level = tyreLevel(value, thresholds);
        const flat = values[`tire.${c.name}.deflated`] === true;
        if (c.g.dataset.level !== level) c.g.dataset.level = level;
        const flatNow = String(flat);
        if (c.g.dataset.flat !== flatNow) c.g.dataset.flat = flatNow;
        set(c.t, value === null ? "--" : value.toFixed(0));
        const pa = values[`tire.${c.name}.pressure`];
        if (flat) {
          set(c.p, "FLAT");
          c.p.classList.add("low");
        } else if (def.showPressure === false || typeof pa !== "number") {
          set(c.p, "");
        } else {
          const shown = press.convert(pa);
          set(c.p, `${shown.toFixed(press.unit === "bar" ? 2 : 1)} ${press.label}`);
          c.p.classList.toggle("low", lowPressure !== undefined && shown < lowPressure);
        }
        if (!flat && def.showPressure === false) c.p.classList.remove("low");
      }
    },
  };
}

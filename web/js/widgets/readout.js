import { h } from "../dom.js";
import { createSkinnedReadout } from "../skins/widgets.js";
import { KIND_OPTIONS, resolveUnit } from "../units.js";

export const spec = {
  label: "Number readout",
  size: { w: 180, h: 90 },
  defaults: { kind: "none", decimals: 0 },
  props: {
    channel: { type: "channel", label: "Channel" },
    kind: { type: "select", label: "Unit type", options: KIND_OPTIONS },
    label: { type: "text", label: "Title", max: 24 },
    showUnit: { type: "text", label: "Unit text", max: 12 },
    decimals: { type: "number", label: "Decimals", min: 0, max: 4, int: true },
    color: { type: "color", label: "Value colour", cleanOnly: true },
    digits: { type: "number", label: "Digits", min: 3, max: 10, int: true, skinOnly: true },
  },
};

/** A title, a big number and a unit: odometer, trip, temperatures, anything numeric. */
export function create(def, ctx) {
  if (ctx.skin) return createSkinnedReadout(def, ctx);
  const { label: unitLabel, convert } = resolveUnit(def.kind, ctx.prefs);
  const root = h("div", "w w-readout");
  if (def.color) root.style.setProperty("--c", def.color);
  if (def.label) h("div", "r-label", root, def.label);
  const row = h("div", "r-row", root);
  const valueEl = h("span", "r-value", row, "--");
  h("span", "r-unit", row, def.showUnit ?? unitLabel);
  let last = "";
  return {
    el: root,
    update(values) {
      const raw = values[def.channel];
      const missing = typeof raw !== "number";
      root.classList.toggle("missing", missing);
      const text = missing ? "--" : convert(raw).toFixed(def.decimals ?? 0);
      if (text !== last) {
        valueEl.textContent = text;
        last = text;
      }
    },
  };
}

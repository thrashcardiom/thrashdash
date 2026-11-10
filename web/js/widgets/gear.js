import { formatGear } from "../channelValues.js";
import { h } from "../dom.js";
import { createSkinnedGear } from "../skins/widgets.js";

export { formatGear } from "../channelValues.js";

export const spec = {
  label: "Gear",
  size: { w: 300, h: 190 },
  defaults: { channel: "gear" },
  props: {
    channel: { type: "channel", label: "Channel" },
    label: { type: "text", label: "Caption", max: 12, cleanOnly: true },
    style: { type: "select", label: "Display", options: ["strip", "digit"], skinOnly: true },
    letters: { type: "textList", label: "Strip letters (comma separated)", maxItems: 10, max: 3, skinOnly: true },
  },
};

export function create(def, ctx) {
  if (ctx.skin) return createSkinnedGear(def, ctx);
  const root = h("div", "w w-gear");
  const valueEl = h("div", "gear-value", root, "-");
  if (def.label !== "") h("div", "gear-label", root, def.label ?? "GEAR");
  let last = "";
  return {
    el: root,
    update(values) {
      const text = formatGear(values[def.channel || "gear"], values.gearIndex);
      root.classList.toggle("missing", text === null);
      const shown = text ?? "-";
      if (shown !== last) {
        valueEl.textContent = shown;
        last = shown;
      }
    },
  };
}

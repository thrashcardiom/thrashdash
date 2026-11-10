import { isOn } from "../channelValues.js";
import { h, s } from "../dom.js";
import { ICON_NAMES } from "../skins/icons.js";
import { createSkinnedLight } from "../skins/widgets.js";

// Arrow shapes for turn signals (viewBox 0 0 24 24).
const ARROWS = {
  left: "M3 12 L12 3 L12 8 L21 8 L21 16 L12 16 L12 21 Z",
  right: "M21 12 L12 3 L12 8 L3 8 L3 16 L12 16 L12 21 Z",
};

export { isOn } from "../channelValues.js";

export const spec = {
  label: "Indicator light",
  size: { w: 48, h: 40 },
  defaults: {},
  props: {
    channel: { type: "channel", label: "Channel" },
    channels: { type: "channels", label: "Any of these channels" },
    text: { type: "text", label: "Text", max: 6 },
    arrow: { type: "select", label: "Arrow", options: ["", "left", "right"] },
    color: { type: "color", label: "Colour when on", cleanOnly: true },
    icon: { type: "select", label: "Symbol", options: ["", ...ICON_NAMES], skinOnly: true },
    lampColor: { type: "select", label: "Lamp colour", options: ["red", "amber", "green", "blue"], skinOnly: true },
  },
};

/**
 * Indicator light. Layout properties:
 *   channel, text (e.g. "ABS"), arrow ("left"|"right"), color (CSS colour or variable)
 *   channels: [..]  optional, lit if ANY of them is on (e.g. TCS or ESC)
 */
export function create(def, ctx) {
  if (ctx.skin) return createSkinnedLight(def, ctx);
  const root = h("div", "w w-light");
  if (def.color) root.style.setProperty("--c", def.color);
  if (def.arrow && ARROWS[def.arrow]) {
    const svg = s("svg", { viewBox: "0 0 24 24" }, root);
    s("path", { d: ARROWS[def.arrow] }, svg);
  } else {
    root.textContent = def.text || "";
  }
  const channels = def.channels || [def.channel];
  let last;
  return {
    el: root,
    update(values) {
      const present = channels.some((c) => c in values);
      root.classList.toggle("missing", !present);
      const on = channels.some((c) => isOn(values[c]));
      if (on !== last) {
        root.dataset.on = String(on);
        last = on;
      }
    },
  };
}

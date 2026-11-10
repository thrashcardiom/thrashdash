import { h } from "../dom.js";

export const spec = {
  label: "Text label",
  size: { w: 200, h: 40 },
  defaults: { text: "Label", fontSize: 22, align: "left" },
  props: {
    text: { type: "text", label: "Text", max: 60 },
    fontSize: { type: "number", label: "Size (px)", min: 8, max: 300, int: true },
    align: { type: "select", label: "Align", options: ["left", "center", "right"] },
    bold: { type: "bool", label: "Bold" },
    color: { type: "color", label: "Colour" },
  },
};

/** Static text, for headings and captions. Set via textContent, so layout text can never become markup. */
export function create(def) {
  const root = h("div", "w w-text", undefined, def.text ?? "");
  root.style.fontSize = `${def.fontSize ?? 22}px`;
  // The box is a flex container (to centre the text vertically), where text-align has no effect.
  root.style.justifyContent = { left: "flex-start", center: "center", right: "flex-end" }[def.align ?? "left"];
  if (def.bold) root.style.fontWeight = "700";
  if (def.color) root.style.color = def.color;
  return { el: root, update() {} };
}

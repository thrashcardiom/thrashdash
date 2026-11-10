// Telltale / gauge-face icons as simple stroke drawings on a 24x24 grid. Stroked with currentColor, so the
// caller colours (and glows) them with CSS `color`. Hand-drawn for this project: no third-party artwork.
import { s } from "../dom.js";

// Each icon: `paths` (SVG path data, stroked) and optional `text` ({value, x, y, size}) for lettered symbols.
export const ICONS = {
  fuel: { paths: ["M5 21V6a2 2 0 0 1 2-2h6a2 2 0 0 1 2 2v15", "M4 21h12", "M8 8h4v4H8z", "M15 9h2.2A1.8 1.8 0 0 1 19 10.8V16a1.5 1.5 0 0 0 3 0V8l-3-3"] },
  temp: { paths: ["M8.5 5a2 2 0 0 1 4 0v8a3.6 3.6 0 1 1-4 0z", "M10.5 9.5v6", "M12.5 6.5h2M12.5 9.5h2", "M3 20c1.2-1.2 2.4 1.2 3.6 0s2.4 1.2 3.6 0 2.4 1.2 3.6 0 2.4 1.2 3.6 0 2.4 1.2 3.6 0"] },
  battery: { paths: ["M3 8h18v11H3z", "M6 8V6h3v2M15 8V6h3v2", "M6 13.5h4M8 11.5v4", "M14 13.5h4"] },
  oil: { paths: ["M8 10h9v8a1 1 0 0 1-1 1H9a1 1 0 0 1-1-1z", "M17 12h2.2a1.6 1.6 0 0 1 0 3.2H17", "M8 11L4 8.5", "M2.6 12.2c0 1 1.1 1.5 1.1 2.4a1.1 1.1 0 0 1-2.2 0c0-.9 1.1-1.4 1.1-2.4z"] },
  engine: { paths: ["M9 8h6M12 8V6M10 6h4", "M5 10h11l2 2v5H8l-3-3z", "M3 12v3", "M20.5 13v3"] },
  highbeam: { paths: ["M13 5c-4 0-6 3-6 7s2 7 6 7z", "M16.5 7h5M16.5 10h5M16.5 13h5M16.5 16h5"] },
  lowbeam: { paths: ["M13 5c-4 0-6 3-6 7s2 7 6 7z", "M16.5 7.5l5 2M16.5 10.5l5 2M16.5 13.5l5 2M16.5 16.5l5 2"] },
  hazard: { paths: ["M12 4l9.5 16.5h-19z", "M12 9.5l4.7 8h-9.4z"] },
  esc: { paths: ["M6 15l1-3.5A2 2 0 0 1 9 10h6a2 2 0 0 1 2 1.5l1 3.5z", "M5 15h14v2H5z", "M3 21c1.5-1.5 3 1.5 4.5 0s3 1.5 4.5 0 3 1.5 4.5 0 3 1.5 4.5 0"] },
  parking: { paths: ["M4.5 7.5a7.5 7.5 0 0 0 0 9", "M19.5 7.5a7.5 7.5 0 0 1 0 9", "M9.2 17V8h3.2a2.6 2.6 0 0 1 0 5.2H9.2"] },
  brake: { paths: ["M4.5 7.5a7.5 7.5 0 0 0 0 9", "M19.5 7.5a7.5 7.5 0 0 1 0 9", "M12 7.5v5.5", "M12 16.2v.3"] },
  abs: { paths: ["M4.5 7.5a7.5 7.5 0 0 0 0 9", "M19.5 7.5a7.5 7.5 0 0 1 0 9"], text: { value: "ABS", x: 12, y: 15, size: 6.6 } },
  left: { paths: ["M3 12l8-8v5h10v6H11v5z"] },
  right: { paths: ["M21 12l-8-8v5H3v6h10v5z"] },
};

/** Draw icon `name` into `parent`, centred at (cx, cy) and `size` units wide. Returns the <g>, or null if unknown. */
export function drawIcon(parent, name, cx, cy, size, { strokeWidth = 1.7 } = {}) {
  const def = Object.hasOwn(ICONS, name) ? ICONS[name] : null;
  if (!def) return null;
  const k = size / 24;
  const g = s("g", { transform: `translate(${cx - size / 2} ${cy - size / 2}) scale(${k})`, fill: "none", stroke: "currentColor", "stroke-width": strokeWidth, "stroke-linecap": "round", "stroke-linejoin": "round" }, parent);
  for (const d of def.paths) s("path", { d }, g);
  if (def.text) s("text", { x: def.text.x, y: def.text.y, "font-size": def.text.size, "font-weight": 700, "text-anchor": "middle", fill: "currentColor", stroke: "none", style: "font-family: 'Barlow Condensed', sans-serif" }, g, def.text.value);
  return g;
}

export const ICON_NAMES = Object.keys(ICONS);

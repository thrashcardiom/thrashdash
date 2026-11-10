// Skin definitions: pure data describing a look. The builders (gauge.js, parts.js) turn them into SVG/DOM.
// A skin is chosen per dashboard; "clean" is the original flat look and has no entry here (it uses the old widgets).

const ZONES = { red: "#d92a2f", amber: "#f2a51f", blue: "#2f74da" };

export const SKINS = {
  classicWhite: {
    id: "classicWhite",
    name: "Classic - white",
    bezel: "chrome",
    face: "white",
    ink: "#15171b",
    inkSoft: "#2c2f35",
    numerals: { family: "'Jost', 'Segoe UI', sans-serif", weight: 500, size: 13.5, letterSpacing: 0 },
    title: { family: "'Jost', 'Segoe UI', sans-serif", weight: 500, size: 8.6, color: "#2c2f35", letterSpacing: 1.2 },
    needle: { style: "classic", color: "#15171b" },
    hub: { rim: "#c9cdd2", dome: ["#7c8087", "#17181b"] },
    zones: ZONES,
    glass: 0.55,
    sub: { kind: "odometer", digit: "#f4f5f6", cell: "#0c0c0d", lastCell: "#f4f5f6", lastDigit: "#d92a2f", family: "'Barlow Condensed', sans-serif" },
    shift: { on: "#35b6ff", off: "#c8ccd2" },
    lamp: { style: "jewel", on: { red: "#ff3b30", amber: "#ffb020", green: "#35d46b", blue: "#3aa3ff" } },
    glow: null,
  },
  classicBlack: {
    id: "classicBlack",
    name: "Classic - black",
    bezel: "chrome",
    face: "black",
    ink: "#f2f3f5",
    inkSoft: "#b9bdc4",
    numerals: { family: "'Barlow Condensed', 'Segoe UI', sans-serif", weight: 500, size: 15, letterSpacing: 0.3 },
    title: { family: "'Barlow Condensed', 'Segoe UI', sans-serif", weight: 500, size: 9, color: "#b9bdc4", letterSpacing: 1 },
    needle: { style: "slim", color: "#e3262b" },
    hub: { rim: "#9aa0a8", dome: ["#4a4e55", "#0b0b0c"] },
    zones: ZONES,
    glass: 0.45,
    sub: { kind: "odometer", digit: "#f4f5f6", cell: "#050506", lastCell: "#f4f5f6", lastDigit: "#d92a2f", family: "'Barlow Condensed', sans-serif" },
    shift: { on: "#ff4d3d", off: "#3a3d42" },
    lamp: { style: "jewel", on: { red: "#ff3b30", amber: "#ffb020", green: "#35d46b", blue: "#3aa3ff" } },
    glow: null,
  },
  backlit: {
    id: "backlit",
    name: "Backlit",
    bezel: "dark",
    face: "frost",
    ink: "#0f8cff",
    inkSoft: "#3aa2ff",
    numerals: { family: "'Barlow Condensed', 'Segoe UI', sans-serif", weight: 600, size: 15.5, letterSpacing: 0.4 },
    title: { family: "'Barlow Condensed', 'Segoe UI', sans-serif", weight: 600, size: 9, color: "#2a86e0", letterSpacing: 1.1 },
    needle: { style: "slim", color: "#ff2a2a", glow: "#ff3b3b" },
    hub: { rim: "#6f7a88", dome: ["#2a3039", "#05070a"] },
    zones: { red: "#ff3b3b", amber: "#ffb020", blue: "#2f9bff" },
    glass: 0.3,
    sub: { kind: "lcd", color: "#8fdcff", back: ["#04101c", "#0a2338"], family: "'DSEG7 Classic', monospace" },
    shift: { on: "#2f9bff", off: "#8da5c4" },
    lamp: { style: "glow", on: { red: "#ff4040", amber: "#ffb020", green: "#35ff8a", blue: "#4db2ff" } },
    glow: "#2f9bff",
  },
};

export const SKIN_IDS = Object.keys(SKINS);

/** Every skin a layout may name. "clean" is the original flat look (rendered by the plain widgets, not by skins/). */
export const SKIN_CHOICES = ["clean", ...SKIN_IDS];
export const SKIN_LABELS = { clean: "Clean (flat)", ...Object.fromEntries(Object.entries(SKINS).map(([id, skin]) => [id, skin.name])) };

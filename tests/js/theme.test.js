import assert from "node:assert/strict";
import test from "node:test";

import { THEME_KEYS, applyTheme, cleanTheme, normaliseColour, themeColour, themeVars } from "../../web/js/theme.js";
import { EditorState } from "../../web/js/editor/state.js";
import { sanitizeLayout } from "../../web/js/layoutSchema.js";
import { specs } from "../../web/js/widgets/index.js";

test("only plain hex colours are colours, and they come out in one form", () => {
  assert.equal(normaliseColour("#ABC"), "#aabbcc");
  assert.equal(normaliseColour(" #1A2B3C "), "#1a2b3c");
  for (const bad of ["red", "rgb(1,2,3)", "#12", "#12345", "#1234567", "url(x)", "#ggg", "", null, 5, undefined, {}, "#fff; background: url(//evil)"]) assert.equal(normaliseColour(bad), null, String(bad));
});

test("a theme keeps the colours that are colours and says what it dropped", () => {
  const { theme, warnings } = cleanTheme({ bg: "#000", panel: "red", nonsense: "#fff", accent: "#00FF00" });
  assert.deepEqual(theme, { bg: "#000000", accent: "#00ff00" });
  assert.equal(warnings.length, 2);
  assert.match(warnings.join("|"), /panel is not a colour/);
  assert.match(warnings.join("|"), /unknown colour "nonsense"/);
});

test("a theme with nothing usable is no theme, and no theme at all is fine", () => {
  assert.deepEqual(cleanTheme(undefined), { theme: null, warnings: [] });
  assert.equal(cleanTheme({}).theme, null);
  assert.equal(cleanTheme({ bg: "nope" }).theme, null);
  for (const bad of [null, "x", 5, [], ["#fff"]]) assert.equal(cleanTheme(bad).theme, null, JSON.stringify(bad));
});

test("a theme becomes CSS variables for the colours it sets, and the rest stay the stylesheet's", () => {
  assert.deepEqual(themeVars({ panel: "#fff", crit: "#f00" }), { "--panel": "#fff", "--crit": "#f00" });
  assert.deepEqual(themeVars(null), {});
  assert.equal(themeColour({ panel: "#fff" }, "panel"), "#fff");
  assert.equal(themeColour({ panel: "#fff" }, "text"), "#e8edf4");
  assert.equal(THEME_KEYS.length, 7);
});

const fakeEl = () => {
  const props = new Map();
  return { props, style: { setProperty: (k, v) => props.set(k, v), removeProperty: (k) => props.delete(k), set background(v) { props.set("background", v); } } };
};

test("applying a theme sets the canvas variables and the page background, and applying none takes them all away", () => {
  const canvas = fakeEl();
  const body = fakeEl();
  applyTheme(canvas, body, { bg: "#101010", panel: "#202020" });
  assert.equal(canvas.props.get("--panel"), "#202020");
  assert.equal(canvas.props.get("--bg"), "#101010");
  assert.equal(body.props.get("background"), "#101010");
  applyTheme(canvas, body, { accent: "#ff00ff" }); // a different theme: nothing of the last one is left
  assert.deepEqual([...canvas.props.keys()], ["--accent"]);
  assert.equal(body.props.has("background"), false);
  applyTheme(canvas, body, undefined);
  assert.equal(canvas.props.size, 0);
});

test("a layout file keeps its theme, cleaned, and none is added when it has none", () => {
  const raw = { format: 1, name: "t", width: 800, height: 300, widgets: [], theme: { bg: "#ABCDEF", panel: "red" } };
  const { layout, warnings } = sanitizeLayout(raw, specs);
  assert.deepEqual(layout.theme, { bg: "#abcdef" });
  assert.equal(warnings.length, 1);
  assert.equal("theme" in sanitizeLayout({ ...raw, theme: undefined }, specs).layout, false);
  assert.equal("theme" in sanitizeLayout({ ...raw, theme: { bg: "x" } }, specs).layout, false);
});

const layout = () => ({ format: 1, name: "T", width: 1000, height: 400, widgets: [] });

test("choosing a colour is an undoable edit of the dashboard, and many in a row are one step", () => {
  const s = new EditorState(layout(), specs, {});
  assert.deepEqual(s.setThemeColour("accent", "#FF0000"), { ok: true });
  s.setThemeColour("accent", "#00ff00");
  s.setThemeColour("accent", "#0000ff"); // (dragging in a picker: three changes)
  assert.deepEqual(s.layout.theme, { accent: "#0000ff" });
  assert.equal(s.dirty, true);
  s.undo();
  assert.equal(s.layout.theme, undefined);
  assert.equal(s.dirty, false);
  s.redo();
  assert.deepEqual(s.layout.theme, { accent: "#0000ff" });
});

test("a colour that is not one is refused, and taking the last colour away leaves no theme", () => {
  const s = new EditorState(layout(), specs, {});
  assert.equal(s.setThemeColour("bg", "blue").ok, false);
  assert.equal(s.layout.theme, undefined);
  s.setThemeColour("bg", "#111111");
  s.setThemeColour("panel", "#222222");
  s.setThemeColour("bg", undefined);
  assert.deepEqual(s.layout.theme, { panel: "#222222" });
  s.setThemeColour("panel", undefined);
  assert.equal("theme" in s.layout, false);
  s.setThemeColour("text", "#333333");
  s.clearTheme();
  assert.equal("theme" in s.layout, false);
  s.clearTheme(); // (nothing to clear: no extra undo step)
  assert.equal(s.undoStack.length, 6); // (each colour change is a step, the repeat of the clear is not)
});

test("setting a colour to the one it already is changes nothing", () => {
  const s = new EditorState(layout(), specs, {});
  s.setThemeColour("bg", "#111111");
  const steps = s.undoStack.length;
  s.setThemeColour("bg", "#111111");
  s.setThemeColour("crit", undefined);
  assert.equal(s.undoStack.length, steps);
});

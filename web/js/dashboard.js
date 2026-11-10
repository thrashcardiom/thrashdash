import { computeScale } from "./scaling.js";
import { SKINS } from "./skins/themes.js";
import { registry } from "./widgets/index.js";

/**
 * Build the widgets described by `layout` (drawn in `skin`) into `container` and return {update(values), elements}.
 * `elements` maps widget id -> DOM element so the editor can move a widget while dragging without a rebuild.
 * Unknown widget types are skipped with a console warning so one bad entry cannot blank the dashboard.
 */
export function buildDashboard(container, layout, prefs, skin = "clean", extras = {}) {
  container.replaceChildren();
  // `skin` names a skin from skins/themes.js; "clean" (or anything unknown) means the plain flat widgets. A skin only
  // changes how widgets LOOK: the layout (which widgets, where, on which channels) is untouched.
  // `extras.canWrite`: this device may change things (only the computer running ThrashDash), so widgets may offer a button that does.
  const ctx = { prefs, skin: Object.hasOwn(SKINS, skin) ? SKINS[skin] : null, canWrite: extras.canWrite === true };
  const widgets = [];
  const elements = new Map();
  for (const def of layout.widgets) {
    const impl = registry[def.type];
    if (!impl) {
      console.warn("ThrashDash: unknown widget type", def.type);
      continue;
    }
    const widget = impl.create(def, ctx);
    Object.assign(widget.el.style, { left: `${def.x}px`, top: `${def.y}px`, width: `${def.w}px`, height: `${def.h}px` });
    widget.el.dataset.id = def.id;
    container.appendChild(widget.el);
    widgets.push(widget);
    elements.set(def.id, widget.el);
  }
  return {
    elements,
    update(values) {
      for (const w of widgets) w.update(values);
    },
  };
}

/**
 * Size, scale and centre the fixed-size canvas inside the window, leaving `insetRight` pixels free
 * (the editor panel). `setting` ({mode, percent}) decides how big it gets; see scaling.js. While
 * editing, pass `{fitAll: true}` so the whole canvas is always visible regardless of the scale setting.
 * Also publishes --inv-scale (1/scale) so editor handles keep a constant on-screen size.
 * @returns {number} the scale applied
 */
export function fitCanvas(canvas, layout, { insetRight = 0, setting = layout, fitAll = false } = {}) {
  const availW = Math.max(100, window.innerWidth - insetRight);
  const scale = computeScale(fitAll ? { mode: "fit" } : setting, layout.width, layout.height, availW, window.innerHeight);
  const dx = (availW - layout.width * scale) / 2;
  const dy = (window.innerHeight - layout.height * scale) / 2;
  canvas.style.width = `${layout.width}px`;
  canvas.style.height = `${layout.height}px`;
  canvas.style.transform = `translate(${dx}px, ${dy}px) scale(${scale})`;
  canvas.style.setProperty("--inv-scale", String(1 / scale));
  return scale;
}

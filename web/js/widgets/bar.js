import { h } from "../dom.js";
import { createSkinnedBar } from "../skins/widgets.js";
import { KIND_OPTIONS, resolveUnit, pick } from "../units.js";

export const spec = {
  label: "Bar",
  size: { w: 340, h: 30 },
  defaults: { kind: "none", min: 0, max: 100, decimals: 0 },
  props: {
    channel: { type: "channel", label: "Channel" },
    kind: { type: "select", label: "Unit type", options: KIND_OPTIONS },
    label: { type: "text", label: "Title", max: 24 },
    decimals: { type: "number", label: "Decimals", min: 0, max: 4, int: true },
    min: { type: "unitNumber", label: "Scale minimum" },
    max: { type: "unitNumber", label: "Scale maximum" },
    warnFrom: { type: "unitNumber", label: "Amber at" },
    critFrom: { type: "unitNumber", label: "Red at" },
    invert: { type: "bool", label: "Low is bad (amber/red below)" },
    maxChannel: { type: "channel", label: "Scale max from channel" },
    maxRound: { type: "number", label: "Round scale up to", min: 1, max: 100000 },
    segments: { type: "number", label: "LED segments", min: 6, max: 60, int: true, skinOnly: true },
  },
};

/**
 * Horizontal bar. Layout properties:
 *   channel, kind, label, decimals, min, max (number or {unit: number})
 *   warnFrom, critFrom   value (in display units) at which the bar turns amber / red. When the scale follows a channel
 *                        (maxChannel, e.g. an rpm bar that tracks each car's redline) they are fractions 0-1 of the scale instead.
 *   maxChannel, maxRound take the scale top from a channel, rounded UP to maxRound
 *   invert               true for "low is bad" bars such as fuel: warn/crit trigger BELOW the value
 */
export function create(def, ctx) {
  if (ctx.skin) return createSkinnedBar(def, ctx);
  const { unit, label: unitLabel, convert } = resolveUnit(def.kind, ctx.prefs);
  const min = pick(def.min, unit, 0);
  let max = pick(def.max, unit, 100);
  const dynamic = Boolean(def.maxChannel);
  const warnRaw = pick(def.warnFrom, unit, undefined);
  const critRaw = pick(def.critFrom, unit, undefined);
  // With a scale that follows a channel, the thresholds are fractions of it; otherwise they are values.
  const limit = (v) => (v === undefined ? undefined : dynamic ? min + v * (max - min) : v);

  const root = h("div", "w w-bar");
  h("div", "b-label", root, def.label || "");
  const track = h("div", "b-track", root);
  const fill = h("div", "b-fill", track);
  const valueEl = h("div", "b-value", root, "--");

  const reached = (v, lim) => lim !== undefined && (def.invert ? v <= lim : v >= lim);

  return {
    el: root,
    update(values) {
      if (dynamic) {
        const top = values[def.maxChannel];
        if (typeof top === "number" && top > 0) max = Math.ceil(convert(top) / (def.maxRound ?? 1000)) * (def.maxRound ?? 1000);
      }
      const raw = values[def.channel];
      const missing = typeof raw !== "number";
      root.classList.toggle("missing", missing);
      if (missing) {
        valueEl.textContent = "--";
        return;
      }
      const v = convert(raw);
      const frac = Math.min(1, Math.max(0, (v - min) / (max - min)));
      fill.style.transform = `scaleX(${frac.toFixed(3)})`;
      root.dataset.level = reached(v, limit(critRaw)) ? "crit" : reached(v, limit(warnRaw)) ? "warn" : "ok";
      valueEl.textContent = `${v.toFixed(def.decimals ?? 0)} ${unitLabel}`.trim();
    },
  };
}

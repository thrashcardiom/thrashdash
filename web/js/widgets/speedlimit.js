import { h } from "../dom.js";
import { resolveUnit } from "../units.js";

// The speed limit of the road the car is on, drawn as a road sign, with the car's own speed beside it. The limit comes from
// BeamNG.Earth (see geo.limit in the channel picker). It is only sometimes read from a real sign: the rest is a guess from the
// kind of road, and the sign says so ("est.") rather than pass a guess off as a fact.
export const spec = {
  label: "Speed limit sign",
  size: { w: 200, h: 240 },
  defaults: { speedChannel: "wheelspeed", style: "round", showSpeed: true },
  props: {
    speedChannel: { type: "channel", label: "Your speed (channel)" },
    style: { type: "select", label: "Sign", options: ["round", "box"] },
    showSpeed: { type: "bool", label: "Show your speed under the sign" },
  },
};

const MISSING = "--";
/** A car this far (m/s) over a posted limit turns the sign red: the same one-metre allowance the trip log's speeding figures use. */
export const OVER_TOLERANCE = 1;

/**
 * What the sign shows, as plain data (so it can be tested without a browser).
 * @returns {{limit: string, known: boolean, posted: boolean, over: boolean, now: string}}
 */
export function limitView(values, speedChannel, prefs) {
  const unit = resolveUnit("speed", prefs ?? {});
  const limit = values["geo.limit"];
  const speed = values[speedChannel || "wheelspeed"];
  const known = typeof limit === "number" && Number.isFinite(limit);
  const posted = known && values["geo.limitPosted"] === true;
  const moving = typeof speed === "number" && Number.isFinite(speed);
  return {
    limit: known ? String(Math.round(unit.convert(limit))) : MISSING,
    known,
    posted,
    // Red only against a posted limit: an estimate must never scold.
    over: posted && moving && Math.abs(speed) > limit + OVER_TOLERANCE,
    now: moving ? `${Math.round(unit.convert(Math.abs(speed)))} ${unit.label}` : MISSING,
  };
}

export function create(def, ctx) {
  const root = h("div", "w w-limit");
  if (ctx.skin) root.classList.add("w-skinned", `sk-${ctx.skin.id}`);
  root.classList.add(def.style === "box" ? "lm-box" : "lm-round");
  const sign = h("div", "lm-sign", root);
  if (def.style === "box") h("div", "lm-top", sign, "SPEED LIMIT"); // (above the number, as on a North American sign)
  const num = h("div", "lm-num", sign, MISSING);
  const caption = h("div", "lm-cap", root, "");
  const now = def.showSpeed === false ? null : h("div", "lm-now", root, MISSING);
  const last = { limit: null, caption: null, now: null };

  return {
    el: root,
    update(values) {
      const v = limitView(values, def.speedChannel, ctx.prefs);
      root.classList.toggle("missing", !v.known);
      root.classList.toggle("estimated", v.known && !v.posted);
      root.classList.toggle("over", v.over);
      if (v.limit !== last.limit) num.textContent = last.limit = v.limit;
      const text = !v.known ? "no limit known" : v.posted ? "" : "est.";
      if (text !== last.caption) caption.textContent = last.caption = text;
      if (now && v.now !== last.now) now.textContent = last.now = v.now;
    },
  };
}

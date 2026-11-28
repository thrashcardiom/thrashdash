import { h } from "../dom.js";
import { resolveUnit } from "../units.js";

// Progress along the GPS route set in BeamNG.Earth: where it goes, when you arrive, how long and how far is left. Without a route
// it says so and dims, so a dashboard that has one still looks right on a drive with none.
export const spec = {
  label: "Route and ETA",
  size: { w: 300, h: 340 },
  defaults: { showName: true, showOff: true, showNext: true, showStop: true },
  props: {
    label: { type: "text", label: "Title", max: 20 },
    showNext: { type: "bool", label: "Show the next turn" },
    showStop: { type: "bool", label: "Show the next stop on the way" },
    showName: { type: "bool", label: "Show the destination" },
    showOff: { type: "bool", label: "Show a warning when off the route" },
  },
};

const MISSING = "--";
/** Further than this (m) from the route counts as off it: GPS and road width alone can put the car a few metres out. */
export const OFF_ROUTE_M = 50;

// ---- the next turn ------------------------------------------------------------------------------------------------------------------
// The relay works the next maneuver out from the route's steps and the car's progress (geo.route.next.*, geo.route.then.*). Steps are
// named the way a routing service names them: a `type` (depart, turn, fork, roundabout, exit roundabout, end of road, merge, arrive...) and,
// for most, a `modifier` (left, slight left, sharp right, straight, uturn). The wording is made here, so it can follow the person's units.

const ARROWS = { left: "\u21B0", "slight left": "\u2196", "sharp left": "\u21B0", right: "\u21B1", "slight right": "\u2197", "sharp right": "\u21B1", straight: "\u2191", uturn: "\u21B6" };

/** The arrow for a maneuver: a turn points the way it turns, a roundabout goes round, the end is a flag. */
export function maneuverArrow(type, modifier) {
  if (type === "arrive") return "\u2691";
  if (type === "roundabout" || type === "rotary" || type === "exit roundabout") return "\u27F3";
  return ARROWS[modifier] ?? "\u2191";
}

/** 1 -> "1st", 2 -> "2nd", 3 -> "3rd", 11 -> "11th". */
export function ordinal(n) {
  const mod100 = n % 100;
  const suffix = mod100 >= 11 && mod100 <= 13 ? "th" : { 1: "st", 2: "nd", 3: "rd" }[n % 10] ?? "th";
  return `${n}${suffix}`;
}

/** A distance in kilometres or miles with the places a person would say: 0.3, 1.2, 9.9, then whole numbers (never "10.0"). */
const bigDistance = (value) => {
  const tenths = Math.round(value * 10) / 10;
  return tenths < 10 ? tenths.toFixed(1) : String(Math.round(value));
};

/** How far off the next maneuver is, in the person's own distance unit: "300 m", "1.2 km", "250 ft", "0.4 mi", or "Now" when it is here. */
export function formatManeuverDistance(metres, prefs) {
  const m = num(metres);
  if (m === null) return "";
  if (m < 20) return "Now";
  if (resolveUnit("distance", prefs ?? {}).unit === "mi") {
    if (m < 160) return `${Math.round((m * 3.28084) / 50) * 50} ft`;
    return `${bigDistance(m / 1609.344)} mi`;
  }
  if (m < 950) return `${Math.round(m / 10) * 10} m`;
  return `${bigDistance(m / 1000)} km`;
}

const side = (modifier) => (modifier?.includes("left") ? "left" : modifier?.includes("right") ? "right" : "");
const onto = (street) => (street ? ` onto ${street}` : "");

/**
 * The words for a maneuver: "Turn left onto Oriental Parade", "At the roundabout take the 2nd exit onto Evans Bay Parade".
 * @param {{type?: string, modifier?: string, street?: string, exit?: number}} step
 * @param {{type?: string, street?: string} | null} after the step after it (a roundabout's exit is named by it)
 */
export function maneuverText(step, after = null) {
  const { type, modifier, street, exit } = step;
  switch (type) {
    case "turn": {
      if (modifier === "uturn") return "Make a U-turn";
      if (modifier === "straight") return street ? `Continue on ${street}` : "Continue straight";
      if (modifier?.startsWith("slight")) return `Bear ${side(modifier)}${onto(street)}`;
      if (modifier?.startsWith("sharp")) return `Turn sharp ${side(modifier)}${onto(street)}`;
      return `Turn ${side(modifier) || "ahead"}${onto(street)}`;
    }
    case "fork":
      return `Keep ${side(modifier) || "straight"} at the fork${onto(street)}`;
    case "roundabout":
    case "rotary": {
      const where = Number.isFinite(exit) ? `take the ${ordinal(exit)} exit` : "go round";
      const exitStreet = after?.type === "exit roundabout" ? after.street : "";
      return `At the roundabout ${where}${onto(exitStreet)}`;
    }
    case "exit roundabout":
      return `Leave the roundabout${onto(street)}`;
    case "end of road":
      return `At the end of the road turn ${side(modifier) || "ahead"}${onto(street)}`;
    case "merge":
      return `Merge${side(modifier) ? ` ${side(modifier)}` : ""}${onto(street)}`;
    case "on ramp":
      return `Take the ramp${side(modifier) ? ` on the ${side(modifier)}` : ""}${onto(street)}`;
    case "off ramp":
      return `Take the exit${side(modifier) ? ` on the ${side(modifier)}` : ""}${onto(street)}`;
    case "new name":
    case "continue":
      return street ? `Continue on ${street}` : "Continue";
    case "arrive":
      return side(modifier) ? `Arrive: your destination is on the ${side(modifier)}` : "Arrive at your destination";
    case "depart":
      return street ? `Head out on ${street}` : "Head out";
    default:
      return [type ? type[0].toUpperCase() + type.slice(1) : "Continue", modifier].filter(Boolean).join(" ") + onto(street);
  }
}

/** A step after the next one is worth a mention ("then turn right") only if it comes soon after it, in metres. */
export const THEN_WITHIN_M = 300;

/**
 * The next-turn block, as plain data: {arrow, distance, text, then}, or null when no maneuver is known (no route, or no steps for it).
 * `then` is "" unless the one after is close behind (and is not the exit of the roundabout the text already names).
 */
export function nextTurn(values, prefs) {
  const type = values["geo.route.next.type"];
  if (typeof type !== "string") return null;
  const read = (prefix) => ({ type: values[`${prefix}.type`], modifier: values[`${prefix}.modifier`], street: values[`${prefix}.street`], exit: values[`${prefix}.exit`] });
  const next = read("geo.route.next");
  const then = typeof values["geo.route.then.type"] === "string" ? read("geo.route.then") : null;
  const gap = num(values["geo.route.then.gap"]);
  const mergedExit = (next.type === "roundabout" || next.type === "rotary") && then?.type === "exit roundabout";
  let after = "";
  if (then && !mergedExit && gap !== null && gap <= THEN_WITHIN_M) {
    const words = maneuverText(then);
    after = `then ${words[0].toLowerCase()}${words.slice(1)}`;
  }
  return { arrow: maneuverArrow(next.type, next.modifier), distance: formatManeuverDistance(values["geo.route.next.distance"], prefs), text: maneuverText(next, then), then: after };
}

const num = (v) => (typeof v === "number" && Number.isFinite(v) ? v : null);

/** 5400 -> "1 h 30 min", 540 -> "9 min", 40 -> "<1 min"; "--" when unknown. Hours and minutes: the seconds of a route are noise. */
export function formatTimeLeft(seconds) {
  const s = num(seconds);
  if (s === null || s < 0) return MISSING;
  if (s < 60) return "<1 min";
  const minutes = Math.round(s / 60);
  const hours = Math.floor(minutes / 60);
  return hours ? `${hours} h ${String(minutes % 60).padStart(2, "0")} min` : `${minutes} min`;
}

/** The clock time of arrival, `eta` seconds after `nowMs` (a number of milliseconds since 1970), in the viewer's own clock format. */
export function formatArrival(eta, nowMs) {
  const s = num(eta);
  if (s === null || s < 0) return MISSING;
  return new Date(nowMs + s * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

/**
 * The next stop before the destination, as plain data: {name, text} ("3.2 km \u00b7 14:05", how far and when), or null with no stop on the way.
 * An unnamed stop is called by its number on the route.
 */
export function nextStop(values, prefs, nowMs) {
  const metres = num(values["geo.route.stop.distance"]);
  if (metres === null) return null;
  const name = values["geo.route.stop.name"];
  const number = num(values["geo.route.stop.number"]);
  const unit = resolveUnit("distance", prefs ?? {});
  const far = unit.convert(metres);
  const arrival = formatArrival(values["geo.route.stop.eta"], nowMs);
  const text = [`${far < 10 ? far.toFixed(1) : Math.round(far)} ${unit.label}`, arrival === MISSING ? "" : arrival].filter(Boolean).join(" \u00b7 ");
  return { name: typeof name === "string" && name ? name : number === null ? "Stop" : `Stop ${number}`, text };
}

/** What the widget shows, as plain data (so it can be tested without a browser). */
export function routeView(values, prefs, nowMs) {
  const distance = resolveUnit("distance", prefs ?? {});
  const active = values["geo.route.active"] === true;
  const left = num(values["geo.route.remaining"]);
  const off = num(values["geo.route.off"]);
  const name = values["geo.route.name"];
  return {
    active,
    name: active && typeof name === "string" ? name : "",
    arrival: active ? formatArrival(values["geo.route.eta"], nowMs) : MISSING,
    timeLeft: active ? formatTimeLeft(values["geo.route.eta"]) : MISSING,
    distanceLeft: active && left !== null ? `${distance.convert(left) < 10 ? distance.convert(left).toFixed(1) : Math.round(distance.convert(left))} ${distance.label}` : MISSING,
    offRoute: active && off !== null && off > OFF_ROUTE_M,
    planning: !active || values["geo.route.planning"] !== true ? "" : values["geo.route.replanning"] === true ? "Re-routing\u2026" : "Planning route\u2026",
    next: active ? nextTurn(values, prefs) : null,
    stop: active ? nextStop(values, prefs, nowMs) : null,
  };
}

export function create(def, ctx) {
  const root = h("div", "w w-route");
  if (ctx.skin) root.classList.add("w-skinned", `sk-${ctx.skin.id}`);
  h("div", "rt-title", root, def.label || "Route");
  const dest = def.showName === false ? null : h("div", "rt-dest", root, "");
  let nextBox = null;
  let arrowEl, distEl, textEl, thenEl;
  if (def.showNext !== false) {
    nextBox = h("div", "rt-next", root);
    nextBox.hidden = true;
    arrowEl = h("span", "rt-arrow", nextBox, "");
    const words = h("div", "rt-words", nextBox);
    distEl = h("div", "rt-ndist", words, "");
    textEl = h("div", "rt-ntext", words, "");
    thenEl = h("div", "rt-then", words, "");
  }
  let stopBox = null;
  let stopName, stopText;
  if (def.showStop !== false) {
    stopBox = h("div", "rt-stop", root);
    stopBox.hidden = true;
    const head = h("div", "rt-stop-head", stopBox);
    h("span", "rt-label", head, "Next stop");
    stopText = h("span", "rt-stop-text", head, "");
    stopName = h("div", "rt-stop-name", stopBox, "");
  }
  const rows = {};
  for (const [key, label] of [["arrival", "Arrive"], ["timeLeft", "Time left"], ["distanceLeft", "Distance left"]]) {
    const row = h("div", "rt-row", root);
    h("span", "rt-label", row, label);
    rows[key] = h("span", "rt-value", row, MISSING);
  }
  const planEl = h("div", "rt-plan", root, "");
  planEl.hidden = true;
  const warn = def.showOff === false ? null : h("div", "rt-off", root, "Off the route");
  const last = {};
  const set = (key, el, text) => {
    if (last[key] !== text) el.textContent = last[key] = text;
  };

  return {
    el: root,
    update(values) {
      const v = routeView(values, ctx.prefs, Date.now());
      root.classList.toggle("missing", !v.active);
      if (dest) set("dest", dest, v.active ? v.name || "Route set" : "No route set");
      for (const key of Object.keys(rows)) set(key, rows[key], v[key]);
      if (nextBox) {
        nextBox.hidden = !v.next;
        if (v.next) {
          set("arrow", arrowEl, v.next.arrow);
          set("ndist", distEl, v.next.distance);
          set("ntext", textEl, v.next.text);
          set("then", thenEl, v.next.then);
          thenEl.hidden = !v.next.then;
        }
      }
      if (stopBox) {
        stopBox.hidden = !v.stop;
        if (v.stop) {
          set("stopname", stopName, v.stop.name);
          set("stoptext", stopText, v.stop.text);
        }
      }
      set("plan", planEl, v.planning);
      planEl.hidden = !v.planning;
      if (warn) warn.hidden = !v.offRoute || Boolean(v.planning); // (one or the other: a new route being made says it all)
    },
  };
}

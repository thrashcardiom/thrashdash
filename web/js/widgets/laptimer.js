import { h } from "../dom.js";

// Lap and sector timing: this lap's clock, the gap to your best lap, the last and best laps, and each sector. The laps are timed by the
// relay from the car's position across lines drawn on the Trips page (Circuits), so this only shows them. Times are shown to a tenth of a
// second: the position the game gives is good to a few hundredths, and a figure with more places would claim more than it knows.
export const spec = {
  label: "Lap timer",
  size: { w: 320, h: 320 },
  defaults: { showDelta: true, showSectors: true },
  props: {
    label: { type: "text", label: "Title", max: 20 },
    showDelta: { type: "bool", label: "Show the gap to your best lap" },
    showSectors: { type: "bool", label: "Show each sector" },
  },
};

const MISSING = "--";
const num = (v) => (typeof v === "number" && Number.isFinite(v) ? v : null);

/** 83.46 -> "1:23.5", 9.04 -> "0:09.0", 3725 -> "62:05.0" (minutes are not wrapped into hours: no lap is that long). "--" for no value. */
export function formatLapTime(seconds) {
  const s = num(seconds);
  if (s === null || s < 0) return MISSING;
  const tenths = Math.round(s * 10);
  const minutes = Math.floor(tenths / 600);
  const rest = (tenths - minutes * 600) / 10;
  return `${minutes}:${rest < 10 ? "0" : ""}${rest.toFixed(1)}`;
}

/** +0.43 -> "+0.4", -1.25 -> "-1.3", 0.02 -> "0.0" (nothing to be ahead or behind by); "--" for no value. */
export function formatDelta(seconds) {
  const s = num(seconds);
  if (s === null) return MISSING;
  const tenths = Math.round(Math.abs(s) * 10); // (of the size: -1.25 and +1.25 both round up, which Math.round alone does not do for the negative one)
  return tenths === 0 ? "0.0" : `${s > 0 ? "+" : "-"}${(tenths / 10).toFixed(1)}`;
}

/** What the widget shows, as plain data (so it can be tested without a browser). */
export function lapView(values) {
  const circuit = typeof values["lap.circuit"] === "string" ? values["lap.circuit"] : null;
  const active = values["lap.active"] === true;
  const delta = num(values["lap.delta"]);
  const lastDelta = num(values["lap.lastDelta"]);
  const count = Math.min(9, Math.max(0, num(values["lap.sectors"]) ?? 0));
  const current = num(values["lap.sector"]);
  const sectors = [];
  for (let n = 1; n <= count; n++) {
    sectors.push({
      n,
      last: formatLapTime(values[`lap.s${n}.last`]),
      best: formatLapTime(values[`lap.s${n}.best`]),
      isBest: values[`lap.s${n}.isBest`] === true,
      current: active && current === n,
    });
  }
  return {
    timing: circuit !== null,
    active,
    circuit: circuit ?? "",
    lap: active && num(values["lap.number"]) !== null ? `Lap ${values["lap.number"]}` : "",
    time: active ? formatLapTime(values["lap.time"]) : MISSING,
    delta: active && delta !== null ? formatDelta(delta) : "",
    deltaSign: delta === null || Math.round(Math.abs(delta) * 10) === 0 ? 0 : Math.sign(delta),
    last: formatLapTime(values["lap.last"]),
    lastNote: lastDelta === null ? "" : formatDelta(lastDelta),
    lastValid: values["lap.lastValid"] !== false,
    lastIsBest: values["lap.lastIsBest"] === true,
    best: formatLapTime(values["lap.best"]),
    sectors,
  };
}

export function create(def, ctx) {
  const root = h("div", "w w-lap");
  if (ctx.skin) root.classList.add("w-skinned", `sk-${ctx.skin.id}`);
  const head = h("div", "lp-head", root);
  const title = h("span", "lp-title", head, def.label || "Lap timer");
  const lapNo = h("span", "lp-lap", head, "");
  const time = h("div", "lp-time", root, MISSING);
  const delta = def.showDelta === false ? null : h("div", "lp-delta", root, "");
  const rows = h("div", "lp-rows", root);
  const rowEls = {};
  for (const [key, label] of [["last", "Last"], ["best", "Best"]]) {
    const row = h("div", "lp-row", rows);
    h("span", "lp-label", row, label);
    rowEls[key] = { value: h("span", "lp-value", row, MISSING), note: key === "last" ? h("span", "lp-note", row, "") : null };
  }
  const sectorBox = def.showSectors === false ? null : h("div", "lp-sectors", root);
  const last = {};
  const set = (key, el, text) => {
    if (last[key] !== text) el.textContent = last[key] = text;
  };
  let sectorKey = "";

  return {
    el: root,
    update(values) {
      const v = lapView(values);
      root.classList.toggle("missing", !v.timing);
      root.classList.toggle("running", v.active);
      set("title", title, v.timing ? v.circuit : def.label || "Lap timer");
      set("lap", lapNo, v.lap);
      set("time", time, v.timing ? v.time : "Not timing");
      if (delta) {
        set("delta", delta, v.delta);
        delta.classList.toggle("ahead", v.deltaSign < 0);
        delta.classList.toggle("behind", v.deltaSign > 0);
      }
      set("last", rowEls.last.value, v.last);
      rowEls.last.value.classList.toggle("best", v.lastIsBest);
      rowEls.last.value.classList.toggle("invalid", !v.lastValid);
      set("lastNote", rowEls.last.note, v.lastValid ? v.lastNote : "not valid");
      set("best", rowEls.best.value, v.best);
      if (sectorBox) {
        const key = v.sectors.map((s) => `${s.n}|${s.last}|${s.best}|${s.isBest}|${s.current}`).join(";");
        if (key !== sectorKey) {
          sectorKey = key;
          sectorBox.replaceChildren();
          for (const s of v.sectors) {
            const cell = h("div", `lp-sec${s.current ? " now" : ""}${s.isBest ? " best" : ""}`, sectorBox);
            h("span", "lp-sec-n", cell, `S${s.n}`);
            h("span", "lp-sec-t", cell, s.last);
          }
        }
      }
    },
  };
}

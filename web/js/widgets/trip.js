import { api } from "../api.js";
import { h } from "../dom.js";
import { formatConsumption, formatDuration, formatEnergy, resolveEconomy } from "../economy.js";
import { resolveUnit } from "../units.js";

// A trip meter: distance, time, average and top speed, fuel (or battery) used, economy and range. The numbers are kept
// by the relay (so every window and phone agrees, and they survive restarts); this only shows them.
export const spec = {
  label: "Trip computer",
  size: { w: 300, h: 340 },
  defaults: { trip: "A", showFuel: true, showRange: true },
  props: {
    trip: { type: "select", label: "Which trip", options: ["A", "B"] },
    label: { type: "text", label: "Title", max: 20 },
    showFuel: { type: "bool", label: "Show fuel / energy used and economy" },
    showRange: { type: "bool", label: "Show range left" },
  },
};

const MISSING = "--";
const CONFIRM_MS = 3000;

/** A number with a sensible number of decimals for its size: 0.42, 4.2, 42, 420. */
export function sizeFormat(value) {
  if (typeof value !== "number" || !Number.isFinite(value)) return MISSING;
  const a = Math.abs(value);
  return a < 10 ? value.toFixed(2) : a < 100 ? value.toFixed(1) : String(Math.round(value));
}

const withUnit = (text, unit) => (text === MISSING || !unit ? text : `${text} ${unit}`);

export function create(def, ctx) {
  const name = def.trip === "B" ? "B" : "A";
  const root = h("div", "w w-trip");
  if (ctx.skin) root.classList.add("w-skinned", `sk-${ctx.skin.id}`);
  const head = h("div", "tr-head", root);
  h("div", "tr-title", head, def.label || `Trip ${name}`);

  let resetBtn = null;
  if (ctx.canWrite) {
    // Only the computer running ThrashDash may reset (a phone just looks). Two presses, so a stray tap cannot wipe a trip.
    resetBtn = h("button", "tr-reset", head, "Reset");
    resetBtn.type = "button";
    let armed = null;
    const disarm = () => {
      clearTimeout(armed);
      armed = null;
      resetBtn.textContent = "Reset";
      resetBtn.classList.remove("armed");
    };
    resetBtn.addEventListener("click", async () => {
      if (armed === null) {
        resetBtn.textContent = "Sure?";
        resetBtn.classList.add("armed");
        armed = setTimeout(disarm, CONFIRM_MS);
        return;
      }
      disarm();
      try {
        await api.resetTrip(name);
      } catch (e) {
        console.warn("ThrashDash: could not reset the trip", e);
        resetBtn.textContent = "Failed";
        setTimeout(() => (resetBtn.textContent = "Reset"), 1500);
      }
    });
  }

  const rows = {};
  const addRow = (key, label) => {
    const row = h("div", "tr-row", root);
    rows[key] = { row, label: h("span", "tr-label", row, label), value: h("span", "tr-value", row, MISSING), last: MISSING };
  };
  addRow("distance", "Distance");
  addRow("time", "Time");
  addRow("avg", "Average speed");
  addRow("top", "Top speed");
  if (def.showFuel !== false) {
    addRow("used", "Fuel used");
    addRow("economy", "Economy");
  }
  if (def.showRange !== false) addRow("range", "Range left");

  const speed = resolveUnit("speed", ctx.prefs);
  const distance = resolveUnit("distance", ctx.prefs);
  const economy = resolveEconomy(ctx.prefs);
  const set = (key, text) => {
    const r = rows[key];
    if (r && r.last !== text) {
      r.last = text;
      r.value.textContent = text;
    }
  };
  const num = (v) => (typeof v === "number" && Number.isFinite(v) ? v : null);

  return {
    el: root,
    update(values) {
      const get = (field) => num(values[`trip.${name}.${field}`]);
      const kind = typeof values["trip.energyKind"] === "string" ? values["trip.energyKind"] : "unknown";
      root.classList.toggle("missing", get("distance") === null);

      const d = get("distance");
      set("distance", d === null ? MISSING : withUnit(sizeFormat(distance.convert(d)), distance.label));
      set("time", formatDuration(get("seconds")));
      const avg = get("avgSpeed");
      set("avg", avg === null ? MISSING : withUnit(String(Math.round(speed.convert(avg))), speed.label));
      const top = get("maxSpeed");
      set("top", top === null ? MISSING : withUnit(String(Math.round(speed.convert(top))), speed.label));

      if (rows.used) {
        const title = kind === "electric" ? "Energy used" : kind === "mixed" ? "Tank / battery used" : "Fuel used";
        if (rows.used.label.textContent !== title) rows.used.label.textContent = title;
        const used = formatEnergy(get("energyUsed"), kind, economy);
        const pct = get("percentUsed");
        const pctText = pct === null ? null : `${pct < 10 ? pct.toFixed(1) : Math.round(pct)}%`;
        if (kind === "liquid" || kind === "electric") {
          set("used", used ? `${withUnit(sizeFormat(used.value), used.unit)}${pctText ? ` · ${pctText}` : ""}` : MISSING);
        } else {
          set("used", pctText ?? MISSING); // a hybrid (or an unknown car): only the share is known
        }
        const eco = formatConsumption(get("consumption"), kind, economy);
        set("economy", eco ? withUnit(sizeFormat(eco.value), eco.unit) : MISSING);
      }
      if (rows.range) {
        const range = get("range");
        set("range", range === null ? MISSING : withUnit(String(Math.round(distance.convert(range))), distance.label));
      }
    },
  };
}

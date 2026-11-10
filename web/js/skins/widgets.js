// Skinned implementations of the widgets. Each widget file (widgets/*.js) calls into here when the layout
// has a skin; otherwise it draws its plain "clean" look. Same contract as the plain widgets: {el, update(values)}.
import { formatGear, isOn } from "../channelValues.js";
import { h } from "../dom.js";
import { pick, resolveUnit } from "../units.js";
import { buildGauge } from "./gauge.js";
import { buildGearDigit, buildGearStrip, buildLamp, buildLcdReadout, buildLedBar } from "./parts.js";

const isNum = (v) => typeof v === "number" && Number.isFinite(v);

export function createSkinnedGauge(def, ctx) {
  const skin = ctx.skin;
  const { unit, label: unitLabel, convert } = resolveUnit(def.kind, ctx.prefs);
  const min = pick(def.min, unit, 0);
  let max = pick(def.max, unit, 100);
  const sub = def.sub ? resolveUnit(def.subKind ?? "distance", ctx.prefs) : null;
  const root = h("div", "w w-skgauge");
  let gauge = null;
  let lastSub = "";

  function build() {
    let step = pick(def.majorStep, unit, (max - min) / 10);
    step *= Math.max(1, Math.ceil((max - min) / step / 10)); // never more than ~10 labels, whatever the scale
    const unitText = def.showUnit ?? unitLabel;
    gauge = buildGauge(
      {
        min, max, majorStep: step, minorDiv: def.minorDiv ?? 2, labelDiv: def.labelDiv ?? 1,
        labels: def.labels?.length ? def.labels : undefined,
        redFrom: def.redFrom, amberFrom: def.amberFrom, coldTo: def.coldTo, lowTo: def.lowTo,
        startAngle: def.startAngle, endAngle: def.endAngle,
        compact: def.compact, icon: def.icon || undefined,
        // Small dials have no room for a separate unit line, so the unit joins the title.
        title: def.compact ? [def.label, unitText].filter(Boolean).join(" ") : def.label,
        unit: def.compact ? undefined : unitText || undefined,
        sub: def.sub || undefined, subLabel: def.subLabel, shiftLight: def.shiftFrom !== undefined,
        // A skin changes how the dashboard LOOKS, not what it tells you: dials that had a printed value keep it.
        showValue: def.showValue ?? (!def.sub && !def.compact),
      },
      skin,
    );
    root.replaceChildren(gauge.el);
    lastSub = "";
  }
  build();

  return {
    el: root,
    update(values) {
      if (def.maxChannel) {
        const top = values[def.maxChannel];
        if (isNum(top) && top > 0) {
          const wanted = Math.ceil(top / (def.maxRound ?? 1000)) * (def.maxRound ?? 1000);
          if (wanted !== max) {
            max = wanted;
            build();
          }
        }
      }
      const raw = values[def.channel];
      const frac = isNum(raw) ? (convert(raw) - min) / (max - min) : null;
      gauge.setFraction(frac);
      gauge.setValue(isNum(raw) ? convert(raw).toFixed(def.decimals ?? 0) : "--");
      if (def.shiftFrom !== undefined) gauge.setShift(frac !== null && frac >= def.shiftFrom);
      if (sub && def.subChannel) {
        const sv = values[def.subChannel];
        if (isNum(sv)) {
          const x = sub.convert(sv);
          const text = def.sub === "odometer" ? String(Math.max(0, Math.floor(x * 10))) : x.toFixed(1); // odometer: last cell = tenths
          if (text !== lastSub) {
            gauge.setSub(text);
            lastSub = text;
          }
        }
      }
    },
  };
}

export function createSkinnedBar(def, ctx) {
  const { unit, label: unitLabel, convert } = resolveUnit(def.kind, ctx.prefs);
  const min = pick(def.min, unit, 0);
  let max = pick(def.max, unit, 100);
  const dynamic = Boolean(def.maxChannel); // the scale follows a channel: thresholds are then fractions of it already
  const frac = (v) => (v - min) / (max - min);
  const warn = pick(def.warnFrom, unit, undefined);
  const crit = pick(def.critFrom, unit, undefined);
  const invert = Boolean(def.invert);
  // The LED bar measures its thresholds from the bottom (normal) or, for "low is bad" bars, from the top.
  const toFrom = (v) => (v === undefined ? 2 : invert ? 1 - (dynamic ? v : frac(v)) : dynamic ? v : frac(v));

  const root = h("div", "w w-bar w-skbar");
  h("div", "b-label", root, def.label || "");
  const track = h("div", "b-track-sk", root);
  const bar = buildLedBar(ctx.skin, { segments: def.segments ?? 20, amberFrom: toFrom(warn), redFrom: toFrom(crit), invert });
  track.appendChild(bar.el);
  const valueEl = h("div", "b-value", root, "--");
  return {
    el: root,
    update(values) {
      if (dynamic) {
        const top = values[def.maxChannel];
        if (isNum(top) && top > 0) max = Math.ceil(convert(top) / (def.maxRound ?? 1000)) * (def.maxRound ?? 1000);
      }
      const raw = values[def.channel];
      const missing = !isNum(raw);
      root.classList.toggle("missing", missing);
      if (missing) {
        bar.set(null);
        valueEl.textContent = "--";
        return;
      }
      const v = convert(raw);
      bar.set(frac(v));
      valueEl.textContent = `${v.toFixed(def.decimals ?? 0)} ${unitLabel}`.trim();
    },
  };
}

const DEFAULT_LETTERS = ["P", "R", "N", "D", "1", "2", "3", "4", "5", "6"];

export function createSkinnedGear(def, ctx) {
  const root = h("div", "w w-sk w-skgear");
  const letters = def.letters?.length ? def.letters : DEFAULT_LETTERS;
  const part = def.style === "digit" ? buildGearDigit(ctx.skin) : buildGearStrip(ctx.skin, { letters });
  root.style.setProperty("--n", String(letters.length));
  root.appendChild(part.el);
  return {
    el: root,
    update(values) {
      part.set(formatGear(values[def.channel || "gear"], values.gearIndex));
    },
  };
}

export function createSkinnedLight(def, ctx) {
  const root = h("div", "w w-sk w-sklamp");
  const icon = def.icon || (def.arrow === "left" ? "left" : def.arrow === "right" ? "right" : undefined);
  const lamp = buildLamp(ctx.skin, { icon, color: def.lampColor ?? "amber", text: icon ? undefined : def.text });
  root.appendChild(lamp.el);
  const channels = def.channels || [def.channel];
  let last;
  lamp.set(false);
  return {
    el: root,
    update(values) {
      root.classList.toggle("missing", !channels.some((c) => c in values));
      const on = channels.some((c) => isOn(values[c]));
      if (on !== last) {
        lamp.set(on);
        last = on;
      }
    },
  };
}

export function createSkinnedReadout(def, ctx) {
  const { label: unitLabel, convert } = resolveUnit(def.kind, ctx.prefs);
  const root = h("div", "w w-sk w-skreadout");
  const lcd = buildLcdReadout(ctx.skin, { label: def.label || "", unit: def.showUnit ?? unitLabel, digits: def.digits ?? 6 });
  root.appendChild(lcd.el);
  lcd.set("");
  let last = null;
  return {
    el: root,
    update(values) {
      const raw = values[def.channel];
      const missing = !isNum(raw);
      root.classList.toggle("missing", missing);
      const text = missing ? "" : convert(raw).toFixed(def.decimals ?? 0);
      if (text !== last) {
        lcd.set(text);
        last = text;
      }
    },
  };
}

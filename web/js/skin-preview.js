// Developer preview of the skins: every widget in every skin, driven by simulated data.
import { buildGauge } from "./skins/gauge.js";
import { buildGearDigit, buildGearStrip, buildLamp, buildLcdReadout, buildLedBar } from "./skins/parts.js";
import { SKINS } from "./skins/themes.js";
import { h } from "./dom.js";

const GAUGES = {
  speed: { min: 0, max: 140, majorStep: 20, minorDiv: 2, title: "SPEED", unit: "mph", redFrom: 0.9, sub: "odometer" },
  rpm: { min: 0, max: 8000, majorStep: 1000, minorDiv: 2, labelDiv: 1000, title: "RPM", unit: "x1000", redFrom: 0.82, shiftLight: true },
  temp: { min: 140, max: 260, majorStep: 60, minorDiv: 3, startAngle: -105, endAngle: 105, title: "TEMP °F", compact: true, icon: "temp", coldTo: 0.12, redFrom: 0.85 },
  oil: { min: 0, max: 100, majorStep: 50, minorDiv: 5, startAngle: -105, endAngle: 105, title: "OIL PSI", compact: true, icon: "oil", lowTo: 0.1 },
  fuel: { min: 0, max: 100, majorStep: 50, minorDiv: 4, startAngle: -62, endAngle: 62, labels: ["E", "½", "F"], title: "FUEL", compact: true, icon: "fuel", lowTo: 0.12 },
  volts: { min: 8, max: 16, majorStep: 4, minorDiv: 4, startAngle: -105, endAngle: 105, title: "VOLTS", compact: true, icon: "battery", lowTo: 0.15, redFrom: 0.92 },
};

const LAMPS = [["left", "green"], ["right", "green"], ["highbeam", "blue"], ["lowbeam", "green"], ["engine", "amber"], ["oil", "red"], ["battery", "red"], ["abs", "amber"], ["parking", "red"], ["brake", "red"], ["hazard", "red"], ["esc", "amber"]];

function skinPanel(skin) {
  const section = h("section", "pv-skin", document.getElementById("pv-root"));
  h("h2", "", section, skin.name);
  const panel = h("div", "pv-panel", section);
  const g = {};
  const place = (key, cls, extra = {}) => {
    const cell = h("div", cls, panel);
    g[key] = buildGauge({ ...GAUGES[key], sub: skin.sub.kind === "lcd" && GAUGES[key].sub ? "lcd" : GAUGES[key].sub, subLabel: "TRIP", ...extra }, skin);
    cell.appendChild(g[key].el);
  };
  place("speed", "pv-big");
  place("rpm", "pv-big");
  place("temp", "pv-small");
  place("oil", "pv-small");
  place("fuel", "pv-small");
  place("volts", "pv-small");

  const row = h("div", "pv-row", panel);
  const strip = buildGearStrip(skin);
  const digit = buildGearDigit(skin);
  const lcd = buildLcdReadout(skin, { label: "ODO", unit: "km", digits: 6 });
  row.append(strip.el, digit.el, lcd.el);
  const bar = buildLedBar(skin, { segments: 20, amberFrom: 0.7, redFrom: 0.9 });
  const barWrap = h("div", "pv-bar", row);
  barWrap.appendChild(bar.el);
  const lamps = h("div", "pv-lamps", row);
  const lampObjs = LAMPS.map(([icon, color]) => {
    const wrap = h("div", "pv-lamp", lamps);
    const l = buildLamp(skin, { icon, color });
    wrap.appendChild(l.el);
    return l;
  });
  h("div", "pv-note", row, skin.id === "backlit" ? "ice-blue backlighting, red needles" : skin.id === "classicBlack" ? "black faces, red needles" : "white faces, black needles");

  return function update(t) {
    const speed = 70 + 66 * Math.sin(t / 3.1);
    const rpm = 3600 + 3300 * Math.sin(t / 3.1 + 0.4);
    g.speed.setFraction(Math.max(0, speed) / 140);
    g.speed.setSub(skin.sub.kind === "lcd" ? (1234.5 + t / 20).toFixed(1) : Math.floor(2358 + t));
    g.rpm.setFraction(Math.max(0, rpm) / 8000);
    g.rpm.setShift(rpm > 6800);
    g.temp.setFraction(0.55 + 0.2 * Math.sin(t / 7));
    g.oil.setFraction(0.5 + 0.35 * Math.sin(t / 4 + 1));
    g.fuel.setFraction(0.5 + 0.45 * Math.sin(t / 12));
    g.volts.setFraction(0.62 + 0.12 * Math.sin(t / 5 + 2));
    const gear = speed < 5 ? "N" : speed < 30 ? "1" : speed < 55 ? "2" : "D";
    strip.set(gear === "1" || gear === "2" ? gear : gear);
    digit.set(gear);
    lcd.set(String(Math.floor(48213 + t)).padStart(6, "0"));
    bar.set(0.5 + 0.5 * Math.sin(t / 2));
    lampObjs.forEach((l, i) => l.set(((Math.floor(t / 1.2) + i * 3) % 7) < 2 || (i < 2 && Math.floor(t * 1.5) % 2 === (i % 2))));
  };
}

const updaters = Object.values(SKINS).map(skinPanel);
let running = true;
let t = 0;
let last = performance.now();
function frame(now) {
  if (running) {
    t += (now - last) / 1000;
    for (const u of updaters) u(t);
  }
  last = now;
  requestAnimationFrame(frame);
}
requestAnimationFrame(frame);
document.getElementById("pv-toggle").addEventListener("click", (e) => {
  running = !running;
  e.target.textContent = running ? "Pause" : "Play";
});

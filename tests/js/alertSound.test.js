import assert from "node:assert/strict";
import test from "node:test";

import { MIN_GAP_MS, chimeFor, createAlertSound, newAlerts, soundSettings } from "../../web/js/alertSound.js";

/** A stand-in for the browser's audio: records what is played, and can start "suspended" like a page nobody has clicked. */
function fakeAudio({ state = "running" } = {}) {
  const log = { contexts: [], oscillators: [], resumed: 0 };
  class Ctx {
    constructor() {
      this.state = state;
      this.currentTime = 10;
      this.destination = {};
      log.contexts.push(this);
    }
    resume() {
      log.resumed++;
      return Promise.resolve();
    }
    createOscillator() {
      const o = { frequency: { value: 0 }, connect: (g) => g, start: (t) => (o.startedAt = t), stop: (t) => (o.stoppedAt = t) };
      log.oscillators.push(o);
      return o;
    }
    createGain() {
      const g = { gain: { setValueAtTime() {}, exponentialRampToValueAtTime() {} }, connect: (d) => d };
      return g;
    }
  }
  return { Ctx, log };
}
const memory = (initial) => {
  const data = new Map(initial ? [["thrashdash.alertsound", initial]] : []);
  return { getItem: (k) => data.get(k) ?? null, setItem: (k, v) => data.set(k, v), data };
};
const warn = { id: "fuel", level: "warn" };
const crit = { id: "overheat", level: "crit" };

test("the sound is off to begin with, and settings are cleaned", () => {
  assert.deepEqual(soundSettings(null), { on: false, volume: 0.5 });
  assert.deepEqual(soundSettings('{"on":true,"volume":0.8}'), { on: true, volume: 0.8 });
  assert.deepEqual(soundSettings('{"on":"yes","volume":9}'), { on: false, volume: 1 });
  assert.deepEqual(soundSettings('{"volume":-3}'), { on: false, volume: 0 });
  assert.deepEqual(soundSettings("not json"), { on: false, volume: 0.5 });
  assert.deepEqual(soundSettings('{"volume":"loud"}'), { on: false, volume: 0.5 });
});

test("?sound=1 turns it on for that window, and nothing else does", () => {
  assert.equal(soundSettings(null, "?sound=1").on, true);
  assert.equal(soundSettings(null, "?d=cluster&sound=1").on, true);
  for (const q of ["", "?sound=0", "?sound=true", "?sound="]) assert.equal(soundSettings(null, q).on, false, q);
});

test("a warning is one note and a critical alert two, falling", () => {
  assert.equal(chimeFor("warn").length, 1);
  const two = chimeFor("crit");
  assert.equal(two.length, 2);
  assert.ok(two[1].freq < two[0].freq && two[1].at > two[0].at);
  assert.equal(chimeFor("anything else").length, 1);
});

test("only alerts that were not showing are new, and the worse level wins", () => {
  assert.deepEqual(newAlerts(new Set(), [warn]).level, "warn");
  assert.equal(newAlerts(new Set(["fuel"]), [warn]).level, null); // still up: no new chime
  assert.equal(newAlerts(new Set(["fuel"]), [warn, crit]).level, "crit");
  assert.equal(newAlerts(new Set(["fuel", "overheat"]), []).level, null); // one going away is not a sound
  assert.deepEqual([...newAlerts(new Set(), [warn, crit]).ids], ["fuel", "overheat"]);
  assert.equal(newAlerts(new Set(), []).level, null);
});

test("a new alert chimes once, the notes are played, and one that stays up does not chime again", () => {
  const { Ctx, log } = fakeAudio();
  let t = 0;
  const sound = createAlertSound({ audio: Ctx, storage: memory('{"on":true,"volume":1}'), now: () => t });
  assert.equal(sound.update([]), false); // (the first look: nothing yet)
  t = 5000;
  assert.equal(sound.update([warn]), true);
  assert.equal(log.oscillators.length, 1);
  assert.equal(log.oscillators[0].frequency.value, 880);
  t = 20000;
  assert.equal(sound.update([warn]), false); // still showing
  sound.update([]);
  t = 40000;
  assert.equal(sound.update([warn]), true); // gone and back: again
  assert.equal(sound.update([warn, crit]), false); // (too soon after the last)
});

test("alerts already up when the window opens do not chime", () => {
  const { Ctx, log } = fakeAudio();
  const sound = createAlertSound({ audio: Ctx, storage: memory('{"on":true}'), now: () => 0 });
  assert.equal(sound.update([warn, crit]), false);
  assert.equal(log.oscillators.length, 0);
});

test("with the sound off nothing plays, and nothing is even set up", () => {
  const { Ctx, log } = fakeAudio();
  const sound = createAlertSound({ audio: Ctx, storage: memory(), now: () => 0 });
  sound.update([]);
  assert.equal(sound.update([crit]), false);
  assert.equal(log.contexts.length, 0);
});

test("chimes are never closer than the gap, however the alerts change", () => {
  const { Ctx, log } = fakeAudio();
  let t = 10000;
  const sound = createAlertSound({ audio: Ctx, storage: memory('{"on":true}'), now: () => t });
  sound.update([]);
  assert.equal(sound.update([warn]), true);
  t += MIN_GAP_MS - 1;
  assert.equal(sound.update([warn, crit]), false);
  sound.update([]);
  t += 10;
  assert.equal(sound.update([crit]), true);
  assert.equal(log.oscillators.length, 1 + 2);
});

test("a browser that has not allowed sound yet is reported, and nothing is played", () => {
  const { Ctx, log } = fakeAudio({ state: "suspended" });
  const sound = createAlertSound({ audio: Ctx, storage: memory('{"on":true}'), now: () => 0 });
  assert.equal(sound.test(), false);
  assert.equal(sound.blocked, true);
  assert.equal(log.oscillators.length, 0);
  assert.ok(log.resumed >= 1); // (it asked to be let through)
  log.contexts[0].state = "running"; // the click arrived
  assert.equal(sound.test(), true);
  assert.equal(sound.blocked, false);
});

test("the Test button plays whatever the setting says", () => {
  const { Ctx, log } = fakeAudio();
  const sound = createAlertSound({ audio: Ctx, storage: memory(), now: () => 0 });
  assert.equal(sound.test("crit"), true);
  assert.equal(log.oscillators.length, 2);
});

test("the settings are remembered in the browser, and bad values never are", () => {
  const store = memory();
  const sound = createAlertSound({ audio: fakeAudio().Ctx, storage: store });
  sound.set({ on: true, volume: 0.25 });
  assert.deepEqual(JSON.parse(store.data.get("thrashdash.alertsound")), { on: true, volume: 0.25 });
  assert.deepEqual(createAlertSound({ audio: fakeAudio().Ctx, storage: store }).settings, { on: true, volume: 0.25 });
  sound.set({ on: "yes", volume: 7 });
  assert.deepEqual(sound.settings, { on: true, volume: 1 });
  sound.set({ volume: NaN });
  sound.set({ volume: "loud" });
  assert.equal(sound.settings.volume, 1); // (not a number: left as it was)
});

test("a browser with no audio at all just stays quiet", () => {
  const sound = createAlertSound({ audio: undefined, storage: memory('{"on":true}'), now: () => 0 });
  sound.update([]);
  assert.equal(sound.update([crit]), false);
  assert.equal(sound.test(), false);
});

test("a storage that throws does not break the page", () => {
  const broken = { getItem() { throw new Error("no"); }, setItem() { throw new Error("no"); } };
  assert.doesNotThrow(() => createAlertSound({ audio: fakeAudio().Ctx, storage: broken }).set({ on: true }));
});

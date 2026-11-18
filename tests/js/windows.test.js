import test from "node:test";
import assert from "node:assert/strict";
import { dashboardUrl, sameSizeFeatures, tripsFeatures, tripsGeometry, loadTripsGeometry, saveTripsGeometry, TRIPS_WINDOW_KEY } from "../../web/js/windows.js";

const win = (over = {}) => ({ innerWidth: 1200, innerHeight: 420, screenX: 100, screenY: 50, screen: { availWidth: 2560, availHeight: 1400 }, ...over });
const parse = (features) => Object.fromEntries(features.split(",").map((kv) => kv.split("=")));

test("a new window is the same size as this one, a little down and to the right", () => {
  const f = parse(sameSizeFeatures({}, win()));
  assert.equal(f.popup, "yes");
  assert.equal(Number(f.width), 1200);
  assert.equal(Number(f.height), 420);
  assert.equal(Number(f.left), 140);
  assert.equal(Number(f.top), 90);
});

test("the editor panel's width is left out, as the new window will not have one", () => {
  assert.equal(Number(parse(sameSizeFeatures({ insetRight: 340 }, win())).width), 860);
});

test("sizes are kept within sensible limits", () => {
  const tiny = parse(sameSizeFeatures({ insetRight: 5000 }, win()));
  assert.equal(Number(tiny.width), 320);
  const huge = parse(sameSizeFeatures({}, win({ innerWidth: 5000, innerHeight: 3000 })));
  assert.equal(Number(huge.width), 2560, "no wider than the screen");
  assert.equal(Number(huge.height), 1400);
  const none = parse(sameSizeFeatures({}, win({ screen: undefined })));
  assert.equal(Number(none.width), 1200, "still works if the screen size is unknown");
});

test("the position is never negative", () => {
  const f = parse(sameSizeFeatures({}, win({ screenX: -2000, screenY: -900 })));
  assert.equal(Number(f.left), 0);
  assert.equal(Number(f.top), 0);
});

test("the address names the dashboard and can ask for the menu", () => {
  assert.equal(dashboardUrl("cluster", {}, "http://127.0.0.1:8765"), "http://127.0.0.1:8765/?d=cluster");
  assert.equal(dashboardUrl("u-0123456789ab", { menu: true }, "http://127.0.0.1:8765"), "http://127.0.0.1:8765/?d=u-0123456789ab&menu=1");
  assert.equal(dashboardUrl("a b&c", {}, "http://x"), "http://x/?d=a%20b%26c", "the id is escaped");
});

const memory = (initial) => {
  const m = new Map(initial ? [[TRIPS_WINDOW_KEY, initial]] : []);
  return { getItem: (k) => (m.has(k) ? m.get(k) : null), setItem: (k, v) => m.set(k, v), writes: () => m.size };
};

test("a Trips window with nothing remembered opens at the default size", () => {
  const f = parse(tripsFeatures(null, win()));
  assert.deepEqual([f.popup, Number(f.width), Number(f.height), f.left], ["yes", 1180, 760, undefined]);
});

test("a Trips window opens as big, and where, the last one was", () => {
  const f = parse(tripsFeatures({ width: 1500, height: 900, x: 200, y: 80 }, win()));
  assert.deepEqual([Number(f.width), Number(f.height), Number(f.left), Number(f.top)], [1500, 900, 200, 80]);
});

test("a place of 0,0 is left out (Wayland cannot report or set one), and a size is kept to the screen", () => {
  const f = parse(tripsFeatures({ width: 9000, height: 9000, x: 0, y: 0 }, win()));
  assert.deepEqual([Number(f.width), Number(f.height), f.left], [2560, 1400, undefined]);
});

test("the geometry that is kept is the inner size and the place", () => {
  assert.deepEqual(tripsGeometry(win({ innerWidth: 1300.4, innerHeight: 777, screenX: 12, screenY: -3 })), { width: 1300, height: 777, x: 12, y: -3 });
});

test("remembering geometry: read back, ignored when nonsense, and not rewritten when unchanged", () => {
  const store = memory();
  assert.equal(loadTripsGeometry(store), null);
  saveTripsGeometry(store, { width: 1400, height: 800, x: 10, y: 20 });
  assert.deepEqual(loadTripsGeometry(store), { width: 1400, height: 800, x: 10, y: 20 });
  let sets = 0;
  const counting = { getItem: store.getItem, setItem: (k, v) => { sets++; store.setItem(k, v); } };
  saveTripsGeometry(counting, { width: 1400, height: 800, x: 10, y: 20 });
  assert.equal(sets, 0, "unchanged: nothing written");
  assert.equal(loadTripsGeometry(memory("not json")), null);
  assert.equal(loadTripsGeometry(memory(JSON.stringify({ width: 10, height: 10 }))), null, "too small to be a real window");
  assert.equal(loadTripsGeometry({ getItem() { throw new Error("no storage"); } }), null);
  assert.doesNotThrow(() => saveTripsGeometry({ getItem() { throw new Error("no storage"); }, setItem() {} }, { width: 1, height: 1, x: 0, y: 0 }));
});

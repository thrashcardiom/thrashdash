import test from "node:test";
import assert from "node:assert/strict";
import { dashboardUrl, sameSizeFeatures } from "../../web/js/windows.js";

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

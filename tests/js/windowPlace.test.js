import test from "node:test";
import assert from "node:assert/strict";
import { applySavedPlacement, SETTLE_MS, TOLERANCE_PX } from "../../web/js/windowPlace.js";

// A fake window that obeys resizeTo / moveTo after a delay, like a window manager does.
function fakeWindow({ outerWidth = 903, outerHeight = 222, screenX = 0, screenY = 28, obeys = true, delay = 0, throws = false } = {}) {
  const win = { outerWidth, outerHeight, screenX, screenY, calls: [] };
  let clock = 0;
  win.tick = (ms) => {
    clock += ms;
    if (win.pending && clock >= win.pending.at) {
      Object.assign(win, win.pending.apply);
      win.pending = null;
    }
  };
  win.resizeTo = (w, h) => {
    if (throws) throw new Error("not allowed");
    win.calls.push(["resizeTo", w, h]);
    if (obeys) win.pending = { at: clock + delay, apply: { ...(win.pending?.apply ?? {}), outerWidth: w, outerHeight: h } };
  };
  win.moveTo = (x, y) => {
    if (throws) throw new Error("not allowed");
    win.calls.push(["moveTo", x, y]);
    if (obeys) win.pending = { at: clock + delay, apply: { ...(win.pending?.apply ?? {}), screenX: x, screenY: y } };
  };
  return win;
}

const run = (win, entry, settings = { exactWindowPlacement: true }) =>
  applySavedPlacement("abc12345", {
    getSettings: async () => settings,
    listWindows: async () => ({ windows: entry ? { abc12345: entry } : {} }),
    win,
    sleep: async (ms) => win.tick(ms),
  });

test("a window that is not where it was remembered is sized and moved there", async () => {
  const win = fakeWindow();
  const moved = await run(win, { width: 1083, height: 430, x: 1300, y: 40 });
  assert.equal(moved, true);
  assert.deepEqual(win.calls, [["resizeTo", 1083, 430], ["moveTo", 1300, 40]]);
  assert.deepEqual([win.outerWidth, win.outerHeight, win.screenX, win.screenY], [1083, 430, 1300, 40]);
});

test("each window uses its own entry: the windows that all started at the first one's size and place go to their own", async () => {
  const a = fakeWindow();
  const b = fakeWindow();
  await run(a, { width: 1083, height: 430, x: 100, y: 50 });
  await run(b, { width: 700, height: 900, x: 1500, y: 300 });
  assert.deepEqual([a.outerWidth, a.screenX], [1083, 100]);
  assert.deepEqual([b.outerWidth, b.screenX], [700, 1500]);
});

test("a window already there is left alone", async () => {
  const win = fakeWindow({ outerWidth: 1083, outerHeight: 430, screenX: 1300, screenY: 40 });
  assert.equal(await run(win, { width: 1085, height: 428, x: 1302, y: 38 }), false, "within a few pixels is there");
  assert.deepEqual(win.calls, []);
});

test("only the size when the position is not known (0,0 is what a system that cannot say reports)", async () => {
  const win = fakeWindow();
  await run(win, { width: 1083, height: 430, x: 0, y: 0 });
  assert.deepEqual(win.calls, [["resizeTo", 1083, 430]]);
});

test("nothing is done unless exact placement is on", async () => {
  const win = fakeWindow();
  assert.equal(await run(win, { width: 1083, height: 430, x: 1, y: 1 }, { exactWindowPlacement: false }), false);
  assert.equal(await run(win, { width: 1083, height: 430, x: 1, y: 1 }, {}), false);
  assert.deepEqual(win.calls, []);
});

test("nothing is done for a window the relay does not remember, or with no usable size", async () => {
  const win = fakeWindow();
  assert.equal(await run(win, null), false);
  assert.equal(await run(win, { width: null, height: null, x: 5, y: 5 }), false);
  assert.equal(await run(win, { width: "wide", height: 430 }), false);
  assert.deepEqual(win.calls, []);
});

test("it waits for the window manager to carry the move out, but not for ever", async () => {
  const slow = fakeWindow({ delay: 400 });
  await run(slow, { width: 1083, height: 430, x: 1300, y: 40 });
  assert.equal(slow.screenX, 1300, "waited for the slow move");
  const never = fakeWindow({ obeys: false });
  let slept = 0;
  const done = await applySavedPlacement("abc12345", {
    getSettings: async () => ({ exactWindowPlacement: true }),
    listWindows: async () => ({ windows: { abc12345: { width: 1083, height: 430, x: 1300, y: 40 } } }),
    win: never,
    sleep: async (ms) => (slept += ms),
  });
  assert.equal(done, true);
  assert.ok(slept >= SETTLE_MS && slept <= SETTLE_MS + 200, `gave up after about ${SETTLE_MS} ms (${slept})`);
});

test("a system that does not let a page move its window, or a relay that does not answer, is not an error", async () => {
  assert.equal(await run(fakeWindow({ throws: true }), { width: 1083, height: 430, x: 1, y: 1 }), false);
  const win = fakeWindow();
  assert.equal(await applySavedPlacement("abc12345", { getSettings: async () => { throw new Error("offline"); }, listWindows: async () => ({}), win, sleep: async () => {} }), false);
  assert.ok(TOLERANCE_PX >= 1);
});

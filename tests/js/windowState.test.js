import test from "node:test";
import assert from "node:assert/strict";
import { DEBOUNCE_MS, HEARTBEAT_MS, POLL_MS, createWindowReporter, ensureWindowId, randomWindowId, readGeometry, sameReport } from "../../web/js/windowState.js";
import { dashboardUrl } from "../../web/js/windows.js";

test("a window id is 12 lowercase letters and digits, and different each time", () => {
  const ids = new Set(Array.from({ length: 50 }, () => randomWindowId()));
  assert.equal(ids.size, 50);
  for (const id of ids) assert.match(id, /^[a-z0-9]{12}$/);
});

test("a window without an id gets one in its address, keeping everything else", () => {
  const history = { calls: [], replaceState(...a) { this.calls.push(a[2]); } };
  const id = ensureWindowId({ search: "?d=cluster&menu=1", pathname: "/", hash: "" }, history, () => "abcdef123456");
  assert.equal(id, "abcdef123456");
  assert.equal(history.calls[0], "/?d=cluster&menu=1&w=abcdef123456");
});

test("a window that has an id keeps it and its address is left alone", () => {
  const history = { calls: [], replaceState(...a) { this.calls.push(a); } };
  assert.equal(ensureWindowId({ search: "?w=abc12345&d=x", pathname: "/", hash: "" }, history, () => "zzzzzzzzzzzz"), "abc12345");
  assert.deepEqual(history.calls, []);
});

test("an id in the address that is not valid is replaced, never trusted", () => {
  for (const bad of ["../x", "UPPERCASE1", "a", "x".repeat(40), ""]) {
    const history = { replaceState() {} };
    assert.equal(ensureWindowId({ search: `?w=${encodeURIComponent(bad)}`, pathname: "/", hash: "" }, history, () => "freshid12345"), "freshid12345", JSON.stringify(bad));
  }
});

test("the geometry is rounded whole pixels, with fullscreen", () => {
  const g = readGeometry({ outerWidth: 1200.4, outerHeight: 419.6, screenX: -1920, screenY: 30 }, { fullscreenElement: {} });
  assert.deepEqual(g, { width: 1200, height: 420, x: -1920, y: 30, fullscreen: true });
  assert.equal(readGeometry({ outerWidth: 800, outerHeight: 600 }, {}).x, 0, "no position known is 0");
});

test("new windows are named in the address the opener builds", () => {
  assert.equal(dashboardUrl("cluster", { windowId: "abc123def456", menu: true }, "http://h"), "http://h/?d=cluster&w=abc123def456&menu=1");
  assert.equal(dashboardUrl("cluster", {}, "http://h"), "http://h/?d=cluster");
});

// ---- the reporter, with a hand-driven clock ----

function rig(initial = {}) {
  const sent = [];
  const forgotten = [];
  const listeners = { window: {}, document: {} };
  let time = 1000;
  const geo = { width: 1200, height: 420, x: 0, y: 0, fullscreen: false, ...initial };
  const state = { id: "cluster", pinned: true };
  const pending = new Map();
  let nextTimer = 1;
  const timers = {
    setTimeout: (fn, ms) => { pending.set(nextTimer, { fn, at: time + ms, every: null }); return nextTimer++; },
    clearTimeout: (id) => pending.delete(id),
    setInterval: (fn, ms) => { pending.set(nextTimer, { fn, at: time + ms, every: ms }); return nextTimer++; },
    clearInterval: (id) => pending.delete(id),
  };
  const advance = (ms) => {
    const end = time + ms;
    for (;;) {
      const due = [...pending.entries()].filter(([, t]) => t.at <= end).sort((a, b) => a[1].at - b[1].at)[0];
      if (!due) break;
      const [id, t] = due;
      time = t.at;
      if (t.every) t.at += t.every; else pending.delete(id);
      t.fn();
    }
    time = end;
  };
  const reporter = createWindowReporter({
    id: "abc12345", state: () => state, send: (b) => sent.push(b), forget: (s) => forgotten.push(s), geometry: () => ({ ...geo }), now: () => time, timers,
    events: { window: { addEventListener: (n, f) => (listeners.window[n] = f) }, document: { addEventListener: (n, f) => (listeners.document[n] = f) } },
  });
  return { reporter, sent, forgotten, listeners, geo, state, advance };
}

test("it reports at once when it starts, with a stamp", () => {
  const r = rig();
  r.reporter.start();
  assert.equal(r.sent.length, 1);
  assert.deepEqual(r.sent[0], { dashboard: "cluster", pinned: true, width: 1200, height: 420, x: 0, y: 0, fullscreen: false, stamp: 1000 });
});

test("a resize is reported after a short pause, once however many events", () => {
  const r = rig();
  r.reporter.start();
  r.geo.width = 900;
  for (let i = 0; i < 20; i++) r.listeners.window.resize();
  r.advance(DEBOUNCE_MS - 50);
  assert.equal(r.sent.length, 1, "not yet");
  r.advance(100);
  assert.equal(r.sent.length, 2);
  assert.equal(r.sent[1].width, 900);
});

test("a move is noticed by looking, since windows have no move event", () => {
  const r = rig();
  r.reporter.start();
  r.geo.x = 400;
  r.advance(POLL_MS + DEBOUNCE_MS + 50);
  assert.equal(r.sent.length, 2);
  assert.equal(r.sent[1].x, 400);
});

test("changing dashboard, or becoming pinned, is reported", () => {
  const r = rig();
  r.reporter.start();
  r.state.id = "drift";
  r.state.pinned = false;
  r.advance(POLL_MS + DEBOUNCE_MS + 50);
  assert.deepEqual([r.sent[1].dashboard, r.sent[1].pinned], ["drift", false]);
});

test("fullscreen changes are reported", () => {
  const r = rig();
  r.reporter.start();
  r.geo.fullscreen = true;
  r.listeners.document.fullscreenchange();
  r.advance(DEBOUNCE_MS + 10);
  assert.equal(r.sent[1].fullscreen, true);
});

test("an unchanged window is not reported again until the heartbeat", () => {
  const r = rig();
  r.reporter.start();
  r.advance(HEARTBEAT_MS - 100);
  assert.equal(r.sent.length, 1, "nothing changed, so nothing sent");
  r.advance(200);
  assert.equal(r.sent.length, 2, "the heartbeat says it is still open");
  assert.ok(r.sent[1].stamp > r.sent[0].stamp);
});

test("notify with now reports straight away (a relay that just restarted)", () => {
  const r = rig();
  r.reporter.start();
  r.reporter.notify({ now: true });
  assert.equal(r.sent.length, 2);
});

test("closing the window says so, with the time", () => {
  const r = rig();
  r.reporter.start();
  r.advance(500);
  r.listeners.window.pagehide();
  assert.deepEqual(r.forgotten, [1500]);
});

test("nothing happens before start, and start twice is harmless", () => {
  const r = rig();
  r.reporter.notify({ now: true });
  assert.equal(r.sent.length, 0);
  r.reporter.start();
  r.reporter.start();
  assert.equal(r.sent.length, 1);
});

test("a failing relay never breaks the page", () => {
  const sent = [];
  const reporter = createWindowReporter({
    id: "abc12345", state: () => ({ id: null, pinned: false }), send: () => { throw new Error("down"); }, forget: () => { throw new Error("down"); },
    geometry: () => ({ width: 1, height: 1, x: 0, y: 0, fullscreen: false }), now: () => 1, timers: { setTimeout() {}, clearTimeout() {}, setInterval() {}, clearInterval() {} },
    events: { window: { addEventListener(n, f) { if (n === "pagehide") sent.push(f); } }, document: { addEventListener() {} } },
  });
  assert.doesNotThrow(() => reporter.start());
  assert.doesNotThrow(() => sent[0]());
  assert.equal(sameReport(null, null), false);
});

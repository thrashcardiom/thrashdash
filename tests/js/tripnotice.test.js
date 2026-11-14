import test from "node:test";
import assert from "node:assert/strict";
import { createTripNotice } from "../../web/js/tripnotice.js";

// A page element and a clock that can be driven by hand: just enough of the DOM for this module.
function fakeElement() {
  const el = { hidden: true, children: [], listeners: {}, replaceChildren(...kids) { this.children = kids; } };
  el.addEventListener = (type, fn) => (el.listeners[type] = fn);
  return el;
}

function setup() {
  globalThis.document = { createElement: () => ({ className: "", textContent: "" }) };
  const el = fakeElement();
  const timers = [];
  const banner = createTripNotice(el, { setTimer: (fn, ms) => (timers.push({ fn, ms }), timers.length), clearTimer: () => {} });
  return { el, timers, banner };
}

test("a notice is shown as a title and a message, in plain text", () => {
  const { el, banner } = setup();
  banner.show({ title: "Trip finished", msg: "<b>A to B</b>: 2.1 km" });
  assert.equal(el.hidden, false);
  assert.equal(el.children[0].textContent, "Trip finished");
  assert.equal(el.children[1].textContent, "<b>A to B</b>: 2.1 km", "markup is shown as text, never interpreted");
});

test("it goes away by itself after a while, and when clicked", () => {
  const { el, timers, banner } = setup();
  banner.show({ title: "t", msg: "m" });
  assert.ok(timers[0].ms >= 5000 && timers[0].ms <= 30000);
  timers[0].fn();
  assert.equal(el.hidden, true);
  banner.show({ title: "t", msg: "m" });
  el.listeners.click();
  assert.equal(el.hidden, true);
});

test("a second notice replaces the first and restarts the time", () => {
  const { el, timers, banner } = setup();
  banner.show({ title: "one", msg: "first" });
  banner.show({ title: "two", msg: "second" });
  assert.equal(el.children[1].textContent, "second");
  assert.equal(timers.length, 2);
});

test("a notice with no text, or a title that is not text, is handled", () => {
  const { el, banner } = setup();
  banner.show({ title: "x" });
  banner.show(null);
  assert.equal(el.hidden, true, "nothing to show: stays hidden");
  banner.show({ title: 5, msg: "hello" });
  assert.equal(el.children[0].textContent, "ThrashDash");
});

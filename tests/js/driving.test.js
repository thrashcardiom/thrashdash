import test from "node:test";
import assert from "node:assert/strict";
import { gPoint, STANDARD_GRAVITY as G } from "../../web/js/widgets/gmeter.js";
import { pedalFill, wheelAngle } from "../../web/js/widgets/inputs.js";

const near = (a, b, eps = 1e-9) => Math.abs(a - b) < eps;

test("the G meter converts m/s^2 to a position as a fraction of the outer ring", () => {
  const p = gPoint(0.75 * G, 0, { range: 1.5 });
  assert.ok(near(Math.abs(p.x), 0.5) && near(p.y, 0) && near(p.g, 0.75));
  const q = gPoint(0, 1.5 * G, { range: 1.5 });
  assert.ok(near(q.x, 0) && near(Math.abs(q.y), 1));
});

test("the dot stays on the ring beyond the range, pointing the right way", () => {
  const p = gPoint(3 * G, 3 * G, { range: 1.5 });
  assert.ok(near(Math.hypot(p.x, p.y), 1), "on the ring");
  assert.ok(near(Math.abs(p.x), Math.abs(p.y)), "direction kept");
  assert.ok(near(p.g, Math.hypot(3, 3)), "the true force is still reported");
});

test("the reverse options flip each axis, and nothing else", () => {
  const base = gPoint(G, 0.5 * G, { range: 2 });
  const lat = gPoint(G, 0.5 * G, { range: 2, flipLateral: true });
  const lon = gPoint(G, 0.5 * G, { range: 2, flipLongitudinal: true });
  assert.ok(near(lat.x, -base.x) && near(lat.y, base.y));
  assert.ok(near(lon.x, base.x) && near(lon.y, -base.y));
});

test("no data, or junk, gives no point", () => {
  assert.equal(gPoint(undefined, 1), null);
  assert.equal(gPoint(1, NaN), null);
  assert.equal(gPoint("1", 1), null);
  assert.equal(gPoint(null, null), null);
});

test("a stationary car sits in the middle", () => {
  const p = gPoint(0.01, -0.02);
  assert.ok(Math.abs(p.x) < 0.01 && Math.abs(p.y) < 0.01);
});

test("the steering wheel turns by the input times the lock angle", () => {
  assert.equal(wheelAngle(0), 0);
  assert.equal(wheelAngle(1, 180), 180);
  assert.equal(wheelAngle(-0.5, 270), -135);
  assert.equal(wheelAngle(1, 180, true), -180);
  assert.equal(wheelAngle(2, 180), 180, "limited to full lock");
  assert.equal(wheelAngle(undefined), null);
  assert.equal(wheelAngle(NaN), null);
});

test("pedals are limited to 0..1 and show nothing without data", () => {
  assert.equal(pedalFill(0.4), 0.4);
  assert.equal(pedalFill(-1), 0);
  assert.equal(pedalFill(7), 1);
  assert.equal(pedalFill(undefined), null);
  assert.equal(pedalFill("0.5"), null);
});

test("real-game signs: braking moves the dot up, accelerating down, a right-hand corner to the left", () => {
  // Values as measured in BeamNG: accY is +ve braking and -ve accelerating; accX is -ve in a right-hand corner.
  assert.ok(gPoint(0, 0.8 * G).y > 0, "braking: up");
  assert.ok(gPoint(0, -0.8 * G).y < 0, "accelerating: down");
  assert.ok(gPoint(-0.8 * G, 0).x < 0, "right-hand corner: left (the force you feel pushes you outwards)");
  assert.ok(gPoint(0.8 * G, 0).x > 0, "left-hand corner: right");
});

test("real-game steering: a positive input (a right turn) turns the wheel clockwise", () => {
  assert.ok(wheelAngle(0.6, 180) > 0);
});

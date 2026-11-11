import test from "node:test";
import assert from "node:assert/strict";
import { safeNext } from "../../web/js/pin.js";

test("after signing in, a device goes back to where it was heading on this site", () => {
  for (const ok of ["/", "/?d=cluster", "/?d=cluster&w=abc12345", "/js/app.js", "/some/path?x=1#frag"]) {
    assert.equal(safeNext(ok), ok);
  }
});

test("it can never be sent to another site", () => {
  for (const bad of ["//evil.example", "//evil.example/x", "/\\evil.example", "https://evil.example", "http://evil.example/", "javascript:alert(1)", "evil.example", "data:text/html,x", "\\\\evil", " //evil.example"]) {
    assert.equal(safeNext(bad), "/", JSON.stringify(bad));
  }
});

test("missing or odd values go to the dashboard", () => {
  for (const odd of [null, undefined, "", 5, {}, [], true]) assert.equal(safeNext(odd), "/");
});

test("it does not loop back to the PIN page", () => {
  assert.equal(safeNext("/pin.html"), "/");
  assert.equal(safeNext("/pin.html?next=%2F"), "/");
});

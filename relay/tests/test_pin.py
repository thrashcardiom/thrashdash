"""Tests for the PIN that devices on the network must enter: the logic, and the server in front of it."""
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import aiohttp  # noqa: E402
from yarl import URL  # noqa: E402

from test_network import NetworkFixture, H  # noqa: E402
from thrashdash.auth import (COOKIE_NAME, FIRST_LOCKOUT_S, FREE_TRIES, GLOBAL_MAX_FAILURES, MAX_LOCKOUT_S, MAX_TOKENS, SESSION_SECONDS,  # noqa: E402
                             TOKEN_SECONDS, AuthStore, Throttle, valid_pin)


class Clock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


class PinRuleTests(unittest.TestCase):
    def test_four_to_eight_digits(self):
        for ok in ("0000", "4821", "123456", "12345678"):
            self.assertTrue(valid_pin(ok), ok)

    def test_nothing_else(self):
        for bad in ("", "123", "123456789", "48 21", "4821\\n", "4821\n", "abcd", "12a4", "-4821", "4.21", "٤٨٢١", "４８２１", None, 4821, ["4821"], b"4821"):
            self.assertFalse(valid_pin(bad), repr(bad))


class StoreTests(unittest.TestCase):
    def test_no_pin_until_one_is_set(self):
        store = AuthStore()
        self.assertFalse(store.has_pin)
        self.assertFalse(store.check_pin("4821"))
        self.assertFalse(store.cookie_valid(store.issue_cookie()), "a cookie means nothing while there is no PIN")

    def test_right_and_wrong_pins(self):
        store = AuthStore()
        store.set_pin("4821")
        self.assertTrue(store.check_pin("4821"))
        for wrong in ("4822", "", "482", "48210", None, 4821, "4821 ", " 4821"):
            self.assertFalse(store.check_pin(wrong), repr(wrong))

    def test_a_bad_pin_is_refused_and_changes_nothing(self):
        store = AuthStore()
        store.set_pin("4821")
        cookie = store.issue_cookie()
        for bad in ("12", "abcdef", "", "123456789"):
            with self.assertRaises(ValueError):
                store.set_pin(bad)
        self.assertTrue(store.check_pin("4821"))
        self.assertTrue(store.cookie_valid(cookie), "a refused change must not sign anyone out")

    def test_it_survives_a_restart_and_the_file_is_private(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "auth.json"
            store = AuthStore(path)
            store.set_pin("906514")
            cookie = store.issue_cookie()
            again = AuthStore(path)
            self.assertTrue(again.check_pin("906514"))
            self.assertTrue(again.cookie_valid(cookie), "phones stay signed in across a restart")
            if os.name == "posix":
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600, "only this user may read it")
            self.assertFalse((Path(d) / "auth.json.tmp").exists())

    def test_damaged_files_mean_no_pin(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "auth.json"
            good_secret = "ab" * 32
            for text in ("garbage{", "[]", "", '{"pin": 4821, "secret": "' + good_secret + '"}', '{"pin": "4821", "secret": "short"}',
                         '{"pin": "12", "secret": "' + good_secret + '"}', '{"pin": "4821"}', '{"secret": "' + good_secret + '"}'):
                path.write_text(text)
                self.assertFalse(AuthStore(path).has_pin, text[:40])

    def test_changing_the_pin_signs_everyone_out_even_to_the_same_pin(self):
        store = AuthStore()
        store.set_pin("4821")
        cookie = store.issue_cookie()
        token = store.new_token()
        store.set_pin("4821")
        self.assertFalse(store.cookie_valid(cookie))
        self.assertFalse(store.use_token(token), "and an unused QR token is void too")
        self.assertTrue(store.cookie_valid(store.issue_cookie()))


class CookieTests(unittest.TestCase):
    def setUp(self):
        self.now = Clock()
        self.store = AuthStore(wall_clock=self.now)
        self.store.set_pin("4821")

    def test_a_fresh_cookie_is_good_for_thirty_days(self):
        cookie = self.store.issue_cookie()
        self.assertTrue(self.store.cookie_valid(cookie))
        self.now.t += SESSION_SECONDS - 60
        self.assertTrue(self.store.cookie_valid(cookie))
        self.now.t += 120
        self.assertFalse(self.store.cookie_valid(cookie), "expired")

    def test_a_tampered_cookie_is_refused(self):
        v, expiry, nonce, mac = self.store.issue_cookie().split(".")
        for forged in (f"{v}.{int(expiry) + 99999}.{nonce}.{mac}", f"{v}.{expiry}.{nonce}x.{mac}", f"{v}.{expiry}.{nonce}.{'0' * len(mac)}",
                       f"v2.{expiry}.{nonce}.{mac}", f"{v}.{expiry}.{mac}", f"{v}.{expiry}.{nonce}.{mac}.extra", f"{v}.abc.{nonce}.{mac}"):
            self.assertFalse(self.store.cookie_valid(forged), forged)

    def test_junk_is_refused_not_an_error(self):
        for junk in ("", "v1", "...", "a.b.c.d", "v1.1.2.3", None, 5, b"v1", "\x00" * 10):
            self.assertFalse(self.store.cookie_valid(junk), repr(junk))

    def test_a_cookie_from_another_installation_is_refused(self):
        other = AuthStore(wall_clock=self.now)
        other.set_pin("4821")  # the same PIN, but its own secret
        self.assertFalse(self.store.cookie_valid(other.issue_cookie()))

    def test_every_cookie_is_different(self):
        self.assertNotEqual(self.store.issue_cookie(), self.store.issue_cookie())


class TokenTests(unittest.TestCase):
    def setUp(self):
        self.now = Clock()
        self.store = AuthStore(wall_clock=self.now)
        self.store.set_pin("4821")

    def test_a_token_works_once(self):
        token = self.store.new_token()
        self.assertTrue(self.store.use_token(token))
        self.assertFalse(self.store.use_token(token))

    def test_a_token_expires(self):
        token = self.store.new_token()
        self.now.t += TOKEN_SECONDS - 1
        self.assertTrue(self.store.use_token(token))
        late = self.store.new_token()
        self.now.t += TOKEN_SECONDS + 1
        self.assertFalse(self.store.use_token(late))

    def test_unknown_and_junk_tokens(self):
        for junk in ("", "nope", None, 5, "x" * 500):
            self.assertFalse(self.store.use_token(junk), repr(junk))

    def test_tokens_are_not_guessable(self):
        tokens = {self.store.new_token() for _ in range(10)}
        self.assertEqual(len(tokens), 10)
        self.assertTrue(all(len(t) >= 30 for t in tokens))

    def test_the_number_of_waiting_tokens_is_bounded(self):
        first = self.store.new_token()
        for _ in range(MAX_TOKENS + 5):
            self.store.new_token()
        self.assertLessEqual(len(self.store._tokens), MAX_TOKENS)
        self.assertFalse(self.store.use_token(first), "the oldest were dropped to make room")


class ThrottleTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock(0.0)
        self.t = Throttle(clock=self.clock)

    def test_a_few_slips_cost_nothing(self):
        for _ in range(FREE_TRIES - 1):
            self.t.failed("phone")
            self.assertEqual(self.t.wait_seconds("phone"), 0)

    def test_then_a_wait_that_doubles(self):
        for _ in range(FREE_TRIES):
            self.t.failed("phone")
        self.assertGreater(self.t.wait_seconds("phone"), FIRST_LOCKOUT_S - 2)
        self.clock.t += FIRST_LOCKOUT_S + 1
        self.assertEqual(self.t.wait_seconds("phone"), 0)
        self.t.failed("phone")  # another miss straight after: twice as long
        self.assertGreater(self.t.wait_seconds("phone"), 2 * FIRST_LOCKOUT_S - 2)

    def test_the_wait_has_a_ceiling(self):
        for _ in range(FREE_TRIES + 20):
            self.t.failed("phone")
            self.clock.t += MAX_LOCKOUT_S + 1
        self.t.failed("phone")
        self.assertLessEqual(self.t.wait_seconds("phone"), MAX_LOCKOUT_S + 1)

    def test_one_device_does_not_lock_out_another(self):
        for _ in range(FREE_TRIES):
            self.t.failed("thief")
        self.assertGreater(self.t.wait_seconds("thief"), 0)
        self.assertEqual(self.t.wait_seconds("me"), 0)

    def test_a_right_pin_wipes_the_record(self):
        for _ in range(FREE_TRIES - 1):
            self.t.failed("phone")
        self.t.succeeded("phone")
        for _ in range(FREE_TRIES - 1):
            self.t.failed("phone")
        self.assertEqual(self.t.wait_seconds("phone"), 0)

    def test_spreading_guesses_over_many_addresses_does_not_help(self):
        for i in range(GLOBAL_MAX_FAILURES):
            self.t.failed(f"10.0.0.{i}")  # one miss each: no single device is over its limit
        self.assertGreater(self.t.wait_seconds("a-brand-new-device"), 0, "but everyone waits")
        self.clock.t += 200
        self.assertEqual(self.t.wait_seconds("a-brand-new-device"), 0)

    def test_its_memory_is_bounded(self):
        for i in range(5000):
            self.t.failed(f"10.1.{i // 250}.{i % 250}")
        self.assertLessEqual(len(self.t._failures), 1001)


class PinGateTests(NetworkFixture):
    """The real server, with network access on, playing a phone that has NOT signed in (unless a test says otherwise)."""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        await self.lan.enable()
        self.remote = True
        self.page = {"Accept": "text/html"}

    async def get(self, path, headers=None, **kw):
        return await self.http.get(self.lan_url + path, headers=headers, allow_redirects=False, **kw)

    async def login(self, pin, headers=None):
        return await self.http.post(self.lan_url + "/api/pin/login", json={"pin": pin}, headers=headers if headers is not None else H)

    # ---- before signing in ------------------------------------------------------------------

    async def test_a_browser_is_sent_to_the_pin_page(self):
        async with await self.get("/", self.page) as r:
            self.assertEqual(r.status, 302)
            where = URL(r.headers["Location"])
            self.assertEqual((where.path, where.query["next"]), ("/pin.html", "/"))

    async def test_where_it_was_going_is_remembered(self):
        async with await self.get("/?d=cluster&w=abc12345", self.page) as r:
            self.assertEqual(r.status, 302)
            self.assertEqual(URL(r.headers["Location"]).query["next"], "/?d=cluster&w=abc12345")

    async def test_nothing_else_is_served_without_the_pin(self):
        for path in ("/api/layouts", "/api/layouts/default", "/api/settings", "/api/status", "/api/client", "/api/windows", "/js/app.js",
                     "/css/dash.css", "/layouts/default.json", "/skin-preview.html", "/index.html", "/fonts/Jost-Variable.woff2", "/api/lan/pin"):
            async with await self.get(path) as r:
                self.assertEqual(r.status, 401, path)
        with self.assertRaises(aiohttp.WSServerHandshakeError) as ctx:
            await self.http.ws_connect(self.lan_url + "/ws", headers={"Origin": self.lan_url})
        self.assertEqual(ctx.exception.status, 401, "no live data either")

    async def test_the_pin_page_and_what_it_needs_are_open(self):
        for path in ("/pin.html", "/js/pin.js", "/css/pin.css"):
            async with await self.get(path) as r:
                self.assertEqual(r.status, 200, path)

    async def test_the_pin_page_loads_nothing_that_needs_the_pin(self):
        async with await self.get("/pin.html") as r:
            html = await r.text()
        import re

        for ref in re.findall(r'(?:src|href)="([^"]+)"', html):
            if ref.startswith("data:"):
                continue
            self.assertIn(ref, ("/css/pin.css", "/js/pin.js"), ref)

    async def test_nothing_can_be_written_without_the_pin_or_from_a_phone(self):
        for method, path, body in (("PUT", "/api/settings", {"units": {"speed": "mph"}}), ("DELETE", "/api/layouts/u-000000000000", None),
                                   ("PUT", "/api/lan/pin", {"pin": "1111"}), ("POST", "/api/lan/token", {}), ("POST", "/api/trips/A/reset", {})):
            async with self.http.request(method, self.lan_url + path, json=body, headers=H) as r:
                self.assertEqual(r.status, 401, f"{method} {path}")
        self.assertTrue(self.auth.check_pin("4821"), "the PIN is unchanged")

    async def test_the_refusals_carry_the_security_headers(self):
        async with await self.get("/api/layouts") as r:
            self.assertIn("default-src 'none'", r.headers["Content-Security-Policy"])
            self.assertEqual(r.headers["Cache-Control"], "no-store")
        async with await self.get("/", self.page) as r:
            self.assertEqual(r.headers["X-Content-Type-Options"], "nosniff")

    # ---- signing in -------------------------------------------------------------------------

    async def test_the_right_pin_signs_a_phone_in(self):
        async with await self.login("4821") as r:
            self.assertEqual(r.status, 200)
            cookie = r.headers["Set-Cookie"]
        self.assertIn(COOKIE_NAME, cookie)
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Strict", cookie)
        self.assertIn(f"Max-Age={SESSION_SECONDS}", cookie)
        for path in ("/api/layouts", "/api/status", "/api/client"):
            async with await self.get(path) as r:
                self.assertEqual(r.status, 200, path)
        async with await self.get("/", self.page) as r:
            self.assertEqual(r.status, 200, "and the dashboard itself")

    async def test_a_signed_in_phone_gets_the_live_stream(self):
        await (await self.login("4821")).release()
        async with self.http.ws_connect(self.lan_url + "/ws", headers={"Origin": self.lan_url}) as ws:
            self.assertEqual((await ws.receive_json(timeout=2))["type"], "hello")

    async def test_the_wrong_pin_is_refused_and_leaves_no_cookie(self):
        async with await self.login("4822") as r:
            self.assertEqual(r.status, 403)
            self.assertNotIn("Set-Cookie", r.headers)
            self.assertEqual((await r.json())["error"], "Wrong PIN")
        async with await self.get("/api/layouts") as r:
            self.assertEqual(r.status, 401)

    async def test_odd_pins_are_just_wrong(self):
        for pin in ("", None, 4821, ["4821"], {"a": 1}, "4821 ", "x" * 1000):
            self.auth.throttle = Throttle()  # (so that trying several in a row is not itself locked out)
            async with await self.login(pin) as r:
                self.assertEqual(r.status, 403, repr(pin)[:30])

    async def test_a_body_that_is_not_json_or_not_an_object_is_handled(self):
        for data in (b"not json", b"[1, 2]", b"null", b""):
            async with self.http.post(self.lan_url + "/api/pin/login", data=data, headers=H) as r:
                self.assertIn(r.status, (400, 403), data)

    async def test_signing_in_still_needs_the_header_and_a_matching_origin(self):
        async with await self.login("4821", headers={}) as r:
            self.assertEqual(r.status, 403)
        async with await self.login("4821", headers={**H, "Origin": "http://evil.example"}) as r:
            self.assertEqual(r.status, 403, "another site cannot sign a visitor in or guess for them")

    async def test_five_wrong_pins_lock_the_device_out_even_for_the_right_one(self):
        for _ in range(FREE_TRIES):
            async with await self.login("0000") as r:
                self.assertEqual(r.status, 403)
        async with await self.login("4821") as r:
            self.assertEqual(r.status, 429)
            self.assertGreater(int(r.headers["Retry-After"]), 0)
            self.assertNotIn("Set-Cookie", r.headers)
        self.remote = False
        async with self.http.get(self.local + "/api/layouts") as r:
            self.assertEqual(r.status, 200, "this computer is not affected by a phone being locked out")

    async def test_the_lockout_ends(self):
        clock = Clock(0.0)
        self.auth.throttle.clock = clock
        for _ in range(FREE_TRIES):
            await (await self.login("0000")).release()
        async with await self.login("4821") as r:
            self.assertEqual(r.status, 429)
        clock.t += FIRST_LOCKOUT_S + 5
        async with await self.login("4821") as r:
            self.assertEqual(r.status, 200)

    # ---- this computer, and the PIN itself --------------------------------------------------

    async def test_this_computer_never_needs_the_pin(self):
        self.remote = False
        async with self.http.get(self.local + "/api/layouts") as r:
            self.assertEqual(r.status, 200)
        async with self.http.get(self.local + "/", headers=self.page) as r:
            self.assertEqual(r.status, 200)

    async def test_only_this_computer_can_read_or_change_the_pin(self):
        await (await self.login("4821")).release()  # a phone that IS signed in
        async with await self.get("/api/lan/pin") as r:
            self.assertEqual(r.status, 403, "not even a signed-in phone may read it")
        async with self.http.put(self.lan_url + "/api/lan/pin", json={"pin": "1111"}, headers=H) as r:
            self.assertEqual(r.status, 403)
        async with await self.get("/api/settings") as r:
            self.assertNotIn("4821", await r.text(), "the PIN is not in the settings a phone may read")
        self.remote = False
        async with self.http.get(self.local + "/api/lan/pin") as r:
            self.assertEqual(await r.json(), {"set": True, "pin": "4821"})

    async def test_changing_the_pin_signs_every_phone_out_at_once(self):
        await (await self.login("4821")).release()
        ws = await self.http.ws_connect(self.lan_url + "/ws", headers={"Origin": self.lan_url})
        self.assertEqual((await ws.receive_json(timeout=2))["type"], "hello")
        self.remote = False
        async with self.http.put(self.local + "/api/lan/pin", json={"pin": "7777"}, headers=H) as r:
            self.assertEqual(r.status, 200)
        closed = False
        for _ in range(20):
            msg = await ws.receive(timeout=2)
            if msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSING, aiohttp.WSMsgType.CLOSED):
                closed = True
                break
        self.assertTrue(closed, "its live connection ends")
        self.remote = True
        async with await self.get("/api/layouts") as r:
            self.assertEqual(r.status, 401, "and its cookie no longer works")
        async with await self.login("4821") as r:
            self.assertEqual(r.status, 403)
        async with await self.login("7777") as r:
            self.assertEqual(r.status, 200)

    async def test_a_bad_new_pin_is_refused(self):
        self.remote = False
        for pin in ("12", "abcdef", "", None, 4821, "123456789"):
            async with self.http.put(self.local + "/api/lan/pin", json={"pin": pin}, headers=H) as r:
                self.assertEqual(r.status, 400, repr(pin))
        self.assertTrue(self.auth.check_pin("4821"))

    # ---- the QR code ------------------------------------------------------------------------

    async def token(self):
        self.remote = False
        async with self.http.post(self.local + "/api/lan/token", json={}, headers=H) as r:
            self.assertEqual(r.status, 200)
            body = await r.json()
        self.remote = True
        self.assertEqual(body["expiresIn"], TOKEN_SECONDS)
        return body["token"]

    async def test_scanning_the_qr_code_signs_a_phone_in_without_typing(self):
        token = await self.token()
        async with await self.get(f"/?d=cluster&t={token}", self.page) as r:
            self.assertEqual(r.status, 302)
            self.assertEqual(r.headers["Location"], "/?d=cluster", "same page, and the token is gone from the address")
            self.assertIn(COOKIE_NAME, r.headers["Set-Cookie"])
        async with await self.get("/api/layouts") as r:
            self.assertEqual(r.status, 200)

    async def test_a_token_works_once(self):
        token = await self.token()
        await (await self.get(f"/?t={token}", self.page)).release()
        self.http.cookie_jar.clear()  # a different phone
        async with await self.get(f"/?d=cluster&t={token}", self.page) as r:
            self.assertEqual(r.status, 302)
            self.assertTrue(r.headers["Location"].startswith("/pin.html"), "it is asked for the PIN instead")
            self.assertNotIn("Set-Cookie", r.headers)

    async def test_a_token_expires(self):
        token = await self.token()
        real = self.auth._wall
        self.auth._wall = lambda: real() + TOKEN_SECONDS + 5
        async with await self.get(f"/?t={token}", self.page) as r:
            self.assertTrue(r.headers["Location"].startswith("/pin.html"))
            self.assertNotIn("Set-Cookie", r.headers)

    async def test_a_made_up_token_does_nothing_and_guessing_is_slowed(self):
        for _ in range(FREE_TRIES):
            async with await self.get("/?t=guess", self.page) as r:
                self.assertEqual(r.status, 302)
                self.assertTrue(r.headers["Location"].startswith("/pin.html"))
        token = await self.token()
        async with await self.get(f"/?t={token}", self.page) as r:
            self.assertEqual(r.status, 429, "too many bad tokens in a row: even a real one waits")

    async def test_a_crafted_address_cannot_turn_the_redirect_into_a_link_to_another_site(self):
        for crafted in ("//evil.example/x", "///evil.example/x", "////evil.example"):
            token = await self.token()
            async with await self.get(f"{crafted}?t={token}", self.page) as r:
                self.assertEqual(r.status, 302, crafted)
                where = r.headers["Location"]
                self.assertTrue(where.startswith("/") and not where.startswith("//"), f"{crafted} -> {where}")
            self.http.cookie_jar.clear()
        async with await self.get("//evil.example/x?t=guess", self.page) as r:  # and the same for the PIN page's "next"
            self.assertEqual(URL(r.headers["Location"]).path, "/pin.html")

    async def test_a_token_in_an_api_call_is_not_a_way_in(self):
        token = await self.token()
        async with await self.get(f"/api/layouts?t={token}") as r:
            self.assertEqual(r.status, 302, "the token signs a browser in and sends it back to the same address, token removed")
            self.assertEqual(r.headers["Location"], "/api/layouts")

    async def test_only_this_computer_can_ask_for_a_token(self):
        async with self.http.post(self.lan_url + "/api/lan/token", json={}, headers=H) as r:
            self.assertEqual(r.status, 401)
        await (await self.login("4821")).release()
        async with self.http.post(self.lan_url + "/api/lan/token", json={}, headers=H) as r:
            self.assertEqual(r.status, 403, "not even a signed-in phone")

    # ---- the PIN is compulsory --------------------------------------------------------------

    async def test_with_no_pin_network_access_cannot_be_switched_on(self):
        self.remote = False
        await self.lan.disable()
        self.auth.pin = None
        async with self.http.put(self.local + "/api/settings", json={"lan": True}, headers=H) as r:
            self.assertEqual(r.status, 409)
            self.assertIn("PIN", (await r.json())["error"])
        async with self.http.get(self.local + "/api/settings") as r:
            self.assertIs((await r.json())["lan"], False, "nothing was changed")
        self.assertFalse(self.lan.enabled)

    async def test_choosing_a_pin_starts_the_network_access_that_was_waiting_for_one(self):
        from thrashdash.api import SETTINGS

        self.remote = False
        await self.lan.disable()
        self.auth.pin = None
        self.runner.app[SETTINGS].update({"lan": True})  # network access was wanted (say from before the PIN existed) but could not start
        self.assertFalse(self.lan.enabled)
        async with self.http.put(self.local + "/api/lan/pin", json={"pin": "2468"}, headers=H) as r:
            self.assertEqual(r.status, 200)
            self.assertIs((await r.json())["lan"], True)
        self.assertTrue(self.lan.enabled)

    async def test_the_lan_flag_is_honoured_once_a_pin_is_chosen_but_not_remembered(self):
        from thrashdash.api import SETTINGS

        self.remote = False
        await self.lan.disable()
        self.auth.pin = None
        self.lan.start_when_pin_set = True  # what __main__ sets for --lan when no PIN exists yet
        async with self.http.put(self.local + "/api/lan/pin", json={"pin": "2468"}, headers=H) as r:
            self.assertIs((await r.json())["lan"], True)
        self.assertTrue(self.lan.enabled)
        self.assertFalse(self.lan.start_when_pin_set, "used up")
        self.assertIs(self.runner.app[SETTINGS].get()["lan"], False, "a command-line flag does not silently change the saved setting")

    async def test_if_it_is_ever_on_without_a_pin_it_fails_closed(self):
        self.auth.pin = None  # (cannot happen through the menu; this is the safety net)
        async with await self.get("/", self.page) as r:
            self.assertEqual(r.status, 302)
        async with await self.get("/api/layouts") as r:
            self.assertEqual(r.status, 401)
        async with await self.login("4821") as r:
            self.assertEqual(r.status, 503)
        stolen = AuthStore()
        stolen.set_pin("4821")
        self.http.cookie_jar.update_cookies({COOKIE_NAME: stolen.issue_cookie()}, URL(self.lan_url))
        async with await self.get("/api/layouts") as r:
            self.assertEqual(r.status, 401, "a cookie from somewhere else gets nowhere")


if __name__ == "__main__":
    unittest.main()

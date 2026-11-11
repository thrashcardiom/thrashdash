"""Tests for serving to other devices on the network: the host policy, the live on/off switch, remote-device limits."""
import asyncio
import socket
import sys
import tempfile
import unittest
from pathlib import Path

import aiohttp
from yarl import URL
from aiohttp import web

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from thrashdash.auth import COOKIE_NAME, AuthStore  # noqa: E402
from thrashdash.hub import Hub  # noqa: E402
from thrashdash.lan import LanController  # noqa: E402
from thrashdash.netinfo import host_allowed, is_local_client, lan_addresses, parse_host  # noqa: E402
from thrashdash.paths import web_root  # noqa: E402
from thrashdash.server import make_app  # noqa: E402
from thrashdash.store import LayoutStore, SettingsStore  # noqa: E402
from thrashdash.validate import ValidationError, validate_settings  # noqa: E402

H = {"X-ThrashDash": "1"}
PORT = 8765


class HostPolicyTests(unittest.TestCase):
    def test_parse_host(self):
        self.assertEqual(parse_host("127.0.0.1:8765"), ("127.0.0.1", 8765))
        self.assertEqual(parse_host("LocalHost:1"), ("localhost", 1))
        self.assertEqual(parse_host("[::1]:8765"), ("::1", 8765))
        self.assertEqual(parse_host("my-pc"), ("my-pc", None))
        for bad in ("", None, "a b", "evil.com/x", "a@b:1", "::1", "[::1", "host:", "host:abc", ":80", "a:1:2", "host:99999x"):
            self.assertIsNone(parse_host(bad), repr(bad))

    def test_loopback_names_always_allowed_but_only_with_our_port(self):
        for host in ("127.0.0.1:8765", "localhost:8765", "[::1]:8765", "LOCALHOST:8765"):
            for lan in (False, True):
                self.assertTrue(host_allowed(host, PORT, lan), f"{host} lan={lan}")
        for host in ("127.0.0.1", "127.0.0.1:80", "127.0.0.1:8766", "localhost"):
            self.assertFalse(host_allowed(host, PORT, True), host)

    def test_without_network_access_only_loopback_names_pass(self):
        for host in ("192.168.1.20:8765", "my-pc:8765", "my-pc.local:8765", "evil.example:8765", "10.0.0.5:8765"):
            self.assertFalse(host_allowed(host, PORT, False), host)

    def test_with_network_access_ip_addresses_and_private_names_pass(self):
        for host in ("192.168.1.20:8765", "10.0.0.5:8765", "172.16.0.9:8765", "[fe80::1]:8765", "my-pc:8765", "my-pc.local:8765", "printer.lan:8765", "x.home.arpa:8765"):
            self.assertTrue(host_allowed(host, PORT, True), host)

    def test_dns_rebinding_names_are_refused_even_with_network_access(self):
        # A hostile page needs a public domain name; those contain dots and end in a public suffix.
        for host in ("evil.example:8765", "attacker.com:8765", "192.168.1.20.evil.com:8765", "localhost.evil.com:8765", "my-pc.local.evil.com:8765", "a.b.c:8765"):
            self.assertFalse(host_allowed(host, PORT, True), host)

    def test_lan_addresses_are_private_ipv4_only(self):
        found = lan_addresses()
        self.assertEqual(len(found), len(set(found)))
        import ipaddress
        for ip in found:
            a = ipaddress.ip_address(ip)
            self.assertTrue(a.version == 4 and a.is_private and not a.is_loopback and not a.is_link_local, ip)

    def test_local_client_detection(self):
        for ok in ("127.0.0.1", "127.9.9.9", "::1", "::ffff:127.0.0.1"):
            self.assertTrue(is_local_client(ok), ok)
        for bad in ("203.0.113.7", "2001:db8::1", "", None, "garbage", "::ffff:203.0.113.7"):
            self.assertFalse(is_local_client(bad), str(bad))
        for own in lan_addresses():  # typing the PC's own LAN address into the PC's browser is still "this computer"
            self.assertTrue(is_local_client(own), own)


class SettingTests(unittest.TestCase):
    def test_lan_setting_validation(self):
        self.assertEqual(validate_settings({"lan": True}), {"lan": True})
        for bad in ("yes", 1, None, [], {}):
            with self.assertRaises(ValidationError, msg=repr(bad)):
                validate_settings({"lan": bad})

    def test_lan_defaults_off_and_persists(self):
        with tempfile.TemporaryDirectory() as d:
            store = SettingsStore(Path(d) / "s.json")
            self.assertIs(store.get()["lan"], False)
            store.update({"lan": True})
            self.assertIs(SettingsStore(Path(d) / "s.json").get()["lan"], True)


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class NetworkFixture(unittest.IsolatedAsyncioTestCase):
    """A real server. 127.0.0.2 stands in for "a LAN address" (Linux accepts any 127.x.x.x on the loopback device).
    It has a PIN (4821); a test that plays a phone calls sign_in() first, unless it is about signing in."""

    FAKE_LAN = "127.0.0.2"

    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        data = Path(self.tmp.name)
        self.port = free_port()
        self.lan = LanController(self.port, addresses=lambda: [self.FAKE_LAN])
        self.remote = False  # flipped by tests to play a phone: the server then sees clients as "not this computer"
        self.auth = AuthStore()
        self.auth.set_pin("4821")
        app = make_app(Hub(), self.port, LayoutStore(data / "layouts", web_root() / "layouts"), SettingsStore(data / "settings.json"),
                       lan=self.lan, auth=self.auth, local_check=lambda remote: not self.remote and remote is not None)
        self.runner = web.AppRunner(app, access_log=None)
        await self.runner.setup()
        self.lan.attach(self.runner)
        await web.TCPSite(self.runner, "127.0.0.1", self.port).start()
        self.http = aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True))  # (unsafe: cookies for an IP address)
        self.local = f"http://127.0.0.1:{self.port}"
        self.lan_url = f"http://{self.FAKE_LAN}:{self.port}"

    async def asyncTearDown(self):
        await self.http.close()
        await self.runner.cleanup()
        self.tmp.cleanup()

    async def reachable(self, url):
        """Status of a brand-new connection to `url` (None if nothing is listening). A new connection each time, because a
        kept-alive one would reach a listener that has since been closed and be refused by the guard instead."""
        try:
            async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(force_close=True)) as session:
                async with session.get(url + "/api/layouts", timeout=aiohttp.ClientTimeout(total=2)) as r:
                    return r.status
        except (aiohttp.ClientError, asyncio.TimeoutError):
            return None

    def sign_in(self):
        """Make this client a phone that has already entered the PIN."""
        self.remote = True
        self.http.cookie_jar.update_cookies({COOKIE_NAME: self.auth.issue_cookie()}, URL(self.lan_url))



class NetworkServerTests(NetworkFixture):
    async def test_off_by_default_not_even_listening_beyond_this_computer(self):
        self.assertEqual(await self.reachable(self.local), 200)
        self.assertIsNone(await self.reachable(self.lan_url))
        self.assertFalse(self.lan.enabled)

    async def test_switch_on_and_off_without_a_restart(self):
        async with self.http.put(self.local + "/api/settings", json={"lan": True}, headers=H) as r:
            self.assertEqual(r.status, 200)
            self.assertIs((await r.json())["lan"], True)
        self.assertTrue(self.lan.enabled)
        self.assertEqual(await self.reachable(self.lan_url), 200)
        async with self.http.get(self.local + "/api/client") as r:
            body = await r.json()
        self.assertEqual((body["local"], body["lan"]["enabled"], body["lan"]["urls"]), (True, True, [self.lan_url + "/"]))
        async with self.http.put(self.local + "/api/settings", json={"lan": False}, headers=H) as r:
            self.assertEqual(r.status, 200)
        self.assertIsNone(await self.reachable(self.lan_url))
        self.assertEqual(await self.reachable(self.local), 200)  # this computer is unaffected

    async def test_turning_it_on_when_no_address_exists_fails_cleanly_and_does_not_stick(self):
        self.lan._addresses = lambda: []
        async with self.http.put(self.local + "/api/settings", json={"lan": True}, headers=H) as r:
            self.assertEqual(r.status, 409)
            self.assertIn("network", (await r.json())["error"].lower())
        async with self.http.get(self.local + "/api/settings") as r:
            self.assertIs((await r.json())["lan"], False)  # the setting was reverted, not left claiming it is on
        self.assertFalse(self.lan.enabled)

    async def test_client_info_carries_no_firewall_information(self):
        # ThrashDash does not look at the firewall; the answer is just who the client is and what is being served.
        await self.lan.enable()
        async with self.http.get(self.local + "/api/client") as r:
            body = await r.json()
        self.assertEqual(set(body), {"local", "pinSet", "lan"})
        self.assertEqual(set(body["lan"]), {"enabled", "urls"})

    async def test_a_port_that_cannot_be_bound_fails_cleanly(self):
        with socket.socket() as blocker:
            blocker.bind((self.FAKE_LAN, self.port))
            blocker.listen()
            async with self.http.put(self.local + "/api/settings", json={"lan": True}, headers=H) as r:
                self.assertEqual(r.status, 409)
                self.assertIn("could not open the port", (await r.json())["error"].lower())
        self.assertFalse(self.lan.enabled)

    async def test_remote_devices_can_look_but_not_touch(self):
        await self.lan.enable()
        self.sign_in()
        for path in ("/api/layouts", "/api/layouts/default", "/api/settings", "/api/status", "/"):
            async with self.http.get(self.lan_url + path) as r:
                self.assertEqual(r.status, 200, path)
        writes = [("POST", "/api/layouts", {"format": 1, "name": "x", "width": 800, "height": 300, "widgets": []}),
                  ("PUT", "/api/settings", {"units": {"speed": "mph"}}), ("PUT", "/api/settings", {"lan": False}),
                  ("DELETE", "/api/layouts/u-000000000000", None)]
        for method, path, body in writes:
            async with self.http.request(method, self.lan_url + path, json=body, headers=H) as r:
                self.assertEqual(r.status, 403, f"{method} {path}")
        self.assertTrue(self.lan.enabled)  # a phone cannot switch the network off either
        async with self.http.get(self.lan_url + "/api/client") as r:
            body = await r.json()
        self.assertEqual((body["local"], body["lan"]["urls"]), (False, []))  # and is not told the addresses
        self.assertNotIn("pin", body)  # (nor the PIN: only whether one is set)

    async def test_remote_devices_get_the_live_data_stream(self):
        await self.lan.enable()
        self.sign_in()
        async with self.http.ws_connect(self.lan_url + "/ws", headers={"Origin": self.lan_url}) as ws:
            self.assertEqual((await ws.receive_json(timeout=2))["type"], "hello")

    async def test_dns_rebinding_is_refused_even_with_network_access_on(self):
        await self.lan.enable()
        for host in (f"evil.example:{self.port}", f"attacker.com:{self.port}", "127.0.0.1", f"127.0.0.1:{self.port + 1}"):
            async with self.http.get(self.lan_url + "/api/layouts", headers={"Host": host}) as r:
                self.assertEqual(r.status, 403, host)
        # the old --lan weakness: with it on, a rebinding page reaching 127.0.0.1 could use the edit API. No longer.
        async with self.http.put(self.local + "/api/settings", json={"units": {"speed": "mph"}}, headers={**H, "Host": f"evil.example:{self.port}", "Origin": f"http://evil.example:{self.port}"}) as r:
            self.assertEqual(r.status, 403)

    async def test_cross_origin_requests_are_still_refused_on_the_network(self):
        await self.lan.enable()
        async with self.http.get(self.lan_url + "/api/layouts", headers={"Origin": "http://evil.example"}) as r:
            self.assertEqual(r.status, 403)
        with self.assertRaises(aiohttp.WSServerHandshakeError):
            await self.http.ws_connect(self.lan_url + "/ws", headers={"Origin": "http://evil.example"})

    async def test_switching_off_drops_connected_remote_devices(self):
        await self.lan.enable()
        self.sign_in()
        ws = await self.http.ws_connect(self.lan_url + "/ws", headers={"Origin": self.lan_url})
        self.assertEqual((await ws.receive_json(timeout=2))["type"], "hello")
        self.remote = False  # the PC itself now turns the switch off
        async with self.http.put(self.local + "/api/settings", json={"lan": False}, headers=H) as r:
            self.assertEqual(r.status, 200)
        closed = False
        for _ in range(20):
            msg = await ws.receive(timeout=2)
            if msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSING, aiohttp.WSMsgType.CLOSED):
                closed = True
                break
        self.assertTrue(closed, "the phone's live connection should end when network access is switched off")
        await ws.close()

    async def test_without_network_access_a_remote_client_is_refused_even_if_it_gets_through(self):
        self.remote = True  # e.g. a keep-alive connection that outlived the switch
        async with self.http.get(self.local + "/api/layouts") as r:
            self.assertEqual(r.status, 403)
            self.assertIn("switched off", await r.text())


if __name__ == "__main__":
    unittest.main()

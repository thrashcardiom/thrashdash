"""Tests for closing ThrashDash's windows when ThrashDash closes."""
import asyncio
import sys
import tempfile
import unittest
from pathlib import Path

import aiohttp
from aiohttp import web

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from thrashdash.hub import Hub  # noqa: E402
from thrashdash.launcher import stop_profile_browser  # noqa: E402
from thrashdash.paths import web_root  # noqa: E402
from thrashdash.api import QUIT  # noqa: E402
from thrashdash.server import LOCAL_SOCKETS, REMOTE_SOCKETS, make_app  # noqa: E402
from thrashdash.store import LayoutStore, SettingsStore  # noqa: E402
from thrashdash.windows import WindowRegistry  # noqa: E402
from test_triplog import free_port  # noqa: E402

W1 = "abc12345"
DASH = {"dashboard": "cluster", "pinned": True, "width": 1200, "height": 420}


class RegistryTests(unittest.TestCase):
    def test_once_frozen_the_windows_that_then_close_are_not_forgotten(self):
        reg = WindowRegistry(None)
        reg.update(W1, DASH, 1000)
        reg.freeze()
        self.assertFalse(reg.remove(W1, 2000))  # (the window closing because ThrashDash told it to: it comes back next time)
        self.assertIn(W1, reg.entries)
        self.assertFalse(reg.update("def67890", DASH, 3000))  # nor is anything new learned while closing
        self.assertNotIn("def67890", reg.entries)

    def test_freezing_writes_what_is_remembered(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "windows.json"
            reg = WindowRegistry(path)
            reg.update(W1, DASH, 1000)
            reg.update("def67890", {**DASH, "width": 900}, 1000)  # (inside the save-at-most-every-2-seconds window: not yet written)
            reg.freeze()
            self.assertIn("def67890", path.read_text())


class QuitTests(unittest.IsolatedAsyncioTestCase):
    async def start(self, closeWindowsOnExit=None):
        self.tmp = tempfile.TemporaryDirectory()
        data = Path(self.tmp.name)
        settings = SettingsStore(data / "settings.json")
        if closeWindowsOnExit is not None:
            settings.update({"closeWindowsOnExit": closeWindowsOnExit})
        self.registry = WindowRegistry(None)
        self.registry.update(W1, DASH, 1000)
        port = free_port()
        app = make_app(Hub(), port, LayoutStore(data / "layouts", web_root() / "layouts"), settings, windows=self.registry)
        self.runner = web.AppRunner(app, access_log=None)
        await self.runner.setup()
        await web.TCPSite(self.runner, "127.0.0.1", port).start()
        self.base = f"http://127.0.0.1:{port}"
        self.http = aiohttp.ClientSession()

    async def stop(self):
        await self.http.close()
        self.tmp.cleanup()

    async def messages_until_close(self, ws, timeout=3):
        seen = []
        try:
            while True:
                msg = await asyncio.wait_for(ws.receive(), timeout)
                if msg.type == aiohttp.WSMsgType.TEXT:
                    seen.append(msg.json())
                else:
                    break
        except asyncio.TimeoutError:
            pass
        return seen

    async def test_a_dashboard_window_and_the_map_window_are_told_to_quit(self):
        await self.start()
        try:
            async with self.http.ws_connect(f"{self.base}/ws") as dash, self.http.ws_connect(f"{self.base}/ws?feed=geo") as feed:
                await asyncio.sleep(0.2)
                cleanup = asyncio.create_task(self.runner.cleanup())
                got_dash = await self.messages_until_close(dash)
                got_feed = await self.messages_until_close(feed)
                await cleanup
            self.assertIn({"type": "quit"}, got_dash)
            self.assertIn({"type": "quit"}, got_feed)
        finally:
            await self.stop()

    async def test_shutting_down_does_not_wait_for_a_window_that_keeps_its_connection_open(self):
        await self.start(closeWindowsOnExit=False)
        try:
            async with self.http.ws_connect(f"{self.base}/ws") as dash:
                await asyncio.sleep(0.2)
                loop = asyncio.get_running_loop()
                start = loop.time()
                await asyncio.wait_for(self.runner.cleanup(), 10)  # (it used to wait up to a minute for the browser to let go)
                self.assertLess(loop.time() - start, 5)
        finally:
            await self.stop()

    async def test_it_can_be_switched_off(self):
        await self.start(closeWindowsOnExit=False)
        try:
            async with self.http.ws_connect(f"{self.base}/ws") as dash:
                await asyncio.sleep(0.2)
                cleanup = asyncio.create_task(self.runner.cleanup())
                got = await self.messages_until_close(dash)
                await cleanup
            self.assertNotIn({"type": "quit"}, got)
        finally:
            await self.stop()

    async def test_a_phone_is_not_told(self):
        await self.start()
        try:
            sent = {"local": [], "remote": []}

            class Fake:
                def __init__(self, bucket):
                    self.bucket = bucket

                async def send_json(self, data):
                    sent[self.bucket].append(data)

                async def close(self, code=1000, message=b""):
                    sent[self.bucket].append(("closed", code))

            app = self.runner.app
            app[LOCAL_SOCKETS].add(Fake("local"))
            app[REMOTE_SOCKETS].add(Fake("remote"))
            await app.shutdown()
            self.assertEqual(sent, {"local": [{"type": "quit"}, ("closed", 1001)], "remote": [("closed", 1001)]})  # (the phone is not told to quit, but is let go)
        finally:
            await self.stop()

    async def test_the_windows_closed_this_way_are_still_remembered(self):
        await self.start()
        try:
            async with self.http.ws_connect(f"{self.base}/ws") as dash:
                await asyncio.sleep(0.2)
                cleanup = asyncio.create_task(self.runner.cleanup())
                await self.messages_until_close(dash)
                # the window, closing, says so (as a page does when it goes away): the relay must not forget it
                try:
                    async with self.http.delete(f"{self.base}/api/windows/{W1}?t=99999", headers={"X-ThrashDash": "1"}) as r:
                        await r.read()
                except aiohttp.ClientError:
                    pass
                await cleanup
            self.assertIn(W1, self.registry.entries)
        finally:
            await self.stop()


class QuitButtonTests(QuitTests):
    async def test_the_quit_button_stops_thrashdash_after_answering(self):
        await self.start()
        try:
            stopped = asyncio.Event()
            self.runner.app[QUIT]["stop"] = stopped.set
            async with self.http.post(f"{self.base}/api/quit", json={}, headers={"X-ThrashDash": "1"}) as r:
                self.assertEqual(r.status, 200)  # (it answers first: the page that asked is not left waiting on a relay that has gone)
                self.assertEqual(await r.json(), {"ok": True})
            await asyncio.wait_for(stopped.wait(), 2)
        finally:
            await self.stop()

    async def test_it_needs_the_write_header_like_every_change(self):
        await self.start()
        try:
            stopped = asyncio.Event()
            self.runner.app[QUIT]["stop"] = stopped.set
            async with self.http.post(f"{self.base}/api/quit", json={}) as r:
                self.assertEqual(r.status, 403)
            await asyncio.sleep(0.5)
            self.assertFalse(stopped.is_set())
        finally:
            await self.stop()

    async def test_a_run_that_cannot_be_stopped_this_way_says_so(self):
        await self.start()
        try:
            async with self.http.post(f"{self.base}/api/quit", json={}, headers={"X-ThrashDash": "1"}) as r:
                self.assertEqual(r.status, 501)
        finally:
            await self.stop()


class FallbackTests(unittest.TestCase):
    def test_it_only_touches_a_browser_started_with_thrashdashs_own_profile(self):
        self.assertEqual(stop_profile_browser("a-profile-nothing-uses-9f3b2"), 0)


if __name__ == "__main__":
    unittest.main()

"""Tests for the live map's position feed: the game's ten-a-second positions, handed to the Trips page over a light websocket."""
import asyncio
import json
import socket
import sys
import tempfile
import unittest
from pathlib import Path

import aiohttp
from aiohttp import web

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from thrashdash.hub import Hub  # noqa: E402
from thrashdash.paths import web_root  # noqa: E402
from thrashdash.server import WINDOW_COUNTER, make_app  # noqa: E402
from thrashdash.store import LayoutStore, SettingsStore  # noqa: E402
from thrashdash.udp import start_listener  # noqa: E402
from test_triplog import free_port  # noqa: E402


def packet(**kw):
    p = {"v": 1, "type": "geolive", "rt": 10.0, "lat": -41.2, "lon": 174.8, "hdg": 90.0, "spd": 12.5}
    p.update(kw)
    return p


class HubTests(unittest.TestCase):
    def test_a_position_is_passed_on_as_it_is(self):
        hub = Hub()
        hub.ingest_packet(packet())
        m, = hub.live
        self.assertEqual((m["type"], m["lat"], m["lon"], m["hdg"], m["spd"], m["rt"]), ("geo", -41.2, 174.8, 90.0, 12.5, 10.0))
        self.assertEqual(hub.stats["live"], 1)

    def test_it_is_not_a_trip_sample_and_does_not_disturb_the_car(self):
        from thrashdash.triplog import TripLog
        with tempfile.TemporaryDirectory() as d:
            log = TripLog(Path(d) / "t", say=lambda t: None)
            hub = Hub(triplog=log)
            for i in range(20):
                hub.ingest_packet(packet(rt=float(i), lat=-41.2 + i * 0.0001))
            self.assertEqual(log.status(), {"recording": False})  # nothing recorded from live positions
            self.assertEqual(hub.stats["geo"], 0)
            self.assertFalse(hub.connected)  # (and it does not make a car look connected)
            self.assertEqual(len(hub.live), 20)

    def test_numbering_and_since(self):
        hub = Hub()
        for i in range(5):
            hub.ingest_packet(packet(rt=float(i)))
        self.assertEqual([m["seq"] for m in hub.live_since(0)], [1, 2, 3, 4, 5])
        self.assertEqual([m["seq"] for m in hub.live_since(3)], [4, 5])
        self.assertEqual(hub.live_since(5), [])

    def test_only_the_latest_few_are_kept(self):
        hub = Hub()
        for i in range(300):
            hub.ingest_packet(packet(rt=float(i)))
        self.assertEqual(len(hub.live), 128)
        self.assertEqual(hub.live_seq, 300)

    def test_bad_positions_are_rejected_and_odd_extras_dropped(self):
        hub = Hub()
        for bad in (packet(lat=None), packet(lat=91), packet(lon=181), packet(rt="x"), packet(lat=float("nan")), packet(lon=True), {"v": 1, "type": "geolive"}):
            hub.ingest_packet(bad)
        self.assertEqual(len(hub.live), 0)
        self.assertEqual(hub.stats["rejected_live"], 7)
        hub.ingest_packet(packet(hdg="north", spd=99999))
        m, = hub.live
        self.assertNotIn("hdg", m)
        self.assertNotIn("spd", m)
        hub.ingest_packet(packet(hdg=-90, rt=11.0))
        self.assertEqual(hub.live[-1]["hdg"], 270.0)


class FeedTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        data = Path(self.tmp.name)
        self.hub = Hub()
        self.port = free_port()
        self.app = make_app(self.hub, self.port, LayoutStore(data / "layouts", web_root() / "layouts"), SettingsStore(data / "settings.json"))
        self.runner = web.AppRunner(self.app, access_log=None)
        await self.runner.setup()
        await web.TCPSite(self.runner, "127.0.0.1", self.port).start()
        self.http = aiohttp.ClientSession()
        self.url = f"http://127.0.0.1:{self.port}/ws?feed=geo"

    async def asyncTearDown(self):
        await self.http.close()
        await self.runner.cleanup()
        self.tmp.cleanup()

    async def test_the_feed_gives_positions_and_only_positions(self):
        async with self.http.ws_connect(self.url) as ws:
            hello = await asyncio.wait_for(ws.receive_json(), 3)
            self.assertEqual((hello["type"], hello.get("feed")), ("hello", "geo"))
            self.hub.ingest_packet({"v": 1, "type": "frame", "vehicle": "x", "electrics": {"rpm": 1}})
            for i in range(5):
                self.hub.ingest_packet(packet(rt=float(i), lat=-41.2 + i * 0.0001))
            got = []
            while len(got) < 5:
                got.append(await asyncio.wait_for(ws.receive_json(), 3))
            self.assertEqual({m["type"] for m in got}, {"geo"})  # no status, catalog or telemetry frames
            self.assertEqual([m["rt"] for m in got], [0.0, 1.0, 2.0, 3.0, 4.0])

    async def test_trip_starts_and_ends_come_down_the_same_feed(self):
        async with self.http.ws_connect(self.url) as ws:
            await asyncio.wait_for(ws.receive_json(), 3)
            self.hub.add_trip_event("started", "20261114T000000Z")
            self.hub.ingest_packet(packet(rt=1.0))
            self.hub.add_trip_event("ended", "20261114T000000Z")
            got = [await asyncio.wait_for(ws.receive_json(), 3) for _ in range(3)]
        self.assertEqual([(m["type"], m.get("event")) for m in got], [("trip", "started"), ("geo", None), ("trip", "ended")])
        self.assertEqual(got[0]["id"], "20261114T000000Z")
        self.assertEqual([m["seq"] for m in got], sorted(m["seq"] for m in got))

    async def test_a_map_opened_now_does_not_get_old_positions(self):
        for i in range(5):
            self.hub.ingest_packet(packet(rt=float(i)))
        async with self.http.ws_connect(self.url) as ws:
            await asyncio.wait_for(ws.receive_json(), 3)  # hello
            self.hub.ingest_packet(packet(rt=99.0))
            msg = await asyncio.wait_for(ws.receive_json(), 3)
            self.assertEqual(msg["rt"], 99.0)

    async def test_it_is_fast(self):
        async with self.http.ws_connect(self.url) as ws:
            await asyncio.wait_for(ws.receive_json(), 3)
            loop = asyncio.get_running_loop()
            start = loop.time()
            self.hub.ingest_packet(packet(rt=1.0))
            await asyncio.wait_for(ws.receive_json(), 3)
            self.assertLess(loop.time() - start, 0.15)  # (a position reaches the page within a few frames of arriving)

    async def test_the_feed_is_not_a_dashboard_window(self):
        counter = self.app[WINDOW_COUNTER]
        before = counter.count if hasattr(counter, "count") else None
        async with self.http.ws_connect(self.url) as ws:
            await asyncio.wait_for(ws.receive_json(), 3)
            during = counter.count if hasattr(counter, "count") else None
        self.assertEqual(before, during)

    async def test_the_ordinary_socket_does_not_get_live_positions(self):
        async with self.http.ws_connect(f"http://127.0.0.1:{self.port}/ws") as ws:
            self.hub.ingest_packet(packet(rt=1.0))
            seen = []
            try:
                for _ in range(10):
                    seen.append(await asyncio.wait_for(ws.receive_json(), 0.3))
            except asyncio.TimeoutError:
                pass
            self.assertNotIn("geo", {m.get("type") for m in seen})


class PageLogTests(FeedTests):
    async def test_the_trips_page_can_leave_notes_that_are_kept_and_bounded(self):
        base = f"http://127.0.0.1:{self.port}/api/pagelog"
        h = {"X-ThrashDash": "1"}
        async with self.http.post(base, json={"lines": ["09:00:00.000 started", 5, "x" * 1000]}, headers=h) as r:
            self.assertEqual(r.status, 200)
        async with self.http.get(base) as r:
            lines = (await r.json())["lines"]
        self.assertEqual(lines[0], "09:00:00.000 started")
        self.assertEqual(len(lines), 2)  # (the number was not text and was left out; the long line was cut)
        self.assertEqual(len(lines[1]), 300)
        for bad in ({"lines": "text"}, {"lines": ["x"] * 51}, ["x"]):
            async with self.http.post(base, json=bad, headers=h) as r:
                self.assertEqual(r.status, 400)
        for i in range(10):
            async with self.http.post(base, json={"lines": [f"n{i}-{j}" for j in range(50)]}, headers=h) as r:
                self.assertEqual(r.status, 200)
        async with self.http.get(base) as r:
            self.assertEqual(len((await r.json())["lines"]), 400)  # (only the latest are kept)
        async with self.http.post(base, json={"lines": ["x"]}) as r:
            self.assertEqual(r.status, 403)  # (like every write: only this computer, with the header)


class UdpToFeedTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_position_sent_by_the_game_reaches_the_page_and_the_game_is_known(self):
        from thrashdash.tripnotice import TripNotices
        with tempfile.TemporaryDirectory() as d:
            data = Path(d)
            settings = SettingsStore(data / "settings.json")
            notices = TripNotices(settings)
            hub = Hub()
            udp_port = free_port()
            transport = await start_listener(hub, "127.0.0.1", udp_port, notices)
            http_port = free_port()
            runner = web.AppRunner(make_app(hub, http_port, LayoutStore(data / "layouts", web_root() / "layouts"), settings, notices=notices), access_log=None)
            await runner.setup()
            await web.TCPSite(runner, "127.0.0.1", http_port).start()
            game = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            game.bind(("127.0.0.1", 0))  # (as the game's extension binds its own)
            try:
                async with aiohttp.ClientSession() as http, http.ws_connect(f"http://127.0.0.1:{http_port}/ws?feed=geo") as ws:
                    await asyncio.wait_for(ws.receive_json(), 3)
                    game.sendto(json.dumps(packet(rt=5.0)).encode(), ("127.0.0.1", udp_port))
                    msg = await asyncio.wait_for(ws.receive_json(), 3)
                    self.assertEqual((msg["lat"], msg["lon"], msg["rt"]), (-41.2, 174.8, 5.0))
                self.assertEqual(notices.game_addr, game.getsockname())  # (live positions tell the relay where to send popups, too)
            finally:
                game.close()
                transport.close()
                await runner.cleanup()


if __name__ == "__main__":
    unittest.main()

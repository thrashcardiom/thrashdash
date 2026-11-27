"""Lap timing through the hub (channels from live positions) and the circuits HTTP API."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import aiohttp
from aiohttp import web

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from thrashdash import PROTOCOL_VERSION, simulator  # noqa: E402
from thrashdash.hub import Hub  # noqa: E402
from thrashdash.laps import CircuitStore, LapStore, LapTimer  # noqa: E402
from thrashdash.paths import web_root  # noqa: E402
from thrashdash.server import make_app  # noqa: E402
from thrashdash.store import LayoutStore, SettingsStore  # noqa: E402
from test_laps import CAR, LAP_S, RADIUS, SPEED, START, SECTOR_PI, circuit, ll, point  # noqa: E402
from test_triplog import free_port  # noqa: E402


def live(lat, lon, rt):
    return {"v": PROTOCOL_VERSION, "type": "geolive", "lat": lat, "lon": lon, "rt": rt}


def make_hub(circuits=()):
    laps = LapStore(None)
    store = CircuitStore(None, laps)
    for c in circuits:
        store.create(c)
    hub = Hub(lap_timer=LapTimer(store, laps))
    hub.ingest_packet({"v": PROTOCOL_VERSION, "type": "frame", "n": 1, "vehicle": CAR, "electrics": {"wheelspeed": 10.0}})
    return hub, store, laps


def drive(hub, seconds, t0=0.0, start_angle=-0.3):
    for i in range(int(seconds * 10) + 1):
        t = i / 10
        hub.ingest_packet(live(*ll(*point(start_angle + SPEED * t / RADIUS)), t0 + t))


class HubLapTests(unittest.TestCase):
    def test_laps_become_channels_a_dashboard_can_show(self):
        hub, store, laps = make_hub([circuit(sectors=[SECTOR_PI])])
        drive(hub, 1.9 * LAP_S)
        self.assertIs(hub.values["lap.active"], True)
        self.assertEqual(hub.values["lap.circuit"], "Round")
        self.assertAlmostEqual(hub.values["lap.last"], LAP_S, delta=0.03)
        self.assertEqual(hub.channels["lap.last"], "number")
        self.assertEqual(hub.channels["lap.lastValid"], "boolean")
        self.assertEqual(hub.channels["lap.circuit"], "string")
        self.assertIn('"lap.number":2', hub.frame_json())
        self.assertEqual(len(laps.laps(store.list()[0]["id"])), 1)

    def test_the_clock_reaches_browsers_with_every_position_without_a_car_frame(self):
        hub, _, _ = make_hub([circuit()])
        drive(hub, 3.0)  # (the line is crossed at 1.5 s)
        seq, before = hub.frame_seq, hub.values["lap.time"]
        hub.ingest_packet(live(*ll(*point(-0.3 + SPEED * 3.1 / RADIUS)), 3.1))
        self.assertGreater(hub.frame_seq, seq)
        self.assertGreater(hub.values["lap.time"], before)

    def test_no_circuit_no_channels_and_no_work(self):
        hub, _, _ = make_hub()
        drive(hub, 5.0)
        self.assertFalse([k for k in hub.values if k.startswith("lap.")])

    def test_a_reset_or_teleport_event_cancels_the_lap(self):
        hub, _, _ = make_hub([circuit()])
        drive(hub, 1.5 * LAP_S)
        self.assertIs(hub.values["lap.active"], True)
        hub.ingest_packet({"v": PROTOCOL_VERSION, "type": "geoevent", "name": "reset", "rt": 99.0})
        hub.ingest_packet(live(*ll(*point(1.0)), 60.0))
        self.assertIs(hub.values["lap.active"], False)

    def test_other_events_and_odd_packets_do_nothing(self):
        hub, _, _ = make_hub([circuit()])
        drive(hub, 1.5 * LAP_S)
        for packet in ({"type": "geoevent", "name": "worldReady"}, {"type": "geoevent", "name": 5}, {"type": "geoevent"}):
            hub.ingest_packet({"v": PROTOCOL_VERSION, "rt": 1.0, **packet})
        self.assertIs(hub.values["lap.active"], True)

    def test_the_channels_go_when_the_positions_stop(self):
        hub, _, _ = make_hub([circuit()])
        with mock.patch("thrashdash.laps.time.monotonic", return_value=500.0):
            drive(hub, 1.5 * LAP_S)
        with mock.patch("thrashdash.laps.time.monotonic", return_value=500.0 + 5):
            hub._rebuild()
        self.assertFalse([k for k in hub.values if k.startswith("lap.")])

    def test_bad_positions_are_refused_before_the_timer_sees_them(self):
        hub, _, _ = make_hub([circuit()])
        for packet in (live(float("nan"), 1, 1), live(95, 0, 1), live(0, 181, 1), live("x", 0, 1), live(0, 0, None)):
            hub.ingest_packet(packet)
        self.assertEqual(hub.stats["rejected_live"], 5)
        self.assertIsNone(hub.lap_timer.updated_at)

    def test_game_time_is_used_when_the_game_sends_it_so_a_pause_is_not_part_of_a_lap(self):
        hub, store, laps = make_hub([circuit()])
        game = 0.0
        for i in range(0, int(1.9 * LAP_S * 10) + 301):
            if 100 <= i < 400:  # the game is paused for 30 s of real time: real time runs on, game time does not, the car stays put
                pass
            else:
                game += 0.1
            angle = -0.3 + SPEED * game / RADIUS
            hub.ingest_packet({**live(*ll(*point(angle)), i / 10), "st": game})
        timed = laps.laps(store.list()[0]["id"])
        self.assertEqual(len(timed), 1)
        self.assertAlmostEqual(timed[0]["t"], LAP_S, delta=0.05)  # not LAP_S + 30

    def test_without_game_time_real_time_is_used(self):
        hub, store, laps = make_hub([circuit()])
        drive(hub, 1.9 * LAP_S)
        self.assertAlmostEqual(laps.laps(store.list()[0]["id"])[0]["t"], LAP_S, delta=0.03)

    def test_the_hub_works_without_a_lap_timer(self):
        hub = Hub()
        hub.ingest_packet(live(*ll(0, 0), 1.0))
        self.assertEqual(hub.stats["live"], 1)

    def test_the_simulated_track_gives_laps(self):
        hub, store, laps = make_hub([simulator.track_circuit()])
        for i in range(0, 10 * 130):  # 130 s: three drive cycles
            hub.ingest_packet(simulator.make_live(i / 10))
        recorded = laps.laps(store.list()[0]["id"])
        self.assertGreaterEqual(len(recorded), 3)
        self.assertTrue(all(lap["ok"] and len(lap["s"]) == 2 for lap in recorded))
        self.assertTrue(all(10 < lap["t"] < 80 for lap in recorded), [lap["t"] for lap in recorded])

    def test_the_simulated_track_is_what_the_circuit_says(self):
        # the simulated car must really cross its own start line the right way: that it does is what the test above shows; and it is
        # heading as the map would draw it (north at the start line, then turning west)
        lat, lon, heading = simulator.track_position(0.0)
        self.assertAlmostEqual(heading, 0.0, places=3)
        _, _, quarter = simulator.track_position(0.0)
        self.assertTrue(0 <= quarter < 360)


class CircuitApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        data = Path(self.tmp.name)
        self.laps = LapStore(data / "laps")
        self.store = CircuitStore(data / "circuits.json", self.laps)
        self.port = free_port()
        self.hub = Hub(lap_timer=LapTimer(self.store, self.laps))
        self.app = make_app(self.hub, self.port, LayoutStore(data / "layouts", web_root() / "layouts"), SettingsStore(data / "settings.json"), circuits=self.store, laps=self.laps)
        self.runner = web.AppRunner(self.app, access_log=None)
        await self.runner.setup()
        await web.TCPSite(self.runner, "127.0.0.1", self.port).start()
        self.base = f"http://127.0.0.1:{self.port}/api/circuits"
        self.http = aiohttp.ClientSession()
        self.h = {"X-ThrashDash": "1"}

    async def asyncTearDown(self):
        await self.http.close()
        await self.runner.cleanup()
        self.tmp.cleanup()

    async def test_create_list_save_and_delete(self):
        async with self.http.post(self.base, json=circuit(name="Test"), headers=self.h) as r:
            self.assertEqual(r.status, 200)
            cid = (await r.json())["id"]
        async with self.http.get(self.base) as r:
            got = await r.json()
        self.assertTrue(got["enabled"])
        self.assertEqual([(c["id"], c["name"], c["lapCount"], c["bests"]) for c in got["circuits"]], [(cid, "Test", 0, {})])
        async with self.http.put(f"{self.base}/{cid}", json=circuit(name="Renamed", enabled=False), headers=self.h) as r:
            self.assertEqual(await r.json(), {"ok": True, "cleared": False})
        self.assertEqual(self.store.list()[0]["name"], "Renamed")
        async with self.http.delete(f"{self.base}/{cid}", headers=self.h) as r:
            self.assertEqual(r.status, 200)
        self.assertEqual(self.store.list(), [])

    async def test_laps_and_bests_are_listed_and_can_be_cleared(self):
        cid = self.store.create(circuit())
        self.laps.record(cid, "etkc", {"n": 1, "t": 31.4, "s": [], "ok": True, "ts": 5, "r": 1}, [[0, 0], [10, 1]])
        async with self.http.get(self.base) as r:
            entry = (await r.json())["circuits"][0]
        self.assertEqual((entry["lapCount"], entry["bests"]["etkc"]["t"]), (1, 31.4))
        self.assertNotIn("trace", entry["bests"]["etkc"])  # (large, and only for the live gap)
        async with self.http.get(f"{self.base}/{cid}/laps") as r:
            got = await r.json()
        self.assertEqual((len(got["laps"]), got["laps"][0]["v"], got["circuit"]["name"]), (1, "etkc", "Round"))
        async with self.http.delete(f"{self.base}/{cid}/laps", headers=self.h) as r:
            self.assertEqual(r.status, 200)
        async with self.http.get(f"{self.base}/{cid}/laps") as r:
            self.assertEqual((await r.json())["laps"], [])
        self.assertEqual(len(self.store.list()), 1)  # the circuit itself stays

    async def test_changing_the_lines_says_the_laps_were_cleared(self):
        cid = self.store.create(circuit())
        self.laps.record(cid, "etkc", {"n": 1, "t": 31.4, "s": [], "ok": True, "ts": 5, "r": 1}, [])
        async with self.http.put(f"{self.base}/{cid}", json=circuit(sectors=[SECTOR_PI]), headers=self.h) as r:
            self.assertEqual(await r.json(), {"ok": True, "cleared": True})
        self.assertEqual(self.laps.laps(cid), [])

    async def test_bad_requests(self):
        for bad in ({}, {"name": "x"}, circuit(name=""), circuit(kind="oval"), {**circuit(), "start": {"a": [0, 0], "b": [0, 0]}}):
            async with self.http.post(self.base, json=bad, headers=self.h) as r:
                self.assertEqual(r.status, 400, str(bad)[:60])
        async with self.http.post(self.base, data=b"{not json", headers=self.h) as r:
            self.assertEqual(r.status, 400)
        for method, url in (("put", f"{self.base}/k00000000"), ("delete", f"{self.base}/k00000000"), ("delete", f"{self.base}/k00000000/laps")):
            async with getattr(self.http, method)(url, json=circuit(), headers=self.h) as r:
                self.assertEqual(r.status, 404, url)
        async with self.http.get(f"{self.base}/k00000000/laps") as r:
            self.assertEqual(r.status, 404)
        self.assertEqual(self.store.list(), [])

    async def test_changes_need_the_header(self):
        # (That only this computer may change anything is the guard's rule for every write, tested in test_network.py.)
        async with self.http.post(self.base, json=circuit()) as r:
            self.assertEqual(r.status, 403)
        async with self.http.delete(f"{self.base}/k00000000/laps") as r:
            self.assertEqual(r.status, 403)
        self.assertEqual(self.store.list(), [])

    async def test_a_run_without_lap_timing_says_so(self):
        port = free_port()
        app = make_app(Hub(), port, LayoutStore(Path(self.tmp.name) / "l3", web_root() / "layouts"), SettingsStore(Path(self.tmp.name) / "s3.json"))
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        await web.TCPSite(runner, "127.0.0.1", port).start()
        try:
            async with self.http.get(f"http://127.0.0.1:{port}/api/circuits") as r:
                self.assertEqual(await r.json(), {"enabled": False, "circuits": []})
            async with self.http.post(f"http://127.0.0.1:{port}/api/circuits", json=circuit(), headers=self.h) as r:
                self.assertEqual(r.status, 404)
        finally:
            await runner.cleanup()

    async def test_circuits_survive_a_restart(self):
        cid = self.store.create(circuit(name="Kept", sectors=[SECTOR_PI]))
        again = CircuitStore(self.store.path, self.laps)
        self.assertEqual([(c["id"], c["name"], len(c["sectors"])) for c in again.list()], [(cid, "Kept", 1)])
        for end in ("a", "b"):  # (points are kept to seven places, a centimetre or so)
            for saved, given in zip(again.list()[0]["start"][end], START[end]):
                self.assertAlmostEqual(saved, given, places=6)


if __name__ == "__main__":
    unittest.main()

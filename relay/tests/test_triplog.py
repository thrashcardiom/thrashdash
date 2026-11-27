"""Tests for the trip log: how a stream of position samples from BeamNG.Earth becomes trips on disk.

The drives here are synthetic but copy what a real recording of the game showed (see triplog.py): not-ready samples with
a nonsense speed, a reset to the spawn point that jumps 1.4 km without any event, a teleport that is announced, and
place names that arrive separately.
"""
import json
import math
import socket
import sys
import tempfile
import unittest
from pathlib import Path

import aiohttp
from aiohttp import web

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from thrashdash import triplog  # noqa: E402
from thrashdash.hub import Hub  # noqa: E402
from thrashdash.paths import web_root  # noqa: E402
from thrashdash.server import make_app  # noqa: E402
from thrashdash.store import LayoutStore, SettingsStore  # noqa: E402
from thrashdash.triplog import TripLog, haversine_m  # noqa: E402

M_PER_DEG_LAT = 111195.0  # metres per degree of latitude on the sphere haversine_m uses
T0 = 1794600000  # a unix time to start every drive from


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


class Driver:
    """Feeds a TripLog the way the game extension would: one sample per second of game time."""

    def __init__(self, log, lat=-41.2, lon=174.8, veh=7, model="etkc"):
        self.log, self.lat, self.lon, self.veh, self.model = log, lat, lon, veh, model
        self.rt = 100.0
        self.car = {}

    def sample(self, speed=0.0, ready=True, dt=1.0, heading=0.0, **extra):
        """Advance dt seconds, moving north at `speed` m/s (the position moves only when ready)."""
        self.rt += dt
        if ready:
            self.lat += speed * dt / M_PER_DEG_LAT
        packet = {"v": 1, "type": "geo", "rt": self.rt, "ts": T0 + int(self.rt), "lat": self.lat, "lon": self.lon, "alt": 3.0,
                  "spd": speed if ready else 99900.9, "hdg": heading, "ready": ready, "veh": self.veh, "model": self.model}
        packet.update(extra)
        self.log.ingest(packet, self.car)

    def drive(self, seconds, speed=15.0, **kw):
        for _ in range(int(seconds)):
            self.sample(speed, **kw)

    def park(self, seconds):
        for _ in range(int(seconds)):
            self.sample(0.0)

    def jump(self, metres, ready_samples=0):
        """Move the car metres north between two samples (a reset to spawn, or a teleport), loading for a few samples first."""
        for _ in range(ready_samples):
            self.sample(0.0, ready=False)
        self.lat += metres / M_PER_DEG_LAT

    def event(self, name, **extra):
        self.log.ingest({"v": 1, "type": "geoevent", "name": name, "rt": self.rt, "ts": T0 + int(self.rt), "lat": self.lat, "lon": self.lon, **extra})


def lines(path):
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]


class LogCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name) / "triplog"
        self.clock = Clock()
        self.said = []
        self.log = TripLog(self.dir, clock=self.clock, say=self.said.append)
        self.d = Driver(self.log)

    def tearDown(self):
        self.log.close_all()  # (releases the file of a trip a test left open)
        self.tmp.cleanup()

    def files(self):
        return sorted(self.dir.glob("*.jsonl")) if self.dir.exists() else []

    def finished(self):
        """Every trip as (header, points, end) once the log is closed."""
        self.log.close_all()
        out = []
        for path in self.files():
            rows = lines(path)
            out.append((rows[0], [r for r in rows if r["k"] == "p"], rows[-1]))
        return out


class RulesTests(LogCase):
    def test_a_drive_becomes_one_trip_with_the_right_distance(self):
        self.d.drive(120, speed=15)  # 1800 m
        (head, points, end), = self.finished()
        self.assertEqual(head["k"], "trip")
        self.assertEqual(head["vehicle"], "etkc")
        self.assertEqual(end["k"], "end")
        self.assertEqual(end["reason"], "shutdown")
        self.assertAlmostEqual(end["dist"], 1800, delta=40)
        self.assertAlmostEqual(end["top"], 15, places=1)

    def test_the_trip_starts_where_the_car_stood(self):
        self.d.park(5)
        start_lat = self.d.lat
        self.d.drive(60, speed=10)
        (head, points, end), = self.finished()
        self.assertAlmostEqual(points[0]["la"], start_lat, places=5)  # the parked spot, not a metre or two later
        self.assertEqual(points[0]["d"], 0)

    def test_thinning_keeps_the_distance(self):
        self.d.park(2)
        self.d.drive(300, speed=3.0)  # 900 m at walking pace: several raw samples per kept point
        (_, points, end), = self.finished()
        self.assertLess(len(points), 300)
        self.assertGreater(len(points), 30)
        self.assertAlmostEqual(points[-1]["d"], 900, delta=20)
        self.assertAlmostEqual(end["dist"], 900, delta=20)

    def test_a_corner_keeps_its_shape(self):
        self.d.park(1)
        for i in range(40):  # a tight crawl (80 m in all) that distance alone would thin away, turning 15 degrees a second
            self.d.sample(2.0, heading=i * 15.0)
        (_, points, _), = self.finished()
        self.assertGreaterEqual(len(points), 30)

    def test_samples_that_are_not_ready_are_dropped(self):
        self.d.drive(60, speed=12)
        for _ in range(4):
            self.d.sample(0.0, ready=False)  # the game reports 99,900 m/s here
        self.d.drive(60, speed=12)
        (_, points, end), = self.finished()
        self.assertLess(end["top"], 20)  # the nonsense speed never became the top speed
        self.assertTrue(all(p.get("s", 0) < 20 for p in points))
        self.assertEqual(self.log.stats["not_ready"], 4)

    def test_an_unannounced_jump_splits_the_trip(self):
        """The reset to the spawn point: 1.4 km away, four loading samples, no teleport event."""
        self.d.drive(60, speed=15)
        self.d.jump(1400, ready_samples=4)
        self.d.drive(60, speed=15)
        trips = self.finished()
        self.assertEqual(len(trips), 2)
        self.assertEqual(trips[0][2]["reason"], "jump")
        self.assertAlmostEqual(trips[0][2]["dist"], 900, delta=30)  # the 1.4 km was not driven
        self.assertAlmostEqual(trips[1][2]["dist"], 900, delta=60)

    def test_a_jump_between_two_ready_samples_splits_too(self):
        self.d.drive(60, speed=15)
        self.d.jump(5000)  # no loading samples at all
        self.d.drive(60, speed=15)
        trips = self.finished()
        self.assertEqual(len(trips), 2)
        self.assertEqual(trips[0][2]["reason"], "jump")

    def test_a_short_loading_gap_with_no_move_does_not_split(self):
        self.d.drive(60, speed=15)
        for _ in range(3):
            self.d.sample(0.0, ready=False)
        self.d.drive(60, speed=15)
        self.assertEqual(len(self.finished()), 1)

    def test_a_teleport_event_ends_the_trip(self):
        self.d.drive(60, speed=15)
        self.d.event("teleported")
        self.d.jump(8700, ready_samples=1)
        self.d.drive(60, speed=15)
        trips = self.finished()
        self.assertEqual([t[2]["reason"] for t in trips], ["teleport", "shutdown"])
        self.assertEqual(self.log.stats["jumps"], 0)  # the event already ended it, so the jump needed no detecting

    def test_the_r_key_reset_does_not_split(self):
        self.d.drive(60, speed=15)
        self.d.event("reset")
        self.d.drive(60, speed=15)
        trips = self.finished()
        self.assertEqual(len(trips), 1)
        rows = lines(self.files()[0])
        self.assertIn("reset", [r.get("name") for r in rows if r["k"] == "e"])

    def test_another_vehicle_is_another_trip(self):
        self.d.drive(60, speed=15)
        self.d.veh, self.d.model = 8, "pickup"
        self.d.drive(60, speed=15)
        trips = self.finished()
        self.assertEqual([t[0]["vehicle"] for t in trips], ["etkc", "pickup"])
        self.assertEqual(trips[0][2]["reason"], "vehicle")

    def test_parking_for_a_long_time_ends_the_trip_at_the_last_movement(self):
        self.d.drive(60, speed=15)
        self.d.park(660)  # 11 minutes
        self.d.drive(60, speed=15)  # the next trip
        trips = self.finished()
        self.assertEqual(len(trips), 2)
        self.assertEqual(trips[0][2]["reason"], "idle")
        self.assertAlmostEqual(trips[0][2]["t"], 60, delta=2)  # the parked minutes are not part of it

    def test_a_short_stop_does_not_end_the_trip(self):
        self.d.drive(60, speed=15)
        self.d.park(90)  # (under the two minutes a trip waits by default)
        self.d.drive(60, speed=15)
        self.assertEqual(len(self.finished()), 1)

    def test_a_trip_waits_two_minutes_by_default(self):
        self.assertEqual(triplog.IDLE_END_S, 120.0)
        self.d.drive(60, speed=15)
        self.d.park(150)
        self.d.drive(60, speed=15)
        trips = self.finished()
        self.assertEqual(len(trips), 2)
        self.assertEqual(trips[0][2]["reason"], "idle")

    def test_the_wait_can_be_changed_while_a_trip_is_being_driven(self):
        self.d.drive(60, speed=15)
        self.log.set_idle_end(300)
        self.d.park(250)
        self.d.drive(60, speed=15)
        self.assertEqual(len(self.finished()), 1)  # (a four minute stop no longer splits it)
        self.setUp()
        self.d.drive(60, speed=15)
        self.log.set_idle_end(60)
        self.d.park(70)
        self.assertEqual(self.log.status(), {"recording": False})  # (and a shorter wait ends it sooner)

    def test_a_nudge_is_not_a_trip(self):
        self.d.park(3)
        self.d.drive(4, speed=2)  # about 8 m
        self.d.park(5)
        self.assertEqual(self.finished(), [])
        self.assertEqual(self.log.stats["trips_discarded"], 1)
        self.assertEqual(self.files(), [])

    def test_a_parked_car_leaves_no_trip(self):
        self.d.park(300)
        self.assertEqual(self.finished(), [])

    def test_the_trip_ends_when_the_game_stops_sending(self):
        self.d.drive(60, speed=15)
        self.clock.t += triplog.LOST_AFTER_S - 1
        self.log.tick()
        self.assertTrue(self.log.status()["recording"])
        self.clock.t += 5
        self.log.tick()
        self.assertFalse(self.log.status()["recording"])
        self.assertEqual(lines(self.files()[0])[-1]["reason"], "lost")

    def test_the_car_the_dashboard_knows_goes_into_the_points(self):
        self.d.car = {"fuel": 0.8, "fuelVolume": 40.0, "damage.brokenPct": 1.5, "energy.liquid": True, "energy.electric": False}
        self.d.drive(30, speed=15)
        (head, points, _), = self.finished()
        self.assertEqual(head["energy"], "liquid")
        self.assertEqual((points[-1]["f"], points[-1]["fv"], points[-1]["dm"]), (0.8, 40.0, 1.5))

    def test_place_limit_weather_and_gps_are_kept_as_info_lines(self):
        self.d.drive(30, speed=15)
        self.log.ingest({"type": "geoinfo", "rt": self.d.rt, "place": {"street": "Oriental Parade", "place": "Wellington", "country": "New Zealand", "junk": "x"},
                         "limit": {"ms": 13.89, "mapped": True, "unit": "kmh"}, "wx": {"tempC": 15.2, "windMs": 4.4, "evil": 1},
                         "gps": {"remaining": 1000.0, "eta": 90.0, "name": "ignored"}})
        self.d.drive(5, speed=15)
        self.finished()
        info, = [r for r in lines(self.files()[0]) if r["k"] == "i"]
        self.assertEqual(info["place"], {"street": "Oriental Parade", "place": "Wellington", "country": "New Zealand"})
        self.assertEqual(info["lim"], {"ms": 13.89, "m": True, "u": "kmh"})
        self.assertEqual(info["wx"], {"tempC": 15.2, "windMs": 4.4})
        self.assertEqual(info["gps"], {"remaining": 1000.0, "eta": 90.0})

    def test_an_estimated_limit_is_not_marked_as_posted(self):
        self.d.drive(30, speed=15)
        self.log.ingest({"type": "geoinfo", "rt": self.d.rt, "limit": {"ms": 22.2, "unit": "kmh"}})
        self.finished()
        info, = [r for r in lines(self.files()[0]) if r["k"] == "i"]
        self.assertNotIn("m", info["lim"])


class RobustnessTests(LogCase):
    def test_bad_packets_are_rejected_and_never_raise(self):
        bad = [{"type": "geo"}, {"type": "geo", "rt": "x", "lat": 1, "lon": 2}, {"type": "geo", "rt": 1, "lat": 91, "lon": 2},
               {"type": "geo", "rt": 1, "lat": 1, "lon": 181}, {"type": "geo", "rt": float("nan"), "lat": 1, "lon": 2},
               {"type": "geo", "rt": 1, "lat": float("inf"), "lon": 2}, {"type": "geo", "rt": 1, "lat": True, "lon": 2},
               {"type": "nonsense"}, {"type": "geoevent", "name": "not-an-event"}, {"type": "geoevent"}, {"type": "geoinfo"}]
        for packet in bad:
            self.log.ingest(packet)
        self.assertEqual(self.files(), [])
        self.assertGreaterEqual(self.log.stats["rejected_sample"], 6)

    def test_odd_values_inside_a_good_packet_are_ignored_not_trusted(self):
        self.d.park(2)
        self.d.sample(15.0, model="../../etc", alt=1e12, hdg="north")
        self.d.drive(60, speed=15)
        (head, points, _), = self.finished()
        self.assertIsNone(head["vehicle"])  # a model name that is not a plain name is dropped
        self.assertTrue(all("al" in p and abs(p["al"]) < 1000 for p in points[2:]))

    def test_an_impossible_speed_is_not_used(self):
        self.d.drive(30, speed=15)
        self.d.sample(15.0, spd=5000.0)
        self.d.drive(30, speed=15)
        (_, _, end), = self.finished()
        self.assertLess(end["top"], 20)

    def test_the_game_side_restarting_its_clock_is_a_new_trip(self):
        self.d.drive(60, speed=15)
        self.d.rt = 5.0  # the extension was reloaded: its clock starts again
        self.d.drive(60, speed=15)
        self.assertEqual(len(self.finished()), 2)

    def test_events_per_trip_are_bounded(self):
        self.d.drive(30, speed=15)
        for _ in range(triplog.MAX_EVENTS_PER_TRIP + 50):
            self.d.event("routeArrived")
        self.finished()
        self.assertEqual(len([r for r in lines(self.files()[0]) if r["k"] == "e"]), triplog.MAX_EVENTS_PER_TRIP)

    def test_a_folder_that_cannot_be_written_does_not_break_the_relay(self):
        blocker = Path(self.tmp.name) / "blocked"
        blocker.write_text("a file where the folder should be")
        log = TripLog(blocker / "triplog", clock=self.clock, say=self.said.append)
        d = Driver(log)
        d.drive(60, speed=15)  # must not raise
        self.assertGreaterEqual(log.stats["write_errors"], 1)
        self.assertEqual(len(self.said), 1)  # said once, not every second

    def test_a_trip_left_open_by_a_crash_is_ended_when_the_relay_starts(self):
        self.d.drive(120, speed=15)
        # (no close: the process died)  a half-written last line must not matter either
        with open(self.files()[0], "a") as f:
            f.write('{"k":"p","t":12')
        again = TripLog(self.dir, clock=self.clock)
        self.assertEqual(again.stats["trips_recovered"], 1)
        end = lines_ignoring_bad(self.files()[0])[-1]
        self.assertEqual(end["reason"], "interrupted")
        self.assertGreater(end["dist"], 1500)
        self.assertGreater(end["n"], 0)

    def test_a_file_with_nothing_usable_is_removed_at_start(self):
        self.dir.mkdir(parents=True)
        (self.dir / "20260101T000000Z.jsonl").write_text('{"k":"trip","id":"20260101T000000Z","ts":1}\n')
        TripLog(self.dir, clock=self.clock)
        self.assertEqual(self.files(), [])


def lines_ignoring_bad(path):
    out = []
    for line in Path(path).read_text().splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            pass
    return out


class ReadBackTests(LogCase):
    def test_the_list_is_newest_first_and_tells_open_from_ended(self):
        self.d.drive(60, speed=15)
        self.d.event("teleported")
        self.d.jump(8000, ready_samples=1)
        self.d.drive(60, speed=15)  # the second trip is still open
        listing = self.log.list()
        self.assertEqual(len(listing), 2)
        newest, oldest = listing
        self.assertTrue(newest["recording"] and not newest["ended"])
        self.assertTrue(oldest["ended"] and oldest["reason"] == "teleport")
        self.assertAlmostEqual(oldest["distance"], 900, delta=30)
        self.assertGreater(newest["distance"], 800)
        self.assertEqual(oldest["vehicle"], "etkc")

    def test_read_returns_every_part(self):
        self.d.drive(40, speed=15)
        self.log.ingest({"type": "geoinfo", "rt": self.d.rt, "place": {"country": "New Zealand"}})
        self.d.event("routeArrived")
        self.d.drive(10, speed=15)
        self.log.close_all("idle")
        tid = self.log.list()[0]["id"]
        trip = self.log.read(tid)
        self.assertEqual(trip["header"]["id"], tid)
        self.assertGreater(len(trip["points"]), 10)
        self.assertEqual(len(trip["info"]), 1)
        self.assertEqual(trip["events"][0]["name"], "routeArrived")
        self.assertEqual(trip["end"]["reason"], "idle")

    def test_read_refuses_anything_that_is_not_a_trip_id(self):
        self.d.drive(60, speed=15)
        self.log.close_all()
        for bad in ("../x", "..", "20260101T000000Z/../../etc", "x", "", None, 5, "20260101T000000Z.jsonl"):
            self.assertIsNone(self.log.read(bad))
        self.assertIsNone(self.log.read("20260101T000000Z"))  # well formed but not there

    def test_listing_stays_quick_with_big_files(self):
        self.d.park(1)
        self.d.drive(3000, speed=10)  # about an hour of points
        self.log.close_all()
        self.assertEqual(len(self.log.list()), 1)

    def test_two_trips_in_the_same_second_get_different_ids(self):
        a = Driver(self.log, veh=1)
        a.drive(60, speed=15)
        self.log.close_all()
        b = Driver(self.log, veh=1)  # same rt origin: starts in the same second
        b.drive(60, speed=15)
        self.log.close_all()
        ids = [t["id"] for t in self.log.list()]
        self.assertEqual(len(ids), 2)
        self.assertEqual(len(set(ids)), 2)


class HubTests(LogCase):
    def packet(self, **kw):
        base = {"v": 1, "type": "geo", "rt": 100.0, "ts": T0, "lat": -41.2, "lon": 174.8, "spd": 12.0, "ready": True, "veh": 1, "model": "etkc"}
        base.update(kw)
        return base

    def test_the_hub_routes_geo_packets_to_the_trip_log(self):
        hub = Hub(triplog=self.log)
        for i in range(90):
            hub.ingest_packet(self.packet(rt=100.0 + i, ts=T0 + i, lat=-41.2 + i * 12.0 / M_PER_DEG_LAT))
        self.assertTrue(self.log.status()["recording"])
        self.assertEqual(hub.stats["geo"], 90)

    def test_geo_packets_do_not_make_the_dashboard_think_a_car_is_sending(self):
        hub = Hub(triplog=self.log)
        hub.ingest_packet(self.packet())
        self.assertFalse(hub.connected)
        self.assertEqual(hub.channels, {})

    def test_without_a_trip_log_they_are_counted_and_ignored(self):
        hub = Hub()
        hub.ingest_packet(self.packet())
        self.assertEqual(hub.stats["geo_ignored"], 1)

    def test_the_cars_numbers_are_merged_only_while_the_car_is_sending(self):
        hub = Hub(triplog=self.log)
        hub.ingest_packet({"v": 1, "type": "frame", "vehicle": "/vehicles/etkc/", "electrics": {"fuel": 0.5, "fuelVolume": 25.0}})
        for i in range(60):
            hub.ingest_packet(self.packet(rt=100.0 + i, ts=T0 + i, lat=-41.2 + i * 12.0 / M_PER_DEG_LAT))
        hub.last_frame_at -= 60  # the car went quiet (the game paused)
        for i in range(60, 70):
            hub.ingest_packet(self.packet(rt=100.0 + i, ts=T0 + i, lat=-41.2 + i * 12.0 / M_PER_DEG_LAT))
        self.log.close_all()
        points = [r for r in lines(self.files()[0]) if r["k"] == "p"]
        self.assertEqual(points[5]["f"], 0.5)
        self.assertNotIn("f", points[-1])


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class HttpTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        data = Path(self.tmp.name)
        self.log = TripLog(data / "triplog")
        self.hub = Hub(triplog=self.log)
        self.port = free_port()
        app = make_app(self.hub, self.port, LayoutStore(data / "layouts", web_root() / "layouts"), SettingsStore(data / "settings.json"))
        self.runner = web.AppRunner(app, access_log=None)
        await self.runner.setup()
        await web.TCPSite(self.runner, "127.0.0.1", self.port).start()
        self.base = f"http://127.0.0.1:{self.port}"
        self.http = aiohttp.ClientSession()
        d = Driver(self.log)
        d.drive(90, speed=15)
        self.log.close_all()

    async def asyncTearDown(self):
        await self.http.close()
        await self.runner.cleanup()
        self.tmp.cleanup()

    async def test_list_and_read(self):
        async with self.http.get(self.base + "/api/triplog") as r:
            self.assertEqual(r.status, 200)
            body = await r.json()
        self.assertTrue(body["enabled"])
        self.assertEqual(len(body["trips"]), 1)
        tid = body["trips"][0]["id"]
        async with self.http.get(self.base + f"/api/triplog/{tid}") as r:
            self.assertEqual(r.status, 200)
            trip = await r.json()
        self.assertEqual(trip["header"]["id"], tid)
        self.assertGreater(len(trip["points"]), 10)

    async def test_unknown_and_malformed_ids_are_404(self):
        for bad in ("20260101T000000Z", "x", "..", "%2e%2e", "20260101T000000Z.jsonl"):
            async with self.http.get(self.base + f"/api/triplog/{bad}") as r:
                self.assertEqual(r.status, 404, bad)

    async def test_without_a_trip_log_the_list_says_so(self):
        data, port = Path(self.tmp.name), free_port()
        app = make_app(Hub(), port, LayoutStore(data / "l2", web_root() / "layouts"), SettingsStore(data / "s2.json"))
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        await web.TCPSite(runner, "127.0.0.1", port).start()
        try:
            async with self.http.get(f"http://127.0.0.1:{port}/api/triplog") as r:
                body = await r.json()
            async with self.http.get(f"http://127.0.0.1:{port}/api/triplog/20260101T000000Z") as r:
                self.assertEqual(r.status, 404)
        finally:
            await runner.cleanup()
        self.assertEqual({k: v for k, v in body.items() if k != "presetTags"}, {"enabled": False, "status": {"recording": False}, "trips": []})
        self.assertIn("Commute", body["presetTags"])  # (the tags offered are the same with or without a log, so the page can build its picker)

    async def test_nothing_here_can_be_written_to(self):
        for method in (self.http.post, self.http.put, self.http.delete):
            async with method(self.base + "/api/triplog", headers={"X-ThrashDash": "1"}) as r:
                self.assertIn(r.status, (404, 405))


class MathTests(unittest.TestCase):
    def test_haversine_agrees_with_a_known_distance(self):
        # one degree of latitude is about 111.2 km
        self.assertAlmostEqual(haversine_m(0, 0, 1, 0) / 1000, 111.2, delta=0.1)
        self.assertEqual(haversine_m(10, 10, 10, 10), 0)
        self.assertAlmostEqual(haversine_m(-41.2, 174.8, -41.2, 174.8001), 8.4, delta=0.2)

    def test_the_turn_between_headings_wraps(self):
        self.assertAlmostEqual(triplog._turn_deg(359, 1), 2)
        self.assertAlmostEqual(triplog._turn_deg(10, 350), 20)
        self.assertAlmostEqual(triplog._turn_deg(90, 270), 180)


if __name__ == "__main__":
    unittest.main()

"""Tests for the trip computer: the maths, saving, and how it reaches the dashboard."""
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
from thrashdash.server import make_app  # noqa: E402
from thrashdash.store import LayoutStore, SettingsStore  # noqa: E402
from thrashdash.trip import MAX_GAP_S, SAVE_EVERY_S, TripComputer, energy_kind  # noqa: E402

H = {"X-ThrashDash": "1"}


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def frame(speed, volume=None, ratio=None, **more):
    flat = {"wheelspeed": speed, **more}
    if volume is not None:
        flat["fuelVolume"] = volume
    if ratio is not None:
        flat["fuel"] = ratio
    return flat


def drive(trip, clock, flat, seconds, vehicle="car", kind="liquid", step=0.1):
    """Feed the same frame for `seconds` of game time, a frame every `step` seconds; returns the last channels."""
    out = None
    for _ in range(round(seconds / step)):
        clock.t += step
        out = trip.update(flat, vehicle, kind)
    return out


class TripMathTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.trip = TripComputer(None, clock=self.clock)

    def test_distance_time_and_speeds(self):
        self.trip.update(frame(0), "car", "liquid")  # the first frame has nothing before it
        out = drive(self.trip, self.clock, frame(20), 30)  # 20 m/s for 30 s
        self.assertAlmostEqual(out["trip.A.distance"], 600, delta=1)
        self.assertAlmostEqual(out["trip.A.seconds"], 30, delta=0.2)
        self.assertAlmostEqual(out["trip.A.avgSpeed"], 20, delta=0.1)
        self.assertEqual(out["trip.A.maxSpeed"], 20)
        self.assertEqual(out["trip.A.distance"], out["trip.B.distance"])  # both trips count together until one is reset

    def test_average_speed_needs_some_driving_behind_it(self):
        self.trip.update(frame(10), "car", None)
        out = drive(self.trip, self.clock, frame(10), 2)
        self.assertNotIn("trip.A.avgSpeed", out)

    def test_a_pause_counts_as_nothing(self):
        self.trip.update(frame(30), "car", None)
        drive(self.trip, self.clock, frame(30), 10)
        before = self.trip.channels(frame(30), None)["trip.A.distance"]
        self.clock.t += MAX_GAP_S + 5  # the game was paused or the connection stalled
        after = self.trip.update(frame(30), "car", None)
        self.assertEqual(after["trip.A.distance"], before)
        self.assertAlmostEqual(after["trip.A.seconds"], 10, delta=0.2)

    def test_speed_falls_back_to_airspeed_and_nonsense_is_ignored(self):
        self.trip.update({"airspeed": 5}, "car", None)
        out = drive(self.trip, self.clock, {"airspeed": 5}, 10)
        self.assertAlmostEqual(out["trip.A.distance"], 50, delta=1)
        out = drive(self.trip, self.clock, frame(9e6), 5)  # a glitch of millions of m/s
        self.assertLess(out["trip.A.maxSpeed"], 400)
        self.assertAlmostEqual(out["trip.A.distance"], 50, delta=1)
        out = drive(self.trip, self.clock, {"wheelspeed": float("nan")}, 3)
        self.assertLess(out["trip.A.distance"], 60)

    def test_reversing_adds_distance_too(self):
        self.trip.update(frame(-3), "car", None)
        out = drive(self.trip, self.clock, frame(-3), 10)
        self.assertAlmostEqual(out["trip.A.distance"], 30, delta=1)

    def test_a_new_car_does_not_count_the_gap_or_compare_tanks(self):
        self.trip.update(frame(10, 40, 0.8), "etkc", "liquid")
        drive(self.trip, self.clock, frame(10, 40, 0.8), 10, vehicle="etkc")
        before = self.trip.channels({}, "liquid")["trip.A.distance"]
        out = self.trip.update(frame(10, 5, 0.1), "ccf", "liquid")  # a different car with a nearly empty tank
        self.assertEqual(out["trip.A.distance"], before)
        self.assertEqual(out.get("trip.A.energyUsed", 0), 0)

    def test_reset_clears_one_trip_only(self):
        self.trip.update(frame(10), "car", None)
        drive(self.trip, self.clock, frame(10), 20)
        self.trip.reset("A")
        out = self.trip.channels(frame(10), None)
        self.assertEqual(out["trip.A.distance"], 0)
        self.assertEqual(out["trip.A.maxSpeed"], 0)
        self.assertAlmostEqual(out["trip.B.distance"], 200, delta=2)
        with self.assertRaises(KeyError):
            self.trip.reset("C")


class TripEnergyTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.trip = TripComputer(None, clock=self.clock)

    def run_fuel(self, kind, start_volume, per_second, seconds, capacity, speed=25.0, vehicle="car"):
        """Drive at constant speed while the energy drops by `per_second` (volume units) each second."""
        flat = frame(speed, start_volume, start_volume / capacity)
        self.trip.update(flat, vehicle, kind)
        volume = start_volume
        out = None
        for _ in range(int(seconds * 10)):
            self.clock.t += 0.1
            volume -= per_second * 0.1
            out = self.trip.update(frame(speed, volume, volume / capacity), vehicle, kind)
        return out

    def test_fuel_economy_and_range(self):
        # 25 m/s for 100 s = 2.5 km, burning 0.01 L/s = 1 L, so 40 L per 100 km
        out = self.run_fuel("liquid", 40.0, 0.01, 100, capacity=50)
        self.assertAlmostEqual(out["trip.A.energyUsed"], 1.0, delta=0.02)
        self.assertAlmostEqual(out["trip.A.consumption"], 40.0, delta=1.0)
        self.assertAlmostEqual(out["trip.A.percentUsed"], 2.0, delta=0.1)  # 1 L of a 50 L tank
        # 39 L left at 40 L/100 km is 97.5 km
        self.assertAlmostEqual(out["trip.A.range"] / 1000, 97.5, delta=3)
        self.assertEqual(out["trip.energyKind"], "liquid")

    def test_no_economy_until_there_is_some_driving_behind_it(self):
        out = self.run_fuel("liquid", 40.0, 0.01, 5, capacity=50, speed=10.0)  # only 50 m
        self.assertIn("trip.A.energyUsed", out)
        self.assertNotIn("trip.A.consumption", out)
        self.assertNotIn("trip.A.range", out)

    def test_refuelling_is_not_negative_use_and_a_jump_is_not_use(self):
        out = self.run_fuel("liquid", 20.0, 0.01, 30, capacity=50)
        used = out["trip.A.energyUsed"]
        out = self.trip.update(frame(25, 50.0, 1.0), "car", "liquid")  # filled up at a station: a 40% jump
        self.assertAlmostEqual(out["trip.A.energyUsed"], used, delta=1e-9)
        self.clock.t += 0.1
        out = self.trip.update(frame(25, 49.9, 0.998), "car", "liquid")
        self.assertGreater(out["trip.A.energyUsed"], used)  # and counting carries on from the new level

    def test_slow_trickle_up_is_ignored_for_fuel(self):
        self.trip.update(frame(10, 20.0, 0.4), "car", "liquid")
        self.clock.t += 0.1
        out = self.trip.update(frame(10, 20.05, 0.401), "car", "liquid")
        self.assertEqual(out["trip.A.energyUsed"], 0)

    def test_electric_counts_net_use_so_regeneration_gives_back(self):
        self.trip.update(frame(20, 60.0, 0.6), "ev", "electric")
        for volume in (59.9, 59.8, 59.9, 59.95):  # uses 0.2 kWh, then regenerates 0.15
            self.clock.t += 0.1
            out = self.trip.update(frame(20, volume, volume / 100), "ev", "electric")
        self.assertAlmostEqual(out["trip.A.energyUsed"], 0.05, delta=1e-6)
        self.assertEqual(out["trip.energyKind"], "electric")

    def test_hybrid_tracks_only_the_percentage(self):
        out = self.run_fuel("mixed", 30.0, 0.01, 50, capacity=60)
        self.assertIn("trip.A.percentUsed", out)
        self.assertNotIn("trip.A.energyUsed", out)
        self.assertNotIn("trip.A.consumption", out)

    def test_unknown_kind_counts_distance_and_the_percentage_but_no_volume(self):
        # An older mod does not say what kind of energy the car has: litres and kWh cannot be told apart, a percentage can.
        out = self.run_fuel(None, 30.0, 0.01, 20, capacity=60)
        self.assertGreater(out["trip.A.distance"], 100)
        self.assertEqual(out["trip.energyKind"], "unknown")
        self.assertAlmostEqual(out["trip.A.percentUsed"], 0.2 / 60 * 100, delta=0.05)
        self.assertNotIn("trip.A.energyUsed", out)
        self.assertNotIn("trip.A.consumption", out)

    def test_the_percentage_carries_on_when_the_kind_becomes_known(self):
        self.run_fuel(None, 30.0, 0.01, 20, capacity=60)  # before the mod is reloaded
        out = self.run_fuel("liquid", 29.8, 0.01, 20, capacity=60)  # after: litres from here on
        self.assertAlmostEqual(out["trip.A.energyUsed"], 0.2, delta=0.02)  # only what was seen since it became known
        self.assertAlmostEqual(out["trip.A.percentUsed"], 0.2 / 60 * 100, delta=0.05)  # the liquid share counted separately

    def test_litres_and_kwh_are_never_added_together(self):
        self.run_fuel("liquid", 40.0, 0.01, 50, capacity=50, vehicle="petrol")
        litres = self.trip.channels(frame(0, 39, 0.78), "liquid")["trip.A.energyUsed"]
        out = self.run_fuel("electric", 60.0, 0.02, 50, capacity=80, vehicle="ev")
        self.assertAlmostEqual(out["trip.A.energyUsed"], 1.0, delta=0.05)  # kWh from the EV only
        back = self.trip.channels(frame(0, 39, 0.78), "liquid")["trip.A.energyUsed"]
        self.assertAlmostEqual(back, litres, delta=1e-9)  # the petrol car's litres are still there, separately

    def test_energy_kind(self):
        self.assertEqual(energy_kind(False, True), "liquid")
        self.assertEqual(energy_kind(True, False), "electric")
        self.assertEqual(energy_kind(True, True), "mixed")
        self.assertIsNone(energy_kind(None, None))
        self.assertIsNone(energy_kind(False, False))


class TripSavingTests(unittest.TestCase):
    def test_saved_and_reloaded(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "trips.json"
            clock = Clock()
            trip = TripComputer(path, clock=clock)
            trip.update(frame(10, 40, 0.8), "car", "liquid")
            drive(trip, clock, frame(10, 40, 0.8), 30)
            trip.reset("B")
            drive(trip, clock, frame(10, 39.9, 0.798), 10)
            trip.save()
            again = TripComputer(path, clock=Clock())
            a, b = again.channels({}, "liquid"), trip.channels({}, "liquid")
            self.assertEqual(a["trip.A.distance"], b["trip.A.distance"])
            self.assertAlmostEqual(a["trip.B.distance"], 100, delta=2)  # B restarted when reset
            self.assertLess(a["trip.B.distance"], a["trip.A.distance"])
            self.assertEqual(a["trip.A.maxSpeed"], 10)

    def test_reset_is_saved_immediately(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "trips.json"
            clock = Clock()
            trip = TripComputer(path, clock=clock)
            trip.update(frame(10), "car", None)
            drive(trip, clock, frame(10), 20)
            trip.reset("A")
            self.assertEqual(json.loads(path.read_text())["trips"]["A"]["distance"], 0)

    def test_saving_while_driving_is_throttled(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "trips.json"
            clock = Clock()
            trip = TripComputer(path, clock=clock)
            trip.update(frame(10), "car", None)
            drive(trip, clock, frame(10), SAVE_EVERY_S - 2)
            self.assertFalse(path.exists(), "not written on every frame")
            drive(trip, clock, frame(10), 4)
            self.assertTrue(path.exists(), "but written every few seconds")

    def test_bad_files_start_from_zero(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "trips.json"
            for text in ("garbage{", "[]", '{"trips": 5}', ""):
                path.write_text(text)
                out = TripComputer(path, clock=Clock()).channels({}, None)
                self.assertEqual(out["trip.A.distance"], 0, text)

    def test_hostile_values_in_the_file_are_cleaned(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "trips.json"
            path.write_text(json.dumps({"trips": {"A": {"distance": -5, "seconds": "x", "maxSpeed": 1e12,
                                                         "energy": {"liquid": {"distance": 1000, "volume": -3, "ratio": 0.1}, "plasma": {"volume": 1}}},
                                                  "B": {"distance": 1234.5}}}))
            out = TripComputer(path, clock=Clock()).channels({"fuelVolume": 10}, "liquid")
            self.assertEqual(out["trip.A.distance"], 0)
            self.assertEqual(out["trip.A.seconds"], 0)
            self.assertLessEqual(out["trip.A.maxSpeed"], 400)
            self.assertEqual(out["trip.A.energyUsed"], 0)
            self.assertEqual(out["trip.B.distance"], 1234.5)


class TripHubTests(unittest.TestCase):
    def test_trip_channels_ride_along_with_every_frame(self):
        hub = Hub()
        hub.ingest_packet({"v": 1, "type": "frame", "vehicle": "/vehicles/x/", "electrics": {"wheelspeed": 5.0}})
        self.assertEqual(hub.channels["trip.A.distance"], "number")
        self.assertEqual(hub.channels["trip.energyKind"], "string")
        self.assertEqual(hub.values["trip.energyKind"], "unknown")  # an older mod sends no energy section

    def test_the_mods_energy_section_sets_the_kind(self):
        hub = Hub()
        hub.ingest_packet({"v": 1, "type": "frame", "vehicle": "/vehicles/x/", "electrics": {"wheelspeed": 5.0}})
        hub.ingest_packet({"v": 1, "type": "slow", "vehicle": "/vehicles/x/", "energy": {"electric": True, "liquid": False}})
        hub.ingest_packet({"v": 1, "type": "frame", "vehicle": "/vehicles/x/", "electrics": {"wheelspeed": 5.0}})
        self.assertEqual(hub.values["trip.energyKind"], "electric")
        self.assertIs(hub.values["energy.electric"], True)

    def test_reset_shows_at_once(self):
        hub = Hub()
        clock = Clock()
        hub.trips = TripComputer(None, clock=clock)
        for _ in range(30):
            clock.t += 0.1
            hub.ingest_packet({"v": 1, "type": "frame", "vehicle": "/vehicles/x/", "electrics": {"wheelspeed": 10.0}})
        self.assertGreater(hub.values["trip.A.distance"], 10)
        seq = hub.frame_seq
        hub.reset_trip("A")
        self.assertEqual(hub.values["trip.A.distance"], 0)
        self.assertGreater(hub.values["trip.B.distance"], 10)
        self.assertGreater(hub.frame_seq, seq)  # so browsers get the new numbers without waiting for a frame


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class TripApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        data = Path(self.tmp.name)
        self.hub = Hub()
        self.local = True
        self.port = free_port()
        app = make_app(self.hub, self.port, LayoutStore(data / "layouts", web_root() / "layouts"), SettingsStore(data / "settings.json"),
                       local_check=lambda remote: self.local)
        self.runner = web.AppRunner(app, access_log=None)
        await self.runner.setup()
        await web.TCPSite(self.runner, "127.0.0.1", self.port).start()
        self.base = f"http://127.0.0.1:{self.port}"
        self.http = aiohttp.ClientSession()

    async def asyncTearDown(self):
        await self.http.close()
        await self.runner.cleanup()
        self.tmp.cleanup()

    async def test_reset_endpoint(self):
        self.hub.trips.trips["A"].distance = 5000.0
        async with self.http.post(self.base + "/api/trips/A/reset", headers=H) as r:
            self.assertEqual(r.status, 200)
        self.assertEqual(self.hub.trips.trips["A"].distance, 0)

    async def test_reset_needs_the_write_header(self):
        self.hub.trips.trips["A"].distance = 5000.0
        async with self.http.post(self.base + "/api/trips/A/reset") as r:
            self.assertEqual(r.status, 403)
        self.assertEqual(self.hub.trips.trips["A"].distance, 5000.0)

    async def test_a_phone_cannot_reset(self):
        self.local = False
        self.hub.trips.trips["B"].distance = 5000.0
        async with self.http.post(self.base + "/api/trips/B/reset", headers=H) as r:
            self.assertEqual(r.status, 403)
        self.assertEqual(self.hub.trips.trips["B"].distance, 5000.0)

    async def test_unknown_trip_is_a_clear_404(self):
        for name in ("C", "a", "..", "A%2FB"):
            async with self.http.post(self.base + f"/api/trips/{name}/reset", headers=H) as r:
                self.assertIn(r.status, (404, 403, 405), name)  # (".." is normalised away before it reaches the handler)

    async def test_shutdown_saves_the_trips(self):
        path = Path(self.tmp.name) / "trips.json"
        self.hub.trips.path = path
        self.hub.trips.trips["A"].distance = 1234.0
        await self.runner.shutdown()
        self.assertEqual(json.loads(path.read_text())["trips"]["A"]["distance"], 1234.0)


if __name__ == "__main__":
    unittest.main()

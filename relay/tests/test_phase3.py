"""Tests for what a trip now records about the drive itself: the strongest force on the car, knocks, waypoints the driver dropped,
the places found near stops, and how a route set in the game went (hub.py, triplog.py, tripstats.py)."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from thrashdash.hub import Hub  # noqa: E402
from thrashdash.tripnotice import TripNotices  # noqa: E402
from thrashdash.triplog import Busy, TripLog  # noqa: E402
from thrashdash.tripstats import summarise  # noqa: E402
from test_triplog import Driver, lines  # noqa: E402


class Case(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name) / "triplog"
        self.notices = []
        self.log = TripLog(self.dir, say=lambda text: None, on_notice=lambda kind, info: self.notices.append((kind, info)))

    def tearDown(self):
        self.log.close_all()
        self.tmp.cleanup()

    def file(self):
        return next(self.dir.glob("*.jsonl"))


class PeakGTests(Case):
    """The game sends a position once a second; a knock lasts far less. The hub watches the car's acceleration in every frame and
    hands the strongest to the trip with the next position."""

    def drive_through_the_hub(self, accelerations):
        hub = Hub(triplog=self.log)
        rt, lat = 100.0, -41.2
        for second, (ax, ay) in enumerate(accelerations):
            for _ in range(3):  # (frames come many times a second: only the strongest counts)
                hub.ingest_packet({"v": 1, "type": "frame", "vehicle": "etkc", "electrics": {"accXSmooth": ax / 3, "accYSmooth": ay / 3, "fuel": 0.5}})
            hub.ingest_packet({"v": 1, "type": "frame", "vehicle": "etkc", "electrics": {"accXSmooth": ax, "accYSmooth": ay, "fuel": 0.5}})
            rt += 1.0
            lat += 15.0 / 111_000.0
            hub.ingest_packet({"v": 1, "type": "geo", "rt": rt, "ts": 1_800_000_000 + second, "lat": lat, "lon": 174.8, "alt": 3.0, "spd": 15.0, "hdg": 0.0, "ready": True, "veh": 7, "model": "etkc"})
        self.log.close_all()
        return [l for l in lines(self.file()) if l["k"] == "p"]

    def test_each_point_carries_the_strongest_force_since_the_last_in_g(self):
        pts = self.drive_through_the_hub([(0.0, 0.0), (3.0, 4.0), (0.0, 19.6133), (1.0, 0.0)] + [(0.5, 0.5)] * 25)
        by_g = [p.get("g") for p in pts[:5]]
        self.assertIn(0.51, by_g, "3 and 4 m/s^2 together are 5 m/s^2, a little over half a g")
        self.assertIn(2.0, by_g, "19.6 m/s^2 is two g")
        self.assertTrue(all(isinstance(p.get("g"), float) for p in pts if "g" in p))

    def test_the_peak_starts_again_after_each_point(self):
        pts = self.drive_through_the_hub([(0.0, 19.6133)] + [(0.1, 0.1)] * 30)
        gs = [p["g"] for p in pts if "g" in p]
        self.assertEqual(max(gs), 2.0)
        self.assertEqual(sum(1 for g in gs if g >= 2.0), 1, "one strong second does not leak into the next")

    def test_a_car_that_sends_no_acceleration_has_no_g_and_a_glitch_is_ignored(self):
        hub = Hub(triplog=self.log)
        hub.ingest_packet({"v": 1, "type": "frame", "vehicle": "etkc", "electrics": {"fuel": 0.5}})
        hub.ingest_packet({"v": 1, "type": "frame", "vehicle": "etkc", "electrics": {"accXSmooth": 1e9, "accYSmooth": 1e9}})
        self.assertEqual(hub._g_peak, 0.0)

    def test_the_summary_gives_the_peak_and_where(self):
        pts = self.drive_through_the_hub([(0.0, 0.0)] * 5 + [(0.0, 29.42)] + [(0.0, 0.0)] * 25)
        data = {"header": {"energy": "liquid"}, "points": pts, "info": [{"k": "i", "t": 0.0, "place": {"street": "Cuba Street"}}], "events": [], "end": None}
        g = summarise(data)["g"]
        self.assertEqual(g["max"], 3.0)
        self.assertEqual(g["at"], "Cuba Street")


class StatHelpers(unittest.TestCase):
    @staticmethod
    def trip(points, info=(), events=(), pois=()):
        pts = [{"k": "p", "t": float(t), "la": -41.2 + t * 1e-4, "lo": 174.8, "s": 10.0, "d": t * 10.0, **extra} for t, extra in points]
        return {"header": {"energy": "liquid"}, "points": pts, "info": list(info), "events": list(events), "pois": list(pois), "end": None}

    def test_a_knock_is_a_rise_in_damage_and_a_crash_over_a_few_samples_is_one(self):
        data = self.trip([(0, {"dm": 0.0}), (1, {"dm": 0.0}), (2, {"dm": 0.6}), (3, {"dm": 1.5}), (4, {"dm": 1.5}), (5, {"dm": 1.55}), (6, {"dm": 1.55})])
        knocks = summarise(data)["knocks"]
        self.assertEqual(len(knocks), 1, "two samples of one crash are one knock")
        self.assertEqual(knocks[0]["rise"], 1.5)
        self.assertEqual(knocks[0]["t"], 2.0)
        self.assertNotIn("g", knocks[0], "no force was sent")

    def test_small_scrapes_are_kept_as_knocks_so_the_setting_can_decide(self):
        data = self.trip([(0, {"dm": 0.0}), (1, {"dm": 0.3}), (2, {"dm": 0.3}), (3, {"dm": 0.3}), (4, {"dm": 0.3}), (5, {"dm": 0.7}), (6, {"dm": 0.7})])
        self.assertEqual([k["rise"] for k in summarise(data)["knocks"]], [0.3, 0.4])

    def test_noise_below_the_step_is_not_a_knock(self):
        data = self.trip([(0, {"dm": 0.0}), (1, {"dm": 0.05}), (2, {"dm": 0.08})])
        self.assertNotIn("knocks", summarise(data))

    def test_separate_knocks_are_separate_and_a_repair_is_not_one(self):
        data = self.trip([(0, {"dm": 0.0}), (1, {"dm": 2.0}), (2, {"dm": 2.0}), (3, {"dm": 2.0}), (4, {"dm": 2.0}), (5, {"dm": 5.0}), (6, {"dm": 0.0}), (7, {"dm": 0.0})])
        self.assertEqual([k["rise"] for k in summarise(data)["knocks"]], [2.0, 3.0])

    def test_a_hard_hit_that_barely_marked_the_body_is_a_knock_with_its_force(self):
        # the real case: a 7.5 g hit one sample before the body moved by 0.9%
        data = self.trip([(0, {"dm": 0.2, "g": 0.8}), (1, {"dm": 0.2, "g": 7.45}), (2, {"dm": 1.1, "g": 7.08}), (3, {"dm": 1.1, "g": 0.9})])
        knocks = summarise(data)["knocks"]
        self.assertEqual(len(knocks), 1, "the force and the damage are one knock")
        self.assertEqual((knocks[0]["t"], knocks[0]["rise"], knocks[0]["g"]), (1.0, 0.9, 7.45))
        self.assertEqual((knocks[0]["la"], knocks[0]["lo"]), (-41.2 + 1e-4, 174.8), "placed where the force was strongest")

    def test_a_force_with_no_damage_is_a_knock_and_ordinary_driving_is_not(self):
        data = self.trip([(0, {"g": 1.2}), (1, {"g": 1.9}), (2, {"g": 5.0}), (3, {"g": 0.5}), (20, {"g": 2.5})])
        knocks = summarise(data)["knocks"]
        self.assertEqual([(k["t"], k["g"], k["rise"]) for k in knocks], [(2.0, 5.0, 0.0), (20.0, 2.5, 0.0)])

    def test_a_trip_without_damage_readings_has_none(self):
        self.assertNotIn("knocks", summarise(self.trip([(0, {}), (1, {}), (2, {})])))

    def test_stops_are_named_by_the_places_found_near_them(self):
        pts = [(0, {})] + [(t, {"s": 0.0}) for t in range(10, 60, 10)] + [(60, {}), (70, {})]
        for t, extra in pts:
            if extra.get("s") == 0.0:
                pass
        data = self.trip([(t, ({"s": 0.0} if 10 <= t <= 50 else {})) for t in range(0, 80, 10)],
                         pois=[{"k": "poi", "t": 25.0, "poi": [{"n": "Z Petone", "c": "gas_station"}, {"n": "Cafe Roma", "c": "cafe"}, {"n": "Third", "c": "x"}]},
                               {"k": "poi", "t": 200.0, "poi": [{"n": "Far away in time"}]}])
        stop, = summarise(data)["stops"]
        self.assertEqual([p["n"] for p in stop["poi"]], ["Z Petone", "Cafe Roma"], "the nearest two, and only readings from during the stop")
        self.assertEqual(stop["poi"][0]["c"], "gas_station")

    def test_a_stop_with_no_place_reading_has_no_poi(self):
        data = self.trip([(t, ({"s": 0.0} if 10 <= t <= 50 else {})) for t in range(0, 80, 10)])
        self.assertNotIn("poi", summarise(data)["stops"][0])

    def test_a_route_that_was_finished_early(self):
        gps = lambda t, remaining, eta, off=1.0, version=3: {"k": "i", "t": t, "gps": {"remaining": remaining, "eta": eta, "off": off, "routeVersion": version}}
        data = self.trip([(t, {}) for t in range(0, 130, 10)], info=[gps(3.3, 4146.6, 192.5), gps(50.0, 2000.0, 90.0, off=38.0), gps(110.0, 480.0, 22.0)],
                         events=[{"k": "e", "t": 121.4, "name": "routeArrived"}])
        route = summarise(data)["route"]
        self.assertEqual(route["planned_m"], 4147)
        self.assertEqual(route["eta_s"], 192.5)
        self.assertEqual(route["arrived_s"], 118.1)
        self.assertEqual(route["early_s"], 74.4, "the game said it would arrive at 195.8 s; it did at 121.4 s")
        self.assertEqual(route["off_max_m"], 38)
        self.assertEqual(route["replans"], 0)
        self.assertIsNone(route["left_m"])

    def test_a_route_that_was_replanned_and_not_finished(self):
        gps = lambda t, remaining, version: {"k": "i", "t": t, "gps": {"remaining": remaining, "eta": remaining / 10, "off": 0.0, "routeVersion": version}}
        data = self.trip([(t, {}) for t in range(0, 130, 10)], info=[gps(3.0, 5000.0, 1), gps(40.0, 4800.0, 2), gps(90.0, 4000.0, 4)])
        route = summarise(data)["route"]
        self.assertEqual(route["replans"], 2, "three route versions: two replans")
        self.assertIsNone(route["arrived_s"])
        self.assertEqual(route["left_m"], 4000)
        self.assertNotIn("early_s", route)

    def test_no_route_set_means_no_route_figures(self):
        self.assertNotIn("route", summarise(self.trip([(t, {}) for t in range(0, 40, 10)])))

    def test_waypoints_are_counted(self):
        data = self.trip([(t, {}) for t in range(0, 40, 10)], events=[{"k": "e", "t": 5.0, "name": "waypoint", "la": -41.2, "lo": 174.8}, {"k": "e", "t": 15.0, "name": "waypoint"}, {"k": "e", "t": 20.0, "name": "reset"}])
        self.assertEqual(summarise(data)["waypoints"], 2)


class WaypointTests(Case):
    def test_a_waypoint_while_driving_is_an_event_in_the_trip_and_is_confirmed(self):
        d = Driver(self.log)
        d.drive(30, speed=15)
        d.event("waypoint")
        d.drive(10, speed=15)
        self.assertEqual(self.notices[-1], ("waypoint", {"recorded": True, "n": 1}))
        d.event("waypoint")
        self.assertEqual(self.notices[-1], ("waypoint", {"recorded": True, "n": 2}))
        events = [l for l in lines(self.file()) if l["k"] == "e"]
        self.assertEqual([e["name"] for e in events], ["waypoint", "waypoint"])
        self.assertIn("la", events[0], "it keeps where it was dropped")

    def test_a_waypoint_with_no_trip_going_says_so(self):
        d = Driver(self.log)
        d.event("waypoint")
        self.assertEqual(self.notices, [("waypoint", {"recorded": False})])

    def test_a_note_is_written_against_a_waypoint_and_the_last_one_wins(self):
        d = Driver(self.log)
        d.drive(30, speed=15)
        d.event("waypoint")
        d.drive(30, speed=15)
        self.log.close_all()
        tid = self.log.list()[0]["id"]
        t = next(l["t"] for l in lines(self.file()) if l["k"] == "e")
        self.assertEqual(self.log.note(tid, t, "  great   coffee\n here "), "great coffee here")
        self.assertEqual(self.log.read(tid)["notes"], [{"t": t, "text": "great coffee here"}])
        self.log.note(tid, t, "second thoughts")
        self.assertEqual(self.log.read(tid)["notes"], [{"t": t, "text": "second thoughts"}])
        self.log.note(tid, t, "")
        self.assertEqual(self.log.read(tid)["notes"], [], "an empty note takes it away")

    def test_a_note_needs_a_real_waypoint_and_text_and_a_finished_trip(self):
        d = Driver(self.log)
        d.drive(30, speed=15)
        d.event("waypoint")
        d.drive(10, speed=15)
        recording = self.log.status()["id"]
        with self.assertRaises(Busy):
            self.log.note(recording, 1.0, "x")
        self.log.close_all()
        tid = self.log.list()[0]["id"]
        for t, text in ((999.0, "x"), ("soon", "x"), (None, "x")):
            with self.assertRaises(ValueError, msg=str(t)):
                self.log.note(tid, t, text)
        t = next(l["t"] for l in lines(self.file()) if l["k"] == "e")
        with self.assertRaises(ValueError):
            self.log.note(tid, t, 5)

    def test_waypoints_and_notes_are_given_to_the_notices_as_a_word_in_the_game(self):
        sent = []
        notices = TripNotices(type("S", (), {"get": staticmethod(lambda: {"tripNotices": {}, "units": {}})})())
        notices._to_game = lambda title, message, seconds=12: sent.append((title, seconds)) or True
        notices.publish("waypoint", {"recorded": True, "n": 3})
        notices.publish("waypoint", {"recorded": False})
        self.assertEqual(sent, [("Waypoint saved", 4), ("No waypoint saved", 5)])


class PoiTests(Case):
    def test_places_found_near_a_stop_are_written_as_a_reading(self):
        d = Driver(self.log)
        d.drive(30, speed=15)
        d.park(30)
        self.log.ingest({"v": 1, "type": "geopoi", "rt": d.rt, "lat": d.lat, "lon": d.lon, "poi": [{"n": "Z Petone", "c": "gas_station"}, {"n": "  ", "c": "x"}, {"c": "no name"}, "junk"]})
        d.drive(30, speed=15)
        pois = [l for l in lines(self.file()) if l["k"] == "poi"]
        self.assertEqual(len(pois), 1)
        self.assertEqual(pois[0]["poi"], [{"n": "Z Petone", "c": "gas_station"}], "only the ones with a name")

    def test_a_reading_with_nothing_usable_is_not_written(self):
        d = Driver(self.log)
        d.drive(30, speed=15)
        for bad in ({"poi": []}, {"poi": "x"}, {"poi": [{"c": "x"}]}, {}):
            self.log.ingest({"v": 1, "type": "geopoi", "rt": d.rt, **bad})
        self.assertEqual([l for l in lines(self.file()) if l["k"] == "poi"], [])

    def test_names_are_cut_short_and_a_trip_holds_a_limited_number(self):
        d = Driver(self.log)
        d.drive(30, speed=15)
        for i in range(250):
            self.log.ingest({"v": 1, "type": "geopoi", "rt": d.rt + i * 0.01, "poi": [{"n": "x" * 500}]})
        pois = [l for l in lines(self.file()) if l["k"] == "poi"]
        self.assertEqual(len(pois), 200)
        self.assertEqual(len(pois[0]["poi"][0]["n"]), 80)


if __name__ == "__main__":
    unittest.main()

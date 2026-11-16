"""Tests for trip summaries: what a recorded trip adds up to (tripstats.py), and that the trip log stores and serves them.

The trips here are built by hand, line by line, so each rule can be checked with numbers worked out on paper.
"""
import json
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

import aiohttp
from aiohttp import web

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from thrashdash import tripexport, tripstats  # noqa: E402
from thrashdash.tripstats import place_label, route_line, summarise  # noqa: E402
from thrashdash.triplog import TripLog  # noqa: E402
from thrashdash.hub import Hub  # noqa: E402
from thrashdash.paths import web_root  # noqa: E402
from thrashdash.server import CSP, TILE_HOSTS, TRIPS_CSP, TRIPS_REFERRER_POLICY, make_app  # noqa: E402
from thrashdash.store import LayoutStore, SettingsStore  # noqa: E402
from thrashdash.triplog import Busy, NotFound, clean_name  # noqa: E402
from test_triplog import Driver, free_port  # noqa: E402


def trip(points, info=(), events=(), energy="liquid", end=None):
    """A trip as TripLog.read returns it. `points` are (t, speed) pairs, driven north from a fixed place."""
    pts = []
    for t, speed in points:
        pts.append({"k": "p", "t": t, "la": -41.2 + t * 0.0001, "lo": 174.8, "s": speed, "d": round(t * 10.0, 1)})
    return {"header": {"k": "trip", "id": "20260101T000000Z", "energy": energy}, "points": pts, "info": list(info),
            "events": list(events), "end": end}


def limit(t, ms, posted=True):
    lim = {"ms": ms, "u": "kmh"}
    if posted:
        lim["m"] = True
    return {"k": "i", "t": t, "lim": lim}


class NameTests(unittest.TestCase):
    def test_a_street_names_a_place(self):
        self.assertEqual(place_label({"street": "Oriental Parade", "place": "Wellington"}), "Oriental Parade")

    def test_a_road_number_is_not_a_name_so_the_town_is_used(self):
        for number in ("2", "SH2", "A 40", "M25"):
            self.assertEqual(place_label({"street": number, "place": "Wellington"}), "Wellington", number)

    def test_with_only_a_road_number_it_is_still_shown(self):
        self.assertEqual(place_label({"street": "2"}), "2")

    def test_it_falls_back_through_the_town_region_and_country(self):
        self.assertEqual(place_label({"region": "Canterbury", "country": "New Zealand"}), "Canterbury")
        self.assertEqual(place_label({"country": "New Zealand"}), "New Zealand")
        self.assertIsNone(place_label({}))
        self.assertIsNone(place_label(None))
        self.assertIsNone(place_label("Wellington"))

    def test_a_trip_is_named_from_where_it_began_and_ended(self):
        info = [{"k": "i", "t": 5, "place": {"street": "Stale Street"}},  # still where the car came from
                {"k": "i", "t": 15, "place": {"street": "Courtenay Place", "country": "New Zealand"}},
                {"k": "i", "t": 60, "place": {"street": "Hutt Road", "country": "New Zealand"}}]
        s = summarise(trip([(0, 5), (60, 10)], info))
        self.assertEqual((s["from"], s["to"], s["name"]), ("Courtenay Place", "Hutt Road", "Courtenay Place to Hutt Road"))

    def test_a_trip_too_short_to_settle_uses_the_last_name_it_has(self):
        s = summarise(trip([(0, 5), (8, 10)], [{"k": "i", "t": 4, "place": {"street": "A Street"}}]))
        self.assertEqual(s["from"], "A Street")

    def test_a_trip_that_ends_where_it_began_is_a_loop(self):
        info = [{"k": "i", "t": 15, "place": {"street": "Oriental Parade"}}, {"k": "i", "t": 60, "place": {"street": "Oriental Parade"}}]
        self.assertEqual(summarise(trip([(0, 5), (60, 10)], info))["name"], "Around Oriental Parade")

    def test_a_trip_without_names_has_none(self):
        s = summarise(trip([(0, 5), (60, 10)]))
        self.assertIsNone(s["name"])

    def test_countries_and_towns_are_listed_once_in_order(self):
        info = [{"k": "i", "t": 15, "place": {"place": "Wellington", "country": "New Zealand"}},
                {"k": "i", "t": 25, "place": {"place": "Lower Hutt", "country": "New Zealand"}},
                {"k": "i", "t": 35, "place": {"place": "Wellington", "country": "New Zealand"}},
                {"k": "i", "t": 45, "place": {"place": "Sydney", "country": "Australia"}}]
        s = summarise(trip([(0, 5), (60, 10)], info))
        self.assertEqual(s["countries"], ["New Zealand", "Australia"])
        self.assertEqual(s["places"], ["Wellington", "Lower Hutt", "Sydney"])


class MotionTests(unittest.TestCase):
    def test_time_is_split_into_moving_and_stopped(self):
        s = summarise(trip([(0, 10), (10, 10), (20, 0), (30, 0), (40, 0), (50, 10)]))
        self.assertEqual(s["dur"], 50)
        self.assertEqual(s["moving_s"], 30)  # 0-10, 10-20 (still moving at its start), and 40-50 (moving at its end)
        self.assertEqual(s["stopped_s"], 20)

    def test_the_closing_line_gives_the_distance_duration_and_top_speed(self):
        s = summarise(trip([(0, 5), (50, 8)]), {"t": 50.0, "dist": 1234.5, "top": 31.4})
        self.assertEqual((s["dur"], s["dist"], s["top_ms"]), (50.0, 1234.5, 31.4))
        self.assertAlmostEqual(s["avg_ms"], 24.69, places=2)

    def test_the_top_speed_comes_from_the_points_when_the_closing_line_lost_it(self):
        s = summarise(trip([(0, 5), (25, 21), (50, 8)]), {"t": 50.0, "dist": 100.0, "top": 0})
        self.assertEqual(s["top_ms"], 21)

    def test_the_bounding_box_covers_every_point(self):
        s = summarise(trip([(0, 5), (100, 8)]))
        self.assertEqual(s["bbox"][0], -41.2)
        self.assertAlmostEqual(s["bbox"][2], -41.19, places=6)

    def test_nothing_to_summarise_gives_none(self):
        self.assertIsNone(summarise({"header": {}, "points": [], "info": [], "events": []}))
        self.assertIsNone(summarise({}))

    def test_climbing_ignores_noise_and_counts_real_hills(self):
        data = trip([(i * 10, 10) for i in range(8)])
        for p, alt in zip(data["points"], (10.0, 11.0, 9.5, 10.5, 20.0, 30.0, 25.0, 12.0)):
            p["al"] = alt
        a = summarise(data)["alt"]
        self.assertEqual((a["min"], a["max"]), (9.5, 30.0))
        self.assertEqual(a["gain"], 20)  # from 10.0 up to 30.0; the wobble under 3 m before it counts for nothing
        self.assertEqual(a["loss"], 18)  # 30.0 down to 12.0


class LimitTests(unittest.TestCase):
    def test_only_posted_limits_are_checked(self):
        # 20 m/s the whole way; one posted limit of 10 (over) and one estimated limit of 10 (not counted at all)
        s = summarise(trip([(0, 20), (60, 20)], [limit(0, 10), limit(10, 10, posted=False)]))
        lim = s["limits"]
        self.assertEqual(lim["checked_s"], 10)  # the posted one, until the next reading
        self.assertEqual(lim["over_s"], 10)
        self.assertEqual(lim["over_m"], 200)
        self.assertEqual(lim["max_over_ms"], 10)

    def test_a_trip_with_no_posted_limit_says_nothing_about_speeding(self):
        s = summarise(trip([(0, 20), (60, 20)], [limit(0, 10, posted=False)]))
        self.assertNotIn("limits", s)

    def test_a_little_over_is_not_speeding_but_a_lot_is(self):
        under = summarise(trip([(0, 10.8), (60, 10.8)], [limit(0, 10)]))["limits"]  # 0.8 m/s over: inside the tolerance
        over = summarise(trip([(0, 11.5), (60, 11.5)], [limit(0, 10)]))["limits"]
        self.assertEqual(under["over_s"], 0)
        self.assertEqual(over["over_s"], 10)

    def test_checked_time_is_what_the_limit_covers_not_the_whole_trip(self):
        s = summarise(trip([(0, 5), (200, 5)], [limit(0, 10), limit(10, 10)]))
        self.assertEqual(s["limits"]["checked_s"], 10 + 10)  # the last reading counts a ten-second interval
        self.assertEqual(s["limits"]["over_s"], 0)

    def test_a_reading_is_not_stretched_over_a_long_gap(self):
        s = summarise(trip([(0, 20), (300, 20)], [limit(0, 10), limit(200, 10)]))
        self.assertEqual(s["limits"]["checked_s"], tripstats.LIMIT_HOLD_S + 10)


class FuelWeatherTests(unittest.TestCase):
    def test_fuel_used_and_per_100km(self):
        data = trip([(0, 10), (50, 10), (100, 10)])
        for p, fv in zip(data["points"], (50.0, 49.0, 48.0)):
            p["fv"] = fv
        s = summarise(data, {"t": 100.0, "dist": 10000.0, "top": 10})
        self.assertEqual(s["fuel"], {"unit": "L", "used": 2.0, "per_100km": 20.0})

    def test_a_refill_is_not_negative_fuel(self):
        data = trip([(0, 10), (50, 10), (100, 10), (150, 10)])
        for p, fv in zip(data["points"], (20.0, 18.0, 48.0, 46.0)):
            p["fv"] = fv
        self.assertEqual(summarise(data)["fuel"]["used"], 4.0)

    def test_an_electric_car_is_in_kwh_and_a_short_trip_has_no_per_100km(self):
        data = trip([(0, 10), (10, 10)], energy="electric")
        for p, fv in zip(data["points"], (60.0, 59.9)):
            p["fv"] = fv
        s = summarise(data, {"t": 10.0, "dist": 100.0, "top": 10})
        self.assertEqual(s["fuel"]["unit"], "kWh")
        self.assertNotIn("per_100km", s["fuel"])

    def test_no_fuel_readings_or_an_unknown_kind_gives_no_fuel(self):
        self.assertNotIn("fuel", summarise(trip([(0, 10), (10, 10)])))
        data = trip([(0, 10), (10, 10)], energy=None)
        for p, fv in zip(data["points"], (60.0, 59.0)):
            p["fv"] = fv
        self.assertNotIn("fuel", summarise(data))

    def test_damage_start_end_and_worst(self):
        data = trip([(0, 10), (10, 10), (20, 10)])
        for p, dm in zip(data["points"], (0, 12.5, 8)):
            p["dm"] = dm
        self.assertEqual(summarise(data)["damage"], {"start": 0, "end": 8, "max": 12.5})

    def test_weather_over_the_trip(self):
        info = [{"k": "i", "t": 0, "wx": {"code": 3, "tempC": 14.0, "windMs": 5.0, "gustMs": 9.0, "rainMmH": 0, "visibilityM": 20000}},
                {"k": "i", "t": 10, "wx": {"code": 61, "tempC": 12.0, "windMs": 8.0, "gustMs": 15.0, "rainMmH": 2.5, "visibilityM": 4000}},
                {"k": "i", "t": 20, "wx": {"code": 61, "tempC": 13.0, "windMs": 6.0, "rainMmH": 1.0, "visibilityM": 6000}}]
        w = summarise(trip([(0, 5), (30, 5)], info))["wx"]
        self.assertEqual((w["temp_min"], w["temp_max"], w["temp_avg"]), (12.0, 14.0, 13.0))
        self.assertEqual((w["wind_max_ms"], w["gust_max_ms"], w["visibility_min_m"]), (8.0, 15.0, 4000))
        self.assertEqual(w["code"], 61)  # raining the longest
        self.assertEqual(w["rain_s"], 20)
        self.assertEqual(w["snow_s"], 0)

    def test_events_are_counted(self):
        events = [{"k": "e", "t": 5, "name": "reset"}, {"k": "e", "t": 9, "name": "reset"}, {"k": "e", "t": 11, "name": "routeArrived"}]
        s = summarise(trip([(0, 5), (30, 5)], events=events))
        self.assertEqual((s["resets"], s["arrivals"]), (2, 1))

    def test_odd_values_do_not_break_it(self):
        data = trip([(0, 5), (30, 5)], [{"k": "i", "t": "soon", "place": "x", "wx": "rain", "lim": 5}, {"k": "i"}, "nonsense"])
        data["points"].append({"k": "p", "t": "x"})
        self.assertEqual(summarise(data)["dist"], 300.0)


class SpanTests(unittest.TestCase):
    def test_spans_are_only_given_when_asked_for(self):
        data = trip([(0, 20), (60, 20)], [limit(0, 10)])
        self.assertNotIn("spans", summarise(data)["limits"])
        self.assertNotIn("posted", summarise(data)["limits"])
        self.assertIn("spans", summarise(data, spans=True)["limits"])

    def test_the_stretch_over_the_limit_is_given_in_seconds_into_the_trip(self):
        # over for the first ten seconds only: the second reading has a limit above the speed
        data = trip([(0, 20), (60, 20)], [limit(0, 10), limit(10, 30)])
        lim = summarise(data, spans=True)["limits"]
        self.assertEqual(lim["spans"], [[0.0, 10.0]])

    def test_stretches_that_touch_are_one(self):
        data = trip([(0, 20), (60, 20)], [limit(0, 10), limit(10, 10), limit(20, 10)])
        self.assertEqual(summarise(data, spans=True)["limits"]["spans"], [[0.0, 30.0]])

    def test_where_a_limit_was_posted_and_what_it_was(self):
        data = trip([(0, 5), (60, 5)], [limit(0, 10), limit(10, 10), limit(20, 15), limit(30, 10, posted=False)])
        self.assertEqual(summarise(data, spans=True)["limits"]["posted"], [[0.0, 20.0, 10.0], [20.0, 30.0, 15.0]])


class TripChangeEventTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.events = []
        self.log = TripLog(Path(self.tmp.name) / "triplog", say=lambda t: None)
        self.log.on_change = lambda kind, tid: self.events.append((kind, tid))
        self.d = Driver(self.log)

    def tearDown(self):
        self.log.close_all()
        self.tmp.cleanup()

    def test_a_trip_is_announced_the_moment_its_file_is_made_and_when_it_ends(self):
        self.d.drive(3, speed=15)
        self.assertEqual([k for k, _ in self.events], ["started"])  # (not after 20 s and 50 m: at once, so a page can open it)
        tid = self.events[0][1]
        self.assertEqual(tid, self.log.status()["id"])
        self.d.drive(60, speed=15)
        self.log.close_all()
        self.assertEqual(self.events, [("started", tid), ("ended", tid)])

    def test_a_nudge_that_is_thrown_away_still_says_it_is_gone(self):
        self.d.drive(3, speed=2)
        self.log.close_all()
        self.assertEqual([k for k, _ in self.events], ["started", "ended"])  # (so a page that opened it does not keep a trip that is gone)
        self.assertEqual(self.log.list(), [])

    def test_a_listener_that_fails_never_breaks_the_log(self):
        said = []
        log = TripLog(Path(self.tmp.name) / "other", say=said.append)
        log.on_change = lambda *a: 1 / 0
        Driver(log).drive(60, speed=15)
        log.close_all()
        self.assertEqual(len(log.list()), 1)
        self.assertEqual(log.stats["change_errors"], 2)
        self.assertTrue(said)


class StoredSummaryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name) / "triplog"
        self.log = TripLog(self.dir, say=lambda text: None)
        self.d = Driver(self.log)

    def tearDown(self):
        self.log.close_all()
        self.tmp.cleanup()

    def drive_with_places(self):
        self.d.drive(30, speed=15)
        self.log.ingest({"type": "geoinfo", "rt": self.d.rt, "place": {"street": "Oriental Parade", "place": "Wellington", "country": "New Zealand"},
                         "limit": {"ms": 10.0, "mapped": True, "unit": "kmh"}})
        self.d.drive(30, speed=15)
        self.log.close_all()

    def end_line(self):
        path, = sorted(self.dir.glob("*.jsonl"))
        return json.loads(path.read_text().splitlines()[-1])

    def test_the_closing_line_carries_the_summary(self):
        self.drive_with_places()
        end = self.end_line()
        self.assertEqual(end["k"], "end")
        self.assertEqual(end["sum"]["v"], tripstats.STATS_VERSION)
        self.assertEqual(end["sum"]["from"], "Oriental Parade")
        self.assertAlmostEqual(end["sum"]["dist"], end["dist"], places=1)
        self.assertGreater(end["sum"]["limits"]["over_s"], 0)

    def test_the_list_shows_the_stored_summary(self):
        self.drive_with_places()
        item, = self.log.list()
        self.assertEqual(item["stats"]["name"], self.end_line()["sum"]["name"])

    def test_a_trip_still_being_driven_has_no_summary_yet(self):
        self.d.drive(60, speed=15)
        item, = self.log.list()
        self.assertTrue(item["recording"])
        self.assertNotIn("stats", item)

    def test_a_summary_from_older_rules_is_worked_out_again(self):
        self.drive_with_places()
        path, = sorted(self.dir.glob("*.jsonl"))
        rows = path.read_text().splitlines()
        end = json.loads(rows[-1])
        end["sum"] = {"v": 0, "name": "stale"}
        path.write_text("\n".join(rows[:-1] + [json.dumps(end)]) + "\n")
        item, = self.log.list()
        self.assertEqual(item["stats"]["v"], tripstats.STATS_VERSION)
        self.assertNotEqual(item["stats"]["name"], "stale")
        self.assertEqual(item["stats"]["from"], "Oriental Parade")

    def test_a_trip_without_any_summary_gets_one_for_the_list(self):
        self.drive_with_places()
        path, = sorted(self.dir.glob("*.jsonl"))
        rows = path.read_text().splitlines()
        end = json.loads(rows[-1])
        del end["sum"]
        path.write_text("\n".join(rows[:-1] + [json.dumps(end)]) + "\n")
        item, = self.log.list()
        self.assertEqual(item["stats"]["from"], "Oriental Parade")
        self.assertEqual(self.log.list()[0]["stats"], item["stats"])  # and again from the memory of it

    def test_detail_is_the_trip_plus_a_fresh_summary(self):
        self.drive_with_places()
        item, = self.log.list()
        detail = self.log.detail(item["id"])
        self.assertEqual(detail["stats"]["from"], "Oriental Parade")
        self.assertGreater(len(detail["points"]), 10)
        self.assertIsNone(self.log.detail("20260101T000000Z"))
        self.assertIsNone(self.log.detail("../x"))

    def test_a_trip_recovered_after_a_crash_gets_a_summary_and_its_top_speed(self):
        self.d.drive(120, speed=15)
        again = TripLog(self.dir, say=lambda text: None)
        self.assertEqual(again.stats["trips_recovered"], 1)
        end = self.end_line()
        self.assertEqual(end["reason"], "interrupted")
        self.assertEqual(end["top"], 15.0)
        self.assertGreater(end["sum"]["dist"], 1500)

    def test_a_failing_summary_never_stops_a_trip_from_ending(self):
        self.d.drive(60, speed=15)
        said = []
        self.log.say = said.append
        original = tripstats.summarise
        tripstats.summarise = lambda *a, **k: 1 / 0
        try:
            self.log.close_all()
        finally:
            tripstats.summarise = original
        end = self.end_line()
        self.assertEqual(end["k"], "end")
        self.assertNotIn("sum", end)
        self.assertEqual(self.log.stats["summary_errors"], 1)
        self.assertTrue(said)


class ReadingTests(unittest.TestCase):
    """The game reads the speed limit every second and the place every two; the file only gets a line when something changed."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name) / "triplog"
        self.log = TripLog(self.dir, say=lambda text: None)
        self.d = Driver(self.log)

    def tearDown(self):
        self.log.close_all()
        self.tmp.cleanup()

    def read(self, ms=13.89, mapped=True, place=None, **extra):
        packet = {"type": "geoinfo", "rt": self.d.rt}
        if ms is not None:
            packet["limit"] = {"ms": ms, "mapped": mapped, "unit": "kmh"}
        if place:
            packet["place"] = place
        packet.update(extra)
        self.log.ingest(packet, {})

    def drive_reading(self, seconds, **kw):
        for _ in range(seconds):
            self.d.sample(15.0)
            self.read(**kw)

    def info_lines(self):
        self.log.close_all()
        path, = sorted(self.dir.glob("*.jsonl"))
        return [json.loads(line) for line in path.read_text().splitlines() if '"k":"i"' in line]

    def test_an_unchanged_reading_every_second_is_not_a_line_every_second(self):
        self.drive_reading(35, place={"street": "Oriental Parade", "country": "New Zealand"})
        lines = self.info_lines()
        self.assertLessEqual(len(lines), 5)  # the first, then one about every ten seconds
        self.assertGreaterEqual(len(lines), 3)

    def test_a_changed_limit_is_written_at_once_with_the_place_it_was_in(self):
        self.drive_reading(25, place={"street": "A Street"})
        self.drive_reading(1, ms=27.78)
        lines = self.info_lines()
        self.assertEqual(lines[-1]["lim"]["ms"], 27.78)
        self.assertEqual(lines[-1]["place"], {"street": "A Street"})
        self.assertEqual(lines[-1]["t"], 25.0)

    def test_a_reading_with_no_place_keeps_the_last_one(self):
        self.drive_reading(1, place={"street": "A Street"})
        self.drive_reading(27)  # the game does not send the place every second
        self.drive_reading(1, ms=20.0)  # a change: its line still says where
        self.assertEqual(self.info_lines()[-1]["place"], {"street": "A Street"})

    def test_a_new_place_is_a_line(self):
        self.drive_reading(25, place={"street": "A Street"})
        self.drive_reading(1, place={"street": "B Street"})
        self.assertEqual(self.info_lines()[-1]["place"], {"street": "B Street"})

    def test_a_limit_that_goes_unknown_is_a_line_without_a_limit(self):
        self.drive_reading(25, place={"street": "A Street"})
        self.drive_reading(1, ms=None)
        last = self.info_lines()[-1]
        self.assertNotIn("lim", last)
        self.assertEqual(last["t"], 25.0)

    def test_the_slow_readings_are_always_written(self):
        self.drive_reading(25, place={"street": "A Street"})
        self.drive_reading(1, wx={"tempC": 15.0, "windMs": 4.0}, gps={"remaining": 900.0})
        last = self.info_lines()[-1]
        self.assertEqual(last["wx"], {"tempC": 15.0, "windMs": 4.0})
        self.assertEqual(last["gps"], {"remaining": 900.0})
        self.assertIn("lim", last)  # and it is a whole picture

    def test_a_reading_before_any_trip_is_ignored(self):
        self.read(place={"street": "A Street"})
        self.assertEqual(list(self.dir.glob("*.jsonl")), [])

    def test_summaries_read_the_lines_the_same_way(self):
        """A limit holds until the next line, however it came: a changed limit and a heartbeat give the same time over."""
        self.drive_reading(30, ms=10.0, place={"street": "A Street"})  # over the limit the whole time (15 m/s against 10)
        self.log.close_all()
        item, = self.log.list()
        lim = item["stats"]["limits"]
        self.assertGreaterEqual(lim["over_s"], 28)  # (every second of it, give or take the end of the trip)
        self.assertLessEqual(lim["checked_s"], 31)


class WeatherWeightTests(unittest.TestCase):
    def test_weather_counts_only_for_the_lines_that_carry_it(self):
        # rain read at 0, a limit change at 3 (no weather), the next weather at 10: the first reading stands for 10 s, not 3
        info = [{"k": "i", "t": 0, "wx": {"tempC": 10.0, "rainMmH": 2.0}}, {"k": "i", "t": 3, "lim": {"ms": 5.0}},
                {"k": "i", "t": 10, "wx": {"tempC": 12.0, "rainMmH": 0}}]
        w = summarise(trip([(0, 5), (20, 5)], info))["wx"]
        self.assertEqual(w["rain_s"], 10)


class RouteLineTests(unittest.TestCase):
    def pts(self, coords):
        return [{"la": a, "lo": b} for a, b in coords]

    def test_a_straight_road_is_two_points(self):
        line = route_line(self.pts([(-41.0 + i * 0.0001, 174.0) for i in range(200)]))
        self.assertEqual(len(line), 2)
        self.assertEqual(line[0], [-41.0, 174.0])
        self.assertEqual(line[-1], [round(-41.0 + 199 * 0.0001, 5), 174.0])

    def test_a_corner_is_kept(self):
        north = [(-41.0 + i * 0.0001, 174.0) for i in range(100)]
        east = [(-41.0 + 99 * 0.0001, 174.0 + i * 0.0001) for i in range(1, 100)]
        line = route_line(self.pts(north + east))
        self.assertEqual(len(line), 3)
        self.assertEqual(line[1], [round(-41.0 + 99 * 0.0001, 5), 174.0])

    def test_a_wiggle_inside_the_tolerance_is_dropped_and_one_outside_is_kept(self):
        small = [(-41.0 + i * 0.0001, 174.0 + (0.00002 if i % 2 else 0.0)) for i in range(100)]  # about 1.7 m
        self.assertEqual(len(route_line(self.pts(small))), 2)
        big = [(-41.0 + i * 0.0001, 174.0 + (0.001 if i == 50 else 0.0)) for i in range(100)]  # about 85 m
        kept = route_line(self.pts(big))
        self.assertGreater(len(kept), 2)
        self.assertIn([round(-41.0 + 50 * 0.0001, 5), 174.001], kept)  # (the apex of the detour)

    def test_it_never_gives_more_than_the_limit(self):
        zigzag = [(-41.0 + i * 0.0001, 174.0 + (0.0005 if i % 2 else 0.0)) for i in range(2000)]
        line = route_line(self.pts(zigzag), max_points=100)
        self.assertLessEqual(len(line), 100)
        self.assertEqual(line[0][0], -41.0)

    def test_short_and_odd_input(self):
        self.assertEqual(route_line([]), [])
        self.assertEqual(route_line(self.pts([(1.0, 2.0)])), [[1.0, 2.0]])
        self.assertEqual(len(route_line(self.pts([(1.0, 2.0), (1.0, 2.0), (1.0, 2.0)]))), 2)  # (no length: still fine)
        self.assertEqual(len(route_line([{"la": 1.0, "lo": 2.0}, {"la": None, "lo": 3.0}, {"la": 1.1, "lo": 2.0}])), 2)


class RoutesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name) / "triplog"
        self.log = TripLog(self.dir, say=lambda text: None)
        self.d = Driver(self.log)

    def tearDown(self):
        self.log.close_all()
        self.tmp.cleanup()

    def test_each_trip_has_a_thinned_route_and_what_a_filter_needs(self):
        self.d.drive(60, speed=15)
        self.log.ingest({"type": "geoinfo", "rt": self.d.rt, "place": {"street": "A Street", "place": "Wellington", "country": "New Zealand"}}, {})
        self.d.drive(5, speed=15)
        self.log.close_all()
        route, = self.log.routes()
        self.assertEqual(route["vehicle"], "etkc")
        self.assertEqual(route["countries"], ["New Zealand"])
        self.assertEqual(len(route["line"]), 2)  # driven dead straight
        self.assertGreater(route["dist"], 900)
        self.assertFalse(route["recording"])
        self.assertIn("name", route)

    def test_the_trip_being_recorded_is_included_and_reread(self):
        self.d.drive(40, speed=15)
        first, = self.log.routes()
        self.assertTrue(first["recording"])
        self.d.drive(40, speed=15)
        second, = self.log.routes()
        self.assertGreater(second["dist"], first["dist"])

    def test_a_finished_trips_route_is_remembered_until_its_file_changes(self):
        self.d.drive(40, speed=15)
        self.log.close_all()
        tid = self.log.list()[0]["id"]
        self.log.routes()
        self.assertIn(tid, self.log._route_cache)
        before = self.log._route_cache[tid]
        self.log.routes()
        self.assertIs(self.log._route_cache[tid], before)
        self.log.rename(tid, "Coast")  # the file changed
        self.log.routes()
        self.assertIsNot(self.log._route_cache[tid], before)

    def test_a_deleted_trip_is_gone_from_the_routes(self):
        self.d.drive(40, speed=15)
        self.log.close_all()
        self.log.delete(self.log.list()[0]["id"])
        self.assertEqual(self.log.routes(), [])
        self.assertEqual(self.log._route_cache, {})


GPX = {"g": "http://www.topografix.com/GPX/1/1", "t": "http://www.garmin.com/xmlschemas/TrackPointExtension/v1"}
KML = {"k": "http://www.opengis.net/kml/2.2", "gx": "http://www.google.com/kml/ext/2.2"}


def export_trip(title=None, with_stats=True):
    data = trip([(0, 5), (10, 12), (20, 15), (30, 0)], [limit(0, 10)])
    data["header"]["ts"] = 1794604000  # 2026-11-13 21:06:40 UTC
    for i, p in enumerate(data["points"]):
        p["al"] = 5.0 + i
    data["title"] = title
    stats = summarise(data, {"t": 30.0, "dist": 400.0, "top": 15.0}, spans=True) if with_stats else None
    return data, stats


class ExportTests(unittest.TestCase):
    def test_gpx_is_valid_xml_with_every_point_timed_and_with_speed(self):
        data, stats = export_trip()
        root = ET.fromstring(tripexport.to_gpx(data, stats))
        pts = root.findall(".//g:trkpt", GPX)
        self.assertEqual(len(pts), 4)
        self.assertEqual(pts[0].get("lat"), "-41.2000000")
        self.assertEqual(pts[0].find("g:time", GPX).text, "2026-11-13T21:06:40.000Z")
        self.assertEqual(pts[1].find("g:time", GPX).text, "2026-11-13T21:06:50.000Z")
        self.assertEqual(pts[1].find("g:ele", GPX).text, "6.0")
        self.assertEqual(pts[1].find(".//t:speed", GPX).text, "12.00")
        self.assertEqual([w.find("g:name", GPX).text for w in root.findall("g:wpt", GPX)], ["Start", "End"])
        self.assertIn("Top speed", root.find(".//g:trk/g:desc", GPX).text)

    def test_kml_draws_the_route_as_a_line_on_the_ground(self):
        # (the first version had only a gx:Track, which the user's Google Earth did not draw: pins and no route)
        data, stats = export_trip()
        root = ET.fromstring(tripexport.to_kml(data, stats))
        placemarks = root.find("k:Document", KML).findall("k:Placemark", KML)  # (the ones shown at once, not in the folder)
        lines = [p for p in placemarks if p.find("k:LineString", KML) is not None]
        self.assertEqual(len(lines), 1)
        line = lines[0].find("k:LineString", KML)
        self.assertEqual(line.find("k:altitudeMode", KML).text, "clampToGround")
        self.assertEqual(line.find("k:tessellate", KML).text, "1")
        coords = line.find("k:coordinates", KML).text.split()
        self.assertEqual(len(coords), 4)
        self.assertEqual(coords[0], "174.8000000,-41.2000000,0")  # longitude first
        self.assertEqual(lines[0].find("k:styleUrl", KML).text, "#route")
        self.assertIn("Top speed", lines[0].find("k:description", KML).text)
        self.assertEqual([p.find("k:name", KML).text for p in placemarks if p.find("k:Point", KML) is not None], ["Start", "End"])

    def test_the_timed_track_is_kept_but_hidden_so_it_does_not_draw_over_the_line(self):
        data, stats = export_trip()
        root = ET.fromstring(tripexport.to_kml(data, stats))
        folder = root.find(".//k:Folder", KML)
        self.assertEqual(folder.find("k:visibility", KML).text, "0")
        track = folder.find(".//gx:Track", KML)
        self.assertEqual(len(track.findall("k:when", KML)), 4)
        coords = track.findall("gx:coord", KML)
        self.assertEqual(len(coords), 4)
        self.assertEqual(coords[0].text.split(), ["174.8000000", "-41.2000000", "5.0"])
        self.assertEqual(track.find("k:altitudeMode", KML).text, "clampToGround")
        self.assertEqual([v.text for v in track.findall(".//gx:value", KML)], ["5.00", "12.00", "15.00", "0.00"])
        self.assertEqual(folder.find("k:Placemark/k:visibility", KML).text, "0")

    def test_the_name_is_the_users_then_the_worked_out_one_then_the_date(self):
        data, stats = export_trip("Coast run")
        self.assertIn("<name>Coast run</name>", tripexport.to_gpx(data, stats))
        data, stats = export_trip(None)
        self.assertEqual(tripexport.trip_title(data, stats), stats["name"] or "Trip 2026-11-13 21:06")
        data, _ = export_trip(None, with_stats=False)
        self.assertEqual(tripexport.trip_title(data, None), "Trip 2026-11-13 21:06")

    def test_a_hostile_name_cannot_break_the_file(self):
        nasty = '</name><script>alert(1)</script> & "quotes" \x00\x0b <![CDATA[ x ]]>'
        data, stats = export_trip(nasty)
        for build in (tripexport.to_gpx, tripexport.to_kml):
            text = build(data, stats)
            ET.fromstring(text)  # still well-formed
            self.assertNotIn("<script>", text)
            self.assertNotIn("\x00", text)
            self.assertNotIn("\x0b", text)

    def test_odd_points_are_left_out_and_an_empty_trip_still_gives_a_valid_file(self):
        data, stats = export_trip()
        data["points"].append({"t": 40, "la": 91.0, "lo": 0.0})  # not a latitude
        data["points"].append({"t": 50, "la": None, "lo": 0.0})
        self.assertEqual(len(ET.fromstring(tripexport.to_gpx(data, stats)).findall(".//g:trkpt", GPX)), 4)
        empty = {"header": {"ts": 1794604000}, "points": [], "info": [], "events": []}
        for build in (tripexport.to_gpx, tripexport.to_kml):
            ET.fromstring(build(empty, None))

    def test_the_file_name_is_safe_and_plain(self):
        data, stats = export_trip('../../etc/passwd "x"; \r\nSet-Cookie: a=b')
        name = tripexport.file_name(data, stats, "gpx")
        self.assertRegex(name, r"^[A-Za-z0-9 ._()\-]+\.gpx$")
        self.assertNotIn("/", name)
        self.assertTrue(name.endswith(" 2026-11-13.gpx"), name)
        data, stats = export_trip("\u2192\u2192")  # nothing but characters a file name cannot hold: falls back to something plain
        self.assertEqual(tripexport.file_name(data, None, "kml"), "trip 2026-11-13.kml")


class NameAndDeleteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name) / "triplog"
        self.log = TripLog(self.dir, say=lambda text: None)
        self.d = Driver(self.log)
        self.d.drive(60, speed=15)
        self.log.close_all()
        self.tid = self.log.list()[0]["id"]

    def tearDown(self):
        self.log.close_all()
        self.tmp.cleanup()

    def test_a_name_is_kept_and_shown_in_the_list_and_the_detail(self):
        self.assertEqual(self.log.rename(self.tid, "Sunday run"), "Sunday run")
        item, = self.log.list()
        self.assertEqual(item["title"], "Sunday run")
        self.assertTrue(item["ended"])  # a name line after the end line does not make the trip look unfinished
        self.assertGreater(item["distance"], 800)
        self.assertEqual(self.log.detail(self.tid)["title"], "Sunday run")

    def test_the_last_name_wins_and_an_empty_one_takes_it_away(self):
        self.log.rename(self.tid, "First")
        self.log.rename(self.tid, "Second")
        self.assertEqual(self.log.list()[0]["title"], "Second")
        self.assertIsNone(self.log.rename(self.tid, "   "))
        self.assertIsNone(self.log.list()[0]["title"])
        self.assertIsNone(self.log.detail(self.tid)["title"])

    def test_renaming_never_rewrites_the_file(self):
        path = self.dir / f"{self.tid}.jsonl"
        before = path.read_bytes()
        self.log.rename(self.tid, "Coast")
        after = path.read_bytes()
        self.assertTrue(after.startswith(before))
        self.assertEqual(json.loads(after[len(before):]), {"k": "name", "name": "Coast"})

    def test_a_name_is_cleaned_before_it_is_kept(self):
        self.assertEqual(clean_name("  a \n\t b\x00c  "), "a b c")
        self.assertEqual(len(clean_name("x" * 500)), 80)
        self.assertEqual(clean_name("Café \u2192 Plage"), "Café \u2192 Plage")
        for bad in (None, 5, ["x"], {"a": 1}):
            with self.assertRaises(ValueError):
                clean_name(bad)

    def test_a_name_with_markup_is_stored_as_plain_text(self):
        self.log.rename(self.tid, "<img src=x onerror=alert(1)>")
        self.assertEqual(self.log.list()[0]["title"], "<img src=x onerror=alert(1)>")  # (the page shows it with textContent)

    def test_unknown_malformed_and_recording_trips_are_refused(self):
        for bad in ("20260101T000000Z", "../x", "", None, 5):
            with self.assertRaises(NotFound):
                self.log.rename(bad, "x")
            with self.assertRaises(NotFound):
                self.log.delete(bad)
        self.d.drive(60, speed=15)  # a new trip, still being recorded
        recording = self.log.status()["id"]
        with self.assertRaises(Busy):
            self.log.rename(recording, "x")
        with self.assertRaises(Busy):
            self.log.delete(recording)

    def test_a_deleted_trip_goes_to_the_trash_and_not_the_list(self):
        self.log.delete(self.tid)
        self.assertEqual(self.log.list(), [])
        self.assertTrue((self.dir / "trash" / f"{self.tid}.jsonl").is_file())
        self.assertIsNone(self.log.read(self.tid))
        with self.assertRaises(NotFound):
            self.log.delete(self.tid)

    def test_deleting_twice_the_same_id_keeps_both_in_the_trash(self):
        (self.dir / "trash").mkdir()
        (self.dir / "trash" / f"{self.tid}.jsonl").write_text("older\n")
        self.log.delete(self.tid)
        self.assertEqual(len(list((self.dir / "trash").glob("*.jsonl"))), 2)
        self.assertEqual((self.dir / "trash" / f"{self.tid}.jsonl").read_text(), "older\n")

    def test_the_trash_is_not_read_as_trips_after_a_restart(self):
        self.log.delete(self.tid)
        again = TripLog(self.dir, say=lambda text: None)
        self.assertEqual(again.list(), [])
        self.assertEqual(again.stats["trips_recovered"], 0)

    def test_a_trip_that_ends_without_a_final_newline_can_still_be_named(self):
        path = self.dir / f"{self.tid}.jsonl"
        path.write_bytes(path.read_bytes().rstrip(b"\n"))
        self.log.rename(self.tid, "Coast")
        self.assertEqual(self.log.list()[0]["title"], "Coast")
        self.assertTrue(self.log.list()[0]["ended"])


class HttpChangeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        data = Path(self.tmp.name)
        self.log = TripLog(data / "triplog", say=lambda text: None)
        d = Driver(self.log)
        d.drive(60, speed=15)
        self.log.close_all()
        self.tid = self.log.list()[0]["id"]
        d.drive(60, speed=15)  # the trip being recorded now
        self.recording = self.log.status()["id"]
        self.port = free_port()
        app = make_app(Hub(triplog=self.log), self.port, LayoutStore(data / "layouts", web_root() / "layouts"), SettingsStore(data / "settings.json"))
        self.runner = web.AppRunner(app, access_log=None)
        await self.runner.setup()
        await web.TCPSite(self.runner, "127.0.0.1", self.port).start()
        self.base = f"http://127.0.0.1:{self.port}"
        self.http = aiohttp.ClientSession()
        self.h = {"X-ThrashDash": "1"}

    async def asyncTearDown(self):
        await self.http.close()
        await self.runner.cleanup()
        self.log.close_all()
        self.tmp.cleanup()

    async def test_rename_then_read_it_back(self):
        async with self.http.patch(f"{self.base}/api/triplog/{self.tid}", json={"name": "Coast run"}, headers=self.h) as r:
            self.assertEqual(r.status, 200)
            self.assertEqual(await r.json(), {"ok": True, "title": "Coast run"})
        async with self.http.get(f"{self.base}/api/triplog/{self.tid}") as r:
            body = await r.json()
        self.assertEqual(body["title"], "Coast run")
        self.assertIn("posted", body["stats"].get("limits", {"posted": []}))
        async with self.http.get(f"{self.base}/api/triplog") as r:
            titles = {t["id"]: t["title"] for t in (await r.json())["trips"]}
        self.assertEqual(titles[self.tid], "Coast run")

    async def test_star_then_read_it_back_and_unstar(self):
        async with self.http.get(f"{self.base}/api/triplog/{self.tid}") as r:
            self.assertFalse((await r.json())["starred"])  # (not starred to begin with)
        async with self.http.patch(f"{self.base}/api/triplog/{self.tid}", json={"starred": True}, headers=self.h) as r:
            self.assertEqual(r.status, 200)
            self.assertEqual(await r.json(), {"ok": True, "starred": True})
        async with self.http.get(f"{self.base}/api/triplog/{self.tid}") as r:
            self.assertTrue((await r.json())["starred"])
        async with self.http.get(f"{self.base}/api/triplog") as r:
            self.assertTrue({t["id"]: t["starred"] for t in (await r.json())["trips"]}[self.tid])
        # naming and starring do not disturb each other, and the last star line wins
        async with self.http.patch(f"{self.base}/api/triplog/{self.tid}", json={"name": "Coast run", "starred": False}, headers=self.h) as r:
            self.assertEqual(await r.json(), {"ok": True, "title": "Coast run", "starred": False})
        item = {t["id"]: t for t in self.log.list()}[self.tid]
        self.assertEqual((item["starred"], item["title"]), (False, "Coast run"))
        self.assertFalse(self.log.read(self.tid)["starred"])

    async def test_star_needs_a_real_boolean_and_something_to_change(self):
        url = f"{self.base}/api/triplog/{self.tid}"
        for body in ({"starred": "yes"}, {"starred": 1}, {}, {"other": 1}):
            async with self.http.patch(url, json=body, headers=self.h) as r:
                self.assertEqual(r.status, 400, body)
        async with self.http.patch(url, json={"starred": True}) as r:
            self.assertEqual(r.status, 403)  # (no write header)
        async with self.http.patch(f"{self.base}/api/triplog/20200101T000000Z", json={"starred": True}, headers=self.h) as r:
            self.assertEqual(r.status, 404)

    async def test_changes_need_the_write_header(self):
        async with self.http.patch(f"{self.base}/api/triplog/{self.tid}", json={"name": "x"}) as r:
            self.assertEqual(r.status, 403)
        async with self.http.delete(f"{self.base}/api/triplog/{self.tid}") as r:
            self.assertEqual(r.status, 403)
        self.assertEqual(len([t for t in self.log.list() if not t["recording"]]), 1)

    async def test_bad_requests_get_clear_answers(self):
        url = f"{self.base}/api/triplog"
        async with self.http.patch(f"{url}/{self.tid}", json={"name": 5}, headers=self.h) as r:
            self.assertEqual(r.status, 400)
        async with self.http.patch(f"{url}/{self.tid}", data="not json", headers=self.h) as r:
            self.assertEqual(r.status, 400)
        async with self.http.patch(f"{url}/{self.tid}", json=["x"], headers=self.h) as r:
            self.assertEqual(r.status, 404)
        for method in (self.http.patch, self.http.delete):
            kwargs = {"json": {"name": "x"}} if method == self.http.patch else {}
            async with method(f"{url}/20260101T000000Z", headers=self.h, **kwargs) as r:
                self.assertEqual(r.status, 404)
            async with method(f"{url}/{self.recording}", headers=self.h, **kwargs) as r:
                self.assertEqual(r.status, 409)

    async def test_the_routes_endpoint_comes_before_the_id_one(self):
        async with self.http.get(f"{self.base}/api/triplog/routes") as r:
            self.assertEqual(r.status, 200)
            body = await r.json()
        self.assertTrue(body["enabled"])
        self.assertEqual(len(body["trips"]), 2)  # the finished one and the one being recorded
        self.assertEqual({t["recording"] for t in body["trips"]}, {True, False})
        self.assertTrue(all(len(t["line"]) >= 2 for t in body["trips"]))

    async def test_a_trip_downloads_as_gpx_and_kml(self):
        for kind, mime, root in (("gpx", "application/gpx+xml", "{http://www.topografix.com/GPX/1/1}gpx"), ("kml", "application/vnd.google-earth.kml+xml", "{http://www.opengis.net/kml/2.2}kml")):
            async with self.http.get(f"{self.base}/api/triplog/{self.tid}/{kind}") as r:
                self.assertEqual(r.status, 200, kind)
                self.assertEqual(r.headers["Content-Type"].split(";")[0], mime)
                disposition = r.headers["Content-Disposition"]
                self.assertTrue(disposition.startswith('attachment; filename="') and disposition.endswith(f'.{kind}"'), disposition)
                self.assertEqual(ET.fromstring(await r.text()).tag, root)

    async def test_live_gives_only_what_is_new(self):
        async with self.http.get(f"{self.base}/api/triplog/{self.recording}/live?after=-1") as r:
            everything = await r.json()
        self.assertGreater(len(everything["points"]), 30)
        self.assertIsNone(everything["end"])
        cut = everything["points"][-10]["t"]
        async with self.http.get(f"{self.base}/api/triplog/{self.recording}/live?after={cut}") as r:
            newer = await r.json()
        self.assertEqual([p["t"] for p in newer["points"]], [p["t"] for p in everything["points"] if p["t"] > cut])
        async with self.http.get(f"{self.base}/api/triplog/{self.recording}/live?after=1e9") as r:
            self.assertEqual((await r.json())["points"], [])

    async def test_live_says_when_the_trip_has_ended_and_refuses_bad_requests(self):
        async with self.http.get(f"{self.base}/api/triplog/{self.tid}/live?after=0") as r:
            self.assertIsNotNone((await r.json())["end"])
        async with self.http.get(f"{self.base}/api/triplog/{self.tid}/live?after=soon") as r:
            self.assertEqual(r.status, 400)
        async with self.http.get(f"{self.base}/api/triplog/20260101T000000Z/live") as r:
            self.assertEqual(r.status, 404)
        async with self.http.get(f"{self.base}/api/triplog/..%2Fx/live") as r:
            self.assertEqual(r.status, 404)

    async def test_a_trip_being_recorded_can_be_downloaded_too(self):
        async with self.http.get(f"{self.base}/api/triplog/{self.recording}/gpx") as r:
            self.assertEqual(r.status, 200)

    async def test_downloads_refuse_unknown_trips_and_kinds(self):
        for path in (f"{self.tid}/pdf", "20260101T000000Z/gpx", "../x/gpx", f"{self.tid}/gpx.exe", f"{self.tid}/"):
            async with self.http.get(f"{self.base}/api/triplog/{path}") as r:
                self.assertIn(r.status, (404, 405), path)

    async def test_delete_moves_the_trip_away(self):
        async with self.http.delete(f"{self.base}/api/triplog/{self.tid}", headers=self.h) as r:
            self.assertEqual(r.status, 200)
        async with self.http.get(f"{self.base}/api/triplog/{self.tid}") as r:
            self.assertEqual(r.status, 404)

    async def test_the_trips_page_may_load_map_tiles_and_nothing_else_may(self):
        async with self.http.get(f"{self.base}/trips") as r:
            self.assertEqual(r.status, 200)
            self.assertEqual(r.headers["Content-Security-Policy"], TRIPS_CSP)
            self.assertIn("<title>ThrashDash: Trips</title>", await r.text())
        async with self.http.get(f"{self.base}/trips.html") as r:
            self.assertEqual(r.headers["Content-Security-Policy"], TRIPS_CSP)
        for path in ("/", "/api/triplog", "/js/trips/trips.js", "/vendor/leaflet/leaflet.js", "/pin.html"):
            async with self.http.get(self.base + path) as r:
                self.assertEqual(r.headers["Content-Security-Policy"], CSP, path)
        self.assertEqual(TRIPS_CSP.replace(f" {TILE_HOSTS}", ""), CSP)  # the only difference is the two tile servers
        self.assertNotIn("script-src 'self' http", TRIPS_CSP)
        self.assertNotIn("connect-src 'self' http", TRIPS_CSP)

    async def test_the_trips_page_sends_its_origin_as_referer_because_openstreetmap_requires_one(self):
        # Without a Referer OpenStreetMap's servers answer every tile with an "Access blocked" picture (found in the real window).
        self.assertEqual(TRIPS_REFERRER_POLICY, "origin")  # the origin only: never the path (a trip id) or the query (a sign-in token)
        for path in ("/trips", "/trips.html"):
            async with self.http.get(self.base + path) as r:
                self.assertEqual(r.headers["Referrer-Policy"], "origin", path)
        for path in ("/", "/api/triplog", "/pin.html", "/js/trips/trips.js"):
            async with self.http.get(self.base + path) as r:
                self.assertEqual(r.headers["Referrer-Policy"], "no-referrer", path)
        source = (web_root() / "js" / "trips" / "trips.js").read_text()
        self.assertIn('referrerPolicy: "origin"', source)  # and the map's own tile requests say so too
        self.assertNotIn('referrerPolicy: "no-referrer"', source)

    async def test_the_page_and_its_map_library_are_served(self):
        for path, kind in (("/vendor/leaflet/leaflet.js", "javascript"), ("/vendor/leaflet/leaflet.css", "css"), ("/css/trips.css", "css"), ("/js/trips/model.js", "javascript")):
            async with self.http.get(self.base + path) as r:
                self.assertEqual(r.status, 200, path)
                self.assertIn(kind, r.headers["Content-Type"], path)


if __name__ == "__main__":
    unittest.main()

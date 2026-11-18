"""Tests for bringing a GPX file in as trips (tripimport.py)."""
import sys
import tempfile
import unittest
from pathlib import Path

import aiohttp
from aiohttp import web

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from thrashdash import tripimport  # noqa: E402
from thrashdash.hub import Hub  # noqa: E402
from thrashdash.paths import web_root  # noqa: E402
from thrashdash.server import make_app  # noqa: E402
from thrashdash.store import LayoutStore, SettingsStore  # noqa: E402
from thrashdash.tripedit import EditError  # noqa: E402
from thrashdash.triplog import TripLog  # noqa: E402
from test_triplog import free_port  # noqa: E402

M_PER_DEG = 111_320.0


def gpx(tracks, ns=True, extra_root=""):
    """A GPX document. `tracks`: a list of (name, [(lat, lon, ele, iso_time or None, speed or None)...]) (one segment each)."""
    ns_attr = ' xmlns="http://www.topografix.com/GPX/1/1" xmlns:gpxtpx="http://www.garmin.com/xmlschemas/TrackPointExtension/v1"' if ns else ""
    out = [f'<?xml version="1.0" encoding="UTF-8"?>\n<gpx version="1.1" creator="test"{ns_attr}>{extra_root}']
    for name, pts in tracks:
        out.append("<trk>" + (f"<name>{name}</name>" if name else "") + "<trkseg>")
        for la, lo, ele, time, speed in pts:
            inner = (f"<ele>{ele}</ele>" if ele is not None else "") + (f"<time>{time}</time>" if time else "")
            if speed is not None:
                inner += f"<extensions><gpxtpx:TrackPointExtension><gpxtpx:speed>{speed}</gpxtpx:speed></gpxtpx:TrackPointExtension></extensions>"
            out.append(f'<trkpt lat="{la}" lon="{lo}">{inner}</trkpt>')
        out.append("</trkseg></trk>")
    out.append("</gpx>")
    return "".join(out).encode("utf-8")


def drive(seconds=300, speed=10.0, lat0=-41.2, lon0=174.8, day=1, with_times=True, step=1, speeds=False):
    """A drive north at `speed` m/s, a point every `step` seconds."""
    pts = []
    for t in range(0, seconds + 1, step):
        h, rest = divmod(t, 3600)
        m, s = divmod(rest, 60)
        iso = f"2026-03-{day:02d}T{10 + h:02d}:{m:02d}:{s:02d}Z" if with_times else None
        pts.append((round(lat0 + speed * t / M_PER_DEG, 7), lon0, 5.0 + t * 0.01, iso, speed if speeds else None))
    return pts


class Case(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.log = TripLog(Path(self.tmp.name) / "triplog", say=lambda t: None)

    def tearDown(self):
        self.log.close_all()
        self.tmp.cleanup()


class ImportTests(Case):
    def test_a_track_becomes_a_trip_with_its_own_clock_distance_and_name(self):
        result = tripimport.import_gpx(self.log, gpx([("Sunday ride", drive(300, 10.0))]))
        self.assertEqual(len(result["ids"]), 1)
        trip = self.log.read(result["ids"][0])
        self.assertEqual(trip["header"]["imported"], "gpx")
        self.assertIsNone(trip["header"]["vehicle"])
        self.assertEqual(trip["header"]["ts"], 1772359200, "2026-03-01 10:00:00 UTC")
        self.assertEqual(trip["title"], "Sunday ride")
        self.assertEqual(trip["end"]["reason"], "imported")
        self.assertAlmostEqual(trip["end"]["t"], 300.0, places=1)
        self.assertAlmostEqual(trip["end"]["dist"], 3000.0, delta=30.0)
        self.assertAlmostEqual(trip["end"]["top"], 10.0, delta=1.0)
        self.assertEqual(trip["points"][0]["t"], 0.0)
        self.assertEqual(trip["points"][0]["d"], 0.0)
        self.assertNotIn("est", trip["header"])

    def test_the_trip_has_a_summary_and_is_listed_as_imported(self):
        tripimport.import_gpx(self.log, gpx([("Ride", drive(300, 10.0))]))
        item, = self.log.list()
        self.assertEqual(item["imported"], "gpx")
        self.assertAlmostEqual(item["stats"]["dist"], 3000.0, delta=30.0)
        self.assertGreater(item["stats"]["moving_s"], 250)

    def test_points_are_thinned_like_the_recorder_but_distance_keeps_every_one(self):
        # a point per second at 10 m/s: one every 10 m: all kept (>= 4 m). At 1 m/s: about one every 10 s.
        slow = tripimport.import_gpx(self.log, gpx([("Walk", drive(600, 1.0))]))
        trip = self.log.read(slow["ids"][0])
        self.assertLess(len(trip["points"]), 200, "600 samples thinned (a point is kept each time the car has moved 4 m)")
        self.assertGreater(len(trip["points"]), 100)
        self.assertAlmostEqual(trip["end"]["dist"], 600.0, delta=10.0, msg="and the distance is every step's, not the kept points'")

    def test_garmin_speeds_are_used_where_the_file_has_them_otherwise_they_are_worked_out(self):
        with_speed = tripimport.import_gpx(self.log, gpx([("A", drive(120, 10.0, speeds=True))]))
        self.assertTrue(all(p["s"] == 10.0 for p in self.log.read(with_speed["ids"][0])["points"]))
        without = tripimport.import_gpx(self.log, gpx([("B", drive(120, 10.0, day=2))]))
        speeds = [p["s"] for p in self.log.read(without["ids"][0])["points"][1:]]
        self.assertTrue(all(abs(s - 10.0) < 1.0 for s in speeds), speeds[:5])

    def test_a_file_with_no_times_gets_estimated_ones_and_says_so(self):
        result = tripimport.import_gpx(self.log, gpx([("No clock", drive(100, 10.0, with_times=False))]))
        trip = self.log.read(result["ids"][0])
        self.assertTrue(trip["header"]["est"])
        self.assertAlmostEqual(trip["end"]["t"], trip["end"]["dist"] / tripimport.EST_SPEED_MS, delta=1.0)

    def test_several_tracks_are_several_trips_and_a_route_is_used_when_there_is_no_track(self):
        two = gpx([("One", drive(120, 10.0)), ("Two", drive(120, 10.0, lat0=-41.0, day=2))])
        result = tripimport.import_gpx(self.log, two)
        self.assertEqual(len(result["ids"]), 2)
        self.assertEqual(sorted(self.log.read(i)["title"] for i in result["ids"]), ["One", "Two"])
        route = b'<?xml version="1.0"?><gpx version="1.1"><rte><name>Planned</name>' + b"".join(
            f'<rtept lat="{-40.0 + i * 0.001}" lon="170.0"/>'.encode() for i in range(10)) + b"</rte></gpx>"
        got = tripimport.import_gpx(self.log, route)
        self.assertEqual(len(got["ids"]), 1)

    def test_a_second_import_of_the_same_file_adds_nothing(self):
        data = gpx([("Ride", drive(300, 10.0))])
        tripimport.import_gpx(self.log, data)
        again = tripimport.import_gpx(self.log, data)
        self.assertEqual((again["ids"], again["duplicates"]), ([], 1))
        self.assertEqual(len(self.log.list()), 1)

    def test_a_track_too_short_to_be_a_trip_is_skipped_and_a_file_of_nothing_else_is_refused(self):
        mixed = gpx([("Real", drive(300, 10.0)), ("Nudge", drive(5, 2.0))])
        result = tripimport.import_gpx(self.log, mixed)
        self.assertEqual((len(result["ids"]), result["skipped"]), (1, 1))
        with self.assertRaisesRegex(EditError, "long enough"):
            tripimport.import_gpx(self.log, gpx([("Nudge", drive(5, 2.0))]))

    def test_a_gps_glitch_is_dropped_not_driven(self):
        pts = drive(120, 10.0)
        la, lo, ele, time, speed = pts[60]
        pts[60] = (la + 5.0, lo, ele, time, speed)  # 550 km away for one second
        trip = self.log.read(tripimport.import_gpx(self.log, gpx([("Glitch", pts)]))["ids"][0])
        self.assertAlmostEqual(trip["end"]["dist"], 1200.0, delta=30.0, msg="the jump is not counted")

    def test_points_with_impossible_coordinates_are_ignored(self):
        pts = drive(120, 10.0)
        pts.insert(30, (95.0, 174.8, 5.0, None, None))
        pts.insert(31, (-41.2, 200.0, 5.0, None, None))
        self.assertEqual(len(self.log.read(tripimport.import_gpx(self.log, gpx([("Bad points", pts)]))["ids"][0])["points"]) > 5, True)

    def test_a_name_is_cleaned_and_cut(self):
        long_name = "x" * 300
        trip = self.log.read(tripimport.import_gpx(self.log, gpx([(long_name, drive(300, 10.0))]))["ids"][0])
        self.assertEqual(len(trip["title"]), 80)

    def test_times_that_go_backwards_are_not_trusted(self):
        pts = drive(120, 10.0)
        pts[50], pts[51] = pts[51], pts[50]
        trip = self.log.read(tripimport.import_gpx(self.log, gpx([("Shuffled", pts)]))["ids"][0])
        self.assertTrue(trip["header"].get("est"), "the clock is made up rather than followed backwards")


class RefusalTests(Case):
    def test_what_is_not_a_gpx_file_is_refused_with_a_reason(self):
        for data, why in ((b"not xml at all", "not a valid GPX"), (b'<?xml version="1.0"?><kml/>', "not a GPX"), (b'<gpx version="1.1"></gpx>', "no track")):
            with self.assertRaisesRegex(EditError, why):
                tripimport.import_gpx(self.log, data)

    def test_a_file_that_declares_entities_is_refused_unread(self):
        bomb = b'<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol"><!ENTITY lol2 "&lol;&lol;&lol;">]><gpx>&lol2;</gpx>'
        with self.assertRaisesRegex(EditError, "entities"):
            tripimport.import_gpx(self.log, bomb)
        with self.assertRaisesRegex(EditError, "entities"):
            tripimport.import_gpx(self.log, b'<?xml version="1.0"?><!doctype gpx><gpx/>')

    def test_a_file_that_is_too_big_or_has_too_many_points_is_refused(self):
        with self.assertRaisesRegex(EditError, "too big"):
            tripimport.import_gpx(self.log, b"x" * (tripimport.MAX_BYTES + 1))
        old = tripimport.MAX_POINTS
        tripimport.MAX_POINTS = 50
        try:
            with self.assertRaisesRegex(EditError, "more than"):
                tripimport.import_gpx(self.log, gpx([("Many", drive(300, 10.0))]))
        finally:
            tripimport.MAX_POINTS = old

    def test_nothing_is_written_for_a_refused_file(self):
        with self.assertRaises(EditError):
            tripimport.import_gpx(self.log, b"junk")
        self.assertEqual(self.log.list(), [])


class ApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        data = Path(self.tmp.name)
        self.log = TripLog(data / "triplog", say=lambda t: None)
        self.changes = []
        self.log.on_change = lambda kind, tid: self.changes.append((kind, tid))
        self.port = free_port()
        app = make_app(Hub(triplog=self.log), self.port, LayoutStore(data / "layouts", web_root() / "layouts"), SettingsStore(data / "settings.json"))
        self.runner = web.AppRunner(app, access_log=None)
        await self.runner.setup()
        await web.TCPSite(self.runner, "127.0.0.1", self.port).start()
        self.base = f"http://127.0.0.1:{self.port}"
        self.http = aiohttp.ClientSession()
        self.h = {"X-ThrashDash": "1", "Content-Type": "application/gpx+xml"}

    async def asyncTearDown(self):
        await self.http.close()
        await self.runner.cleanup()
        self.log.close_all()
        self.tmp.cleanup()

    async def test_a_gpx_file_is_imported_and_the_page_is_told(self):
        async with self.http.post(f"{self.base}/api/triplog/import", data=gpx([("Ride", drive(300, 10.0))]), headers=self.h) as r:
            self.assertEqual(r.status, 200)
            body = await r.json()
        self.assertEqual((body["ok"], len(body["ids"]), body["skipped"], body["duplicates"]), (True, 1, 0, 0))
        self.assertEqual(self.changes, [("ended", body["ids"][0])])
        async with self.http.get(f"{self.base}/api/triplog") as r:
            self.assertEqual((await r.json())["trips"][0]["imported"], "gpx")

    async def test_junk_is_a_400_with_a_reason_and_a_missing_header_a_403(self):
        async with self.http.post(f"{self.base}/api/triplog/import", data=b"junk", headers=self.h) as r:
            self.assertEqual(r.status, 400)
            self.assertIn("not a valid GPX", (await r.json())["error"])
        async with self.http.post(f"{self.base}/api/triplog/import", data=gpx([("Ride", drive(300, 10.0))])) as r:
            self.assertEqual(r.status, 403)
        self.assertEqual(self.log.list(), [])

    async def test_a_body_that_is_too_big_is_refused_while_it_is_read(self):
        old = tripimport.MAX_BYTES
        tripimport.MAX_BYTES = 1000
        try:
            async with self.http.post(f"{self.base}/api/triplog/import", data=b"x" * 5000, headers=self.h) as r:
                self.assertEqual(r.status, 413)
        finally:
            tripimport.MAX_BYTES = old


if __name__ == "__main__":
    unittest.main()

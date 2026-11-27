"""Trip tags: cleaning, storing (a line at the end of the trip file), reading, joining, cutting and the API."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

import aiohttp
from aiohttp import web

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from thrashdash import tripedit  # noqa: E402
from thrashdash.hub import Hub  # noqa: E402
from thrashdash.paths import web_root  # noqa: E402
from thrashdash.server import make_app  # noqa: E402
from thrashdash.store import LayoutStore, SettingsStore  # noqa: E402
from thrashdash.triplog import MAX_TAG_CHARS, MAX_TAGS, PRESET_TAGS, Busy, NotFound, TripLog, clean_tags, stored_tags  # noqa: E402
from test_tripedit import write_trip  # noqa: E402
from test_triplog import free_port  # noqa: E402

A, B = "20260101T000000Z", "20260101T001000Z"


class CleanTagsTests(unittest.TestCase):
    def test_the_presets_are_offered_and_spelled_their_own_way(self):
        self.assertEqual(PRESET_TAGS, ("Commute", "Tour", "Track", "Off-road", "Test", "Event"))
        self.assertEqual(clean_tags(["commute", "TRACK", "off-ROAD"]), ["Commute", "Track", "Off-road"])

    def test_your_own_tags_are_kept_as_typed(self):
        self.assertEqual(clean_tags(["Hill climb", "Dad's car"]), ["Hill climb", "Dad's car"])

    def test_one_line_no_control_characters_short_and_no_repeats(self):
        self.assertEqual(clean_tags(["  Hill \n\tclimb  "]), ["Hill climb"])
        self.assertEqual(clean_tags(["a\x00b"]), ["a b"])
        self.assertEqual(len(clean_tags(["x" * 200])[0]), MAX_TAG_CHARS)
        self.assertEqual(clean_tags(["Rain", "rain", "RAIN", "Wet"]), ["Rain", "Wet"])  # (the first spelling stays)
        self.assertEqual(clean_tags(["", "   ", "\n"]), [])
        self.assertEqual(clean_tags([]), [])

    def test_the_order_given_is_kept(self):
        self.assertEqual(clean_tags(["b", "a", "c"]), ["b", "a", "c"])

    def test_too_many_is_an_error_unless_it_is_not_strict(self):
        many = [f"t{i}" for i in range(MAX_TAGS + 3)]
        with self.assertRaises(ValueError):
            clean_tags(many)
        self.assertEqual(clean_tags(many, strict=False), many[:MAX_TAGS])
        self.assertEqual(len(clean_tags(many[:MAX_TAGS])), MAX_TAGS)
        self.assertEqual(clean_tags(["same"] * 20), ["same"])  # repeats do not count

    def test_things_that_are_not_tags_are_errors(self):
        for bad in ("Track", None, 5, {"a": 1}, [5], ["ok", None], [["nested"]]):
            with self.assertRaises(ValueError, msg=repr(bad)):
                clean_tags(bad)

    def test_a_damaged_stored_value_gives_none_rather_than_an_error(self):
        for bad in ("Track", None, 5, [5], [["x"]]):
            self.assertEqual(stored_tags(bad), [], repr(bad))
        self.assertEqual(stored_tags(["Track", "Track"]), ["Track"])


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name) / "triplog"
        self.dir.mkdir()
        self.log = TripLog(self.dir, say=lambda text: None)

    def tearDown(self):
        self.log.close_all()
        self.tmp.cleanup()


class StoreTests(Base):
    def test_a_trip_has_no_tags_to_begin_with(self):
        write_trip(self.dir, A, 100)
        self.assertEqual(self.log.list()[0]["tags"], [])
        self.assertEqual(self.log.read(A)["tags"], [])

    def test_tags_are_set_as_a_whole_list_and_read_back_everywhere(self):
        write_trip(self.dir, A, 100)
        self.assertEqual(self.log.tag(A, ["track", "Hill climb"]), ["Track", "Hill climb"])
        self.assertEqual(self.log.list()[0]["tags"], ["Track", "Hill climb"])
        self.assertEqual(self.log.read(A)["tags"], ["Track", "Hill climb"])
        self.assertEqual(self.log.detail(A)["tags"], ["Track", "Hill climb"])
        self.assertEqual(self.log.routes()[0]["tags"], ["Track", "Hill climb"])

    def test_it_is_a_line_added_to_the_end_and_nothing_is_rewritten(self):
        write_trip(self.dir, A, 100, title="Morning run", starred=True)
        before = (self.dir / f"{A}.jsonl").read_text()
        self.log.tag(A, ["Commute"])
        after = (self.dir / f"{A}.jsonl").read_text()
        self.assertTrue(after.startswith(before))
        self.assertEqual(json.loads(after[len(before):]), {"k": "tags", "v": ["Commute"]})
        self.assertEqual((self.log.read(A)["title"], self.log.read(A)["starred"]), ("Morning run", True))  # (the name and star are untouched)

    def test_the_last_line_wins_and_an_empty_list_clears(self):
        write_trip(self.dir, A, 100)
        self.log.tag(A, ["Commute", "Test"])
        self.log.tag(A, ["Tour"])
        self.assertEqual(self.log.list()[0]["tags"], ["Tour"])
        self.assertEqual(self.log.read(A)["tags"], ["Tour"])
        self.log.tag(A, [])
        self.assertEqual(self.log.list()[0]["tags"], [])
        self.assertEqual(self.log.read(A)["tags"], [])

    def test_tagging_a_trip_keeps_its_summary_and_name(self):
        write_trip(self.dir, A, 100, title="Morning run")
        self.log.tag(A, ["Commute"])
        item = self.log.list()[0]
        self.assertEqual((item["title"], item["ended"]), ("Morning run", True))
        self.assertIn("stats", item)

    def test_bad_requests_change_nothing(self):
        write_trip(self.dir, A, 100)
        before = (self.dir / f"{A}.jsonl").read_text()
        for bad in ("Track", None, [5], [f"t{i}" for i in range(MAX_TAGS + 1)]):
            with self.assertRaises(ValueError, msg=repr(bad)):
                self.log.tag(A, bad)
        self.assertEqual((self.dir / f"{A}.jsonl").read_text(), before)
        with self.assertRaises(NotFound):
            self.log.tag("20200101T000000Z", ["Track"])
        with self.assertRaises(NotFound):
            self.log.tag("../../etc/passwd", ["Track"])

    def test_a_damaged_tags_line_does_not_stop_the_trip_being_read(self):
        write_trip(self.dir, A, 100)
        with open(self.dir / f"{A}.jsonl", "a") as f:
            f.write('{"k":"tags","v":"oops"}\n')
        self.assertEqual(self.log.list()[0]["tags"], [])
        self.assertEqual(self.log.read(A)["tags"], [])
        self.assertTrue(self.log.list()[0]["ended"])

    def test_a_hand_edited_file_with_too_many_tags_keeps_the_first_few(self):
        write_trip(self.dir, A, 100)
        with open(self.dir / f"{A}.jsonl", "a") as f:
            f.write(json.dumps({"k": "tags", "v": [f"t{i}" for i in range(20)]}) + "\n")
        self.assertEqual(self.log.read(A)["tags"], [f"t{i}" for i in range(MAX_TAGS)])

    def test_the_trip_being_recorded_cannot_be_tagged(self):
        class Fake:
            id = A

        self.log._trip = Fake()
        write_trip(self.dir, A, 100)
        with self.assertRaises(Busy):
            self.log.tag(A, ["Track"])
        self.log._trip = None


class EditTests(Base):
    def test_joining_two_trips_gives_the_tags_of_both(self):
        write_trip(self.dir, A, 100)
        write_trip(self.dir, B, 100, lat0=-41.2 + 0.001)  # (starts where the first ended)
        self.log.tag(A, ["Commute", "Test"])
        self.log.tag(B, ["test", "Event"])
        new = tripedit.join(self.log, A, B)
        self.assertEqual(self.log.read(new)["tags"], ["Commute", "Test", "Event"])

    def test_joining_never_gives_a_trip_more_tags_than_one_may_have(self):
        write_trip(self.dir, A, 100)
        write_trip(self.dir, B, 100, lat0=-41.2 + 0.001)
        self.log.tag(A, [f"a{i}" for i in range(MAX_TAGS)])
        self.log.tag(B, [f"b{i}" for i in range(MAX_TAGS)])
        new = tripedit.join(self.log, A, B)
        self.assertEqual(self.log.read(new)["tags"], [f"a{i}" for i in range(MAX_TAGS)])

    def test_cutting_a_trip_gives_both_halves_its_tags(self):
        write_trip(self.dir, A, 200)
        self.log.tag(A, ["Track"])
        first, second = tripedit.cut(self.log, A, 95)
        self.assertEqual(self.log.read(first)["tags"], ["Track"])
        self.assertEqual(self.log.read(second)["tags"], ["Track"])

    def test_untagged_trips_join_and_cut_without_a_tags_line(self):
        write_trip(self.dir, A, 200)
        first, second = tripedit.cut(self.log, A, 95)
        self.assertNotIn('"k":"tags"', (self.dir / f"{first}.jsonl").read_text())

    def test_tags_survive_a_backup_and_restore(self):
        write_trip(self.dir, A, 100)
        self.log.tag(A, ["Track", "Hill climb"])
        data = tripedit.backup(self.log)
        (self.dir / f"{A}.jsonl").unlink()
        tripedit.restore(self.log, data)
        self.assertEqual(self.log.read(A)["tags"], ["Track", "Hill climb"])


class ApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        data = Path(self.tmp.name)
        (data / "triplog").mkdir()
        write_trip(data / "triplog", A, 100)
        self.log = TripLog(data / "triplog", say=lambda text: None)
        self.port = free_port()
        app = make_app(Hub(triplog=self.log), self.port, LayoutStore(data / "layouts", web_root() / "layouts"), SettingsStore(data / "settings.json"))
        self.runner = web.AppRunner(app, access_log=None)
        await self.runner.setup()
        await web.TCPSite(self.runner, "127.0.0.1", self.port).start()
        self.base = f"http://127.0.0.1:{self.port}/api/triplog"
        self.http = aiohttp.ClientSession()
        self.h = {"X-ThrashDash": "1"}

    async def asyncTearDown(self):
        await self.http.close()
        await self.runner.cleanup()
        self.log.close_all()
        self.tmp.cleanup()

    async def test_the_list_says_which_tags_are_offered_to_begin_with(self):
        async with self.http.get(self.base) as r:
            got = await r.json()
        self.assertEqual(got["presetTags"], list(PRESET_TAGS))
        self.assertEqual(got["trips"][0]["tags"], [])

    async def test_patching_tags_and_reading_them_back(self):
        async with self.http.patch(f"{self.base}/{A}", json={"tags": ["track", "Hill climb"]}, headers=self.h) as r:
            self.assertEqual(r.status, 200)
            self.assertEqual(await r.json(), {"ok": True, "tags": ["Track", "Hill climb"]})
        async with self.http.get(f"{self.base}/{A}") as r:
            self.assertEqual((await r.json())["tags"], ["Track", "Hill climb"])
        async with self.http.get(self.base) as r:
            self.assertEqual((await r.json())["trips"][0]["tags"], ["Track", "Hill climb"])
        async with self.http.get(f"{self.base}/routes") as r:
            self.assertEqual((await r.json())["trips"][0]["tags"], ["Track", "Hill climb"])

    async def test_tags_can_go_with_a_name_and_a_star(self):
        async with self.http.patch(f"{self.base}/{A}", json={"name": "Hill", "starred": True, "tags": ["Event"]}, headers=self.h) as r:
            self.assertEqual(await r.json(), {"ok": True, "title": "Hill", "starred": True, "tags": ["Event"]})

    async def test_bad_tags_are_refused_and_change_nothing(self):
        for bad in ("Track", None, [5], [f"t{i}" for i in range(MAX_TAGS + 1)]):
            async with self.http.patch(f"{self.base}/{A}", json={"tags": bad}, headers=self.h) as r:
                self.assertEqual(r.status, 400, repr(bad))
        async with self.http.get(f"{self.base}/{A}") as r:
            self.assertEqual((await r.json())["tags"], [])
        async with self.http.patch(f"{self.base}/20200101T000000Z", json={"tags": ["Track"]}, headers=self.h) as r:
            self.assertEqual(r.status, 404)
        async with self.http.patch(f"{self.base}/{A}", json={"tags": ["Track"]}) as r:
            self.assertEqual(r.status, 403)  # no X-ThrashDash header


if __name__ == "__main__":
    unittest.main()

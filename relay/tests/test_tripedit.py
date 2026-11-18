"""Tests for changing finished trips: joining two, cutting one, and backing up and restoring (tripedit.py).

The trips are written by hand, line by line, so each rule can be checked against numbers worked out on paper.
"""
import io
import json
import sys
import tempfile
import unittest
import zipfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from thrashdash import tripedit  # noqa: E402
from thrashdash.tripedit import EditError, cut, join  # noqa: E402
from thrashdash.triplog import NotFound, TripLog  # noqa: E402


def write_trip(folder, tid, seconds, lat0=-41.2, lon0=174.8, veh=1, model="etkc", reason="idle", title=None, starred=False, speed=10.0, step=0.0001):
    """A finished trip of `seconds` seconds: a point every 10 s heading north at `speed` m/s, a place reading each 20 s."""
    ts = int(datetime.strptime(tid[:15], "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc).timestamp())  # (the trip's start time is its id, as in a real trip file)
    rows = [{"k": "trip", "fmt": 1, "id": tid, "ts": ts, "vehicle": model, "veh": veh, "energy": "liquid"}]
    n = 0
    t = 0
    while t <= seconds:
        rows.append({"k": "p", "t": float(t), "la": round(lat0 + n * step, 7), "lo": lon0, "al": 5.0, "s": speed, "h": 0.0, "d": round(t * speed, 1), "fv": round(40.0 - t * 0.01, 3)})
        if t % 20 == 0:
            rows.append({"k": "i", "t": float(t), "place": {"street": f"Street {int(t)}", "place": "Wellington", "country": "New Zealand"}})
        n += 1
        t += 10
    rows.append({"k": "end", "reason": reason, "t": float(seconds), "ts": ts + seconds, "dist": round(seconds * speed, 1), "top": speed, "n": n})
    if title:
        rows.append({"k": "name", "name": title})
    if starred:
        rows.append({"k": "star", "v": True})
    (folder / f"{tid}.jsonl").write_text("\n".join(json.dumps(r, separators=(",", ":")) for r in rows) + "\n")


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name) / "triplog"
        self.dir.mkdir()
        self.log = TripLog(self.dir, say=lambda text: None)

    def tearDown(self):
        self.log.close_all()
        self.tmp.cleanup()

    def files(self):
        return sorted(p.stem for p in self.dir.glob("*.jsonl"))

    def trashed(self):
        trash = self.dir / "trash"
        return sorted(p.stem for p in trash.glob("*.jsonl")) if trash.is_dir() else []


class JoinTests(Base):
    # the first drive: 0 to 100 s; a stop; the second starts 7 minutes (420 s) after the first began
    def setUp(self):
        super().setUp()
        write_trip(self.dir, "20260101T000000Z", 100, title="Morning run", starred=True)
        write_trip(self.dir, "20260101T000700Z", 60, lat0=-41.2 + 0.0010, reason="teleport")  # (starts where the first ended: ten points of 0.0001 degrees north)

    def test_two_trips_become_one_with_the_real_clock_and_distance(self):
        new = join(self.log, "20260101T000000Z", "20260101T000700Z")
        self.assertEqual(new, "20260101T000000Z")
        self.assertEqual(self.files(), ["20260101T000000Z"])
        self.assertEqual(self.trashed(), ["20260101T000000Z", "20260101T000700Z"])
        data = self.log.read(new)
        self.assertEqual(data["end"]["t"], 480.0, "420 s to the second trip's start, plus its 60 s")
        self.assertEqual(data["end"]["dist"], 1000.0 + 600.0)
        self.assertEqual(data["end"]["reason"], "teleport", "it ends the way the second one did")
        times = [p["t"] for p in data["points"]]
        self.assertEqual(times, sorted(times))
        self.assertEqual(data["points"][-1]["d"], 1600.0)
        self.assertEqual(data["end"]["n"], 11 + 7, "the stored count is of the real points; the two that mark the wait are not counted")
        self.assertEqual(len(data["points"]), 11 + 2 + 7)
        self.assertEqual((data["title"], data["starred"]), ("Morning run", True), "the first trip's name and star are kept")

    def test_the_joined_trip_has_a_summary_worked_out_afresh(self):
        new = join(self.log, "20260101T000000Z", "20260101T000700Z")
        item = {t["id"]: t for t in self.log.list()}[new]
        self.assertEqual(item["stats"]["dist"], 1600.0)
        self.assertGreater(item["stats"]["stopped_s"], 300, "the wait between them counts as time stood still")
        stops = item["stats"].get("stops") or []
        self.assertEqual(len(stops), 1, "and shows as a stop")
        self.assertGreater(stops[0]["dur"], 300)

    def test_how_the_first_trip_ended_does_not_matter_where_the_car_was_does(self):
        # a teleport or a jump that landed right where the car was still leaves one drive...
        for n, reason in enumerate(("teleport", "jump", "level")):
            first, second = f"2026020{n + 1}T000000Z", f"2026020{n + 1}T000700Z"
            write_trip(self.dir, first, 100, reason=reason)
            write_trip(self.dir, second, 60, lat0=-41.2 + 0.0010)
            self.assertEqual(join(self.log, first, second), first, reason)
        # ...and one that landed far away does not
        write_trip(self.dir, "20260110T000000Z", 100, reason="teleport")
        write_trip(self.dir, "20260110T000700Z", 60, lat0=-35.0)
        with self.assertRaisesRegex(EditError, "km from where the first ended"):
            join(self.log, "20260110T000000Z", "20260110T000700Z")
        self.assertIn("20260110T000000Z", self.files(), "nothing is moved when it is refused")

    def test_a_trip_that_starts_the_moment_the_last_one_ended_is_not_an_overlap(self):
        # Start times are whole seconds and end times are not: a drive cut by a position jump has its second trip start "0.01 s early".
        write_trip(self.dir, "20260113T000000Z", 30)
        path = self.dir / "20260113T000000Z.jsonl"
        lines = path.read_text().splitlines()
        end = json.loads(lines[-1])
        end["t"] = 30.01
        path.write_text("\n".join(lines[:-1] + [json.dumps(end, separators=(",", ":"))]) + "\n")
        write_trip(self.dir, "20260113T000030Z", 40, lat0=-41.2 + 0.0003 + 0.001)  # (124 m on, as after a jump)
        self.assertEqual(join(self.log, "20260113T000000Z", "20260113T000030Z"), "20260113T000000Z")
        data = self.log.read("20260113T000000Z")
        times = [p["t"] for p in data["points"]]
        self.assertEqual(times, sorted(times), "the joined route is in time order")

    def test_trips_that_truly_overlap_are_still_refused(self):
        write_trip(self.dir, "20260114T000000Z", 100)
        write_trip(self.dir, "20260114T000030Z", 40)  # (starts 30 s in, while the first is still going)
        with self.assertRaisesRegex(EditError, "overlap"):
            join(self.log, "20260114T000000Z", "20260114T000030Z")

    def test_a_join_that_does_not_look_like_one_drive_can_be_forced(self):
        # hours apart, far apart, and a different car: all the "does it look like one drive" rules, past in one go
        write_trip(self.dir, "20260120T000000Z", 100)
        write_trip(self.dir, "20260120T090000Z", 60, lat0=-35.0, veh=2, model="pickup")
        with self.assertRaises(EditError):
            join(self.log, "20260120T000000Z", "20260120T090000Z")
        self.assertEqual(self.files().count("20260120T000000Z"), 1, "refused: nothing moved")
        new = join(self.log, "20260120T000000Z", "20260120T090000Z", force=True)
        self.assertEqual(new, "20260120T000000Z")
        data = self.log.read(new)
        self.assertEqual(data["header"]["vehicle"], "etkc", "it keeps the first trip's vehicle")
        self.assertEqual(data["end"]["t"], 9 * 3600 + 60.0)
        self.assertEqual(data["end"]["dist"], 1000.0 + 600.0, "the distance is what was driven, not the gap")
        times = [p["t"] for p in data["points"]]
        self.assertEqual(times, sorted(times))
        stats = {t["id"]: t for t in self.log.list()}[new]["stats"]
        self.assertEqual(len(stats["stops"]), 1, "the break shows as a stop")
        self.assertGreater(stats["stops"][0]["dur"], 8 * 3600)

    def test_what_makes_no_sense_cannot_be_forced(self):
        write_trip(self.dir, "20260121T000000Z", 100)
        write_trip(self.dir, "20260121T000030Z", 40)  # (starts while the first is still going)
        write_trip(self.dir, "20260121T120000Z", 40)
        for first, second, why in (("20260121T000000Z", "20260121T000000Z", "different"), ("20260121T120000Z", "20260121T000000Z", "earlier"), ("20260121T000000Z", "20260121T000030Z", "overlap")):
            with self.assertRaisesRegex(EditError, why):
                join(self.log, first, second, force=True)

    def test_join_options_says_when_a_refusal_could_be_forced(self):
        write_trip(self.dir, "20260122T000000Z", 100)
        write_trip(self.dir, "20260122T120000Z", 60, lat0=-35.0)
        options = tripedit.join_options(self.log, "20260122T000000Z")["next"]
        self.assertEqual((options["ok"], options["forceable"]), (False, True))
        self.assertIsNotNone(options["reason"])
        self.assertEqual(tripedit.join_options(self.log, "20260101T000000Z")["next"]["forceable"], False, "a join that works needs no forcing")

    def test_the_same_model_with_a_different_game_id_is_the_same_vehicle(self):
        write_trip(self.dir, "20260111T000000Z", 100, veh=1)
        write_trip(self.dir, "20260111T000700Z", 60, veh=7, lat0=-41.2 + 0.0010)  # (respawned: the game gave the car a new id)
        self.assertEqual(join(self.log, "20260111T000000Z", "20260111T000700Z"), "20260111T000000Z")

    def test_join_options_offers_only_the_joins_that_would_work(self):
        write_trip(self.dir, "20260112T000000Z", 100)  # (a day on: too far in time from the pair set up above)
        options = tripedit.join_options(self.log, "20260101T000000Z")
        self.assertEqual((options["next"]["id"], options["next"]["ok"], options["next"]["reason"]), ("20260101T000700Z", True, None))
        self.assertTrue(options["next"]["title"], "unnamed trips are shown by the name worked out for them")
        self.assertIsNone(options["prev"], "nothing before it")
        options = tripedit.join_options(self.log, "20260101T000700Z")
        self.assertEqual((options["prev"]["id"], options["prev"]["title"], options["prev"]["ok"]), ("20260101T000000Z", "Morning run", True))
        self.assertEqual(options["next"]["id"], "20260112T000000Z", "the neighbour is still named...")
        self.assertFalse(options["next"]["ok"])
        self.assertIn("too far apart in time", options["next"]["reason"], "...with the reason it cannot be joined")
        self.assertEqual(tripedit.join_options(self.log, "20990101T000000Z"), {"prev": None, "next": None}, "an unknown trip has none")

    def test_different_vehicles_cannot_be_joined(self):
        write_trip(self.dir, "20260103T000000Z", 100, veh=1)
        write_trip(self.dir, "20260103T000700Z", 60, veh=2, model="pickup", lat0=-41.2 + 0.0010)
        with self.assertRaisesRegex(EditError, "different vehicles"):
            join(self.log, "20260103T000000Z", "20260103T000700Z")

    def test_trips_far_apart_cannot_be_joined(self):
        write_trip(self.dir, "20260104T000000Z", 100)
        write_trip(self.dir, "20260104T000700Z", 60, lat0=-40.0)
        with self.assertRaisesRegex(EditError, "km from where the first ended"):
            join(self.log, "20260104T000000Z", "20260104T000700Z")

    def test_trips_hours_apart_cannot_be_joined(self):
        write_trip(self.dir, "20260105T000000Z", 100)
        write_trip(self.dir, "20260105T120000Z", 60, lat0=-41.2 + 0.0010)
        with self.assertRaisesRegex(EditError, "far apart in time"):
            join(self.log, "20260105T000000Z", "20260105T120000Z")

    def test_the_order_matters_and_a_trip_cannot_join_itself(self):
        with self.assertRaisesRegex(EditError, "earlier"):
            join(self.log, "20260101T000700Z", "20260101T000000Z")
        with self.assertRaisesRegex(EditError, "different"):
            join(self.log, "20260101T000000Z", "20260101T000000Z")

    def test_an_unknown_trip_is_not_found(self):
        with self.assertRaises(NotFound):
            join(self.log, "20260101T000000Z", "20300101T000000Z")


class CutTests(Base):
    def setUp(self):
        super().setUp()
        write_trip(self.dir, "20260201T000000Z", 200, title="Long drive", starred=True)
        self.start = int(datetime(2026, 2, 1, tzinfo=timezone.utc).timestamp())

    def test_a_trip_is_cut_in_two_each_with_its_own_clock_and_distance(self):
        first, second = cut(self.log, "20260201T000000Z", 95)
        self.assertEqual(first, "20260201T000000Z")
        self.assertNotEqual(second, first)
        self.assertEqual(self.trashed(), ["20260201T000000Z"])
        a, b = self.log.read(first), self.log.read(second)
        self.assertEqual(a["points"][-1]["t"], 90.0, "the first part ends at its last point before the cut")
        self.assertEqual(a["end"]["reason"], "split")
        self.assertEqual(a["end"]["dist"], 900.0)
        self.assertEqual(b["points"][0]["t"], 0.0, "the second part's clock starts at zero")
        self.assertEqual(b["points"][0]["d"], 0.0, "and its distance")
        self.assertEqual(b["end"]["t"], 100.0)
        self.assertEqual(b["end"]["dist"], 1000.0)
        self.assertEqual(b["end"]["reason"], "idle", "the second part ends the way the original did")
        self.assertEqual(b["header"]["ts"], self.start + 100, "it begins at its first point's real time")

    def test_the_first_part_keeps_the_name_and_star_and_the_second_is_plain(self):
        first, second = cut(self.log, "20260201T000000Z", 95)
        self.assertEqual((self.log.read(first)["title"], self.log.read(first)["starred"]), ("Long drive", True))
        self.assertEqual((self.log.read(second)["title"], self.log.read(second)["starred"]), (None, False))

    def test_the_second_part_knows_where_it_is_from_its_first_moment(self):
        _, second = cut(self.log, "20260201T000000Z", 95)
        first_reading = self.log.read(second)["info"][0]
        self.assertEqual(first_reading["t"], 0.0)
        self.assertEqual(first_reading["place"]["street"], "Street 80", "the last reading before the cut is carried over")

    def test_both_parts_have_summaries_that_add_up(self):
        first, second = cut(self.log, "20260201T000000Z", 95)
        items = {t["id"]: t for t in self.log.list()}
        self.assertAlmostEqual(items[first]["stats"]["dist"] + items[second]["stats"]["dist"], 1900.0, delta=1.0)  # (the 100 m between the last point and the first of the next is the gap)

    def test_cuts_too_near_an_end_or_outside_are_refused(self):
        for at in (0, 2, 4.9, 196, 200, 500, -5):
            with self.assertRaises(EditError, msg=str(at)):
                cut(self.log, "20260201T000000Z", at)
        for bad in (None, "x", float("nan"), True):
            with self.assertRaises(EditError, msg=str(bad)):
                cut(self.log, "20260201T000000Z", bad)
        self.assertEqual(self.files(), ["20260201T000000Z"], "nothing is moved when it is refused")

    def test_a_second_cut_on_the_same_second_gets_a_free_id(self):
        write_trip(self.dir, "20260201T000140Z", 30)  # (the id the second part would take)
        _, second = cut(self.log, "20260201T000000Z", 95)
        self.assertTrue(second.startswith("20260201T000140Z-"), second)

    def test_an_unknown_trip_is_not_found(self):
        with self.assertRaises(NotFound):
            cut(self.log, "20300101T000000Z", 50)


class BackupTests(Base):
    def setUp(self):
        super().setUp()
        write_trip(self.dir, "20260301T000000Z", 100)
        write_trip(self.dir, "20260302T000000Z", 100)
        (self.dir / "trash").mkdir()
        write_trip(self.dir / "trash", "20260303T000000Z", 100)

    def test_a_backup_holds_every_trip_but_not_the_trash(self):
        data = tripedit.backup(self.log)
        names = sorted(zipfile.ZipFile(io.BytesIO(data)).namelist())
        self.assertEqual(names, ["20260301T000000Z.jsonl", "20260302T000000Z.jsonl", "README.txt"])

    def test_restoring_into_an_empty_log_brings_everything_back(self):
        data = tripedit.backup(self.log)
        other = TripLog(Path(self.tmp.name) / "other", say=lambda text: None)
        result = tripedit.restore(other, data)
        self.assertEqual(result, {"added": 2, "skipped": 0, "rejected": 0})
        self.assertEqual((Path(self.tmp.name) / "other" / "20260301T000000Z.jsonl").read_bytes(), (self.dir / "20260301T000000Z.jsonl").read_bytes())
        other.close_all()

    def test_restoring_twice_adds_nothing_the_second_time_and_never_overwrites(self):
        data = tripedit.backup(self.log)
        (self.dir / "20260301T000000Z.jsonl").write_text((self.dir / "20260301T000000Z.jsonl").read_text() + '{"k":"name","name":"Mine"}\n')
        result = tripedit.restore(self.log, data)
        self.assertEqual(result, {"added": 0, "skipped": 2, "rejected": 0})
        self.assertEqual(self.log.read("20260301T000000Z")["title"], "Mine", "the trip that was there is untouched")

    def test_odd_entries_are_rejected_not_extracted(self):
        buf = io.BytesIO()
        good = (self.dir / "20260301T000000Z.jsonl").read_text()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("../escape.jsonl", good)  # (a path out of the folder)
            z.writestr("sub/20260301T000000Z.jsonl", good)  # (not flat)
            z.writestr("notes.txt", "hello")
            z.writestr("20260401T000000Z.jsonl", "this is not json\n")  # (damaged)
            z.writestr("20260402T000000Z.jsonl", good)  # (the header's id does not match the name)
            z.writestr("20260301T000000Z.jsonl", good)  # (fine)
        other = TripLog(Path(self.tmp.name) / "other", say=lambda text: None)
        result = tripedit.restore(other, buf.getvalue())
        self.assertEqual(result, {"added": 1, "skipped": 0, "rejected": 5})
        self.assertEqual(sorted(p.name for p in (Path(self.tmp.name) / "other").iterdir()), ["20260301T000000Z.jsonl"])
        self.assertFalse((Path(self.tmp.name) / "escape.jsonl").exists())
        other.close_all()

    def test_something_that_is_not_a_zip_is_refused(self):
        with self.assertRaisesRegex(EditError, "not a zip"):
            tripedit.restore(self.log, b"this is not a zip")
        with self.assertRaisesRegex(EditError, "too big"):
            tripedit.restore(self.log, b"x" * (tripedit.RESTORE_MAX_BYTES + 1))

    def test_a_restored_trip_is_listed_with_its_summary(self):
        data = tripedit.backup(self.log)
        other = TripLog(Path(self.tmp.name) / "other", say=lambda text: None)
        tripedit.restore(other, data)
        self.assertEqual(len(other.list()), 2)
        self.assertTrue(all(t.get("stats") for t in other.list()))
        other.close_all()


if __name__ == "__main__":
    unittest.main()

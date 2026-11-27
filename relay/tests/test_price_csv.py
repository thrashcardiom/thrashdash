"""The fuel price saved with each trip (and the setting it is copied from), and the CSV of a trip's points."""
import csv
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

import aiohttp
from aiohttp import web

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from thrashdash import tripedit, tripexport  # noqa: E402
from thrashdash.hub import Hub  # noqa: E402
from thrashdash.paths import web_root  # noqa: E402
from thrashdash.server import make_app  # noqa: E402
from thrashdash.store import LayoutStore, SettingsStore  # noqa: E402
from thrashdash.triplog import Busy, NotFound, TripLog, clean_price, stored_price  # noqa: E402
from thrashdash.validate import FUEL_PRICE_MAX, ValidationError, clean_fuel_price, validate_settings  # noqa: E402
from test_tripedit import write_trip  # noqa: E402
from test_triplog import LogCase, free_port  # noqa: E402

A, B = "20260101T000000Z", "20260101T001000Z"
PRICE = {"liquid": 2.45, "electric": 0.31, "currency": "$"}


class SettingTests(unittest.TestCase):
    def test_a_price_is_checked(self):
        self.assertEqual(validate_settings({"fuelPrice": {"liquid": 2.45}})["fuelPrice"], {"liquid": 2.45})  # (a patch names only what it changes)
        self.assertEqual(validate_settings({"fuelPrice": PRICE})["fuelPrice"], PRICE)
        self.assertEqual(validate_settings({"fuelPrice": {"liquid": 0, "currency": ""}})["fuelPrice"], {"liquid": 0.0, "currency": ""})
        for bad in ("2", 5, [], {"liquid": -1}, {"liquid": FUEL_PRICE_MAX + 1}, {"liquid": "2"}, {"liquid": True}, {"liquid": float("nan")}, {"liquid": float("inf")},
                    {"electric": None}, {"currency": 5}, {"currency": "TOOLONG"}):
            with self.assertRaises(ValidationError, msg=repr(bad)):
                validate_settings({"fuelPrice": bad})

    def test_a_currency_is_one_printable_line(self):
        self.assertEqual(clean_fuel_price({"currency": " NZ\n$ "})["currency"], "NZ $")

    def test_the_store_has_no_price_to_begin_with_and_keeps_each_part(self):
        with tempfile.TemporaryDirectory() as d:
            store = SettingsStore(Path(d) / "settings.json")
            self.assertEqual(store.get()["fuelPrice"], {"liquid": 0.0, "electric": 0.0, "currency": ""})
            store.update({"fuelPrice": {"liquid": 2.5}})
            store.update({"fuelPrice": {"currency": "€"}})  # (separately: each part is kept)
            self.assertEqual(SettingsStore(Path(d) / "settings.json").get()["fuelPrice"], {"liquid": 2.5, "electric": 0.0, "currency": "€"})
            store.update({"units": {"speed": "mph"}})
            self.assertEqual(store.get()["fuelPrice"]["liquid"], 2.5)


class CleanPriceTests(unittest.TestCase):
    def test_a_price_with_nothing_in_it_is_no_price(self):
        for none in (None, {}, {"liquid": 0, "electric": 0, "currency": "$"}):
            self.assertIsNone(clean_price(none))

    def test_a_price_is_filled_out(self):
        self.assertEqual(clean_price({"liquid": 2}), {"liquid": 2.0, "electric": 0.0, "currency": ""})
        self.assertEqual(clean_price(PRICE), PRICE)

    def test_not_a_price_is_an_error_when_set_and_ignored_when_read(self):
        for bad in ("2.45", 5, {"liquid": "x"}, {"liquid": -1}):
            with self.assertRaises(ValueError):
                clean_price(bad)
            self.assertIsNone(stored_price(bad))


class RecordingTests(LogCase):
    def test_a_trip_begins_with_the_price_as_it_is_when_it_starts(self):
        self.log.price_source = lambda: PRICE
        self.d.drive(120, speed=15)
        (head, _, _), = self.finished()
        self.assertEqual(head["price"], PRICE)
        item = self.log.list()[0]
        self.assertEqual(item["price"], PRICE)
        self.assertEqual(self.log.read(item["id"])["price"], PRICE)

    def test_changing_the_price_later_does_not_change_a_trip_already_recorded(self):
        current = {"liquid": 2.0, "electric": 0.0, "currency": "$"}
        self.log.price_source = lambda: current
        self.d.drive(60, speed=15)
        self.log.close_all()
        current["liquid"] = 9.99  # the price went up
        self.assertEqual(self.log.list()[0]["price"]["liquid"], 2.0)

    def test_with_no_price_set_the_trip_has_none(self):
        self.log.price_source = lambda: {"liquid": 0.0, "electric": 0.0, "currency": ""}
        self.d.drive(60, speed=15)
        (head, _, _), = self.finished()
        self.assertNotIn("price", head)
        self.assertIsNone(self.log.list()[0]["price"])

    def test_no_source_means_no_price(self):
        self.d.drive(60, speed=15)
        (head, _, _), = self.finished()
        self.assertNotIn("price", head)

    def test_a_broken_source_never_stops_a_trip_being_recorded(self):
        def boom():
            raise RuntimeError("settings unreadable")

        self.log.price_source = boom
        self.d.drive(60, speed=15)
        (head, points, _), = self.finished()
        self.assertTrue(points)
        self.assertNotIn("price", head)

    def test_a_damaged_price_in_a_header_is_ignored(self):
        self.log.price_source = lambda: PRICE
        self.d.drive(60, speed=15)
        self.log.close_all()
        path = self.files()[0]
        text = path.read_text().replace('"liquid":2.45', '"liquid":"x"', 1)
        path.write_text(text)
        self.assertIsNone(self.log.list()[0]["price"])


class SetPriceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name) / "triplog"
        self.dir.mkdir()
        self.log = TripLog(self.dir, say=lambda text: None)

    def tearDown(self):
        self.log.close_all()
        self.tmp.cleanup()

    def test_a_trip_can_be_priced_afterwards_by_a_line_at_the_end(self):
        write_trip(self.dir, A, 100)
        before = (self.dir / f"{A}.jsonl").read_text()
        self.assertEqual(self.log.set_price(A, PRICE), PRICE)
        after = (self.dir / f"{A}.jsonl").read_text()
        self.assertTrue(after.startswith(before))
        self.assertEqual(json.loads(after[len(before):]), {"k": "price", "v": PRICE})
        self.assertEqual(self.log.list()[0]["price"], PRICE)
        self.assertEqual(self.log.read(A)["price"], PRICE)
        self.assertEqual(self.log.detail(A)["price"], PRICE)

    def test_the_last_line_beats_the_price_the_trip_began_with(self):
        write_trip(self.dir, A, 100)
        path = self.dir / f"{A}.jsonl"
        rows = path.read_text().splitlines()
        rows[0] = json.dumps({**json.loads(rows[0]), "price": {"liquid": 1.0, "electric": 0.0, "currency": "$"}})
        path.write_text("\n".join(rows) + "\n")
        self.assertEqual(self.log.list()[0]["price"]["liquid"], 1.0)
        self.log.set_price(A, {"liquid": 2.0, "currency": "$"})
        self.assertEqual(self.log.list()[0]["price"]["liquid"], 2.0)
        self.assertEqual(self.log.read(A)["price"]["liquid"], 2.0)
        self.log.set_price(A, None)  # taken away: the header's no longer counts either
        self.assertIsNone(self.log.list()[0]["price"])
        self.assertIsNone(self.log.read(A)["price"])

    def test_bad_prices_change_nothing(self):
        write_trip(self.dir, A, 100)
        before = (self.dir / f"{A}.jsonl").read_text()
        for bad in ("2", 5, {"liquid": -3}, {"liquid": "x"}):
            with self.assertRaises(ValueError, msg=repr(bad)):
                self.log.set_price(A, bad)
        self.assertEqual((self.dir / f"{A}.jsonl").read_text(), before)
        with self.assertRaises(NotFound):
            self.log.set_price("20200101T000000Z", PRICE)

    def test_the_trip_being_recorded_cannot_be_priced(self):
        class Fake:
            id = A

        write_trip(self.dir, A, 100)
        self.log._trip = Fake()
        with self.assertRaises(Busy):
            self.log.set_price(A, PRICE)
        self.log._trip = None

    def test_joining_and_cutting_keep_the_price(self):
        write_trip(self.dir, A, 100)
        write_trip(self.dir, B, 100, lat0=-41.2 + 0.001)
        self.log.set_price(B, PRICE)
        new = tripedit.join(self.log, A, B)
        self.assertEqual(self.log.read(new)["price"], PRICE)  # (the first had none: the second's is used)
        first, second = tripedit.cut(self.log, new, 95)
        self.assertEqual(self.log.read(first)["price"], PRICE)
        self.assertEqual(self.log.read(second)["price"], PRICE)

    def test_joining_uses_the_first_trips_price_when_both_have_one(self):
        write_trip(self.dir, A, 100)
        write_trip(self.dir, B, 100, lat0=-41.2 + 0.001)
        self.log.set_price(A, {"liquid": 2.0, "currency": "$"})
        self.log.set_price(B, {"liquid": 3.0, "currency": "$"})
        self.assertEqual(self.log.read(tripedit.join(self.log, A, B))["price"]["liquid"], 2.0)


def parse(text):
    assert text.startswith("﻿")
    return list(csv.reader(io.StringIO(text[1:])))


class CsvTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name) / "triplog"
        self.dir.mkdir()
        self.log = TripLog(self.dir, say=lambda text: None)

    def tearDown(self):
        self.log.close_all()
        self.tmp.cleanup()

    def csv(self, tid=A):
        return parse(tripexport.to_csv(self.log.detail(tid)))

    def test_one_row_per_point_with_units_in_the_column_names(self):
        write_trip(self.dir, A, 100)
        rows = self.csv()
        self.assertEqual(rows[0], ["t_s", "time_utc", "lat", "lon", "altitude_m", "speed_ms", "heading_deg", "distance_m", "speed_limit_ms", "limit_is_posted",
                                   "street", "place", "fuel_fraction", "fuel_volume_l", "body_damage_pct", "peak_g"])
        self.assertEqual(len(rows) - 1, 11)  # a point every 10 s for 100 s
        self.assertTrue(all(len(r) == len(rows[0]) for r in rows))
        self.assertEqual(rows[1][:2], ["0.00", "2026-01-01T00:00:00.000Z"])
        self.assertEqual(rows[2][:2], ["10.00", "2026-01-01T00:00:10.000Z"])
        self.assertEqual([rows[1][2], rows[1][3], rows[1][5], rows[1][13]], ["-41.2000000", "174.8000000", "10.00", "40.000"])

    def test_the_volume_column_says_litres_or_kilowatt_hours(self):
        write_trip(self.dir, A, 50)
        path = self.dir / f"{A}.jsonl"
        path.write_text(path.read_text().replace('"energy":"liquid"', '"energy":"electric"'))
        self.assertIn("fuel_volume_kwh", self.csv()[0])
        path.write_text(path.read_text().replace('"energy":"electric"', '"energy":null'))
        self.assertIn("fuel_volume", self.csv()[0])

    def test_the_street_and_limit_are_those_of_the_latest_reading(self):
        write_trip(self.dir, A, 60)
        path = self.dir / f"{A}.jsonl"
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        rows.insert(-1, {"k": "i", "t": 25.0, "place": {"street": "Oriental Parade", "place": "Wellington"}, "lim": {"ms": 13.89, "m": True, "u": "kmh"}})
        rows.insert(-1, {"k": "i", "t": 45.0, "place": {"street": "Cuba Street", "place": "Wellington"}, "lim": {"ms": 11.11, "m": False, "u": "kmh"}})
        path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
        out = {r[0]: r for r in self.csv()[1:]}
        self.assertEqual([out["30.00"][8], out["30.00"][9], out["30.00"][10]], ["13.89", "true", "Oriental Parade"])
        self.assertEqual([out["50.00"][8], out["50.00"][9], out["50.00"][10]], ["11.11", "false", "Cuba Street"])  # a limit that was only a guess is "false", not blank

    def test_a_reading_that_is_too_old_is_not_where_the_car_is(self):
        write_trip(self.dir, A, 100)
        path = self.dir / f"{A}.jsonl"
        rows = [json.loads(line) for line in path.read_text().splitlines() if '"k":"i"' not in line]
        rows.insert(1, {"k": "i", "t": 0.0, "place": {"street": "Old Road"}, "lim": {"ms": 10.0, "m": True}})
        path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
        out = {r[0]: r for r in self.csv()[1:]}
        self.assertEqual(out["10.00"][10], "Old Road")
        self.assertEqual(out["50.00"][10], "")  # (the reading is from 50 s earlier: it holds for 15 s, not for ever)

    def test_text_from_the_map_can_not_run_as_a_formula(self):
        self.assertEqual(tripexport.csv_text("=HYPERLINK(\"http://x\")"), "'=HYPERLINK(\"http://x\")")
        for lead in ("+1", "-1", "@SUM(A1)"):
            self.assertTrue(tripexport.csv_text(lead).startswith("'"), repr(lead))
        for lead in ("\t=1", "\r=1"):  # (a tab or carriage return in front is turned into a space, and what follows is still checked)
            self.assertNotIn(tripexport.csv_text(lead)[0], "\t\r=+-@")
        self.assertEqual(tripexport.csv_text("Oriental Parade"), "Oriental Parade")
        self.assertEqual(tripexport.csv_text("a\x00b\nc"), "a b c")
        write_trip(self.dir, A, 30)
        path = self.dir / f"{A}.jsonl"
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        rows.insert(-1, {"k": "i", "t": 0.0, "place": {"street": "=cmd|' /C calc'!A0", "place": "Wellington, \"NZ\""}})
        path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
        row = self.csv()[1]
        self.assertEqual(row[10], "'=cmd|' /C calc'!A0")
        self.assertEqual(row[11], 'Wellington, "NZ"')  # (commas and quotes survive the quoting)

    def test_numbers_are_not_mistaken_for_text_when_negative(self):
        write_trip(self.dir, A, 30)
        row = self.csv()[1]
        self.assertEqual(row[2], "-41.2000000")  # a latitude below zero starts with "-" but is a number, not a formula

    def test_names_like_taupo_with_macrons_survive_and_the_file_says_it_is_utf8(self):
        write_trip(self.dir, A, 30)
        path = self.dir / f"{A}.jsonl"
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        rows.insert(-1, {"k": "i", "t": 0.0, "place": {"street": "Māori Road"}})
        path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
        text = tripexport.to_csv(self.log.detail(A))
        self.assertTrue(text.startswith("﻿"))
        self.assertIn("Māori Road", text)
        self.assertIn("\r\n", text)

    def test_a_trip_with_no_points_is_just_the_header(self):
        rows = parse(tripexport.to_csv({"header": {"ts": 0}, "points": [], "info": []}))
        self.assertEqual(len(rows), 1)

    def test_odd_values_become_empty_cells(self):
        data = {"header": {"ts": 1767225600, "energy": "liquid"}, "info": [], "points": [{"t": 0.0, "la": -41.2, "lo": 174.8, "s": float("nan"), "al": None, "h": "x", "fv": float("inf")}]}
        row = parse(tripexport.to_csv(data))[1]
        self.assertEqual((row[4], row[5], row[13]), ("", "", ""))


class ApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        data = Path(self.tmp.name)
        (data / "triplog").mkdir()
        write_trip(data / "triplog", A, 100, title="Coast run")
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

    async def test_the_csv_is_a_download(self):
        async with self.http.get(f"{self.base}/{A}/csv") as r:
            self.assertEqual(r.status, 200)
            self.assertTrue(r.headers["Content-Type"].startswith("text/csv"))
            self.assertIn("attachment", r.headers["Content-Disposition"])
            self.assertIn("Coast run 2026-01-01.csv", r.headers["Content-Disposition"])
            raw = await r.read()
        self.assertTrue(raw.startswith(b"\xef\xbb\xbft_s,time_utc"))
        self.assertEqual(len(parse(raw.decode("utf-8"))) - 1, 11)

    async def test_the_csv_of_a_trip_that_is_not_there_is_404(self):
        async with self.http.get(f"{self.base}/20200101T000000Z/csv") as r:
            self.assertEqual(r.status, 404)

    async def test_patching_a_price_and_reading_it_back(self):
        async with self.http.patch(f"{self.base}/{A}", json={"price": PRICE}, headers=self.h) as r:
            self.assertEqual(await r.json(), {"ok": True, "price": PRICE})
        async with self.http.get(f"{self.base}/{A}") as r:
            self.assertEqual((await r.json())["price"], PRICE)
        async with self.http.get(self.base) as r:
            self.assertEqual((await r.json())["trips"][0]["price"], PRICE)
        async with self.http.patch(f"{self.base}/{A}", json={"price": None}, headers=self.h) as r:
            self.assertEqual(await r.json(), {"ok": True, "price": None})
        async with self.http.get(self.base) as r:
            self.assertIsNone((await r.json())["trips"][0]["price"])

    async def test_bad_prices_are_refused(self):
        for bad in ("2", [], {"liquid": -1}, {"liquid": "x"}, {"currency": 5}, {"liquid": 99999}):
            async with self.http.patch(f"{self.base}/{A}", json={"price": bad}, headers=self.h) as r:
                self.assertEqual(r.status, 400, repr(bad))
        async with self.http.patch(f"{self.base}/{A}", json={"price": PRICE}) as r:
            self.assertEqual(r.status, 403)


if __name__ == "__main__":
    unittest.main()

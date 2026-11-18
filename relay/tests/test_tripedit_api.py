"""Tests for the trip-editing endpoints: join, cut, backup and restore (api.py, tripedit.py), as the Trips page uses them."""
import io
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

import aiohttp
from aiohttp import web

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from thrashdash.hub import Hub  # noqa: E402
from thrashdash.paths import web_root  # noqa: E402
from thrashdash.server import make_app  # noqa: E402
from thrashdash.store import LayoutStore, SettingsStore  # noqa: E402
from thrashdash.triplog import TripLog  # noqa: E402
from test_triplog import free_port  # noqa: E402
from test_tripedit import write_trip  # noqa: E402


class EditApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        data = Path(self.tmp.name)
        self.dir = data / "triplog"
        self.dir.mkdir()
        write_trip(self.dir, "20260101T000000Z", 100, title="Morning run")
        write_trip(self.dir, "20260101T000700Z", 60, lat0=-41.2 + 0.0010)
        write_trip(self.dir, "20260201T000000Z", 200)
        self.changes = []
        self.log = TripLog(self.dir, say=lambda text: None)
        self.log.on_change = lambda kind, tid: self.changes.append((kind, tid))
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

    async def test_join_two_trips(self):
        async with self.http.post(f"{self.base}/api/triplog/join", json={"first": "20260101T000000Z", "second": "20260101T000700Z"}, headers=self.h) as r:
            self.assertEqual(r.status, 200)
            self.assertEqual(await r.json(), {"ok": True, "id": "20260101T000000Z"})
        self.assertEqual([t["id"] for t in self.log.list() if t["id"].startswith("20260101")], ["20260101T000000Z"])
        self.assertIn(("ended", "20260101T000000Z"), self.changes, "pages watching the list are told")

    async def test_join_options_say_what_could_be_joined(self):
        async with self.http.get(f"{self.base}/api/triplog/20260101T000000Z/joins") as r:
            self.assertEqual(r.status, 200)
            body = await r.json()
        self.assertEqual((body["next"]["id"], body["next"]["ok"]), ("20260101T000700Z", True))
        self.assertIsNone(body["prev"])
        async with self.http.get(f"{self.base}/api/triplog/20260201T000000Z/joins") as r:
            body = await r.json()
        self.assertIsNone(body["next"], "no later trip at all")
        self.assertEqual((body["prev"]["id"], body["prev"]["ok"]), ("20260101T000700Z", False))
        self.assertIn("too far apart in time", body["prev"]["reason"], "a trip that cannot be joined is named, with why")
        async with self.http.get(f"{self.base}/api/triplog/20300101T000000Z/joins") as r:
            self.assertEqual(await r.json(), {"prev": None, "next": None}, "an unknown trip has none")

    async def test_a_join_that_makes_no_sense_is_a_clear_400_and_changes_nothing(self):
        async with self.http.post(f"{self.base}/api/triplog/join", json={"first": "20260101T000000Z", "second": "20260201T000000Z"}, headers=self.h) as r:
            self.assertEqual(r.status, 400)
            self.assertIn("error", await r.json())
        self.assertEqual(len(self.log.list()), 3)

    async def test_force_joins_trips_that_do_not_look_like_one_drive(self):
        async with self.http.post(f"{self.base}/api/triplog/join", json={"first": "20260101T000000Z", "second": "20260201T000000Z"}, headers=self.h) as r:
            self.assertEqual(r.status, 400)
        async with self.http.post(f"{self.base}/api/triplog/join", json={"first": "20260101T000000Z", "second": "20260201T000000Z", "force": True}, headers=self.h) as r:
            self.assertEqual(r.status, 200)
            self.assertEqual((await r.json())["id"], "20260101T000000Z")
        self.assertNotIn("20260201T000000Z", [t["id"] for t in self.log.list()])

    async def test_force_must_be_true_or_false(self):
        for bad in ("yes", 1, None):
            async with self.http.post(f"{self.base}/api/triplog/join", json={"first": "20260101T000000Z", "second": "20260201T000000Z", "force": bad}, headers=self.h) as r:
                self.assertEqual(r.status, 400, bad)

    async def test_join_needs_two_ids_and_a_known_trip(self):
        for body, status in (({}, 400), ({"first": "20260101T000000Z"}, 400), ({"first": 5, "second": 6}, 400), ({"first": "20260101T000000Z", "second": "20300101T000000Z"}, 404)):
            async with self.http.post(f"{self.base}/api/triplog/join", json=body, headers=self.h) as r:
                self.assertEqual(r.status, status, body)

    async def test_cut_a_trip(self):
        async with self.http.post(f"{self.base}/api/triplog/20260201T000000Z/cut", json={"at": 95}, headers=self.h) as r:
            self.assertEqual(r.status, 200)
            body = await r.json()
        self.assertEqual(body["ids"][0], "20260201T000000Z")
        self.assertEqual(len(body["ids"]), 2)
        self.assertEqual(len(self.log.list()), 4)

    async def test_a_cut_too_near_the_end_is_a_400(self):
        async with self.http.post(f"{self.base}/api/triplog/20260201T000000Z/cut", json={"at": 2}, headers=self.h) as r:
            self.assertEqual(r.status, 400)
        async with self.http.post(f"{self.base}/api/triplog/20260201T000000Z/cut", json={"at": "soon"}, headers=self.h) as r:
            self.assertEqual(r.status, 400)
        async with self.http.post(f"{self.base}/api/triplog/20300101T000000Z/cut", json={"at": 50}, headers=self.h) as r:
            self.assertEqual(r.status, 404)

    async def test_these_changes_need_the_write_header(self):
        for url, body in (("/api/triplog/join", {"first": "20260101T000000Z", "second": "20260101T000700Z"}), ("/api/triplog/20260201T000000Z/cut", {"at": 95})):
            async with self.http.post(self.base + url, json=body) as r:
                self.assertEqual(r.status, 403, url)
        async with self.http.post(f"{self.base}/api/triplog/restore", data=b"x", headers={"Content-Type": "application/zip"}) as r:
            self.assertEqual(r.status, 403)
        self.assertEqual(len(self.log.list()), 3)

    async def test_backup_downloads_a_zip_of_every_trip(self):
        async with self.http.get(f"{self.base}/api/triplog/backup") as r:
            self.assertEqual(r.status, 200)
            self.assertEqual(r.headers["Content-Type"], "application/zip")
            self.assertIn("attachment", r.headers["Content-Disposition"])
            self.assertIn(".zip", r.headers["Content-Disposition"])
            data = await r.read()
        self.assertEqual(len([n for n in zipfile.ZipFile(io.BytesIO(data)).namelist() if n.endswith(".jsonl")]), 3)

    async def test_restore_adds_missing_trips_and_reports_what_it_did(self):
        async with self.http.get(f"{self.base}/api/triplog/backup") as r:
            data = await r.read()
        async with self.http.delete(f"{self.base}/api/triplog/20260201T000000Z", headers=self.h) as r:  # (to the trash)
            self.assertEqual(r.status, 200)
        async with self.http.post(f"{self.base}/api/triplog/restore", data=data, headers={**self.h, "Content-Type": "application/zip"}) as r:
            self.assertEqual(r.status, 200)
            self.assertEqual(await r.json(), {"ok": True, "added": 1, "skipped": 2, "rejected": 0})
        self.assertEqual(len(self.log.list()), 3)
        self.assertIn(("ended", "restored"), self.changes, "pages watching the list look again")

    async def test_restore_refuses_what_is_not_a_zip(self):
        async with self.http.post(f"{self.base}/api/triplog/restore", data=b"not a zip", headers={**self.h, "Content-Type": "application/zip"}) as r:
            self.assertEqual(r.status, 400)
            self.assertIn("not a zip", (await r.json())["error"])

    async def test_a_restore_body_that_is_too_big_is_refused_while_it_is_read(self):
        from thrashdash import tripedit
        old = tripedit.RESTORE_MAX_BYTES
        tripedit.RESTORE_MAX_BYTES = 1000  # (the limit is read when the request comes in)
        try:
            async with self.http.post(f"{self.base}/api/triplog/restore", data=b"x" * 5000, headers={**self.h, "Content-Type": "application/zip"}) as r:
                self.assertEqual(r.status, 413)
        finally:
            tripedit.RESTORE_MAX_BYTES = old


if __name__ == "__main__":
    unittest.main()

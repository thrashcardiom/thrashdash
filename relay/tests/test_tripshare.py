"""Tests for sharing a trip as one html file: the module joiner, the page it builds, and the download."""
import base64
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import aiohttp
from aiohttp import web

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from thrashdash import tripshare  # noqa: E402
from thrashdash.hub import Hub  # noqa: E402
from thrashdash.paths import web_root  # noqa: E402
from thrashdash.server import make_app  # noqa: E402
from thrashdash.store import LayoutStore, SettingsStore  # noqa: E402
from thrashdash.triplog import TripLog  # noqa: E402
from test_triplog import Driver, free_port  # noqa: E402

NODE = shutil.which("node")


def write(root, files):
    for name, text in files.items():
        path = Path(root) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def run_bundle(root, entry="js/main.js"):
    """Run a joined script in Node and return what it printed."""
    code = tripshare.bundle(root, entry, overrides={"js/api.js": "export const api = {};"})
    done = subprocess.run([NODE, "-e", code], capture_output=True, text=True, timeout=20)
    return done.returncode, done.stdout.strip(), done.stderr.strip()


@unittest.skipIf(NODE is None, "needs Node to run the joined script")
class BundleRunTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def test_modules_see_what_they_import_and_nothing_else(self):
        write(self.root, {
            "js/a.js": 'export const value = 40;\nexport function twice(n) { return n * 2; }\nconst hidden = "a";\n',
            "js/b.js": 'import { value, twice } from "./a.js";\nexport const answer = twice(value) + 2;\nconst hidden = "b";\nexport const seen = hidden;\n',
            "js/main.js": 'import { answer, seen } from "./b.js";\nimport { value as v } from "./a.js";\nconsole.log(answer, seen, v);\n',
        })
        self.assertEqual(run_bundle(self.root), (0, "82 b 40", ""))

    def test_modules_are_joined_once_and_in_the_order_they_need(self):
        write(self.root, {
            "js/log.js": 'globalThis.order = (globalThis.order || []).concat("log");\nexport const x = 1;\n',
            "js/a.js": 'import { x } from "./log.js";\nglobalThis.order.push("a");\nexport const a = x;\n',
            "js/b.js": 'import { x } from "./log.js";\nglobalThis.order.push("b");\nexport const b = x;\n',
            "js/main.js": 'import { a } from "./a.js";\nimport { b } from "./b.js";\nconsole.log(globalThis.order.join(","), a + b);\n',
        })
        self.assertEqual(run_bundle(self.root), (0, "log,a,b 2", ""))

    def test_async_functions_and_classes_and_parent_folders(self):
        write(self.root, {
            "js/lib/util.js": 'export async function later() { return "ok"; }\nexport class Box { constructor() { this.n = 3; } }\nexport let counter = 7;\n',
            "js/main.js": 'import { later, Box, counter } from "./lib/util.js";\nlater().then((r) => console.log(r, new Box().n, counter));\n',
        })
        self.assertEqual(run_bundle(self.root), (0, "ok 3 7", ""))

    def test_the_real_trips_page_joins_into_valid_javascript(self):
        code = tripshare.bundle(web_root())
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
            f.write(code)
        try:
            done = subprocess.run([NODE, "--check", f.name], capture_output=True, text=True)
            self.assertEqual(done.returncode, 0, done.stderr)
        finally:
            Path(f.name).unlink()


class BundleRuleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def test_a_circle_of_imports_is_reported(self):
        write(self.root, {"js/a.js": 'import { b } from "./b.js";\nexport const a = 1;\n', "js/b.js": 'import { a } from "./a.js";\nexport const b = 1;\n'})
        with self.assertRaises(tripshare.BundleError):
            tripshare.bundle(self.root, "js/a.js")

    def test_an_import_form_that_cannot_be_joined_is_reported_not_guessed(self):
        for text in ('import * as x from "./b.js";\n', 'import x from "./b.js";\n', 'export { y };\nconst y = 1;\n', 'export default 1;\n'):
            write(self.root, {"js/main.js": text, "js/b.js": "export const q = 1;\n"})
            with self.assertRaises(tripshare.BundleError, msg=text):
                tripshare.bundle(self.root, "js/main.js")

    def test_a_module_outside_the_web_folder_is_refused(self):
        write(self.root, {"js/main.js": 'import { x } from "../../outside.js";\n'})
        with self.assertRaises(tripshare.BundleError):
            tripshare.bundle(self.root, "js/main.js")

    def test_the_shared_api_stands_in_for_the_real_one(self):
        write(self.root, {"js/api.js": "export const api = { real: true };\n", "js/main.js": 'import { api } from "./api.js";\n'})
        code = tripshare.bundle(self.root, "js/main.js")
        self.assertIn("share-data", code)
        self.assertNotIn("real: true", code)


def finished_trip(title=None):
    tmp = tempfile.TemporaryDirectory()
    log = TripLog(Path(tmp.name) / "triplog", say=lambda t: None)
    d = Driver(log)
    d.drive(40, speed=15)
    log.ingest({"type": "geoinfo", "rt": d.rt, "place": {"street": "Oriental Parade", "place": "Wellington", "country": "New Zealand"},
                "limit": {"ms": 10.0, "mapped": True, "unit": "kmh"}}, {})
    d.drive(30, speed=15, car_ignored=None) if False else d.drive(30, speed=15)
    log.close_all()
    tid = log.list()[0]["id"]
    if title is not None:
        log.rename(tid, title)
    trip = log.detail(tid)
    return tmp, trip


class PageTests(unittest.TestCase):
    def setUp(self):
        self.tmp, self.trip = finished_trip()
        self.page = tripshare.build_share_page(self.trip, {"speed": "kmh", "distance": "km", "temp": "c"}, web_root())

    def tearDown(self):
        self.tmp.cleanup()

    def embedded(self):
        match = re.search(r'<script type="application/json" id="share-data">(.*?)</script>', self.page, re.S)
        return json.loads(match.group(1))

    def test_the_trip_is_in_the_file_and_only_what_the_page_needs(self):
        data = self.embedded()
        self.assertEqual(len(data["trip"]["points"]), len(self.trip["points"]))
        self.assertEqual(set(data["trip"]["points"][0]) - {"t", "la", "lo", "al", "s", "h", "d"}, set())
        self.assertNotIn("veh", data["trip"]["header"])
        self.assertNotIn('"fv"', self.page.split('id="share-data">')[1].split("</script>")[0])
        self.assertEqual(data["trip"]["stats"]["from"], "Oriental Parade")
        self.assertEqual(data["units"]["speed"], "kmh")
        self.assertTrue(data["trip"]["end"]["reason"])

    def test_it_is_one_file_with_nothing_to_fetch(self):
        self.assertNotRegex(self.page, r'<script[^>]+src=')
        self.assertNotRegex(self.page, r'<link[^>]+href="(?!data:)')
        self.assertLess(self.page.index('<meta charset="utf-8">'), 100)  # (a browser must see the character set in the first kilobyte)
        self.assertNotIn("@import", self.page)
        self.assertIn("<style>", self.page)
        self.assertIn("THRASHDASH_SHARE", self.page)
        self.assertIn("L.map", self.page.replace("L.map(", "L.map"))  # the trips page's own map code is in it

    def test_the_policy_names_every_script_by_its_hash_and_allows_nothing_else(self):
        csp = html_unescape(re.search(r'<meta http-equiv="Content-Security-Policy" content="([^"]*)">', self.page).group(1))
        scripts = re.findall(r"<script>(.*?)</script>", self.page, re.S)
        self.assertEqual(len(scripts), 3)
        for body in scripts:
            digest = base64.b64encode(hashlib.sha256(body.encode("utf-8")).digest()).decode()
            self.assertIn(f"'sha256-{digest}'", csp)
        self.assertIn("connect-src 'none'", csp)
        self.assertIn("default-src 'none'", csp)
        self.assertNotIn("'unsafe-eval'", csp)
        self.assertNotRegex(csp, r"script-src[^;]*'unsafe-inline'")
        self.assertIn("img-src data: https://tile.openstreetmap.org https://tiles.maps.eox.at", csp)

    def test_a_hostile_name_cannot_break_out_of_the_data_or_the_title(self):
        tmp, trip = finished_trip('</script><img src=x onerror=alert(1)> <!-- & "q"')
        try:
            page = tripshare.build_share_page(trip, {}, web_root())
        finally:
            tmp.cleanup()
        self.assertNotIn("<img src=x", page)
        self.assertEqual(len(re.findall(r"</script>", page)), 4)  # the data and the three scripts: no more
        match = re.search(r'<script type="application/json" id="share-data">(.*?)</script>', page, re.S)
        self.assertEqual(json.loads(match.group(1))["trip"]["title"], '</script><img src=x onerror=alert(1)> <!-- & "q"')
        self.assertNotIn("<!--", match.group(1))
        self.assertIn("&lt;/script&gt;", re.search(r"<title>(.*?)</title>", page).group(1))

    def test_the_map_librarys_licence_travels_with_the_file(self):
        self.assertIn("BSD 2-Clause License", self.page)
        self.assertIn("Agafonkin", self.page)
        comment = re.search(r"<!-- Leaflet, bundled in this page:(.*?)-->", self.page, re.S).group(1)
        self.assertNotIn("--", comment)  # (so the comment cannot end early)

    def test_the_size_is_reasonable_for_a_short_trip(self):
        self.assertLess(len(self.page), 600_000)


def html_unescape(text):
    import html
    return html.unescape(text)


class ShareHttpTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        data = Path(self.tmp.name)
        self.log = TripLog(data / "triplog", say=lambda t: None)
        d = Driver(self.log)
        d.drive(60, speed=15)
        self.log.close_all()
        self.tid = self.log.list()[0]["id"]
        d.drive(60, speed=15)
        self.recording = self.log.status()["id"]
        self.port = free_port()
        app = make_app(Hub(triplog=self.log), self.port, LayoutStore(data / "layouts", web_root() / "layouts"), SettingsStore(data / "settings.json"))
        self.runner = web.AppRunner(app, access_log=None)
        await self.runner.setup()
        await web.TCPSite(self.runner, "127.0.0.1", self.port).start()
        self.base = f"http://127.0.0.1:{self.port}"
        self.http = aiohttp.ClientSession()

    async def asyncTearDown(self):
        await self.http.close()
        await self.runner.cleanup()
        self.log.close_all()
        self.tmp.cleanup()

    async def test_a_finished_trip_downloads_as_one_html_file(self):
        async with self.http.get(f"{self.base}/api/triplog/{self.tid}/share") as r:
            self.assertEqual(r.status, 200)
            self.assertEqual(r.headers["Content-Type"].split(";")[0], "text/html")
            self.assertRegex(r.headers["Content-Disposition"], r'^attachment; filename="[A-Za-z0-9 ._()\-]+\.html"$')
            text = await r.text()
        self.assertTrue(text.lstrip().startswith("<!doctype html>"))
        self.assertIn("share-data", text)

    async def test_a_trip_still_being_recorded_is_not_shared_yet(self):
        async with self.http.get(f"{self.base}/api/triplog/{self.recording}/share") as r:
            self.assertEqual(r.status, 409)

    async def test_unknown_trips_are_not_found(self):
        for tid in ("20260101T000000Z", "..%2Fx"):
            async with self.http.get(f"{self.base}/api/triplog/{tid}/share") as r:
                self.assertEqual(r.status, 404)

    async def test_the_users_units_go_into_the_file(self):
        async with self.http.put(f"{self.base}/api/settings", json={"units": {"speed": "mph", "distance": "mi"}}, headers={"X-ThrashDash": "1"}) as r:
            self.assertEqual(r.status, 200)
        async with self.http.get(f"{self.base}/api/triplog/{self.tid}/share") as r:
            text = await r.text()
        data = json.loads(re.search(r'<script type="application/json" id="share-data">(.*?)</script>', text, re.S).group(1))
        self.assertEqual((data["units"]["speed"], data["units"]["distance"]), ("mph", "mi"))


if __name__ == "__main__":
    unittest.main()

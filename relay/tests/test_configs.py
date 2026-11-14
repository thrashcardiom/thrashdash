"""Tests for saved configs (named sets of windows, the map window included): saving, Save as, which opens at start-up."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

import aiohttp
from aiohttp import web

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from thrashdash.configs import MAX_CONFIGS, MAX_NAME, ConfigStore, clean_name, clean_windows, windows_differ  # noqa: E402
from thrashdash.hub import Hub  # noqa: E402
from thrashdash.paths import web_root  # noqa: E402
from thrashdash.server import make_app  # noqa: E402
from thrashdash.store import LayoutStore, NotFound, SettingsStore  # noqa: E402
from thrashdash.validate import ValidationError  # noqa: E402
from thrashdash.windows import WindowRegistry, clean_window, open_config_windows, open_windows, window_path  # noqa: E402
from test_triplog import free_port  # noqa: E402

W1, W2, W3 = "abc12345", "def67890", "ghi13579"
DASH = {"kind": "dashboard", "dashboard": "cluster", "pinned": True, "fullscreen": False, "width": 1200, "height": 420, "x": 100, "y": 50}
MAP = {"kind": "trips", "dashboard": None, "pinned": False, "fullscreen": False, "width": 1180, "height": 760, "x": 1300, "y": 40}


class FakeSettings:
    def __init__(self, **kw):
        self.prefs = {"restoreWindows": True, "exactWindowPlacement": False, **kw}

    def get(self):
        return self.prefs


class WindowKindTests(unittest.TestCase):
    def test_the_map_window_is_a_kind_of_window(self):
        self.assertEqual(clean_window({"kind": "trips", "width": 1180, "height": 760})["kind"], "trips")
        self.assertEqual(clean_window({"dashboard": "x"})["kind"], "dashboard")  # (the default, as every window was before)
        with self.assertRaises(ValidationError):
            clean_window({"kind": "evil"})

    def test_the_map_window_reopens_on_the_trips_page_with_its_id(self):
        self.assertEqual(window_path(W2, MAP), f"/trips?w={W2}")
        self.assertEqual(window_path(W1, DASH), f"/?d=cluster&w={W1}")

    def test_the_registry_knows_which_windows_are_open_now(self):
        reg = WindowRegistry(None)
        reg.update(W1, DASH, 1000)
        reg.update(W2, MAP, 1000)
        self.assertEqual(set(reg.live_entries()), {W1, W2})
        self.assertNotIn("seen", reg.live_entries()[W1])
        reg.remove(W2, 2000)
        self.assertEqual(set(reg.live_entries()), {W1})

    def test_adopting_a_config_replaces_what_the_last_run_left_but_keeps_windows_that_are_open(self):
        reg = WindowRegistry(None)
        reg.entries = {W3: {**DASH, "seen": 1.0}}  # (left over from the last run)
        reg.update(W1, DASH, 1000)  # (open now)
        reg.adopt({W2: MAP})
        self.assertEqual(set(reg.entries), {W1, W2})
        self.assertEqual(reg.entries[W2]["kind"], "trips")


class ConfigTests(unittest.TestCase):
    def test_windows_are_checked_all_or_nothing(self):
        self.assertEqual(set(clean_windows({W1: DASH, W2: MAP})), {W1, W2})
        for bad in ([], {"BAD ID": DASH}, {W1: "x"}, {W1: {**DASH, "kind": "evil"}}, {f"w{i:07d}": DASH for i in range(13)}):
            with self.assertRaises(ValidationError, msg=str(bad)[:60]):
                clean_windows(bad)

    def test_names_are_plain_one_line_and_short(self):
        self.assertEqual(clean_name("  Two \n monitors\t"), "Two monitors")
        self.assertEqual(len(clean_name("x" * 300)), MAX_NAME)
        for bad in ("", "   ", None, 5):
            with self.assertRaises(ValidationError):
                clean_name(bad)

    def test_what_counts_as_a_change(self):
        saved = {W1: DASH, W2: MAP}
        self.assertFalse(windows_differ(saved, {W1: {**DASH}, W2: {**MAP}}))
        self.assertFalse(windows_differ(saved, {W1: {**DASH, "width": 1205}, W2: MAP}), "a few pixels are the same size")
        self.assertTrue(windows_differ(saved, {W1: {**DASH, "width": 1400}, W2: MAP}), "a resized window")
        self.assertTrue(windows_differ(saved, {W1: {**DASH, "x": 600}, W2: MAP}), "a moved window")
        self.assertFalse(windows_differ(saved, {W1: {**DASH, "x": 0, "y": 0}, W2: MAP}), "0,0 means the system does not say")
        self.assertTrue(windows_differ(saved, {W1: DASH}), "a window closed")
        self.assertTrue(windows_differ(saved, {W1: DASH, W2: MAP, W3: DASH}), "a window opened")
        self.assertTrue(windows_differ(saved, {W1: {**DASH, "dashboard": "drift"}, W2: MAP}), "another dashboard")
        self.assertTrue(windows_differ(saved, {W1: DASH, W2: {**MAP, "fullscreen": True}}), "fullscreen")


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "configs.json"
        self.store = ConfigStore(self.path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_it_starts_empty(self):
        self.assertEqual(self.store.get(), {"active": None, "configs": []})
        self.assertIsNone(self.store.active_windows())

    def test_save_as_makes_a_new_config_which_becomes_the_one_that_opens(self):
        a = self.store.create("Driving", {W1: DASH, W2: MAP})
        b = self.store.create("Review", {W1: DASH})
        got = self.store.get()
        self.assertEqual([c["name"] for c in got["configs"]], ["Driving", "Review"])
        self.assertEqual(got["active"], b)
        self.assertNotEqual(a, b)
        self.assertEqual(set(got["configs"][0]["windows"]), {W1, W2})  # the map window is in it

    def test_save_overwrites_the_config_it_is_given_and_nothing_else(self):
        a = self.store.create("Driving", {W1: DASH, W2: MAP})
        b = self.store.create("Review", {W1: DASH})
        self.store.save(a, {W2: {**MAP, "width": 900}})
        got = self.store.get()
        self.assertEqual(set(got["configs"][0]["windows"]), {W2})
        self.assertEqual(got["configs"][0]["windows"][W2]["width"], 900)
        self.assertEqual(set(got["configs"][1]["windows"]), {W1})
        self.assertEqual(len(got["configs"]), 2)  # replaced, no copy made
        self.assertEqual(got["active"], a)  # (the one last saved is the one that opens)
        self.assertNotEqual(got["active"], b)

    def test_it_is_remembered_across_a_restart(self):
        a = self.store.create("Driving", {W1: DASH, W2: MAP})
        again = ConfigStore(self.path)
        self.assertEqual(again.get()["active"], a)
        self.assertEqual(set(again.active_windows()), {W1, W2})

    def test_picking_one_makes_it_the_one_that_opens(self):
        a = self.store.create("A", {W1: DASH})
        self.store.create("B", {W2: MAP})
        self.store.activate(a)
        self.assertEqual(set(self.store.active_windows()), {W1})
        self.store.activate(None)
        self.assertIsNone(self.store.active_windows())
        with self.assertRaises(NotFound):
            self.store.activate("c00000000")

    def test_a_name_in_use_gets_a_number(self):
        self.store.create("Driving", {W1: DASH})
        self.store.create("driving", {W1: DASH})
        self.assertEqual([c["name"] for c in self.store.get()["configs"]], ["Driving", "driving (2)"])

    def test_there_must_be_windows_to_save(self):
        with self.assertRaises(ValidationError):
            self.store.create("Empty", {})
        a = self.store.create("A", {W1: DASH})
        with self.assertRaises(ValidationError):
            self.store.save(a, {})
        self.assertEqual(set(self.store.active_windows()), {W1})  # (the failed save changed nothing)

    def test_delete_removes_it_and_the_active_one_falls_back_to_none(self):
        a = self.store.create("A", {W1: DASH})
        b = self.store.create("B", {W2: MAP})
        self.store.delete(b)
        got = self.store.get()
        self.assertEqual([c["id"] for c in got["configs"]], [a])
        self.assertIsNone(got["active"])
        with self.assertRaises(NotFound):
            self.store.delete(b)

    def test_unknown_configs_cannot_be_saved(self):
        with self.assertRaises(NotFound):
            self.store.save("c12345678", {W1: DASH})

    def test_there_is_a_limit_on_how_many(self):
        for i in range(MAX_CONFIGS):
            self.store.create(f"C{i}", {W1: DASH})
        with self.assertRaises(ValidationError):
            self.store.create("one too many", {W1: DASH})

    def test_a_corrupt_file_starts_empty_and_a_damaged_entry_loses_only_itself(self):
        self.path.write_text("not json")
        self.assertEqual(self.store.get(), {"active": None, "configs": []})
        good = {"id": "caaaaaaaa", "name": "Good", "windows": {W1: DASH}}
        self.path.write_text(json.dumps({"active": "caaaaaaaa", "configs": [{"id": "../x", "name": "Bad"}, {"id": "cbbbbbbbb", "name": 5}, good,
                                                                           {"id": "cccccccc1", "name": "Bad windows", "windows": {"BAD": {}}}, "junk"]}))
        got = self.store.get()
        self.assertEqual([c["id"] for c in got["configs"]], ["caaaaaaaa"])
        self.assertEqual(got["active"], "caaaaaaaa")

    def test_the_file_is_written_whole(self):
        self.store.create("A", {W1: DASH})
        json.loads(self.path.read_text())
        self.assertEqual([p.name for p in self.path.parent.iterdir()], ["configs.json"])


class StartupTests(unittest.IsolatedAsyncioTestCase):
    async def start(self, registry, configs, settings=None, running=False):
        opened = []

        def launch(url, size, position, fullscreen, x11):
            opened.append((url, size, position, fullscreen))
            return object()

        async def sleep(s):
            pass

        count = await open_windows(registry, settings or FakeSettings(), launch, "http://127.0.0.1:8765", lambda: running, sleep, configs=configs)
        return count, opened

    async def test_the_last_saved_config_is_what_opens_map_window_included(self):
        with tempfile.TemporaryDirectory() as d:
            configs = ConfigStore(Path(d) / "configs.json")
            configs.create("Driving", {W1: DASH, W2: MAP})
            reg = WindowRegistry(None)
            reg.entries = {W3: {**DASH, "dashboard": "drift", "seen": 5.0}}  # (what the last run left: not part of the config)
            count, opened = await self.start(reg, configs)
            urls = sorted(o[0] for o in opened)
            self.assertEqual(count, 2)
            self.assertEqual(urls, sorted([f"http://127.0.0.1:8765/?d=cluster&w={W1}", f"http://127.0.0.1:8765/trips?w={W2}"]))
            sizes = {o[0].split("?")[0]: o[1] for o in opened}
            self.assertEqual(sizes["http://127.0.0.1:8765/trips"], (1180, 760))  # the map window comes back at its size

    async def test_with_no_config_it_is_the_last_run_as_before(self):
        with tempfile.TemporaryDirectory() as d:
            reg = WindowRegistry(None)
            reg.entries = {W3: {**DASH, "seen": 5.0}}
            count, opened = await self.start(reg, ConfigStore(Path(d) / "configs.json"))
            self.assertEqual((count, [o[0] for o in opened]), (1, [f"http://127.0.0.1:8765/?d=cluster&w={W3}"]))

    async def test_restoring_switched_off_ignores_the_config_too(self):
        with tempfile.TemporaryDirectory() as d:
            configs = ConfigStore(Path(d) / "configs.json")
            configs.create("Driving", {W1: DASH})
            count, opened = await self.start(WindowRegistry(None), configs, FakeSettings(restoreWindows=False))
            self.assertEqual([o[0] for o in opened], ["http://127.0.0.1:8765/"])  # (just the default window, as always)

    async def test_picking_a_config_while_running_opens_only_its_windows_that_are_not_open(self):
        reg = WindowRegistry(None)
        reg.update(W1, DASH, 1000)  # open now
        opened = []

        def launch(url, size, position, fullscreen, x11):
            opened.append(url)
            return object()

        async def sleep(s):
            pass

        count = await open_config_windows(reg, {W1: DASH, W2: MAP}, False, launch, "http://127.0.0.1:8765", sleep)
        self.assertEqual((count, opened), (1, [f"http://127.0.0.1:8765/trips?w={W2}"]))


class HttpTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        data = Path(self.tmp.name)
        self.store = ConfigStore(data / "configs.json")
        self.registry = WindowRegistry(None)
        self.registry.update(W1, DASH, 1000)
        self.registry.update(W2, MAP, 1000)
        self.port = free_port()
        self.app = make_app(Hub(), self.port, LayoutStore(data / "layouts", web_root() / "layouts"), SettingsStore(data / "settings.json"), windows=self.registry, configs=self.store)
        self.opened_for = []

        async def opener(windows):
            self.opened_for.append(set(windows))
            return len(windows)

        self.app[__import__("thrashdash.api", fromlist=["x"]).WINDOW_OPENER]["open"] = opener
        self.runner = web.AppRunner(self.app, access_log=None)
        await self.runner.setup()
        await web.TCPSite(self.runner, "127.0.0.1", self.port).start()
        self.base = f"http://127.0.0.1:{self.port}/api/configs"
        self.http = aiohttp.ClientSession()
        self.h = {"X-ThrashDash": "1"}

    async def asyncTearDown(self):
        await self.http.close()
        await self.runner.cleanup()
        self.tmp.cleanup()

    async def test_save_as_captures_the_open_windows_including_the_map_window(self):
        async with self.http.post(self.base, json={"name": "Driving"}, headers=self.h) as r:
            self.assertEqual(r.status, 200)
            got = await r.json()
        self.assertEqual(got["active"], got["configs"][0]["id"])
        self.assertEqual({w["kind"] for w in got["configs"][0]["windows"]}, {"dashboard", "trips"})
        self.assertFalse(got["changed"])
        self.assertEqual(set(self.store.active_windows()), {W1, W2})

    async def test_save_overwrites_with_the_windows_open_now_and_make_no_copy(self):
        cid = self.store.create("Driving", {W1: DASH})
        async with self.http.put(f"{self.base}/{cid}", json={}, headers=self.h) as r:
            self.assertEqual(r.status, 200)
        got = self.store.get()
        self.assertEqual(len(got["configs"]), 1)
        self.assertEqual(set(got["configs"][0]["windows"]), {W1, W2})  # (the map window opened since is now in it)

    async def test_the_list_says_when_the_windows_have_changed_since_the_save(self):
        self.store.create("Driving", {W1: DASH, W2: MAP})
        async with self.http.get(self.base) as r:
            self.assertFalse((await r.json())["changed"])
        self.registry.update(W2, {**MAP, "width": 700}, 2000)  # the map window was resized
        async with self.http.get(self.base) as r:
            got = await r.json()
        self.assertTrue(got["changed"])
        self.assertEqual(got["open"], 2)

    async def test_picking_one_makes_it_active_and_opens_its_missing_windows(self):
        a = self.store.create("A", {W1: DASH, W3: {**DASH, "dashboard": "drift"}})
        self.store.create("B", {W2: MAP})
        async with self.http.put(f"{self.base}/active", json={"id": a}, headers=self.h) as r:
            got = await r.json()
        self.assertEqual((got["active"], got["opened"]), (a, 2))
        self.assertEqual(self.opened_for, [{W1, W3}])

    async def test_with_nothing_open_there_is_nothing_to_save(self):
        self.registry.remove(W1, 5000)
        self.registry.remove(W2, 5000)
        async with self.http.post(self.base, json={"name": "Empty"}, headers=self.h) as r:
            self.assertEqual(r.status, 400)
            self.assertIn("no windows", (await r.json())["error"])

    async def test_bad_requests_and_deleting(self):
        async with self.http.post(self.base, json={"name": ""}, headers=self.h) as r:
            self.assertEqual(r.status, 400)
        async with self.http.post(self.base, json=["x"], headers=self.h) as r:
            self.assertEqual(r.status, 400)
        async with self.http.put(f"{self.base}/c12345678", json={}, headers=self.h) as r:
            self.assertEqual(r.status, 404)
        async with self.http.put(f"{self.base}/active", json={"id": "c12345678"}, headers=self.h) as r:
            self.assertEqual(r.status, 404)
        cid = self.store.create("A", {W1: DASH})
        async with self.http.delete(f"{self.base}/{cid}", headers=self.h) as r:
            self.assertEqual((await r.json())["configs"], [])
        async with self.http.delete(f"{self.base}/{cid}", headers=self.h) as r:
            self.assertEqual(r.status, 404)

    async def test_changes_need_the_write_header_but_reading_does_not(self):
        async with self.http.post(self.base, json={"name": "X"}) as r:
            self.assertEqual(r.status, 403)
        async with self.http.get(self.base) as r:
            self.assertEqual(r.status, 200)

    async def test_the_map_window_can_report_itself_like_a_dashboard_window(self):
        async with self.http.put(f"http://127.0.0.1:{self.port}/api/windows/{W3}", json={"kind": "trips", "width": 1000, "height": 700, "x": 5, "y": 6, "stamp": 9000}, headers=self.h) as r:
            self.assertEqual(r.status, 200)
        self.assertEqual(self.registry.entries[W3]["kind"], "trips")


if __name__ == "__main__":
    unittest.main()

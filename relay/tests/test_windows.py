"""Tests for remembering and restoring dashboard windows."""
import asyncio
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
from thrashdash.launcher import window_args  # noqa: E402
from thrashdash.paths import web_root  # noqa: E402
from thrashdash.server import make_app  # noqa: E402
from thrashdash.store import LayoutStore, SettingsStore  # noqa: E402
from thrashdash.validate import ValidationError, validate_settings  # noqa: E402
from thrashdash.windows import MAX_WINDOWS, REATTACH_GRACE_S, STALE_AFTER_S, WindowRegistry, clean_window, open_windows, plan_restore, window_path  # noqa: E402

H = {"X-ThrashDash": "1"}
W1, W2, W3 = "abc12345", "def67890", "ghi13579"


def report(**over):
    body = {"dashboard": "cluster", "pinned": True, "width": 1200, "height": 420, "x": 100, "y": 50, "fullscreen": False}
    body.update(over)
    return body


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


class CleanTests(unittest.TestCase):
    def test_a_good_report(self):
        e = clean_window(report())
        self.assertEqual((e["dashboard"], e["pinned"], e["width"], e["height"], e["x"], e["y"], e["fullscreen"]), ("cluster", True, 1200, 420, 100, 50, False))

    def test_missing_parts_are_fine(self):
        e = clean_window({})
        self.assertEqual((e["dashboard"], e["pinned"], e["width"], e["x"]), (None, False, None, None))

    def test_bad_reports_are_refused(self):
        for bad in ([], "x", {"dashboard": "../x"}, {"dashboard": 5}, {"width": "wide"}, {"height": float("nan")}, {"width": True}, {"y": float("inf")}):
            with self.assertRaises(ValidationError, msg=repr(bad)):
                clean_window(bad)

    def test_a_size_or_position_that_makes_no_sense_is_unknown_not_an_error(self):
        # A minimised window, or a headless browser, reports a size of 0 by 0. The report must still be accepted so the
        # window is remembered (with the size unknown).
        e = clean_window({"width": 0, "height": 0, "x": 1e9, "y": -1e9, "dashboard": "cluster", "pinned": True})
        self.assertEqual((e["width"], e["height"], e["x"], e["y"]), (None, None, None, None))
        self.assertEqual((e["dashboard"], e["pinned"]), ("cluster", True))
        self.assertEqual(clean_window({"width": 50})["width"], None)
        self.assertEqual(clean_window({"width": 99999})["width"], None)

    def test_the_address_reopens_the_same_window(self):
        self.assertEqual(window_path(W1, {"pinned": True, "dashboard": "cluster"}), f"/?d=cluster&w={W1}")
        self.assertEqual(window_path(W1, {"pinned": False, "dashboard": "cluster"}), f"/?w={W1}", "a window that follows the vehicle rules is not pinned")
        self.assertEqual(window_path(W1, {"pinned": True, "dashboard": None}), f"/?w={W1}")


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.wall, self.mono = Clock(1_000_000.0), Clock(0.0)
        self.reg = WindowRegistry(None, wall_clock=self.wall, clock=self.mono)

    def test_windows_come_and_go(self):
        self.assertTrue(self.reg.update(W1, report(), 1000))
        self.reg.update(W2, report(dashboard="drift"), 1001)
        self.assertEqual(set(self.reg.entries), {W1, W2})
        self.assertEqual(self.reg.live, {W1, W2})
        self.assertTrue(self.reg.remove(W1, 2000))
        self.assertEqual(set(self.reg.entries), {W2})
        self.assertEqual(self.reg.live, {W2})

    def test_a_reload_is_not_a_close(self):
        # A page being reloaded says "closed" and then, from the new page, reports again. Whichever reaches the relay
        # first, the window must survive: the close carries an older time than the new report.
        self.reg.update(W1, report(), 1000)
        self.assertTrue(self.reg.remove(W1, 1500))  # the old page closes (nothing newer yet, so the close is accepted)...
        self.reg.update(W1, report(), 2000)  # ...and the reloaded page reports straight after
        self.assertIn(W1, self.reg.entries)
        # now the other order: the new page's report first, then the OLD page's late close
        self.reg.update(W2, report(), 5000)
        self.assertFalse(self.reg.remove(W2, 4000), "a close older than the latest report is ignored")
        self.assertIn(W2, self.reg.entries)

    def test_a_late_report_from_before_a_close_does_not_bring_it_back(self):
        self.reg.update(W1, report(), 1000)
        self.reg.remove(W1, 3000)
        self.assertFalse(self.reg.update(W1, report(), 2500))
        self.assertNotIn(W1, self.reg.entries)
        self.assertTrue(self.reg.update(W1, report(), 4000), "but a newer report (a reopened window) is accepted")

    def test_closing_an_unknown_window_is_harmless(self):
        self.assertTrue(self.reg.remove(W3, 10))
        self.assertEqual(self.reg.entries, {})

    def test_bad_ids_and_stamps_are_refused(self):
        for bad_id in ("", "UPPER123", "short", "a" * 40, "has space", "../etc", None, 5):
            with self.assertRaises(ValidationError, msg=repr(bad_id)):
                self.reg.update(bad_id, report(), 1)
        for bad_stamp in (None, "now", -1, float("nan"), 1e30):
            with self.assertRaises(ValidationError, msg=repr(bad_stamp)):
                self.reg.update(W1, report(), bad_stamp)
        with self.assertRaises(ValidationError):
            self.reg.remove("../x", 1)

    def test_there_is_a_limit_on_windows(self):
        for i in range(MAX_WINDOWS):
            self.reg.update(f"win{i:05d}", report(), 1)
        with self.assertRaises(ValidationError):
            self.reg.update("onetoomany", report(), 1)
        self.reg.update("win00003", report(width=900), 2)  # but a window already known can still report
        self.assertEqual(self.reg.entries["win00003"]["width"], 900)


class SavingTests(unittest.TestCase):
    def make(self, d, wall=None, mono=None):
        return WindowRegistry(Path(d) / "windows.json", wall_clock=wall or Clock(1_000_000.0), clock=mono or Clock(0.0))

    def test_saved_and_reloaded_without_counting_as_open(self):
        with tempfile.TemporaryDirectory() as d:
            reg = self.make(d)
            reg.update(W1, report(), 1)
            reg.update(W2, report(dashboard=None, pinned=False, x=None, y=None), 2)
            reg.flush()
            again = self.make(d)
            self.assertEqual(set(again.entries), {W1, W2})
            self.assertEqual(again.entries[W1]["width"], 1200)
            self.assertEqual(again.live, set(), "windows from the last run are not open until they report again")

    def test_a_close_is_saved_at_once(self):
        with tempfile.TemporaryDirectory() as d:
            reg = self.make(d)
            reg.update(W1, report(), 1)
            reg.update(W2, report(), 2)
            reg.flush()
            reg.remove(W1, 10)
            self.assertEqual(set(json.loads((Path(d) / "windows.json").read_text())["windows"]), {W2})

    def test_saving_while_windows_move_is_throttled_and_flushed_at_the_end(self):
        with tempfile.TemporaryDirectory() as d:
            mono = Clock(100.0)
            reg = self.make(d, mono=mono)
            reg.update(W1, report(x=1), 1)  # first change: written
            path = Path(d) / "windows.json"
            self.assertEqual(json.loads(path.read_text())["windows"][W1]["x"], 1)
            mono.t += 0.5
            reg.update(W1, report(x=2), 2)  # a moment later: held back
            self.assertEqual(json.loads(path.read_text())["windows"][W1]["x"], 1)
            reg.flush()  # shutdown
            self.assertEqual(json.loads(path.read_text())["windows"][W1]["x"], 2)

    def test_a_heartbeat_that_changes_nothing_does_not_write(self):
        with tempfile.TemporaryDirectory() as d:
            mono = Clock(100.0)
            reg = self.make(d, mono=mono)
            reg.update(W1, report(), 1)
            path = Path(d) / "windows.json"
            first = path.stat().st_mtime_ns
            mono.t += 10
            reg.update(W1, report(), 11)
            self.assertEqual(path.stat().st_mtime_ns, first)

    def test_old_windows_are_forgotten(self):
        with tempfile.TemporaryDirectory() as d:
            wall = Clock(1_000_000.0)
            reg = self.make(d, wall=wall)
            reg.update(W1, report(), 1)
            reg.flush()
            wall.t += STALE_AFTER_S + 10
            self.assertEqual(self.make(d, wall=wall).entries, {})

    def test_damaged_files_do_not_stop_it(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "windows.json"
            for text in ("garbage{", "[]", '{"windows": 5}', ""):
                path.write_text(text)
                self.assertEqual(self.make(d).entries, {}, text)
            path.write_text(json.dumps({"windows": {W1: report(), "BAD ID": report(), W2: {"width": "x"}, W3: report(dashboard="../x")}}))
            self.assertEqual(set(self.make(d).entries), {W1}, "one good entry survives the bad ones")


class PlanTests(unittest.TestCase):
    def test_everything_not_already_open_is_planned(self):
        reg = WindowRegistry(None, wall_clock=Clock(1000.0))
        reg.update(W1, report(), 1)
        reg._wall = Clock(2000.0)
        reg.update(W2, report(dashboard="drift", pinned=False, width=800, height=600), 2)
        plan = plan_restore(reg.entries, set(), True, False)
        self.assertEqual([p["id"] for p in plan], [W1, W2], "oldest first")
        self.assertEqual(plan[0]["path"], f"/?d=cluster&w={W1}")
        self.assertEqual(plan[1]["path"], f"/?w={W2}")
        self.assertEqual(plan[1]["size"], (800, 600))

    def test_windows_that_survived_are_left_alone(self):
        reg = WindowRegistry(None)
        reg.update(W1, report(), 1)
        reg.update(W2, report(), 2)
        self.assertEqual([p["id"] for p in plan_restore(reg.entries, {W1}, True, False)], [W2])
        self.assertEqual(plan_restore(reg.entries, {W1, W2}, True, False), [])

    def test_nothing_when_switched_off(self):
        reg = WindowRegistry(None)
        reg.update(W1, report(), 1)
        self.assertEqual(plan_restore(reg.entries, set(), False, False), [])

    def test_positions_only_when_exact_placement_is_on(self):
        reg = WindowRegistry(None)
        reg.update(W1, report(x=300, y=-40), 1)
        reg.update(W2, report(x=None, y=None), 2)
        by_id = {p["id"]: p for p in plan_restore(reg.entries, set(), True, True)}
        self.assertEqual(by_id[W1]["position"], (300, -40))
        self.assertIsNone(by_id[W2]["position"], "no position known")
        self.assertIsNone(plan_restore(reg.entries, set(), True, False)[0]["position"], "off: the system chooses")

    def test_missing_size_falls_back_to_the_default(self):
        reg = WindowRegistry(None)
        reg.update(W1, report(width=None, height=None), 1)
        self.assertEqual(plan_restore(reg.entries, set(), True, False)[0]["size"], (1200, 420))

    def test_fullscreen_is_carried(self):
        reg = WindowRegistry(None)
        reg.update(W1, report(fullscreen=True), 1)
        self.assertTrue(plan_restore(reg.entries, set(), True, False)[0]["fullscreen"])


class FakeSettings:
    def __init__(self, **over):
        self.values = {"restoreWindows": True, "exactWindowPlacement": False, **over}

    def get(self):
        return self.values


class StartupTests(unittest.IsolatedAsyncioTestCase):
    async def run_startup(self, registry, settings, running=False):
        opened, slept = [], []

        def launch(url, size, position, fullscreen, x11):
            opened.append((url, size, position, fullscreen, x11))
            return object()

        async def sleep(s):
            slept.append(s)

        count = await open_windows(registry, settings, launch, "http://127.0.0.1:8765", lambda: running, sleep)
        return count, opened, slept

    async def test_first_ever_start_opens_the_one_default_window(self):
        count, opened, slept = await self.run_startup(WindowRegistry(None), FakeSettings())
        self.assertEqual((count, [o[0] for o in opened], slept), (1, ["http://127.0.0.1:8765/"], []), "no browser running: no waiting")

    async def test_the_remembered_windows_are_reopened_one_at_a_time(self):
        reg = WindowRegistry(None, wall_clock=Clock(1000.0))
        reg.update(W1, report(x=10, y=20), 1)
        reg._wall = Clock(2000.0)
        reg.update(W2, report(dashboard="drift", width=900, height=500), 2)
        reg._live.clear()  # as after a restart
        count, opened, slept = await self.run_startup(reg, FakeSettings(exactWindowPlacement=True))
        self.assertEqual(count, 2)
        self.assertEqual(opened[0][:3], (f"http://127.0.0.1:8765/?d=cluster&w={W1}", (1200, 420), (10, 20)))
        self.assertEqual(opened[1][1], (900, 500))
        self.assertTrue(all(o[4] for o in opened), "X11 mode is requested for every window when exact placement is on")
        self.assertEqual(len(slept), 1, "a pause between windows, not before the first")

    async def test_without_exact_placement_no_positions_and_no_x11(self):
        reg = WindowRegistry(None)
        reg.update(W1, report(x=10, y=20), 1)
        reg._live.clear()
        _, opened, _ = await self.run_startup(reg, FakeSettings())
        self.assertEqual((opened[0][2], opened[0][4]), (None, False))

    async def test_windows_that_stayed_open_are_not_duplicated(self):
        reg = WindowRegistry(None)
        reg.update(W1, report(), 1)  # open, and reported since the relay started
        reg.update(W2, report(), 2)
        reg._live.discard(W2)  # this one did not come back
        count, opened, slept = await self.run_startup(reg, FakeSettings(), running=True)
        self.assertEqual([o[0] for o in opened], [f"http://127.0.0.1:8765/?d=cluster&w={W2}"])
        self.assertEqual(slept, [REATTACH_GRACE_S], "waits for the survivors to reconnect first")

    async def test_all_windows_survived_so_nothing_opens(self):
        reg = WindowRegistry(None)
        reg.update(W1, report(), 1)
        count, opened, _ = await self.run_startup(reg, FakeSettings(), running=True)
        self.assertEqual((count, opened), (0, []))

    async def test_restoring_off_opens_just_the_default_window(self):
        reg = WindowRegistry(None)
        reg.update(W1, report(), 1)
        reg._live.clear()
        count, opened, _ = await self.run_startup(reg, FakeSettings(restoreWindows=False))
        self.assertEqual([o[0] for o in opened], ["http://127.0.0.1:8765/"])

    async def test_a_browser_that_will_not_start_is_counted(self):
        reg = WindowRegistry(None)
        reg.update(W1, report(), 1)
        reg._live.clear()
        count = await open_windows(reg, FakeSettings(), lambda *a: None, "http://x", lambda: False, lambda s: asyncio.sleep(0))
        self.assertEqual(count, 0)


class LauncherArgsTests(unittest.TestCase):
    def args(self, **kw):
        return window_args("/usr/bin/chromium", "http://127.0.0.1:8765/?w=abc12345", "/profile", platform=kw.pop("platform", "linux"), **kw)

    def test_the_basics(self):
        a = self.args()
        self.assertIn("--app=http://127.0.0.1:8765/?w=abc12345", a)
        self.assertIn("--window-size=1200,420", a)
        for flag in ("--window-position", "--start-fullscreen", "--ozone-platform=x11"):
            self.assertFalse(any(x.startswith(flag) for x in a), flag)

    def test_windows_may_play_the_alert_sound_without_being_clicked_first(self):
        for platform in ("linux", "win32", "darwin"):
            self.assertIn("--autoplay-policy=no-user-gesture-required", self.args(platform=platform))

    def test_size_position_and_fullscreen(self):
        a = self.args(size=(900, 500), position=(-1920, 40), fullscreen=True)
        self.assertIn("--window-size=900,500", a)
        self.assertIn("--window-position=-1920,40", a)
        self.assertIn("--start-fullscreen", a)

    def test_x11_mode_is_a_linux_thing(self):
        self.assertIn("--ozone-platform=x11", self.args(x11=True))
        for other in ("win32", "darwin"):
            self.assertNotIn("--ozone-platform=x11", self.args(x11=True, platform=other))


class SettingsTests(unittest.TestCase):
    def test_the_two_switches(self):
        self.assertEqual(validate_settings({"restoreWindows": False, "exactWindowPlacement": True}), {"restoreWindows": False, "exactWindowPlacement": True})
        for bad in ({"restoreWindows": "yes"}, {"exactWindowPlacement": 1}, {"restoreWindows": None}):
            with self.assertRaises(ValidationError, msg=repr(bad)):
                validate_settings(bad)

    def test_defaults_and_persistence(self):
        with tempfile.TemporaryDirectory() as d:
            store = SettingsStore(Path(d) / "s.json")
            self.assertEqual((store.get()["restoreWindows"], store.get()["exactWindowPlacement"]), (True, False))
            store.update({"exactWindowPlacement": True})
            store.update({"units": {"speed": "mph"}})  # an unrelated update leaves it alone
            again = SettingsStore(Path(d) / "s.json").get()
            self.assertEqual((again["restoreWindows"], again["exactWindowPlacement"]), (True, True))


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class WindowApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        data = Path(self.tmp.name)
        self.registry = WindowRegistry(data / "windows.json")
        self.local = True
        self.port = free_port()
        app = make_app(Hub(), self.port, LayoutStore(data / "layouts", web_root() / "layouts"), SettingsStore(data / "settings.json"),
                       local_check=lambda remote: self.local, windows=self.registry)
        self.runner = web.AppRunner(app, access_log=None)
        await self.runner.setup()
        await web.TCPSite(self.runner, "127.0.0.1", self.port).start()
        self.base = f"http://127.0.0.1:{self.port}"
        self.http = aiohttp.ClientSession()

    async def asyncTearDown(self):
        await self.http.close()
        await self.runner.cleanup()
        self.tmp.cleanup()

    async def put(self, wid, body, headers=H):
        async with self.http.put(f"{self.base}/api/windows/{wid}", json=body, headers=headers) as r:
            return r.status

    async def test_report_list_and_close(self):
        self.assertEqual(await self.put(W1, {**report(), "stamp": 1000}), 200)
        async with self.http.get(self.base + "/api/windows") as r:
            data = await r.json()
        self.assertEqual((list(data["windows"]), data["live"]), ([W1], [W1]))
        async with self.http.delete(f"{self.base}/api/windows/{W1}?t=2000", headers=H) as r:
            self.assertEqual(r.status, 200)
        self.assertEqual(self.registry.entries, {})

    async def test_it_needs_the_write_header_and_this_computer(self):
        self.assertEqual(await self.put(W1, {**report(), "stamp": 1}, headers={}), 403)
        self.local = False
        self.assertEqual(await self.put(W1, {**report(), "stamp": 1}), 403)
        async with self.http.delete(f"{self.base}/api/windows/{W1}?t=1", headers=H) as r:
            self.assertEqual(r.status, 403)
        self.assertEqual(self.registry.entries, {})

    async def test_bad_input_is_a_clear_400(self):
        self.assertEqual(await self.put("UPPER", {**report(), "stamp": 1}), 400)
        self.assertEqual(await self.put(W1, {**report(width="wide"), "stamp": 1}), 400)
        self.assertEqual(await self.put(W1, report()), 400, "no stamp")
        self.assertEqual(await self.put(W1, ["not", "an", "object"]), 400)
        async with self.http.delete(f"{self.base}/api/windows/{W1}?t=soon", headers=H) as r:
            self.assertEqual(r.status, 400)
        self.assertEqual(self.registry.entries, {})

    async def test_shutdown_writes_what_is_pending(self):
        await self.put(W1, {**report(x=1), "stamp": 1})
        await self.put(W1, {**report(x=2), "stamp": 2})  # inside the save throttle, so only in memory so far
        path = Path(self.tmp.name) / "windows.json"
        self.assertEqual(json.loads(path.read_text())["windows"][W1]["x"], 1)
        await self.runner.shutdown()
        self.assertEqual(json.loads(path.read_text())["windows"][W1]["x"], 2)


if __name__ == "__main__":
    unittest.main()

"""Tests for what the packaged app needs: quitting when the windows close, a clean browser environment, installing the mod."""
import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from thrashdash.__main__ import parse_args  # noqa: E402
from thrashdash.launcher import child_env  # noqa: E402
from thrashdash.lifecycle import QUIT_GRACE_S, STARTUP_TIMEOUT_S, QuitWatcher, WindowCounter, quit_when_windows_close  # noqa: E402
from thrashdash.modinstall import LEGACY_MARKER, MARKER, candidate_user_folders, install_mod  # noqa: E402
from thrashdash.paths import legacy_data_dir, migrate_legacy_data, user_data_dir  # noqa: E402
from thrashdash.paths import mod_root  # noqa: E402


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


class WindowCounterTests(unittest.TestCase):
    def test_counts_open_and_remembers_that_one_ever_was(self):
        c = WindowCounter()
        self.assertEqual((c.open, c.opened_ever), (0, 0))
        c.connected()
        c.connected()
        c.disconnected()
        self.assertEqual((c.open, c.opened_ever), (1, 2))
        c.disconnected()
        c.disconnected()  # an extra close can never take it below zero
        self.assertEqual((c.open, c.opened_ever), (0, 2))


class QuitWatcherTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.counter = WindowCounter()
        self.watch = QuitWatcher(self.counter, clock=self.clock, grace=10, startup_timeout=90)

    # (the number of windows open is set through these, as the relay's WebSocket handler would)
    @property
    def open(self):
        return self.counter.open

    @open.setter
    def open(self, n):
        while self.counter.open < n:
            self.counter.connected()
        while self.counter.open > n:
            self.counter.disconnected()

    def tick(self, seconds):
        self.clock.t += seconds
        return self.watch.reason_to_quit()

    def test_runs_while_a_window_is_open(self):
        self.open = 1
        for _ in range(60):
            self.assertIsNone(self.tick(5))

    def test_a_hidden_or_minimised_window_still_counts(self):
        # Its page keeps its WebSocket however long its timers are throttled, so it stays open from the relay's side.
        self.open = 1
        self.assertIsNone(self.tick(1))
        for _ in range(100):
            self.assertIsNone(self.tick(60))

    def test_quits_a_moment_after_the_last_window_closes(self):
        self.open = 1
        self.assertIsNone(self.tick(1))
        self.open = 0
        self.assertIsNone(self.tick(1), "not at once")
        self.assertIsNone(self.tick(8))
        self.assertIn("closed", self.tick(2))

    def test_a_reload_or_a_new_window_in_time_keeps_it_running(self):
        self.open = 1
        self.tick(1)
        self.open = 0  # the old page closes...
        self.assertIsNone(self.tick(3))
        self.open = 1  # ...and the reloaded page connects
        for _ in range(10):
            self.assertIsNone(self.tick(5))

    def test_the_grace_period_starts_again_each_time(self):
        self.open = 1
        self.tick(1)
        self.open = 0
        self.tick(8)  # nearly out of time
        self.open = 1
        self.assertIsNone(self.tick(1))
        self.open = 0
        self.assertIsNone(self.tick(1), "a fresh grace period, not what was left of the last one")
        self.assertIsNone(self.tick(8))
        self.assertIn("closed", self.tick(2))

    def test_one_window_closing_does_not_matter_while_another_is_open(self):
        self.open = 2
        self.tick(1)
        self.open = 1
        for _ in range(10):
            self.assertIsNone(self.tick(5))

    def test_a_page_that_comes_and_goes_between_two_looks_still_counts(self):
        # (a page that connects and disconnects within the watcher's polling interval: it must still have "opened")
        self.counter.connected()
        self.counter.disconnected()
        self.assertIsNone(self.tick(1))
        self.assertIsNone(self.tick(8))
        self.assertIn("closed", self.tick(2), "it quits after the grace period, not after the long wait for a first window")

    def test_quits_if_no_window_ever_opens(self):
        self.assertIsNone(self.tick(60))
        self.assertIn("no dashboard window", self.tick(40))

    def test_a_late_first_window_is_fine(self):
        self.assertIsNone(self.tick(80))
        self.open = 1
        self.assertIsNone(self.tick(30))
        self.assertIsNone(self.tick(30))

    def test_the_defaults_leave_time_for_a_reload(self):
        self.assertGreaterEqual(QUIT_GRACE_S, 5)
        self.assertGreater(STARTUP_TIMEOUT_S, 30)


class QuitRunnerTests(unittest.IsolatedAsyncioTestCase):
    async def test_it_stops_the_app_and_says_why(self):
        clock = Clock()
        counter = WindowCounter()
        counter.connected()
        watch = QuitWatcher(counter, clock=clock, grace=2, startup_timeout=90)
        said, stop = [], asyncio.Event()

        async def sleep(seconds):
            clock.t += seconds
            await asyncio.sleep(0)
            if clock.t > 1003 and counter.open:
                counter.disconnected()  # the window is closed part way through

        await asyncio.wait_for(quit_when_windows_close(watch, stop, say=said.append, sleep=sleep, interval=1.0), 5)
        self.assertTrue(stop.is_set())
        self.assertEqual(len(said), 1)
        self.assertIn("quitting", said[0])

    async def test_it_does_nothing_if_already_stopped(self):
        stop = asyncio.Event()
        stop.set()
        await asyncio.wait_for(quit_when_windows_close(QuitWatcher(WindowCounter()), stop, say=lambda s: None), 2)


def free_port():
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class OpenWindowCountTests(unittest.IsolatedAsyncioTestCase):
    """An open dashboard window is an open WebSocket from this computer; a phone's does not count."""

    async def asyncSetUp(self):
        import aiohttp
        from aiohttp import web

        from thrashdash.hub import Hub
        from thrashdash.paths import web_root
        from thrashdash.server import make_app
        from thrashdash.store import LayoutStore, SettingsStore

        self.aiohttp = aiohttp
        self.tmp = tempfile.TemporaryDirectory()
        data = Path(self.tmp.name)
        self.local = True
        self.port = free_port()
        self.app = make_app(Hub(), self.port, LayoutStore(data / "l", web_root() / "layouts"), SettingsStore(data / "s.json"), local_check=lambda remote: self.local)
        self.runner = web.AppRunner(self.app, access_log=None)
        await self.runner.setup()
        await web.TCPSite(self.runner, "127.0.0.1", self.port).start()
        self.http = aiohttp.ClientSession()

    async def asyncTearDown(self):
        await self.http.close()
        await self.runner.cleanup()
        self.tmp.cleanup()

    def count(self):
        from thrashdash.server import WINDOW_COUNTER

        return self.app[WINDOW_COUNTER].open

    async def connect(self):
        return await self.http.ws_connect(f"http://127.0.0.1:{self.port}/ws", headers={"Origin": f"http://127.0.0.1:{self.port}"})

    async def test_each_open_page_counts_and_closing_uncounts_it(self):
        self.assertEqual(self.count(), 0)
        a, b = await self.connect(), await self.connect()
        await asyncio.sleep(0.1)
        self.assertEqual(self.count(), 2)
        await a.close()
        await asyncio.sleep(0.2)
        self.assertEqual(self.count(), 1)
        await b.close()
        await asyncio.sleep(0.2)
        self.assertEqual(self.count(), 0)

    async def test_a_phone_does_not_count(self):
        # With network access off a device that is not this computer is refused outright, so it can never keep ThrashDash
        # running; with it on, the relay files it under remote sockets (see server.ws_handler) and it is still not counted.
        self.local = False
        with self.assertRaises(self.aiohttp.WSServerHandshakeError):
            await self.connect()
        self.assertEqual(self.count(), 0)

    async def test_it_drives_the_watcher_end_to_end(self):
        clock = Clock()
        from thrashdash.server import WINDOW_COUNTER

        watch = QuitWatcher(self.app[WINDOW_COUNTER], clock=clock, grace=5, startup_timeout=90)
        ws = await self.connect()
        await asyncio.sleep(0.1)
        clock.t += 30
        self.assertIsNone(watch.reason_to_quit())
        await ws.close()
        await asyncio.sleep(0.2)
        clock.t += 1
        self.assertIsNone(watch.reason_to_quit())
        clock.t += 6
        self.assertIn("closed", watch.reason_to_quit())


class ArgsTests(unittest.TestCase):
    def test_the_new_options(self):
        a = parse_args([])
        self.assertFalse(a.quit_when_windows_close)
        self.assertIsNone(a.install_mod)
        self.assertTrue(parse_args(["--quit-when-windows-close"]).quit_when_windows_close)
        self.assertEqual(parse_args(["--install-mod"]).install_mod, "")
        self.assertEqual(parse_args(["--install-mod", "/some/where"]).install_mod, "/some/where")


class ChildEnvTests(unittest.TestCase):
    def test_a_plain_environment_is_left_alone(self):
        env = {"PATH": "/usr/bin:/bin", "HOME": "/home/x", "LD_LIBRARY_PATH": "/opt/lib"}
        self.assertEqual(child_env(env, bundle_roots=[]), env)

    def test_pyinstallers_saved_originals_are_put_back(self):
        env = {"LD_LIBRARY_PATH": "/tmp/_MEI123:/opt/lib", "LD_LIBRARY_PATH_ORIG": "/opt/lib", "HOME": "/h"}
        out = child_env(env, bundle_roots=["/tmp/_MEI123"])
        self.assertEqual(out["LD_LIBRARY_PATH"], "/opt/lib")
        self.assertNotIn("LD_LIBRARY_PATH_ORIG", out)

    def test_an_originally_unset_variable_is_removed_not_left_empty(self):
        out = child_env({"LD_LIBRARY_PATH": "/tmp/_MEI123", "LD_LIBRARY_PATH_ORIG": ""}, bundle_roots=["/tmp/_MEI123"])
        self.assertNotIn("LD_LIBRARY_PATH", out)

    def test_entries_inside_the_bundle_are_dropped_from_search_paths(self):
        env = {"PATH": "/tmp/.mount_abc/usr/bin:/usr/bin:/bin", "LD_LIBRARY_PATH": "/tmp/.mount_abc/usr/lib", "XDG_DATA_DIRS": "/tmp/.mount_abc/usr/share:/usr/share"}
        out = child_env(env, bundle_roots=["/tmp/.mount_abc"])
        self.assertEqual(out["PATH"], "/usr/bin:/bin")
        self.assertNotIn("LD_LIBRARY_PATH", out)
        self.assertEqual(out["XDG_DATA_DIRS"], "/usr/share")

    def test_a_similarly_named_folder_is_not_mistaken_for_the_bundle(self):
        out = child_env({"PATH": "/tmp/.mount_abcdef/bin:/usr/bin"}, bundle_roots=["/tmp/.mount_abc"])
        self.assertEqual(out["PATH"], "/tmp/.mount_abcdef/bin:/usr/bin")

    def test_appdir_from_the_environment_counts_as_the_bundle(self):
        out = child_env({"APPDIR": "/tmp/.mount_x", "PATH": "/tmp/.mount_x/usr/bin:/usr/bin"}, bundle_roots=None)
        self.assertEqual(out["PATH"], "/usr/bin")

    def test_other_variables_and_the_callers_dictionary_are_untouched(self):
        env = {"LD_LIBRARY_PATH": "/b/lib", "LD_LIBRARY_PATH_ORIG": "", "DISPLAY": ":0", "WAYLAND_DISPLAY": "wayland-1"}
        out = child_env(env, bundle_roots=["/b"])
        self.assertEqual((out["DISPLAY"], out["WAYLAND_DISPLAY"]), (":0", "wayland-1"))
        self.assertIn("LD_LIBRARY_PATH_ORIG", env, "the dictionary passed in is not changed")

    def test_windows_style_paths(self):
        out = child_env({"PATH": "C:\\bundle\\lib;C:\\Windows"}, bundle_roots=["C:\\bundle"], sep=";")
        self.assertEqual(out["PATH"], "C:\\Windows")


def fake_mod(root, content="-- the mod"):
    f = Path(root) / MARKER
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(content)
    return Path(root)


class InstallModTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.source = fake_mod(self.root / "src-mod", "-- version 2")
        self.said = []

    def tearDown(self):
        self.tmp.cleanup()

    def run_install(self, target=None, **kw):
        return install_mod(target, source=self.source, say=self.said.append, **kw)

    def test_the_real_mod_is_found(self):
        self.assertTrue((mod_root() / MARKER).is_file())

    def test_installs_into_a_named_user_folder(self):
        user = self.root / "BeamNG.drive" / "current"
        user.mkdir(parents=True)
        self.assertEqual(self.run_install(str(user)), 0)
        self.assertEqual((user / "mods" / "unpacked" / "thrashdash" / MARKER).read_text(), "-- version 2")
        self.assertIn("spawn it again", "\n".join(self.said))

    def test_accepts_the_folder_above_current_and_the_mods_folders(self):
        for name, make in (("a", lambda r: (r / "current").mkdir(parents=True)), ("b", lambda r: (r / "mods").mkdir(parents=True)), ("c", lambda r: (r / "mods" / "unpacked").mkdir(parents=True))):
            base = self.root / name
            make(base)
            given = base / "mods" / "unpacked" if name == "c" else base / "mods" if name == "b" else base
            self.assertEqual(self.run_install(str(given)), 0, name)
            expected = (base / "current" if name == "a" else base) / "mods" / "unpacked" / "thrashdash" / MARKER
            self.assertTrue(expected.is_file(), name)

    def test_finds_the_linux_user_folder_by_itself(self):
        data = self.root / "share"
        (data / "BeamNG" / "BeamNG.drive" / "current").mkdir(parents=True)
        self.assertEqual(self.run_install(platform="linux", home=self.root, env={"XDG_DATA_HOME": str(data)}), 0)
        self.assertTrue((data / "BeamNG" / "BeamNG.drive" / "current" / "mods" / "unpacked" / "thrashdash" / MARKER).is_file())

    def test_the_windows_folder_is_looked_for_under_localappdata(self):
        self.assertEqual(candidate_user_folders("win32", self.root, {"LOCALAPPDATA": "C:\\L"})[0], Path("C:\\L") / "BeamNG" / "BeamNG.drive" / "current")

    def test_says_where_it_looked_when_it_cannot_find_the_game(self):
        self.assertEqual(self.run_install(platform="linux", home=self.root, env={"XDG_DATA_HOME": str(self.root / "nothing")}), 1)
        text = "\n".join(self.said)
        self.assertIn("Could not find", text)
        self.assertIn("--install-mod PATH", text)

    def test_a_missing_folder_is_an_error_not_a_silent_mkdir(self):
        self.assertEqual(self.run_install(str(self.root / "typo")), 1)
        self.assertFalse((self.root / "typo").exists())

    def test_an_earlier_copy_is_replaced(self):
        user = self.root / "current"
        old = fake_mod(user / "mods" / "unpacked" / "thrashdash", "-- version 1")
        (old / "stale.txt").write_text("left over")
        self.assertEqual(self.run_install(str(user)), 0)
        self.assertEqual((old / MARKER).read_text(), "-- version 2")
        self.assertFalse((old / "stale.txt").exists(), "a clean replacement, not a merge")

    def test_a_link_to_the_source_is_left_alone(self):
        user = self.root / "current"
        unpacked = user / "mods" / "unpacked"
        unpacked.mkdir(parents=True)
        (unpacked / "thrashdash").symlink_to(self.source)
        self.assertEqual(self.run_install(str(user)), 0)
        self.assertTrue((unpacked / "thrashdash").is_symlink())
        self.assertIn("Already installed", "\n".join(self.said))

    def test_a_link_to_somewhere_else_is_not_followed_or_removed(self):
        elsewhere = fake_mod(self.root / "elsewhere", "-- mine")
        user = self.root / "current"
        unpacked = user / "mods" / "unpacked"
        unpacked.mkdir(parents=True)
        (unpacked / "thrashdash").symlink_to(elsewhere)
        self.assertEqual(self.run_install(str(user)), 0)
        self.assertEqual((elsewhere / MARKER).read_text(), "-- mine")
        self.assertTrue((unpacked / "thrashdash").is_symlink())

    def test_a_folder_that_is_not_ours_is_never_deleted(self):
        user = self.root / "current"
        theirs = user / "mods" / "unpacked" / "thrashdash"
        theirs.mkdir(parents=True)
        (theirs / "precious.txt").write_text("keep")
        self.assertEqual(self.run_install(str(user)), 1)
        self.assertEqual((theirs / "precious.txt").read_text(), "keep")

    def test_a_file_in_the_way_is_an_error(self):
        user = self.root / "current"
        (user / "mods" / "unpacked").mkdir(parents=True)
        (user / "mods" / "unpacked" / "thrashdash").write_text("a file")
        self.assertEqual(self.run_install(str(user)), 1)

    def test_a_broken_source_is_reported(self):
        empty = self.root / "empty"
        empty.mkdir()
        self.assertEqual(install_mod(str(self.root), source=empty, say=self.said.append), 1)
        self.assertIn("not found", "\n".join(self.said))

    def legacy_copy(self, unpacked):
        old = unpacked / "beamdash"
        f = old / LEGACY_MARKER
        f.parent.mkdir(parents=True)
        f.write_text("-- the mod under its previous name")
        return old

    def test_an_old_copy_under_the_previous_name_is_replaced_so_nothing_is_sent_twice(self):
        user = self.root / "current"
        unpacked = user / "mods" / "unpacked"
        old = self.legacy_copy(unpacked)
        self.assertEqual(self.run_install(str(user)), 0)
        self.assertFalse(old.exists(), "the old copy is gone")
        self.assertTrue((unpacked / "thrashdash" / MARKER).is_file(), "and the new one is in")
        self.assertIn("Removed the earlier copy", "\n".join(self.said))

    def test_the_old_copy_is_kept_if_the_new_install_fails(self):
        user = self.root / "current"
        unpacked = user / "mods" / "unpacked"
        old = self.legacy_copy(unpacked)
        (unpacked / "thrashdash").write_text("a file in the way")
        self.assertEqual(self.run_install(str(user)), 1)
        self.assertTrue(old.exists(), "a failed install must not cost the working old one")

    def test_an_old_link_is_mentioned_but_never_removed(self):
        user = self.root / "current"
        unpacked = user / "mods" / "unpacked"
        unpacked.mkdir(parents=True)
        elsewhere = self.root / "my-checkout-mod"
        (elsewhere / LEGACY_MARKER).parent.mkdir(parents=True)
        (elsewhere / LEGACY_MARKER).write_text("-- mine")
        (unpacked / "beamdash").symlink_to(elsewhere)
        self.assertEqual(self.run_install(str(user)), 0)
        self.assertTrue((unpacked / "beamdash").is_symlink())
        self.assertIn("sent twice", "\n".join(self.said))

    def test_an_unrelated_folder_with_the_old_name_is_left_alone(self):
        user = self.root / "current"
        unpacked = user / "mods" / "unpacked"
        (unpacked / "beamdash").mkdir(parents=True)
        (unpacked / "beamdash" / "someone_elses.txt").write_text("theirs")
        self.assertEqual(self.run_install(str(user)), 0)
        self.assertEqual((unpacked / "beamdash" / "someone_elses.txt").read_text(), "theirs")

    def test_an_old_link_whose_target_is_the_new_mod_is_fine_to_leave(self):
        # Someone who linked the game's folder to a source tree keeps working after the tree is renamed inside:
        # the old-named link now just points at the new files.
        user = self.root / "current"
        unpacked = user / "mods" / "unpacked"
        unpacked.mkdir(parents=True)
        (unpacked / "beamdash").symlink_to(self.source)
        self.assertEqual(self.run_install(str(user)), 0)
        self.assertTrue((unpacked / "beamdash").is_symlink())

    def test_bytecode_is_not_copied(self):
        (self.source / "__pycache__").mkdir()
        (self.source / "__pycache__" / "x.pyc").write_text("x")
        user = self.root / "current"
        user.mkdir()
        self.run_install(str(user))
        self.assertFalse((user / "mods" / "unpacked" / "thrashdash" / "__pycache__").exists())


class MigrateDataTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.old = self.root / "old"
        self.new = self.root / "new"
        self.said = []

    def tearDown(self):
        self.tmp.cleanup()

    def fill_old(self):
        (self.old / "layouts").mkdir(parents=True)
        (self.old / "layouts" / "u-0123456789ab.json").write_text('{"format": 1}')
        (self.old / "settings.json").write_text('{"units": {"speed": "mph"}}')
        (self.old / "trips.json").write_text("{}")
        (self.old / "windows.json").write_text("{}")
        (self.old / "window-profiles" / "main").mkdir(parents=True)
        (self.old / "window-profiles" / "main" / "Cookies").write_text("a browser profile")

    def test_the_saved_work_is_carried_over_and_the_old_folder_is_untouched(self):
        self.fill_old()
        self.assertTrue(migrate_legacy_data(self.new, self.old, say=self.said.append))
        self.assertEqual((self.new / "settings.json").read_text(), '{"units": {"speed": "mph"}}')
        self.assertEqual((self.new / "layouts" / "u-0123456789ab.json").read_text(), '{"format": 1}')
        self.assertTrue((self.new / "trips.json").is_file() and (self.new / "windows.json").is_file())
        self.assertTrue((self.old / "settings.json").is_file() and (self.old / "layouts").is_dir(), "copied, not moved")
        self.assertIn("left as it was", "\n".join(self.said))

    def test_the_browser_profile_is_not_carried_over(self):
        self.fill_old()
        migrate_legacy_data(self.new, self.old, say=self.said.append)
        self.assertFalse((self.new / "window-profiles").exists())

    def test_it_only_ever_happens_once(self):
        self.fill_old()
        migrate_legacy_data(self.new, self.old, say=self.said.append)
        (self.new / "settings.json").write_text('{"units": {"speed": "kmh"}}')  # the person changed something since
        self.assertFalse(migrate_legacy_data(self.new, self.old, say=self.said.append))
        self.assertEqual((self.new / "settings.json").read_text(), '{"units": {"speed": "kmh"}}')

    def test_a_new_folder_that_only_holds_a_log_still_gets_the_old_work(self):
        # The packaged app's launcher creates the new data folder (for its log file) before the program runs.
        self.fill_old()
        self.new.mkdir()
        (self.new / "thrashdash.log").write_text("the launcher's log")
        self.assertTrue(migrate_legacy_data(self.new, self.old, say=self.said.append))
        self.assertEqual((self.new / "settings.json").read_text(), '{"units": {"speed": "mph"}}')
        self.assertEqual((self.new / "thrashdash.log").read_text(), "the launcher's log", "what was there is kept")

    def test_a_new_folder_with_saved_work_is_never_touched(self):
        self.fill_old()
        self.new.mkdir()
        (self.new / "settings.json").write_text("mine")
        self.assertFalse(migrate_legacy_data(self.new, self.old, say=self.said.append))
        self.assertEqual([p.name for p in self.new.iterdir()], ["settings.json"])

    def test_nothing_to_do_without_an_old_folder(self):
        self.assertFalse(migrate_legacy_data(self.new, self.old, say=self.said.append))
        self.assertFalse(self.new.exists(), "no empty folder is made for nothing")
        self.assertEqual(self.said, [])

    def test_a_partly_filled_old_folder_copies_what_there_is(self):
        self.old.mkdir()
        (self.old / "settings.json").write_text("{}")
        self.assertTrue(migrate_legacy_data(self.new, self.old, say=self.said.append))
        self.assertEqual(sorted(p.name for p in self.new.iterdir()), ["settings.json"])

    def test_an_old_folder_with_nothing_worth_keeping_says_nothing(self):
        (self.old / "window-profiles").mkdir(parents=True)
        self.assertFalse(migrate_legacy_data(self.new, self.old, say=self.said.append))
        self.assertEqual(self.said, [])

    def test_the_two_folders_sit_side_by_side(self):
        old, new = legacy_data_dir(), user_data_dir()
        self.assertNotEqual(old, new)
        self.assertEqual(old.parent, new.parent)
        self.assertEqual(old.name.lower(), "beamdash")
        self.assertEqual(new.name.lower(), "thrashdash")


if __name__ == "__main__":
    unittest.main()

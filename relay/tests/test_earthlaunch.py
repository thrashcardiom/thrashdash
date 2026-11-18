"""Tests for starting BeamNG.Earth along with ThrashDash and quitting when it closes (earthlaunch.py)."""
import asyncio
import os
import stat
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from thrashdash import earthlaunch  # noqa: E402
from thrashdash.earthlaunch import EarthWatcher, candidates, keep_with_earth, pick_earth, version_key  # noqa: E402
from test_triplog import free_port  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
NAMES = [
    "BeamNG.Earth-1.10.1-beta.dev.459-x86_64.AppImage",
    "BeamNG.Earth-1.10.0-beta.dev.450-x86_64.AppImage",
    "BeamNG.Earth-1.10.0-beta.dev.449-x86_64.AppImage",
    "BeamNG.Earth-1.9.1-beta.dev.445-x86_64.AppImage",
    "BeamNG.Earth-1.9.1-beta.dev.440-x86_64.AppImage",
]


def make(folder, name, executable=True, text="#!/bin/sh\nexit 0\n"):
    path = Path(folder) / name
    path.write_text(text)
    path.chmod(0o755 if executable else 0o644)
    return path


class VersionTests(unittest.TestCase):
    def test_versions_order_by_number_not_by_text(self):
        keys = {n: version_key(n) for n in NAMES}
        ordered = sorted(NAMES, key=lambda n: keys[n])
        self.assertEqual(ordered[-1], "BeamNG.Earth-1.10.1-beta.dev.459-x86_64.AppImage", "1.10.1 beats 1.9.1 (a text sort would get this wrong)")
        self.assertEqual(ordered[0], "BeamNG.Earth-1.9.1-beta.dev.440-x86_64.AppImage")
        self.assertLess(keys["BeamNG.Earth-1.10.0-beta.dev.449-x86_64.AppImage"], keys["BeamNG.Earth-1.10.0-beta.dev.450-x86_64.AppImage"], "then by build")

    def test_a_release_beats_a_pre_release_of_the_same_numbers(self):
        self.assertGreater(version_key("BeamNG.Earth-1.11.0-x86_64.AppImage"), version_key("BeamNG.Earth-1.11.0-beta.dev.999-x86_64.AppImage"))
        self.assertGreater(version_key("BeamNG.Earth-1.11.0-beta.dev.1-x86_64.AppImage"), version_key("BeamNG.Earth-1.10.9-x86_64.AppImage"))

    def test_a_name_with_no_version_has_none(self):
        for name in ("BeamNG.Earth.AppImage", "SomethingElse-1.0.AppImage", "BeamNG.Earth-latest-x86_64.AppImage"):
            self.assertIsNone(version_key(name), name)


class PickTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def downloads(self):
        for n in NAMES:
            make(self.dir, n)
        for noise in ("BeamNG.Earth-keep.json", "BeamNG.Earth-update.json", "BeamNG.Earth-downloaded.json", "BeamNG.Earth-2.0.0-beta.dev.1-x86_64.AppImage.part",
                      "BeamNG.Earth-2.0.0-beta.dev.2-x86_64.AppImage.crdownload", "notes.txt", "Other-9.9.9.AppImage"):
            make(self.dir, noise, executable=True)

    def test_the_newest_version_in_a_folder_is_chosen_and_the_clutter_is_ignored(self):
        self.downloads()
        path, message = pick_earth(str(self.dir), "linux")
        self.assertEqual(path.name, "BeamNG.Earth-1.10.1-beta.dev.459-x86_64.AppImage")
        self.assertIn("newest version", message)
        self.assertEqual(len(candidates(self.dir, "linux")), 5)

    def test_it_is_the_highest_version_even_if_an_older_one_was_downloaded_later(self):
        self.downloads()
        old = self.dir / "BeamNG.Earth-1.9.1-beta.dev.440-x86_64.AppImage"
        os.utime(old, (time.time() + 1000, time.time() + 1000))
        self.assertEqual(pick_earth(str(self.dir), "linux")[0].name, "BeamNG.Earth-1.10.1-beta.dev.459-x86_64.AppImage")

    def test_a_file_can_be_named_directly(self):
        path = make(self.dir, "BeamNG.Earth-1.9.1-beta.dev.440-x86_64.AppImage")
        make(self.dir, NAMES[0])
        self.assertEqual(pick_earth(str(path), "linux")[0], path)

    def test_a_file_that_is_not_executable_is_not_started_and_the_message_says_so(self):
        make(self.dir, NAMES[0], executable=False)
        path, message = pick_earth(str(self.dir), "linux")
        self.assertIsNone(path)
        self.assertIn("not marked as executable", message)
        self.assertIn(NAMES[0], message)

    def test_the_newest_not_being_executable_does_not_fall_back_to_an_older_one(self):
        make(self.dir, NAMES[3])
        make(self.dir, NAMES[0], executable=False)
        self.assertIsNone(pick_earth(str(self.dir), "linux")[0], "an older program is not started in place of the one asked for")

    def test_nothing_to_start_is_said_plainly(self):
        self.assertEqual(pick_earth("", "linux")[0], None)
        self.assertEqual(pick_earth(None, "linux")[0], None)
        path, message = pick_earth(str(self.dir), "linux")
        self.assertIsNone(path)
        self.assertIn("no BeamNG.Earth program", message)
        path, message = pick_earth(str(self.dir / "missing"), "linux")
        self.assertIsNone(path)
        self.assertIn("does not exist", message)

    def test_a_tilde_means_the_home_folder(self):
        home = Path(self.tmp.name)
        make(home, NAMES[0])
        old = os.environ.get("HOME")
        os.environ["HOME"] = str(home)
        try:
            self.assertEqual(pick_earth("~", "linux")[0].name, NAMES[0])
        finally:
            if old is None:
                del os.environ["HOME"]
            else:
                os.environ["HOME"] = old

    def test_on_windows_it_is_the_exe_files(self):
        make(self.dir, "BeamNG.Earth-1.10.1-Setup.exe", executable=False)
        make(self.dir, NAMES[0])
        path, _ = pick_earth(str(self.dir), "win32")
        self.assertEqual(path.name, "BeamNG.Earth-1.10.1-Setup.exe", "no executable bit is asked for on Windows")


class WatcherTests(unittest.TestCase):
    def test_an_earth_that_answers_is_fine(self):
        w = EarthWatcher(0.0)
        self.assertIsNone(w.update(1.0, True, True))
        self.assertIsNone(w.update(100.0, True, True))

    def test_one_that_was_seen_and_goes_silent_has_closed_after_the_wait(self):
        w = EarthWatcher(0.0, gone_after_s=15.0)
        w.update(5.0, True, True)
        self.assertIsNone(w.update(10.0, False, True), "a short silence is not the end (a busy moment)")
        self.assertIsNone(w.update(19.9, False, True))
        self.assertEqual(w.update(20.0, False, True), "gone")

    def test_a_silence_that_ends_starts_the_count_again(self):
        w = EarthWatcher(0.0, gone_after_s=15.0)
        w.update(1.0, True)
        w.update(12.0, False)
        w.update(13.0, True)
        self.assertIsNone(w.update(27.0, False))
        self.assertEqual(w.update(28.0, False), "gone")

    def test_one_that_never_comes_up_fails_only_after_the_long_wait_and_only_if_it_is_not_running(self):
        w = EarthWatcher(0.0, never_up_s=120.0)
        self.assertIsNone(w.update(60.0, False, True), "it is still starting")
        self.assertIsNone(w.update(119.0, False, False), "even an exited program gets the wait: an installer-style launcher may hand over to another")
        self.assertEqual(w.update(121.0, False, False), "failed")

    def test_one_that_is_running_but_slow_is_waited_for_and_said_so_once(self):
        w = EarthWatcher(0.0, never_up_s=120.0)
        self.assertEqual(w.update(121.0, False, True), "slow")
        self.assertIsNone(w.update(130.0, False, True))
        self.assertIsNone(w.update(500.0, False, True), "it is never given up on while its program runs")
        self.assertIsNone(w.update(501.0, True, True))
        self.assertTrue(w.seen)

    def test_an_earth_ThrashDash_did_not_start_is_also_followed(self):
        w = EarthWatcher(0.0, gone_after_s=15.0)
        w.seen = True
        self.assertEqual(w.update(16.0, False, None), "gone")


class FakeProcess:
    """An Earth program that is running until told otherwise. It has no terminate or kill: ThrashDash must never close Earth."""

    def __init__(self):
        self.code = None

    def poll(self):
        return self.code


class KeepTests(unittest.IsolatedAsyncioTestCase):
    async def run_it(self, answers, launch_result=None, launch_error=None, gone_after_s=15.0, never_up_s=120.0, process=None):
        """`answers`: what each look at Earth's service finds, in order (the last one repeats)."""
        said, stopped, calls = [], [], {"probe": 0, "launch": 0}
        clock = {"t": 0.0}

        def probe_fn(port):
            i = min(calls["probe"], len(answers) - 1)
            calls["probe"] += 1
            return answers[i]

        def launch_fn(path, log_path):
            calls["launch"] += 1
            if launch_error:
                raise launch_error
            return process or FakeProcess()

        async def sleep(s):
            clock["t"] += s
            await asyncio.sleep(0)

        await asyncio.wait_for(keep_with_earth("/x/earth", lambda: stopped.append(1), say=said.append, poll_s=3.0, clock=lambda: clock["t"], sleep=sleep, probe_fn=probe_fn,
                                               launch_fn=launch_fn, gone_after_s=gone_after_s, never_up_s=never_up_s), timeout=10)
        return said, stopped, calls

    async def test_earth_is_started_and_when_it_closes_ThrashDash_quits(self):
        said, stopped, calls = await self.run_it([False] + [True] * 5 + [False])
        self.assertEqual(calls["launch"], 1)
        self.assertEqual(stopped, [1])
        self.assertTrue(any("Started BeamNG.Earth" in s for s in said))
        self.assertTrue(any("has closed" in s for s in said))

    async def test_an_earth_that_is_already_running_is_not_started_again_but_is_followed(self):
        said, stopped, calls = await self.run_it([True, True, True, False])
        self.assertEqual(calls["launch"], 0)
        self.assertEqual(stopped, [1])
        self.assertTrue(any("already running" in s for s in said))

    async def test_an_earth_that_cannot_be_started_is_said_and_ThrashDash_carries_on(self):
        said, stopped, calls = await self.run_it([False], launch_error=OSError("Permission denied"))
        self.assertEqual(stopped, [], "ThrashDash does not quit for an Earth that never started")
        self.assertTrue(any("Could not start" in s and "Permission denied" in s for s in said))

    async def test_an_earth_that_exits_without_ever_answering_is_reported_and_ThrashDash_carries_on(self):
        process = FakeProcess()
        process.code = 1
        said, stopped, calls = await self.run_it([False], process=process)
        self.assertEqual(stopped, [])
        self.assertTrue(any("did not come up" in s for s in said))

    async def test_ThrashDash_never_closes_Earth(self):
        process = FakeProcess()  # (a terminate() or kill() would raise AttributeError here)
        await self.run_it([False] + [True] * 3 + [False], process=process)
        self.assertIsNone(process.code, "Earth's program is left exactly as it was")


class LaunchTests(unittest.TestCase):
    def test_earth_is_started_on_its_own_with_a_clean_environment_and_its_messages_kept(self):
        with tempfile.TemporaryDirectory() as d:
            marker = Path(d) / "env.txt"
            program = make(d, "BeamNG.Earth-1.0.0-x86_64.AppImage", text=f"#!/bin/sh\necho \"LD=$LD_LIBRARY_PATH\" > {marker}\necho \"APPDIR=$APPDIR\" >> {marker}\necho hello-from-earth\n")
            old = {k: os.environ.get(k) for k in ("LD_LIBRARY_PATH", "LD_LIBRARY_PATH_ORIG", "APPDIR")}
            os.environ.update({"LD_LIBRARY_PATH": "/bundle/lib", "LD_LIBRARY_PATH_ORIG": "/original/lib", "APPDIR": "/bundle"})
            try:
                log = Path(d) / "earth.log"
                process = earthlaunch.launch(program, log)
                process.wait(timeout=10)
            finally:
                for k, v in old.items():
                    if v is None:
                        os.environ.pop(k, None)
                    else:
                        os.environ[k] = v
            text = marker.read_text()
            self.assertIn("LD=/original/lib", text, "the environment ThrashDash was given, not its own bundle's libraries")
            self.assertIn("hello-from-earth", log.read_text(), "its messages are kept")

    def test_a_program_that_cannot_be_run_raises_oserror(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(OSError):
                earthlaunch.launch(Path(d) / "BeamNG.Earth-missing.AppImage")


FAKE_EARTH = textwrap.dedent('''\
    #!/usr/bin/env python3
    # A stand-in for BeamNG.Earth: answers on its local service for a few seconds, then closes.
    import http.server, json, sys, threading, time
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body = json.dumps({"api": 1, "app": "fake-earth"}).encode()
            self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
        def log_message(self, *a): pass
    server = http.server.HTTPServer(("127.0.0.1", {port}), H)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    time.sleep({seconds})
    server.shutdown()
''')


class EndToEndTests(unittest.TestCase):
    def test_thrashdash_starts_the_newest_earth_and_quits_when_it_closes_without_closing_it(self):
        with tempfile.TemporaryDirectory() as d:
            folder = Path(d) / "downloads"
            folder.mkdir()
            port = free_port()
            make(folder, "BeamNG.Earth-1.9.1-beta.dev.440-x86_64.AppImage", text="#!/bin/sh\necho OLD > " + str(Path(d) / "old-was-run") + "\n")
            make(folder, "BeamNG.Earth-1.10.1-beta.dev.459-x86_64.AppImage", text=FAKE_EARTH.replace("{port}", str(port)).replace("{seconds}", "5"))
            data = Path(d) / "data"
            env = {**os.environ, "XDG_DATA_HOME": str(data), "LOCALAPPDATA": str(data)}
            started = time.monotonic()
            relay = subprocess.Popen([sys.executable, str(ROOT / "relay" / "run.py"), "--no-window", "--http-port", str(free_port()), "--udp-port", str(free_port()),
                                      "--with-earth", str(folder), "--earth-port", str(port), "--debug-earth-timing", "0.3,1.5"],
                                     env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            try:
                output, _ = relay.communicate(timeout=40)
            except subprocess.TimeoutExpired:
                relay.kill()
                output, _ = relay.communicate()
                self.fail("ThrashDash did not quit when Earth closed:\n" + output)
            self.assertEqual(relay.returncode, 0, output)
            self.assertIn("BeamNG.Earth-1.10.1-beta.dev.459", output)
            self.assertIn("has closed, so ThrashDash is closing too", output)
            self.assertFalse((Path(d) / "old-was-run").exists(), "the older version was not started")
            self.assertGreater(time.monotonic() - started, 4.0, "it waited for Earth (which ran for five seconds)")

    def test_a_setting_that_names_nothing_usable_is_reported_and_ThrashDash_runs_on(self):
        with tempfile.TemporaryDirectory() as d:
            data = Path(d) / "data"
            env = {**os.environ, "XDG_DATA_HOME": str(data), "LOCALAPPDATA": str(data)}
            relay = subprocess.Popen([sys.executable, str(ROOT / "relay" / "run.py"), "--no-window", "--http-port", str(free_port()), "--udp-port", str(free_port()), "--with-earth", str(Path(d) / "nothing-here")],
                                     env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                time.sleep(3.0)
                self.assertIsNone(relay.poll(), "ThrashDash is still running")
            finally:
                relay.terminate()
                _, err = relay.communicate(timeout=10)
            self.assertIn("not started", err)
            self.assertIn("does not exist", err)


if __name__ == "__main__":
    unittest.main()

"""Runs the Lua tests (tests/lua) for the BeamNG mod against fake game objects. Skipped if LuaJIT is not installed."""
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LUAJIT = shutil.which("luajit")


@unittest.skipIf(LUAJIT is None, "needs LuaJIT (the interpreter BeamNG itself uses)")
class ModLuaTests(unittest.TestCase):
    def test_telemetry_mod_against_fake_game_objects(self):
        done = subprocess.run([LUAJIT, "tests/lua/test_telemetry.lua"], cwd=ROOT, capture_output=True, text=True, timeout=60)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("0 failed", done.stdout)

    def test_trip_log_extension_against_fake_game_objects(self):
        done = subprocess.run([LUAJIT, "tests/lua/test_trips.lua"], cwd=ROOT, capture_output=True, text=True, timeout=60)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("0 failed", done.stdout)


if __name__ == "__main__":
    unittest.main()

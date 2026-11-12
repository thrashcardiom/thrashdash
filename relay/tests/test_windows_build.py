"""Tests for the Windows packaging that can be run anywhere: the program's behaviour with no console, the packaged-app
defaults, the licence tables, and the build files. (Running the build itself needs Windows.)"""
import importlib.util
import io
import os
import re
import struct
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from thrashdash.__main__ import apply_packaged_defaults, parse_args  # noqa: E402
from thrashdash.modinstall import candidate_user_folders  # noqa: E402
from thrashdash.output import ensure_output  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]


def load_collector():
    spec = importlib.util.spec_from_file_location("collect_licenses", ROOT / "packaging" / "collect_licenses.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class NoConsoleTests(unittest.TestCase):
    """The Windows program people double-click has no standard output at all (sys.stdout is None)."""

    def setUp(self):
        self.saved = (sys.stdout, sys.stderr)
        self.tmp = tempfile.TemporaryDirectory()
        self.log = Path(self.tmp.name) / "data" / "thrashdash.log"

    def tearDown(self):
        for stream in {sys.stdout, sys.stderr}:
            if stream not in self.saved and stream is not None:
                stream.close()
        sys.stdout, sys.stderr = self.saved
        self.tmp.cleanup()

    def test_nothing_happens_when_there_is_a_console(self):
        self.assertIsNone(ensure_output(self.log))
        self.assertFalse(self.log.exists())
        self.assertEqual((sys.stdout, sys.stderr), self.saved)

    def test_without_stdout_messages_go_to_the_log(self):
        sys.stdout = sys.stderr = None
        self.assertEqual(ensure_output(self.log), self.log)
        print("a message")
        print("a problem", file=sys.stderr)
        self.assertEqual(self.log.read_text(encoding="utf-8"), "a message\na problem\n", "written as it happens, not at exit")

    def test_each_run_keeps_only_its_own_messages(self):
        self.log.parent.mkdir(parents=True)
        self.log.write_text("the last run\n")
        sys.stdout = sys.stderr = None
        ensure_output(self.log)
        print("this run")
        self.assertEqual(self.log.read_text(encoding="utf-8"), "this run\n")

    def test_only_the_missing_stream_is_replaced(self):
        sys.stdout = io.StringIO()
        sys.stderr = None
        ensure_output(self.log)
        print("to stdout")
        print("to stderr", file=sys.stderr)
        self.assertEqual(sys.stdout.getvalue(), "to stdout\n")
        self.assertEqual(self.log.read_text(encoding="utf-8"), "to stderr\n")

    def test_argparse_messages_do_not_crash_without_a_console(self):
        sys.stdout = sys.stderr = None
        ensure_output(self.log)
        with self.assertRaises(SystemExit) as ctx:
            parse_args(["--version"])
        self.assertEqual(ctx.exception.code, 0)
        self.assertIn("thrashdash", self.log.read_text(encoding="utf-8"))

    def test_an_unwritable_log_is_not_fatal(self):
        sys.stdout = sys.stderr = None
        blocked = Path(self.tmp.name) / "a-file"
        blocked.write_text("in the way")
        self.assertIsNone(ensure_output(blocked / "thrashdash.log"))  # its parent "folder" is a file
        print("this goes nowhere, but does not raise")
        print("nor does this", file=sys.stderr)


class PackagedDefaultsTests(unittest.TestCase):
    def test_a_packaged_app_quits_when_its_last_window_closes(self):
        self.assertTrue(apply_packaged_defaults(parse_args([]), frozen=True).quit_when_windows_close)

    def test_unless_told_to_keep_running(self):
        self.assertFalse(apply_packaged_defaults(parse_args(["--keep-running"]), frozen=True).quit_when_windows_close)

    def test_from_the_source_nothing_changes(self):
        self.assertFalse(apply_packaged_defaults(parse_args([]), frozen=False).quit_when_windows_close)
        self.assertTrue(apply_packaged_defaults(parse_args(["--quit-when-windows-close"]), frozen=False).quit_when_windows_close)

    def test_keep_running_does_not_cancel_an_explicit_request_to_quit_from_the_source(self):
        # (--keep-running only turns off the packaged default; it is not an opposite of the other flag)
        self.assertTrue(apply_packaged_defaults(parse_args(["--quit-when-windows-close", "--keep-running"]), frozen=False).quit_when_windows_close)

    def test_the_real_environment_is_not_packaged(self):
        self.assertFalse(getattr(sys, "frozen", False))
        self.assertFalse(apply_packaged_defaults(parse_args([])).quit_when_windows_close)


class WindowsGameFolderTests(unittest.TestCase):
    def test_the_folders_looked_in_on_windows(self):
        found = candidate_user_folders("win32", Path("C:/Users/me"), {"LOCALAPPDATA": "C:/Users/me/AppData/Local"})
        self.assertEqual(found[0], Path("C:/Users/me/AppData/Local/BeamNG/BeamNG.drive/current"))
        self.assertIn(Path("C:/Users/me/Documents/BeamNG.drive"), found, "the older location")
        self.assertGreaterEqual(len(found), 3)


class NativeLibraryTableTests(unittest.TestCase):
    def setUp(self):
        self.cl = load_collector()

    def test_what_a_python_org_build_is_expected_to_contain_is_recognised(self):
        for name in ("python313.dll", "python312.dll", "VCRUNTIME140.dll", "VCRUNTIME140_1.dll", "MSVCP140.dll", "libssl-3.dll", "libcrypto-3-x64.dll",
                     "libffi-8.dll", "_ssl.pyd", "_ctypes.pyd", "unicodedata.pyd", "select.pyd", "ucrtbase.dll", "api-ms-win-crt-runtime-l1-1-0.dll", "sqlite3.dll"):
            self.assertIsNotNone(self.cl.classify_native(name, "win32"), name)

    def test_an_unknown_windows_library_is_not_guessed_at(self):
        for name in ("mystery.dll", "tcl86t.dll", "some_dependency.dll"):
            self.assertIsNone(self.cl.classify_native(name, "win32"), name)

    def test_each_windows_component_has_a_licence_and_a_home(self):
        for pattern, component, licence, url in self.cl.NATIVE_WINDOWS:
            self.assertTrue(component and licence and url.startswith("https://"), pattern)
            re.compile(pattern)

    def test_the_linux_libraries_are_still_recognised(self):
        for name in ("libpython3.10.so.1.0", "libssl.so.3", "libcrypto.so.3", "libffi.so.8", "libexpat.so.1", "libbz2.so.1.0", "liblzma.so.5",
                     "libmpdec.so.3", "libz.so.1", "libuuid.so.1", "libgcc_s.so.1", "libstdc++.so.6"):
            self.assertIsNotNone(self.cl.classify_native(name, "linux"), name)
        self.assertIsNone(self.cl.classify_native("libmystery.so.1", "linux"))

    def test_the_platforms_do_not_mix(self):
        self.assertIsNone(self.cl.classify_native("libssl.so.3", "win32"))
        self.assertIsNone(self.cl.classify_native("python313.dll", "linux"))

    def test_only_native_files_are_looked_at(self):
        with tempfile.TemporaryDirectory() as d:
            for name in ("python313.dll", "_ssl.pyd", "base_library.zip", "thrashdash.exe", "libssl.so.3", "notes.txt"):
                (Path(d) / name).write_text("x")
            (Path(d) / "aiohttp").mkdir()
            (Path(d) / "aiohttp" / "_http_parser.pyd").write_text("x")
            self.assertEqual([p.name for p in self.cl.native_files(d, "win32")], ["_ssl.pyd", "python313.dll"])
            self.assertEqual([p.name for p in self.cl.native_files(d, "linux")], ["libssl.so.3"])


class BuildFileTests(unittest.TestCase):
    def read(self, relative):
        return (ROOT / relative).read_text(encoding="utf-8")

    def test_the_icon_is_a_real_multi_size_windows_icon(self):
        data = (ROOT / "packaging" / "thrashdash.ico").read_bytes()
        reserved, kind, count = struct.unpack("<HHH", data[:6])
        self.assertEqual((reserved, kind), (0, 1), "an .ico header")
        self.assertGreaterEqual(count, 5)
        sizes = {data[6 + 16 * i] or 256 for i in range(count)}
        self.assertTrue({16, 32, 48, 256} <= sizes, sizes)

    def test_the_spec_builds_a_window_less_program_and_a_console_one_on_windows(self):
        spec = self.read("packaging/thrashdash.spec")
        self.assertIn('sys.platform == "win32"', spec)
        self.assertRegex(spec, r'name="thrashdash",\s*console=False')
        self.assertRegex(spec, r'name="thrashdash-cli",\s*console=True')
        self.assertIn("thrashdash.ico", spec)
        self.assertRegex(spec, r'else:\s*\n\s*exe = EXE\([^\n]*console=True', "Linux keeps its single console program")

    def test_the_windows_script_builds_and_ships_what_it_should(self):
        script = self.read("packaging/build.ps1")
        for needed in ("collect_licenses.py", "thrashdash.spec", "Compress-Archive", "Get-FileHash", "thrashdash-cli.exe", "LICENSE", "THIRD_PARTY_NOTICES.md", "requirements-build.txt"):
            self.assertIn(needed, script, needed)
        self.assertNotIn("--update-repo-file", script, "a Windows build must never overwrite the repository's (Linux-generated) notices file")

    def test_the_linux_build_still_refreshes_the_notices_file(self):
        self.assertIn("--update-repo-file", self.read("packaging/build.sh"))

    def test_the_double_click_wrapper_bypasses_the_script_execution_policy(self):
        bat = self.read("packaging/build-windows.bat")
        self.assertIn("-ExecutionPolicy Bypass", bat)
        self.assertIn("build.ps1", bat)

    def test_the_windows_script_has_balanced_brackets_and_quotes(self):
        # (PowerShell is not available on the machine this was written on, so at least the cheap structural checks)
        script = self.read("packaging/build.ps1")
        body = re.sub(r"<#.*?#>", "", re.sub(r'@"\n.*?\n"@', "", script, flags=re.S), flags=re.S)
        for opening, closing in (("{", "}"), ("(", ")"), ("[", "]")):
            self.assertEqual(body.count(opening), body.count(closing), opening)
        for line in body.split("\n"):
            self.assertEqual(line.split("#")[0].count('"') % 2, 0, line)


if __name__ == "__main__":
    unittest.main()

# PyInstaller recipe for ThrashDash. Run from the repository root (packaging/build.sh does).
#
# It makes a folder (dist/thrashdash/) holding the program, Python, aiohttp, the dashboard (web/) and the BeamNG mod (mod/):
# everything ThrashDash needs except a browser. The AppImage wraps that folder. paths.web_root() and paths.mod_root() find
# the data files in the unpacked bundle (sys._MEIPASS).
import os
import sys

from PyInstaller.utils.hooks import collect_submodules

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))  # (paths in a spec file are otherwise relative to the spec's own folder)

a = Analysis(
    [os.path.join(ROOT, "relay", "run.py")],
    pathex=[os.path.join(ROOT, "relay")],
    datas=[(os.path.join(ROOT, "web"), "web"), (os.path.join(ROOT, "mod"), "mod")],
    hiddenimports=collect_submodules("thrashdash"),
    excludes=["tkinter", "unittest", "pydoc", "doctest", "test"],
    noarchive=False,
)
pyz = PYZ(a.pure)

if sys.platform == "win32":
    # Two programs sharing one set of files. thrashdash.exe has no console window (what you double-click): its messages go to
    # thrashdash.log in the data folder. thrashdash-cli.exe is the same program WITH a console, for running from a terminal
    # (--help, --version, --install-mod ... print where you ran them, which a window-less program cannot do).
    icon = os.path.join(ROOT, "packaging", "thrashdash.ico")
    exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="thrashdash", console=False, icon=icon)
    cli = EXE(pyz, a.scripts, [], exclude_binaries=True, name="thrashdash-cli", console=True, icon=icon)
    programs = [exe, cli]
else:
    exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="thrashdash", console=True)  # console: its messages go to the log file (AppRun)
    programs = [exe]
coll = COLLECT(*programs, a.binaries, a.datas, name="thrashdash")

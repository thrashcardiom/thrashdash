# PyInstaller recipe for ThrashDash. Run from the repository root (packaging/build.sh does).
#
# It makes a folder (dist/thrashdash/) holding the program, Python, aiohttp, the dashboard (web/) and the BeamNG mod (mod/):
# everything ThrashDash needs except a browser. The AppImage wraps that folder. paths.web_root() and paths.mod_root() find
# the data files in the unpacked bundle (sys._MEIPASS).
import os

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
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="thrashdash", console=True)  # console: its messages go to the log file
coll = COLLECT(exe, a.binaries, a.datas, name="thrashdash")

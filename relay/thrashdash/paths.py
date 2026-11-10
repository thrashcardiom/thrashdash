"""Where things live on disk, on Windows and Linux (and macOS, untested).

Deliberately tiny and dependency-free instead of pulling in platformdirs: we need a handful of
locations, and one fewer dependency means one fewer thing to bundle and audit in the shared build.
"""
import os
import shutil
import sys
from pathlib import Path


def _data_dir(win_mac_name: str, linux_name: str) -> Path:
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")
        return Path(base) / win_mac_name
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / win_mac_name
    base = os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share")
    return Path(base) / linux_name


def user_data_dir() -> Path:
    """Per-user writable folder for layouts, settings and the dashboard window's browser profile."""
    return _data_dir("ThrashDash", "thrashdash")


def legacy_data_dir() -> Path:
    """The data folder this program used under its previous name (see migrate_legacy_data)."""
    return _data_dir("BeamDash", "beamdash")


# What is carried over. The window profile (a browser profile) is deliberately not: it may be in use by a window that is
# still open, and it holds nothing worth keeping (the browser's own per-page memory of zoom and look).
_MIGRATED = ("layouts", "settings.json", "trips.json", "windows.json")


def migrate_legacy_data(new=None, old=None, say=print) -> bool:
    """On the first run under the new name, copy what the program saved under its old name, so nothing seems to be lost.

    Only when the old folder exists and the new one holds none of the saved files yet. (The new folder itself may already
    exist: the packaged app's launcher makes it to write its log file in, before this program runs.) It COPIES: the old
    folder is left exactly as it was (a backup, and safe if the old version is still running). Returns True if anything
    was copied.
    """
    new = Path(new) if new else user_data_dir()
    old = Path(old) if old else legacy_data_dir()
    if not old.is_dir() or any((new / name).exists() for name in _MIGRATED):
        return False
    copied = []
    try:
        new.mkdir(parents=True, exist_ok=True)
        for name in _MIGRATED:
            source = old / name
            if source.is_dir():
                shutil.copytree(source, new / name)
                copied.append(name)
            elif source.is_file():
                shutil.copy2(source, new / name)
                copied.append(name)
    except OSError as e:
        say(f"Could not copy your saved dashboards and settings from {old}: {e.strerror or e}")
        return bool(copied)
    if copied:
        say(f"Copied your saved dashboards and settings from {old} to {new}. The old folder was left as it was; delete it when you no longer need it.")
    return bool(copied)


def web_root() -> Path:
    """Folder holding the dashboard front end (index.html, js/, css/)."""
    if getattr(sys, "frozen", False):  # PyInstaller bundle: data files are unpacked next to the code
        return Path(getattr(sys, "_MEIPASS")) / "web"
    # Development layout: <repo>/relay/thrashdash/paths.py -> <repo>/web
    return Path(__file__).resolve().parents[2] / "web"


def mod_root() -> Path:
    """Folder holding the BeamNG mod (the `thrashdash` folder that goes into the game's mods/unpacked)."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS")) / "mod"
    return Path(__file__).resolve().parents[2] / "mod"

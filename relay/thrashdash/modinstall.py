"""`thrashdash --install-mod`: put the BeamNG mod where the game will find it.

The mod is a folder that goes into the game's user folder as mods/unpacked/thrashdash. The packaged app carries a copy and
this does the copying, so installing is one command and does not need the source tree.

It never replaces a link (someone developing ThrashDash links the folder to the source tree: leave that alone) and it only
removes an earlier *copy* of the mod, never a folder it does not recognise.
"""
import os
import shutil
import sys
from pathlib import Path

from .paths import mod_root

MOD_NAME = "thrashdash"
MARKER = Path("lua") / "vehicle" / "extensions" / "auto" / "thrashdash_telemetry.lua"  # present in the mod, so we know a folder is ours
# The mod's name before this program was renamed. A copy under that name would load next to the new one and send every
# packet twice, so an old copy is replaced; a link is only mentioned (it is not ours to remove).
LEGACY_NAME = "beamdash"
LEGACY_MARKER = Path("lua") / "vehicle" / "extensions" / "auto" / "beamdash_telemetry.lua"


def candidate_user_folders(platform=None, home=None, env=None):
    """Where BeamNG.drive keeps its user folder, most likely first (each is the folder that contains `mods`)."""
    platform = platform or sys.platform
    home = Path(home) if home else Path.home()
    env = os.environ if env is None else env
    if platform == "win32":
        base = Path(env.get("LOCALAPPDATA") or home / "AppData" / "Local")
        # (unverified: where BeamNG puts its user folder on Windows; the older "Documents" location is tried last)
        return [base / "BeamNG" / "BeamNG.drive" / "current", base / "BeamNG.drive" / "current", home / "Documents" / "BeamNG.drive"]
    data = Path(env.get("XDG_DATA_HOME") or home / ".local" / "share")
    return [data / "BeamNG" / "BeamNG.drive" / "current", data / "BeamNG.drive" / "current"]


def _unpacked_folder(given):
    """The mods/unpacked folder for whatever the user pointed at: the user folder, its `current`, `mods` or `unpacked`."""
    path = Path(given).expanduser()
    if (path / "current").is_dir():
        path = path / "current"
    if path.name == "unpacked":
        return path
    if path.name == "mods":
        return path / "unpacked"
    return path / "mods" / "unpacked"


def retire_legacy_copy(unpacked, say=print):
    """Deal with the mod installed under its previous name, so the game does not load two."""
    old = Path(unpacked) / LEGACY_NAME
    if old.is_symlink():
        say(f"Note: {old} is a link to {old.resolve()}, left as it is. If that folder holds an older version of the mod the game "
            "will load it as well, and every packet will be sent twice: remove the link if so.")
    elif old.is_dir() and (old / LEGACY_MARKER).is_file() and not (old / MARKER).is_file():
        try:
            shutil.rmtree(old)
            say(f"Removed the earlier copy of the mod at {old}, which this one replaces.")
        except OSError as e:
            say(f"Could not remove the earlier copy of the mod at {old} ({e.strerror or e}); remove it yourself, or every packet will be sent twice.")


def install_mod(target=None, source=None, say=print, platform=None, home=None, env=None):
    """Install the mod. Returns a process exit code: 0 done (or already installed), 1 could not."""
    source = Path(source) if source else mod_root()
    if not (source / MARKER).is_file():
        say(f"The mod's files were not found at {source}.")
        return 1
    if target:
        if not Path(target).expanduser().is_dir():
            say(f"{target} is not a folder.")
            return 1
        unpacked = _unpacked_folder(target)
    else:
        tried = candidate_user_folders(platform, home, env)
        found = next((p for p in tried if p.is_dir()), None)
        if found is None:
            say("Could not find BeamNG.drive's user folder. Looked in:")
            for p in tried:
                say(f"  {p}")
            say("Run the game once, or name the folder: thrashdash --install-mod PATH  (the game launcher's 'Manage User Folder' opens it)")
            return 1
        unpacked = found / "mods" / "unpacked"
    dest = unpacked / MOD_NAME

    if dest.is_symlink() or (dest.exists() and dest.resolve() == source.resolve()):
        say(f"Already installed: {dest} is a link to {dest.resolve()}. Left as it is.")
        retire_legacy_copy(unpacked, say)
        return 0
    if dest.exists():
        if not dest.is_dir() or not (dest / MARKER).is_file():
            say(f"{dest} exists but does not look like ThrashDash's mod, so it was not touched. Move it away and try again.")
            return 1
        shutil.rmtree(dest)  # an earlier copy of the mod: replace it
    try:
        unpacked.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, dest, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    except OSError as e:
        say(f"Could not install the mod into {dest}: {e.strerror or e}")
        return 1
    retire_legacy_copy(unpacked, say)  # only now that the new one is in place: a failed install must not cost the old one
    say(f"Installed the mod into {dest}")
    say("Remove your car and spawn it again to load it (Ctrl+L in the game is not enough). Look for 'telemetry ready' in beamng.log.")
    return 0

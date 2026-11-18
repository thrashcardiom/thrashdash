"""Starting BeamNG.Earth along with ThrashDash, and quitting when it closes.

BeamNG.Earth is a separate program (an AppImage on Linux) that starts BeamNG.drive itself; the trip log needs it running. A
person who always starts the two together can tell ThrashDash where Earth is kept (a folder or a file) and ThrashDash starts it,
so one thing is started instead of two.

Earth's file changes every time it is updated (the name carries its version), so the setting is normally a FOLDER, and the newest
version in it is the one started: pick_earth reads the version out of each file's name and takes the highest, never the most
recently downloaded, so a newer build is not displaced by an older one fetched later to try.

ThrashDash never closes Earth (it may be holding the game). It does follow it: once Earth has been seen answering on its local
service (127.0.0.1:47615), a service that has been silent for GONE_AFTER_S seconds means Earth has closed, and ThrashDash quits too,
the same way the Quit button does. Watching the service rather than the process is deliberate: on Windows an installer-style launcher
may start Earth and exit at once, and a process check would then call a running Earth "closed".

Nothing here touches Earth's files. A file is only started if it is already marked executable (on Linux): the person chose the
folder, but the program does not make a download executable on their behalf.
"""
import asyncio
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from .launcher import child_env

EARTH_PORT = 47615  # Earth's read-only local service (the same one the game's API talks to)
POLL_S = 3.0  # how often it is asked whether Earth is still there
GONE_AFTER_S = 15.0  # Earth, once seen, silent this long has closed
NEVER_UP_S = 120.0  # an Earth that has not answered by now (and has no running process) did not start

_NAME_RE = re.compile(r"^BeamNG\.Earth-(?P<ver>\d+(?:\.\d+)*)(?:-(?P<pre>[A-Za-z]+(?:\.[A-Za-z]+)*)\.(?P<build>\d+))?(?:-.*)?$")
_PARTIAL_SUFFIXES = (".part", ".crdownload", ".download", ".tmp", ".partial", ".aria2")


def version_key(name):
    """What orders Earth's files by version, from a name like `BeamNG.Earth-1.10.1-beta.dev.459-x86_64.AppImage`: the numbers (1, 10, 1),
    then a release before a pre-release of the same numbers, then the build number. None when the name carries no version."""
    m = _NAME_RE.match(name)
    if not m:
        return None
    numbers = tuple(int(n) for n in m.group("ver").split("."))
    return (numbers + (0,) * (6 - len(numbers)), 0 if m.group("pre") else 1, int(m.group("build") or 0))


def _wanted_suffix(platform):
    return ".exe" if platform == "win32" else ".appimage"


def candidates(folder, platform=None):
    """The files in `folder` that look like an Earth program for this system, as Paths (unordered). Partial downloads and everything that is
    not the program (its update notes, checksums) are left out."""
    platform = platform or sys.platform
    suffix = _wanted_suffix(platform)
    out = []
    try:
        for entry in Path(folder).iterdir():
            name = entry.name
            if name.startswith("BeamNG.Earth") and name.lower().endswith(suffix) and not name.lower().endswith(_PARTIAL_SUFFIXES) and entry.is_file():
                out.append(entry)
    except OSError:
        pass
    return out


def pick_earth(setting, platform=None):
    """The Earth program to start for a setting that names a folder or a file: (path, message). `path` is None when there is nothing to start;
    `message` always says what was found or why not, in a sentence for the person who set it."""
    platform = platform or sys.platform
    text = (setting or "").strip()
    if not text:
        return None, "No BeamNG.Earth folder is set."
    target = Path(os.path.expanduser(text))
    if target.is_file():
        chosen, how = target, "the file you named"
    elif target.is_dir():
        files = candidates(target, platform)
        if not files:
            return None, f"There is no BeamNG.Earth program in {target}."
        ranked = sorted(files, key=lambda p: (version_key(p.name) or ((0,) * 6, 0, 0), p.name))
        chosen, how = ranked[-1], "the newest version in the folder"
    else:
        return None, f"{target} does not exist."
    if platform != "win32" and not os.access(chosen, os.X_OK):
        return None, f"{chosen.name} ({how}) is not marked as executable; mark it so (chmod +x) and ThrashDash will start it."
    return chosen, f"{chosen.name} ({how})."


def probe(port=EARTH_PORT, timeout=1.0):
    """Whether Earth's local service answers. Never raises."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/v1/info", timeout=timeout) as response:
            return response.status == 200 and isinstance(json.loads(response.read(65536) or b"null"), dict)
    except (OSError, ValueError, urllib.error.URLError):
        return False


def launch(path, log_path=None):
    """Start Earth the way a person would, on its own: its own session (so closing ThrashDash does not take it with it), no input, its
    messages in `log_path` (the latest run only), and the environment ThrashDash was given rather than its own bundle's (an AppImage
    starting another AppImage can otherwise hand it the wrong libraries). Returns the Popen."""
    kwargs = {"stdin": subprocess.DEVNULL, "cwd": str(Path(path).parent), "env": child_env()}
    if log_path:
        Path(log_path).parent.mkdir(parents=True, exist_ok=True)  # (the data folder may not exist yet on a first run)
    out = open(log_path, "wb") if log_path else subprocess.DEVNULL  # (the child keeps its own copy; ours may be closed)
    kwargs["stdout"] = out
    kwargs["stderr"] = subprocess.STDOUT
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    else:
        kwargs["start_new_session"] = True
    try:
        return subprocess.Popen([str(path)], **kwargs)
    finally:
        if log_path:
            out.close()


class EarthWatcher:
    """Decides, from what each look at Earth finds, whether it has closed. A plain state machine (no clocks or processes of its own), so
    it can be tested with numbers."""

    def __init__(self, started_at, gone_after_s=GONE_AFTER_S, never_up_s=NEVER_UP_S):
        self.started_at = started_at
        self.gone_after_s = gone_after_s
        self.never_up_s = never_up_s
        self.seen = False
        self.last_answer = started_at
        self.warned_slow = False

    def update(self, now, answering, process_alive=None):
        """One look. `answering`: Earth's service replied. `process_alive`: the program ThrashDash started is still running (None when
        Earth was already running and ThrashDash did not start it). Returns None to carry on, "gone" (Earth was there and has closed),
        "failed" (it never came up) or "slow" (once: it is taking long to come up)."""
        if answering:
            self.seen = True
            self.last_answer = now
            return None
        if self.seen:
            return "gone" if now - self.last_answer >= self.gone_after_s else None
        waited = now - self.started_at
        if waited >= self.never_up_s and process_alive is not True:
            return "failed"
        if waited >= self.never_up_s and not self.warned_slow:
            self.warned_slow = True
            return "slow"
        return None


async def keep_with_earth(path, stop, say=print, log_path=None, port=EARTH_PORT, poll_s=POLL_S, clock=time.monotonic, sleep=asyncio.sleep,
                          probe_fn=probe, launch_fn=launch, gone_after_s=GONE_AFTER_S, never_up_s=NEVER_UP_S):
    """Start Earth (unless it is already running) and call `stop()` when it closes. Never closes Earth. Returns when ThrashDash is
    quitting, Earth has closed, or Earth could not be started."""
    process = None
    already = await asyncio.to_thread(probe_fn, port)
    if already:
        say("BeamNG.Earth is already running; ThrashDash will quit when it closes.")
    else:
        try:
            process = launch_fn(path, log_path)
        except OSError as e:
            say(f"Could not start BeamNG.Earth ({path}): {e}")
            return
        say(f"Started BeamNG.Earth: {path}")
    watcher = EarthWatcher(clock(), gone_after_s, never_up_s)
    if already:
        watcher.seen = True
    while True:
        await sleep(poll_s)
        answering = await asyncio.to_thread(probe_fn, port)
        alive = None if process is None else process.poll() is None
        verdict = watcher.update(clock(), answering, alive)
        if verdict == "gone":
            say("BeamNG.Earth has closed, so ThrashDash is closing too.")
            stop()
            return
        if verdict == "failed":
            say(f"BeamNG.Earth did not come up ({path}); ThrashDash carries on without it." + (f" Its messages are in {log_path}." if log_path else ""))
            return
        if verdict == "slow":
            say("BeamNG.Earth is taking a long time to start; still waiting for it.")


def describe(setting, platform=None, port=EARTH_PORT):
    """What the Trips page's setting box shows: {configured, chosen (the file's name, or None), message, running}. Reads nothing but the
    folder's names and Earth's service."""
    path, message = pick_earth(setting, platform)
    return {"configured": (setting or "").strip(), "chosen": path.name if path else None, "message": message, "running": probe(port)}

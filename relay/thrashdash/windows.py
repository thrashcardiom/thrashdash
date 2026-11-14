"""Remembering which dashboard windows are open, so ThrashDash can reopen them the next time it starts.

Every dashboard window running on this computer tells the relay its dashboard, size and (where the system reports one)
position: when it opens, whenever it changes, and every few seconds as a heartbeat. A window that is closed says so and
is forgotten; a window that simply disappears (a crash, the relay stopped first) stays remembered, which is what makes
"restore what was open when ThrashDash last ran" work. Phones and tablets never take part: only this computer may write.

Windows are identified by an id the page keeps in its own address (?w=...), so a reload is the same window.
Positions are saved but only used when the user has asked for exact placement: under Wayland an application cannot
position (or even see the position of) its own windows, so restoring them is only possible through X11 compatibility mode.
"""
import json
import re
import time

from .store import _atomic_write_json
from .validate import LAYOUT_ID_RE, ValidationError

WINDOW_ID_RE = re.compile(r"^[a-z0-9]{6,24}$")
KINDS = ("dashboard", "trips")  # what a window shows: a dashboard, or the Trips page (the map window)
MAX_WINDOWS = 12
STALE_AFTER_S = 30 * 24 * 3600  # a window not heard from for a month is not coming back
MIN_SIZE, MAX_SIZE = 200, 10000
MAX_COORD = 30000
DEFAULT_SIZE = (1200, 420)
SAVE_AT_MOST_EVERY_S = 2.0
# After the relay starts, windows left open by the previous run reconnect and report within about a second; anything
# not heard from by then is not open and needs reopening.
REATTACH_GRACE_S = 3.5


def _int(value, path, lo, hi, lenient=False):
    """`value` as a whole number between lo and hi. Not a number at all is always an error; a number outside the range is an
    error too, unless `lenient`, when it becomes None ("not known")."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value or abs(value) == float("inf"):
        raise ValidationError(f"{path}: must be a number")
    value = int(round(value))
    if not lo <= value <= hi:
        if lenient:
            return None
        raise ValidationError(f"{path}: must be between {lo} and {hi}")
    return value


def clean_window(raw):
    """A window's report, validated and reduced to what is kept. Raises ValidationError for anything odd."""
    if not isinstance(raw, dict):
        raise ValidationError("window: must be an object")
    dashboard = raw.get("dashboard")
    if dashboard is not None and (not isinstance(dashboard, str) or not LAYOUT_ID_RE.match(dashboard)):
        raise ValidationError("dashboard: not a valid dashboard id")
    kind = raw.get("kind", "dashboard")
    if kind not in KINDS:
        raise ValidationError(f"kind: must be one of {', '.join(KINDS)}")
    entry = {"kind": kind, "dashboard": dashboard, "pinned": raw.get("pinned") is True, "fullscreen": raw.get("fullscreen") is True}
    # A size or position that makes no sense (a minimised window reports 0 by 0, a headless browser the same) is "not known",
    # not an error: the report must still be accepted, or that window would never be remembered.
    entry["width"] = _int(raw["width"], "width", MIN_SIZE, MAX_SIZE, lenient=True) if raw.get("width") is not None else None
    entry["height"] = _int(raw["height"], "height", MIN_SIZE, MAX_SIZE, lenient=True) if raw.get("height") is not None else None
    for axis in ("x", "y"):
        entry[axis] = _int(raw[axis], axis, -MAX_COORD, MAX_COORD, lenient=True) if raw.get(axis) is not None else None
    return entry


def window_path(wid, entry):
    """The address (path and query) that reopens this window: the Trips page for the map window, else pinned to its dashboard if
    it was; always carrying its id."""
    if entry.get("kind") == "trips":
        return f"/trips?w={wid}"
    if entry.get("pinned") and entry.get("dashboard"):
        return f"/?d={entry['dashboard']}&w={wid}"
    return f"/?w={wid}"


class WindowRegistry:
    def __init__(self, path=None, wall_clock=time.time, clock=time.monotonic):
        self.path = path
        self._wall = wall_clock
        self._clock = clock
        self.entries = {}  # window id -> cleaned entry (+ "seen": seconds since the epoch)
        self._stamps = {}  # window id -> newest client timestamp (ms) accepted for it
        self._removed = {}  # window id -> client timestamp (ms) of its close
        self._live = set()  # windows heard from since this relay started
        self._dirty = False
        self._saved_at = -SAVE_AT_MOST_EVERY_S
        self._load()

    # ---- keeping ---------------------------------------------------------------------------

    def _load(self):
        if self.path is None:
            return
        try:
            with open(self.path, encoding="utf-8") as f:
                raw = json.load(f)
        except (OSError, ValueError):
            return
        windows = raw.get("windows") if isinstance(raw, dict) else None
        if not isinstance(windows, dict):
            return
        now = self._wall()
        for wid, entry in windows.items():
            if len(self.entries) >= MAX_WINDOWS or not isinstance(wid, str) or not WINDOW_ID_RE.match(wid):
                continue
            try:
                cleaned = clean_window(entry)
                seen = entry.get("seen")
                cleaned["seen"] = float(seen) if isinstance(seen, (int, float)) and not isinstance(seen, bool) else now
            except (ValidationError, AttributeError):
                continue  # one bad entry does not cost the others
            if now - cleaned["seen"] <= STALE_AFTER_S:
                self.entries[wid] = cleaned

    def save(self):
        if self.path is None:
            return
        try:
            _atomic_write_json(self.path, {"v": 1, "windows": self.entries})
            self._dirty = False
            self._saved_at = self._clock()
        except OSError:
            pass  # a full disk must not stop the dashboard

    def _save_soon(self):
        self._dirty = True
        if self._clock() - self._saved_at >= SAVE_AT_MOST_EVERY_S:
            self.save()

    def flush(self):
        """Write anything not yet written (at shutdown)."""
        if self._dirty:
            self.save()

    # ---- reports ---------------------------------------------------------------------------

    def update(self, wid, raw, stamp):
        """A window reports itself. `stamp` is the page's clock in milliseconds, which orders a report against a close."""
        if not isinstance(wid, str) or not WINDOW_ID_RE.match(wid):
            raise ValidationError("window id: 6 to 24 lowercase letters and digits")
        stamp = _int(stamp, "stamp", 0, 4_000_000_000_000)
        entry = clean_window(raw)
        if stamp < self._removed.get(wid, -1):
            return False  # a late report from before the window was closed
        if wid not in self.entries and len(self.entries) >= MAX_WINDOWS:
            raise ValidationError(f"more than {MAX_WINDOWS} windows")
        self._stamps[wid] = max(stamp, self._stamps.get(wid, 0))
        self._live.add(wid)
        entry["seen"] = self._wall()
        before = {k: v for k, v in self.entries.get(wid, {}).items() if k != "seen"}
        self.entries[wid] = entry
        if before != {k: v for k, v in entry.items() if k != "seen"}:
            self._save_soon()
        return True

    def remove(self, wid, stamp):
        """A window was closed. Ignored if the same window has reported since (a reload closes and reopens it)."""
        if not isinstance(wid, str) or not WINDOW_ID_RE.match(wid):
            raise ValidationError("window id: 6 to 24 lowercase letters and digits")
        stamp = _int(stamp, "stamp", 0, 4_000_000_000_000)
        if stamp < self._stamps.get(wid, 0):
            return False
        self._removed[wid] = max(stamp, self._removed.get(wid, 0))
        self._live.discard(wid)
        if self.entries.pop(wid, None) is not None:
            self.save()
        return True

    def live_entries(self):
        """The windows open right now, as {id: entry} without the bookkeeping: what a saved config captures."""
        return {wid: {k: v for k, v in self.entries[wid].items() if k != "seen"} for wid in sorted(self._live) if wid in self.entries}

    def adopt(self, windows):
        """Make `windows` ({id: entry}, from a saved config) the set of windows to open at start-up, in place of whatever the last run
        left behind (windows open right now are kept as they are). Saves the result."""
        now = self._wall()
        adopted = {wid: self.entries[wid] for wid in self._live if wid in self.entries}
        for wid, entry in windows.items():
            if wid in adopted or len(adopted) >= MAX_WINDOWS:
                continue
            try:
                cleaned = clean_window(entry)
            except ValidationError:
                continue
            cleaned["seen"] = now
            adopted[wid] = cleaned
        self.entries = adopted
        self.save()

    @property
    def live(self):
        """Windows heard from since this relay started, i.e. open right now."""
        return set(self._live)


def plan_restore(entries, live, enabled, exact):
    """Which windows to open at startup. Returns [{id, path, size, position, fullscreen}] oldest first.

    Windows already open (`live`: they survived the relay being restarted) are left alone. Nothing is planned when
    restoring is switched off. A position is only included when `exact` is on (otherwise the system chooses it).
    """
    if not enabled:
        return []
    plan = []
    for wid, entry in sorted(entries.items(), key=lambda kv: kv[1].get("seen", 0)):
        if wid in live:
            continue
        x, y = entry.get("x"), entry.get("y")
        plan.append({
            "id": wid,
            "path": window_path(wid, entry),
            "size": (entry.get("width") or DEFAULT_SIZE[0], entry.get("height") or DEFAULT_SIZE[1]),
            "position": (x, y) if exact and x is not None and y is not None else None,
            "fullscreen": entry.get("fullscreen") is True,
        })
    return plan[:MAX_WINDOWS]


async def open_windows(registry, settings, open_window, base_url, profile_in_use, sleep, default_size=DEFAULT_SIZE, gap_s=0.7, configs=None):
    """Open the dashboard window(s) at startup. Returns how many were opened.

    The windows left open by the previous run (the relay was only restarted) reconnect by themselves within a moment, so
    when a browser may still have them open we wait briefly and then open only the ones that did not come back. Otherwise:
    reopen the windows of the config that was last saved or picked (if there is one), else what was open last time (if the setting
    is on), or just the one default window as always.
    `open_window(url, size, position, fullscreen, x11)` starts one window; both it and `sleep` are passed in so this can
    be tested without a browser or a clock.
    """
    if profile_in_use():
        await sleep(REATTACH_GRACE_S)
    prefs = settings.get()
    exact = prefs.get("exactWindowPlacement") is True
    saved = configs.active_windows() if configs is not None else None
    if saved and prefs.get("restoreWindows") is not False:
        registry.adopt(saved)  # the last saved config, not whatever the last run happened to leave open
    plan = plan_restore(registry.entries, registry.live, prefs.get("restoreWindows") is not False, exact)
    opened = 0
    for i, item in enumerate(plan):
        if i:
            await sleep(gap_s)  # one at a time: windows opened in the same instant can start separate browsers
        if open_window(base_url + item["path"], item["size"], item["position"], item["fullscreen"], exact):
            opened += 1
    if not plan and not registry.live:
        if open_window(base_url + "/", default_size, None, False, exact):
            opened += 1
    return opened


async def open_config_windows(registry, windows, exact, open_window, base_url, sleep, gap_s=0.7):
    """Open the windows of a config ({id: entry}) that are not open already (picking a config while ThrashDash runs). Windows that are
    open stay as they are, and so do any not in the config: ThrashDash cannot close another program's window. Returns how many
    were opened. `open_window(url, size, position, fullscreen, x11)` starts one."""
    plan = plan_restore(windows, registry.live, True, exact)
    opened = 0
    for i, item in enumerate(plan):
        if i:
            await sleep(gap_s)
        if open_window(base_url + item["path"], item["size"], item["position"], item["fullscreen"], exact):
            opened += 1
    return opened

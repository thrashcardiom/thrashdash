"""Popups about trips: a message in the game and a banner on the dashboard windows when a trip ends (or starts).

The trip log (triplog.py) says when a trip starts and ends; this module turns that into text in the user's units and delivers
it two ways:

* To the GAME, as a UDP datagram sent back to wherever the game's trip-log extension (trips.lua) sends its positions from. That
  extension binds its socket to 127.0.0.1 so the reply can only come from, and go to, this computer. The extension shows it as a
  toast, the way BeamNG.Earth shows its own messages (the Messages app that BeamNG's normal notice uses is not in BeamNG.Earth's
  screen layout; found when testing in the real game).
* To the DASHBOARD WINDOWS, as a "notice" message on their WebSocket, which they show as a banner for a few seconds.

What is shown where is a setting (settings.tripNotices): {game: popup in the game when a trip ends, gameStart: also when one
starts, window: banner on the dashboard windows when one ends}.
"""
import json
import time
from collections import Counter, deque

GAME_FRESH_S = 120.0  # the game's address is only used while it has sent a position this recently (it may have been closed)
MAX_TEXT = 200
POPUP_SECONDS = 12  # how long a popup stays in the game: the game's own 5 seconds is easy to miss while driving
KEEP = 20


def _distance(metres, unit):
    value = metres / 1609.344 if unit == "mi" else metres / 1000.0
    return f"{value:.2f}" if value < 10 else f"{value:.1f}" if value < 100 else f"{value:.0f}"


def _duration(seconds):
    total = int(round(seconds))
    h, rest = divmod(total, 3600)
    m, s = divmod(rest, 60)
    if h:
        return f"{h} h {m:02d} min"
    if m:
        return f"{m} min {s:02d} s"
    return f"{s} s"


def format_ended(summary, units):
    """(title, message) for a trip that has just ended. `summary` is the trip's stored summary (tripstats), possibly empty."""
    summary = summary or {}
    dist_unit = "mi" if (units or {}).get("distance") == "mi" else "km"
    speed_mph = (units or {}).get("speed") == "mph"
    name = summary.get("name") or "Trip"
    parts = [f"{_distance(summary.get('dist', 0.0), dist_unit)} {dist_unit}", _duration(summary.get("dur", 0.0))]
    top = summary.get("top_ms")
    if isinstance(top, (int, float)) and top > 0:
        parts.append(f"top {round(top * (2.2369363 if speed_mph else 3.6))} {'mph' if speed_mph else 'km/h'}")
    limits = summary.get("limits")
    if isinstance(limits, dict) and limits.get("over_s", 0) > 0:
        parts.append(f"{_duration(limits['over_s'])} over the limit")
    fuel = summary.get("fuel")
    if isinstance(fuel, dict) and isinstance(fuel.get("used"), (int, float)) and fuel["used"] > 0:
        parts.append(f"{fuel['used']:.2f} {fuel.get('unit', '')}".strip())
    return "Trip finished", (f"{name}: " + ", ".join(parts))[:MAX_TEXT]


def format_started(info):
    model = (info or {}).get("vehicle")
    return "Trip started", (f"Recording a new trip in the {model}" if model else "Recording a new trip")[:MAX_TEXT]


class TripNotices:
    def __init__(self, settings, clock=time.monotonic):
        self.settings = settings  # the SettingsStore: read each time, so a change in the Trips page takes effect at once
        self.clock = clock
        self.seq = 0  # the number of the latest notice for the dashboard windows
        self._items = deque(maxlen=KEEP)
        self.transport = None  # the UDP transport that receives the game's packets, used to answer it
        self.game_addr = None
        self.game_seen = None
        self.stats = Counter()  # what has happened to popups since ThrashDash started (shown on the Trips page and in /api/triplog)

    # ---- the game ----------------------------------------------------------------------------------------------------

    def attach(self, transport):
        self.transport = transport

    def seen_game(self, addr):
        """A position packet came from the game's trip-log extension at `addr`: that is where its messages go."""
        self.game_addr = addr
        self.game_seen = self.clock()

    def _to_game(self, title, message, seconds=POPUP_SECONDS):
        if self.transport is None or self.game_addr is None or self.game_seen is None or self.clock() - self.game_seen > GAME_FRESH_S:
            self.stats["game_unreachable"] += 1  # the game has not sent a position lately (no BeamNG.Earth level loaded, or it is closed)
            return False
        try:
            self.transport.sendto(json.dumps({"type": "toast", "title": title, "msg": message, "seconds": seconds}).encode(), self.game_addr)
            self.stats["game_sent"] += 1
            return True
        except (OSError, ValueError, AttributeError):  # (a closed socket, an odd address): a popup is never worth a failure
            self.stats["game_errors"] += 1
            return False

    # ---- the trip log calls this ---------------------------------------------------------------------------------------

    def publish(self, kind, info):
        """A trip started or ended. `kind` is "started" or "ended"; `info` is the summary (ended) or {"vehicle": ...} (started)."""
        try:
            config = self.settings.get()
        except Exception:  # noqa: BLE001 - unreadable settings: stay quiet rather than fail the trip log
            return
        notices = config.get("tripNotices", {})
        if kind == "ended":
            title, message = format_ended(info, config.get("units"))
            if notices.get("game"):
                self._to_game(title, message)
            if notices.get("window"):
                self._to_windows(kind, title, message)
        elif kind == "started":
            title, message = format_started(info)
            if notices.get("gameStart"):
                self._to_game(title, message)

    def _to_windows(self, kind, title, message):
        self.seq += 1
        self.stats["windows"] += 1
        self._items.append({"type": "notice", "id": self.seq, "kind": kind, "title": title, "msg": message})

    def test(self):
        """A popup on demand, whatever the settings say, to see whether each way works. Returns {"game": whether it was sent to
        the game}; the dashboard windows are always told."""
        title, message = "Test popup", "If you can see this, ThrashDash popups work."
        game = self._to_game(title, message)
        self._to_windows("test", title, message)
        return {"game": game}

    # ---- the dashboard windows -----------------------------------------------------------------------------------------

    def since(self, seq):
        """The notices newer than number `seq`, oldest first."""
        return [n for n in self._items if n["id"] > seq]

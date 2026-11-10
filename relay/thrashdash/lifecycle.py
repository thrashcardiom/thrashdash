"""Quitting by itself when the last dashboard window closes.

The packaged app (an AppImage, or an .exe) is started by double-clicking, so there is no terminal to press Ctrl-C in. It
asks for this behaviour (--quit-when-windows-close): ThrashDash runs while at least one dashboard window is open on this
computer and ends a moment after the last one closes. A window that reloads, or one opened a second later, must not end
it, hence the grace period.

An open window is an open WebSocket from this computer: every dashboard page keeps one, and it closes when the window does
or the browser dies. (A heartbeat from the page would not do: browsers slow the timers of hidden and minimised windows to
about one a minute, which would look like a closed window.) Phones and tablets do not count.
"""
import asyncio
import time

QUIT_GRACE_S = 10.0  # every window gone for this long: quit
STARTUP_TIMEOUT_S = 90.0  # no window ever appeared (the browser would not start): quit rather than run unseen


class WindowCounter:
    """Counts the dashboard windows open on this computer, as their WebSockets come and go (so even a page that connects
    and disconnects between two looks is noticed)."""

    def __init__(self):
        self.open = 0
        self.opened_ever = 0

    def connected(self):
        self.open += 1
        self.opened_ever += 1

    def disconnected(self):
        self.open = max(0, self.open - 1)


class QuitWatcher:
    def __init__(self, counter, clock=time.monotonic, grace=QUIT_GRACE_S, startup_timeout=STARTUP_TIMEOUT_S):
        """`counter` is a WindowCounter."""
        self.counter = counter
        self.clock = clock
        self.grace = grace
        self.startup_timeout = startup_timeout
        self.started = clock()
        self.seen_any = False
        self.empty_since = None

    def reason_to_quit(self):
        """None while ThrashDash should keep running, otherwise a sentence saying why it should stop."""
        now = self.clock()
        if self.counter.opened_ever > 0:
            self.seen_any = True
        if self.counter.open > 0:
            self.empty_since = None
            return None
        if self.seen_any:
            if self.empty_since is None:
                self.empty_since = now
            if now - self.empty_since >= self.grace:
                return "all dashboard windows have been closed"
            return None
        if now - self.started >= self.startup_timeout:
            return "no dashboard window opened"
        return None


async def quit_when_windows_close(watcher, stop, say=print, sleep=asyncio.sleep, interval=1.0):
    """Set `stop` when the watcher says ThrashDash should end. Runs until then (or until cancelled)."""
    while not stop.is_set():
        reason = watcher.reason_to_quit()
        if reason:
            say(f"{reason}: quitting.")
            stop.set()
            return
        await sleep(interval)

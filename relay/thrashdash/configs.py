"""Saved configs: named sets of windows, so ThrashDash comes back the way you left it.

A config is the windows that were open when it was saved: each dashboard window (which dashboard, its size and, where the system
reports one, its position) and the map window (the Trips page, with its size and position). One config is the *active* one: the
last saved or last picked, and the one ThrashDash opens at start-up.

Saving works like the dashboard editor's: **Save** overwrites the config that is loaded, **Save as** makes a new one. Both take the
windows open at that moment from the window registry (windows.py), so what you see is what is saved. (What a dashboard window
*shows*, its gauges and map widget, is saved with the dashboard itself in the editor.)

Kept in configs.json in the data folder, written whole through a temporary file (a crash cannot leave half a file).
"""
import json
import re
import secrets
from pathlib import Path

from .store import NotFound, _atomic_write_json
from .validate import ValidationError
from .windows import MAX_WINDOWS, WINDOW_ID_RE, clean_window

MAX_CONFIGS = 30
MAX_NAME = 40
CONFIG_ID_RE = re.compile(r"^c[0-9a-f]{8}$")
SIZE_TOLERANCE = 12  # pixels: a window a few pixels off its saved size (borders, rounding) is the same size


def clean_name(value):
    """A config name as stored: one line, no control characters, at most MAX_NAME. Raises ValidationError if empty or not text."""
    if not isinstance(value, str):
        raise ValidationError("name: must be text")
    name = " ".join("".join(c if c.isprintable() else " " for c in value).split())[:MAX_NAME].strip()
    if not name:
        raise ValidationError("name: must not be empty")
    return name


def clean_windows(raw):
    """A config's windows ({id: entry}), validated; anything odd is an error, so a bad save never half-works."""
    if not isinstance(raw, dict):
        raise ValidationError("windows: must be an object")
    if len(raw) > MAX_WINDOWS:
        raise ValidationError(f"more than {MAX_WINDOWS} windows")
    out = {}
    for wid, entry in raw.items():
        if not isinstance(wid, str) or not WINDOW_ID_RE.match(wid):
            raise ValidationError("window id: 6 to 24 lowercase letters and digits")
        cleaned = clean_window(entry)
        cleaned.pop("seen", None)
        out[wid] = cleaned
    return out


def windows_differ(saved, live):
    """Whether the windows open now ({id: entry}) differ from a saved config's: a window opened or closed, another dashboard, another
    size (a few pixels do not count), another place (only compared when both are known and not 0,0, which a system that does not
    tell a page where its window is reports)."""
    if set(saved) != set(live):
        return True
    for wid, a in saved.items():
        b = live[wid]
        if (a.get("kind"), a.get("dashboard"), bool(a.get("pinned")), bool(a.get("fullscreen"))) != (b.get("kind"), b.get("dashboard"), bool(b.get("pinned")), bool(b.get("fullscreen"))):
            return True
        for key in ("width", "height"):
            if a.get(key) is not None and b.get(key) is not None and abs(a[key] - b[key]) > SIZE_TOLERANCE:
                return True
        known = lambda w: (w.get("x") or 0) != 0 or (w.get("y") or 0) != 0  # noqa: E731
        if known(a) and known(b) and (abs(a["x"] - b["x"]) > SIZE_TOLERANCE or abs(a["y"] - b["y"]) > SIZE_TOLERANCE):
            return True
    return False


class ConfigStore:
    def __init__(self, path):
        self.path = Path(path)

    def _read(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                raw = json.load(f)
        except (OSError, ValueError):
            return {"active": None, "configs": []}  # missing or corrupt: start empty rather than break ThrashDash
        configs, seen = [], set()
        for item in raw.get("configs", []) if isinstance(raw, dict) else []:
            try:
                config_id = item["id"]
                if not CONFIG_ID_RE.match(config_id) or config_id in seen:
                    continue
                configs.append({"id": config_id, "name": clean_name(item["name"]), "windows": clean_windows(item.get("windows", {}))})
                seen.add(config_id)
            except (KeyError, TypeError, ValidationError):
                continue  # one damaged entry must not lose the others
        active = raw.get("active") if isinstance(raw, dict) else None
        return {"active": active if active in seen else None, "configs": configs[:MAX_CONFIGS]}

    def get(self):
        return self._read()

    def active_windows(self):
        """The windows of the active config ({id: entry}), or None when there is none (or it holds no windows)."""
        data = self._read()
        for c in data["configs"]:
            if c["id"] == data["active"]:
                return c["windows"] or None
        return None

    def _write(self, data):
        _atomic_write_json(self.path, data)

    @staticmethod
    def _unique_name(name, configs, ignore=None):
        taken = {c["name"].lower() for c in configs if c["id"] != ignore}
        if name.lower() not in taken:
            return name
        n = 2
        while f"{name} ({n})".lower() in taken:
            n += 1
        return f"{name} ({n})"

    def create(self, name, windows):
        """Save as: a new config holding `windows`, which becomes the active one. Returns its id."""
        data = self._read()
        if len(data["configs"]) >= MAX_CONFIGS:
            raise ValidationError(f"too many saved configs (limit {MAX_CONFIGS}): delete one first")
        windows = clean_windows(windows)
        if not windows:
            raise ValidationError("there are no windows open to save")
        config_id = "c" + secrets.token_hex(4)
        data["configs"].append({"id": config_id, "name": self._unique_name(clean_name(name), data["configs"]), "windows": windows})
        data["active"] = config_id
        self._write(data)
        return config_id

    def save(self, config_id, windows):
        """Save: overwrite a config's windows; it becomes the active one."""
        data = self._read()
        config = next((c for c in data["configs"] if c["id"] == config_id), None)
        if config is None:
            raise NotFound(config_id)
        windows = clean_windows(windows)
        if not windows:
            raise ValidationError("there are no windows open to save")
        config["windows"] = windows
        data["active"] = config_id
        self._write(data)

    def activate(self, config_id):
        """Make a config the one ThrashDash opens with (picking it does this). None means none."""
        data = self._read()
        if config_id is not None and not any(c["id"] == config_id for c in data["configs"]):
            raise NotFound(config_id)
        data["active"] = config_id
        self._write(data)

    def delete(self, config_id):
        data = self._read()
        remaining = [c for c in data["configs"] if c["id"] != config_id]
        if len(remaining) == len(data["configs"]):
            raise NotFound(config_id)
        data["configs"] = remaining
        if data["active"] == config_id:
            data["active"] = None
        self._write(data)

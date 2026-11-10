"""On-disk storage for layouts and settings.

Built-in layouts ship inside the app (web/layouts/*.json) and are read-only: users edit copies, so an
app update can improve the defaults without ever overwriting someone's work. User layouts live in the
per-user data folder, one JSON file each, named by a relay-generated id.

The id is the ONLY thing that ever becomes part of a file path, and it must match a strict pattern
(or be generated here), so there is no way to address files outside the layouts folder.
"""
import json
import os
import re
import secrets
import tempfile
from pathlib import Path

from .validate import LAYOUT_ID_RE, MAX_SKIN_ENTRIES, MAX_VEHICLE_RULES, ValidationError, validate_layout, validate_settings

USER_ID_RE = re.compile(r"^u-[a-f0-9]{12}$")
MAX_USER_LAYOUTS = 100


class NotFound(LookupError):
    pass


class ReadOnly(PermissionError):
    pass


def _atomic_write_json(path, data):
    """Write via a temp file + rename so a crash mid-save cannot leave a half-written layout."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False, allow_nan=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)  # atomic on both POSIX and Windows
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


class LayoutStore:
    def __init__(self, user_dir: Path, builtin_dir: Path, on_skip=None):
        self.user_dir = Path(user_dir)
        self.builtin_dir = Path(builtin_dir)
        self.on_skip = on_skip or (lambda *_: None)  # called with (path, reason) for unreadable files

    # ---- reading ----------------------------------------------------------------------------

    def _load(self, path):
        try:
            with open(path, encoding="utf-8") as f:
                return validate_layout(json.load(f))
        except (OSError, ValueError) as e:  # ValueError covers bad JSON and ValidationError
            self.on_skip(path, str(e))
            return None

    def _builtin_path(self, layout_id):
        return self.builtin_dir / f"{layout_id}.json"

    def _user_path(self, layout_id):
        return self.user_dir / f"{layout_id}.json"

    def list(self):
        """Summaries for every readable layout, in the order the Dashboards menu shows them: the plain Default first, then
        the user's own dashboards by name, then the built-in templates A-Z. (The menu's 1-9 shortcuts go by position,
        so templates must not push the user's own dashboards out of reach.)"""
        default, templates = [], []
        for path in sorted(self.builtin_dir.glob("*.json"), key=lambda p: p.stem):
            if LAYOUT_ID_RE.match(path.stem) and (layout := self._load(path)):
                (default if path.stem == "default" else templates).append(self._summary(path.stem, layout, True, path))
        user = []
        if self.user_dir.is_dir():
            for path in self.user_dir.glob("u-*.json"):
                if USER_ID_RE.match(path.stem) and (layout := self._load(path)):
                    user.append(self._summary(path.stem, layout, False, path))
        user.sort(key=lambda s: s["name"].lower())
        return default + user + templates

    @staticmethod
    def _summary(layout_id, layout, builtin, path):
        return {"id": layout_id, "name": layout["name"], "builtin": builtin, "width": layout["width"], "height": layout["height"], "modified": int(path.stat().st_mtime)}

    def get(self, layout_id):
        """Return (layout, builtin_flag). Raises NotFound."""
        if USER_ID_RE.match(layout_id):
            path, builtin = self._user_path(layout_id), False
        elif LAYOUT_ID_RE.match(layout_id):
            path, builtin = self._builtin_path(layout_id), True
        else:
            raise NotFound(layout_id)
        if not path.is_file():
            raise NotFound(layout_id)
        layout = self._load(path)
        if layout is None:
            raise NotFound(layout_id)
        return layout, builtin

    # ---- writing ----------------------------------------------------------------------------

    def create(self, raw):
        layout = validate_layout(raw)
        existing = len(list(self.user_dir.glob("u-*.json"))) if self.user_dir.is_dir() else 0
        if existing >= MAX_USER_LAYOUTS:
            raise ValidationError(f"too many layouts (limit {MAX_USER_LAYOUTS}); delete some first")
        for _ in range(10):
            layout_id = "u-" + secrets.token_hex(6)
            if not self._user_path(layout_id).exists():
                break
        else:  # pragma: no cover - 48 random bits colliding ten times is not a real case
            raise OSError("could not allocate a layout id")
        _atomic_write_json(self._user_path(layout_id), layout)
        return layout_id

    def save(self, layout_id, raw):
        if not USER_ID_RE.match(layout_id):
            if LAYOUT_ID_RE.match(layout_id) and self._builtin_path(layout_id).is_file():
                raise ReadOnly("built-in layouts are read-only; duplicate it to make changes")
            raise NotFound(layout_id)
        if not self._user_path(layout_id).is_file():
            raise NotFound(layout_id)
        _atomic_write_json(self._user_path(layout_id), validate_layout(raw))

    def delete(self, layout_id):
        if not USER_ID_RE.match(layout_id):
            if LAYOUT_ID_RE.match(layout_id) and self._builtin_path(layout_id).is_file():
                raise ReadOnly("built-in layouts cannot be deleted")
            raise NotFound(layout_id)
        path = self._user_path(layout_id)
        if not path.is_file():
            raise NotFound(layout_id)
        path.unlink()


class SettingsStore:
    """Display and behaviour settings kept by the relay (units, per-dashboard looks, the default dashboard, vehicle rules)."""

    DEFAULTS = {"units": {"speed": "kmh", "temp": "c", "distance": "km", "pressure": "psi", "economy": "auto"}, "defaultLayout": "default", "skins": {}, "vehicleDashboards": {}, "autoSwitch": True, "lan": False, "alerts": {"enabled": True, "off": []},
                # reopen the dashboard windows that were open last time; and place them exactly (X11 mode on Wayland)
                "restoreWindows": True, "exactWindowPlacement": False}

    def __init__(self, path: Path):
        self.path = Path(path)

    @classmethod
    def _fresh(cls):
        d = cls.DEFAULTS
        return {"units": dict(d["units"]), "defaultLayout": d["defaultLayout"], "skins": {}, "vehicleDashboards": {}, "autoSwitch": d["autoSwitch"], "lan": d["lan"], "alerts": {"enabled": True, "off": []},
                "restoreWindows": d["restoreWindows"], "exactWindowPlacement": d["exactWindowPlacement"]}

    def get(self):
        merged = self._fresh()
        try:
            with open(self.path, encoding="utf-8") as f:
                stored = validate_settings(json.load(f))
        except (OSError, ValueError):
            return merged  # missing or corrupt settings fall back to defaults instead of breaking the app
        merged["units"].update(stored.get("units", {}))
        if "defaultLayout" in stored:
            merged["defaultLayout"] = stored["defaultLayout"]
        merged["skins"] = {k: v for k, v in stored.get("skins", {}).items() if v != "clean"}
        merged["vehicleDashboards"] = {k: v for k, v in stored.get("vehicleDashboards", {}).items() if v}
        if "autoSwitch" in stored:
            merged["autoSwitch"] = stored["autoSwitch"]
        if "lan" in stored:
            merged["lan"] = stored["lan"]
        merged["alerts"].update(stored.get("alerts", {}))
        for flag in ("restoreWindows", "exactWindowPlacement"):
            if flag in stored:
                merged[flag] = stored[flag]
        return merged

    def update(self, raw):
        patch = validate_settings(raw)
        current = self.get()
        current["units"].update(patch.get("units", {}))
        if "defaultLayout" in patch:
            current["defaultLayout"] = patch["defaultLayout"]
        # Per-dashboard skins merge in; choosing "clean" removes the entry (clean is what a dashboard has by default).
        for layout_id, skin in patch.get("skins", {}).items():
            if skin == "clean":
                current["skins"].pop(layout_id, None)
            else:
                current["skins"][layout_id] = skin
        # Vehicle rules merge in too; null removes one.
        for vehicle, layout_id in patch.get("vehicleDashboards", {}).items():
            if layout_id is None:
                current["vehicleDashboards"].pop(vehicle, None)
            else:
                current["vehicleDashboards"][vehicle] = layout_id
        if "autoSwitch" in patch:
            current["autoSwitch"] = patch["autoSwitch"]
        if "lan" in patch:
            current["lan"] = patch["lan"]
        current["alerts"].update(patch.get("alerts", {}))
        for flag in ("restoreWindows", "exactWindowPlacement"):
            if flag in patch:
                current[flag] = patch[flag]
        if len(current["skins"]) > MAX_SKIN_ENTRIES:
            raise ValidationError(f"too many dashboards with a skin (limit {MAX_SKIN_ENTRIES})")
        if len(current["vehicleDashboards"]) > MAX_VEHICLE_RULES:
            raise ValidationError(f"too many vehicle rules (limit {MAX_VEHICLE_RULES})")
        _atomic_write_json(self.path, current)
        return current

    def forget_layout(self, layout_id):
        """A dashboard was deleted: drop everything that referred to it (its look, vehicle rules, being the default)."""
        current = self.get()
        changed = current["skins"].pop(layout_id, None) is not None
        for vehicle in [v for v, target in current["vehicleDashboards"].items() if target == layout_id]:
            del current["vehicleDashboards"][vehicle]
            changed = True
        if current["defaultLayout"] == layout_id:
            current["defaultLayout"] = self.DEFAULTS["defaultLayout"]
            changed = True
        if changed:
            _atomic_write_json(self.path, current)

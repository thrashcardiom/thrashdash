"""Structural validation for layouts and settings that cross the HTTP API.

Two layers guard layout data, deliberately:

* THIS module, on the relay: *structure and size*. It knows nothing about individual widget types, so
  adding a widget never means touching Python. It guarantees that what lands on disk is plain,
  bounded JSON: no giant payloads, no deep nesting, no odd keys, no control characters.
* The browser (web/js/layoutSchema.js): *meaning*. Each widget declares its properties
  (type, range, allowed values), and layouts are sanitised against those specs on load, so a layout
  from a stranger can never put an invalid colour, unit or channel name into the page.

Layouts are data, never code: nothing in them is ever evaluated.
"""
import math
import re

LAYOUT_FORMAT = 1
MAX_WIDGETS = 200
MAX_NAME = 60
MAX_STR = 200
MAX_LIST = 20
MAX_DICT = 10

KEY_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,39}$")
ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")

# Settings the API accepts. Unknown settings are dropped rather than stored.
UNIT_CHOICES = {"speed": {"kmh", "mph"}, "temp": {"c", "f"}, "distance": {"km", "mi"}, "pressure": {"kpa", "psi", "bar"},
                # fuel economy for the trip computer; "auto" follows the distance unit (km -> L/100 km, miles -> mpg)
                "economy": {"auto", "l100", "kmpl", "mpgus", "mpguk"}}
LAYOUT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
SCALE_MODES = {"fit", "max", "fixed"}
MAX_SKIN_ENTRIES = 200
MAX_VEHICLE_RULES = 100
# Alerts the user switched off (their ids are defined in web/js/alerts.js; only the shape is checked here, so adding an
# alert never means touching Python).
MAX_ALERTS_OFF = 40
# A vehicle key is the model folder from the game ("/vehicles/etkc/" -> "etkc").
VEHICLE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,59}$")
# Must match SKIN_CHOICES in web/js/skins/themes.js ("clean" = the original flat widgets). Used by settings.skins.
SKIN_IDS = {"clean", "classicWhite", "classicBlack", "backlit"}


class ValidationError(ValueError):
    """Raised with a human-readable message that names the offending path."""


def _num(value, path, lo=-1e9, hi=1e9):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValidationError(f"{path}: must be a number")
    if not lo <= value <= hi:
        raise ValidationError(f"{path}: must be between {lo} and {hi}")
    return value


def _str(value, path, maxlen=MAX_STR):
    if not isinstance(value, str):
        raise ValidationError(f"{path}: must be text")
    if len(value) > maxlen:
        raise ValidationError(f"{path}: longer than {maxlen} characters")
    if CONTROL_RE.search(value):
        raise ValidationError(f"{path}: contains control characters")
    return value


def _scalar(value, path):
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return _num(value, path)
    return _str(value, path)


def _prop_value(value, path):
    """A widget property: scalar, short list of scalars, or one-level {unit: scalar} map."""
    if isinstance(value, list):
        if len(value) > MAX_LIST:
            raise ValidationError(f"{path}: more than {MAX_LIST} items")
        return [_scalar(v, f"{path}[{i}]") for i, v in enumerate(value)]
    if isinstance(value, dict):
        if len(value) > MAX_DICT:
            raise ValidationError(f"{path}: more than {MAX_DICT} entries")
        out = {}
        for k, v in value.items():
            if not isinstance(k, str) or not KEY_RE.match(k):
                raise ValidationError(f"{path}: bad key {k!r}")
            out[k] = _scalar(v, f"{path}.{k}")
        return out
    return _scalar(value, path)


def validate_layout(raw):
    """Return a cleaned copy of a layout or raise ValidationError. Unknown top-level keys are dropped."""
    if not isinstance(raw, dict):
        raise ValidationError("layout: must be an object")
    fmt = raw.get("format")
    if fmt != LAYOUT_FORMAT:
        raise ValidationError(f"format: this version of ThrashDash understands format {LAYOUT_FORMAT}, got {fmt!r}")
    name = _str(raw.get("name", "Untitled"), "name", MAX_NAME).strip() or "Untitled"
    width = _num(raw.get("width"), "width", 200, 4000)
    height = _num(raw.get("height"), "height", 100, 4000)
    widgets_raw = raw.get("widgets")
    if not isinstance(widgets_raw, list):
        raise ValidationError("widgets: must be a list")
    if len(widgets_raw) > MAX_WIDGETS:
        raise ValidationError(f"widgets: more than {MAX_WIDGETS}")

    widgets = []
    seen_ids = set()
    for i, w in enumerate(widgets_raw):
        path = f"widgets[{i}]"
        if not isinstance(w, dict):
            raise ValidationError(f"{path}: must be an object")
        clean = {}
        for key, value in w.items():
            if not isinstance(key, str) or not KEY_RE.match(key):
                raise ValidationError(f"{path}: bad property name {key!r}")
            clean[key] = _prop_value(value, f"{path}.{key}")
        _str(clean.get("type"), f"{path}.type", 40)
        for geo, lo, hi in (("x", -4000, 8000), ("y", -4000, 8000), ("w", 1, 8000), ("h", 1, 8000)):
            _num(clean.get(geo), f"{path}.{geo}", lo, hi)
        if "id" in clean:
            wid = clean["id"]
            if not isinstance(wid, str) or not ID_RE.match(wid):
                raise ValidationError(f"{path}.id: letters, digits, - and _ only (max 40)")
            if wid in seen_ids:
                raise ValidationError(f"{path}.id: duplicate id {wid!r}")
            seen_ids.add(wid)
        widgets.append(clean)

    out = {"format": LAYOUT_FORMAT, "name": name, "width": width, "height": height, "widgets": widgets}
    # How the canvas scales to the window: optional, so layouts saved before this existed stay valid.
    if "scaleMode" in raw:
        if raw["scaleMode"] not in SCALE_MODES:
            raise ValidationError(f"scaleMode: must be one of {sorted(SCALE_MODES)}")
        out["scaleMode"] = raw["scaleMode"]
    if "scalePercent" in raw:
        out["scalePercent"] = _num(raw["scalePercent"], "scalePercent", 10, 400)
    # (A skin is NOT part of a layout: it is a display setting kept per dashboard in settings.skins. A "skin" key in a
    # layout file, e.g. from an earlier version, is simply ignored like any unknown key.)
    return out


def validate_settings(raw):
    """Return cleaned settings ({units: {...}, defaultLayout: id}) or raise ValidationError."""
    if not isinstance(raw, dict):
        raise ValidationError("settings: must be an object")
    clean = {}
    units = raw.get("units")
    if units is not None:
        if not isinstance(units, dict):
            raise ValidationError("units: must be an object")
        clean_units = {}
        for kind, choice in units.items():
            if kind not in UNIT_CHOICES:
                continue  # forward compatibility: ignore kinds we do not know
            if choice not in UNIT_CHOICES[kind]:
                raise ValidationError(f"units.{kind}: must be one of {sorted(UNIT_CHOICES[kind])}")
            clean_units[kind] = choice
        clean["units"] = clean_units
    skins = raw.get("skins")
    if skins is not None:
        if not isinstance(skins, dict):
            raise ValidationError("skins: must be an object")
        if len(skins) > MAX_SKIN_ENTRIES:
            raise ValidationError(f"skins: more than {MAX_SKIN_ENTRIES} entries")
        clean_skins = {}
        for layout_id, skin in skins.items():
            if not isinstance(layout_id, str) or not LAYOUT_ID_RE.match(layout_id):
                raise ValidationError(f"skins: {layout_id!r} is not a valid dashboard id")
            if skin not in SKIN_IDS:
                raise ValidationError(f"skins.{layout_id}: must be one of {sorted(SKIN_IDS)}")
            clean_skins[layout_id] = skin
        clean["skins"] = clean_skins
    rules = raw.get("vehicleDashboards")
    if rules is not None:
        if not isinstance(rules, dict):
            raise ValidationError("vehicleDashboards: must be an object")
        if len(rules) > MAX_VEHICLE_RULES:
            raise ValidationError(f"vehicleDashboards: more than {MAX_VEHICLE_RULES} entries")
        clean_rules = {}
        for vehicle, layout_id in rules.items():
            if not isinstance(vehicle, str) or not VEHICLE_RE.match(vehicle):
                raise ValidationError(f"vehicleDashboards: {vehicle!r} is not a valid vehicle name")
            # null removes a rule (see SettingsStore.update); otherwise it names a dashboard
            if layout_id is not None and (not isinstance(layout_id, str) or not LAYOUT_ID_RE.match(layout_id)):
                raise ValidationError(f"vehicleDashboards.{vehicle}: not a valid dashboard id")
            clean_rules[vehicle] = layout_id
        clean["vehicleDashboards"] = clean_rules
    if "lan" in raw:
        if not isinstance(raw["lan"], bool):
            raise ValidationError("lan: must be true or false")
        clean["lan"] = raw["lan"]
    for flag in ("restoreWindows", "exactWindowPlacement"):
        if flag in raw:
            if not isinstance(raw[flag], bool):
                raise ValidationError(f"{flag}: must be true or false")
            clean[flag] = raw[flag]
    if "autoSwitch" in raw:
        if not isinstance(raw["autoSwitch"], bool):
            raise ValidationError("autoSwitch: must be true or false")
        clean["autoSwitch"] = raw["autoSwitch"]
    alerts = raw.get("alerts")
    if alerts is not None:
        if not isinstance(alerts, dict):
            raise ValidationError("alerts: must be an object")
        clean_alerts = {}
        if "enabled" in alerts:
            if not isinstance(alerts["enabled"], bool):
                raise ValidationError("alerts.enabled: must be true or false")
            clean_alerts["enabled"] = alerts["enabled"]
        if "off" in alerts:
            off = alerts["off"]
            if not isinstance(off, list) or len(off) > MAX_ALERTS_OFF:
                raise ValidationError(f"alerts.off: must be a list of at most {MAX_ALERTS_OFF} alert ids")
            for alert_id in off:
                if not isinstance(alert_id, str) or not KEY_RE.match(alert_id):
                    raise ValidationError(f"alerts.off: {alert_id!r} is not a valid alert id")
            clean_alerts["off"] = sorted(set(off))
        clean["alerts"] = clean_alerts
    default_layout = raw.get("defaultLayout")
    if default_layout is not None:
        if not isinstance(default_layout, str) or not LAYOUT_ID_RE.match(default_layout):
            raise ValidationError("defaultLayout: not a valid layout id")
        clean["defaultLayout"] = default_layout
    return clean

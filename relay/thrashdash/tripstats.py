"""What a recorded trip adds up to: its name, distance and speeds, the places and countries it went through, how much of it was
over the speed limit, the weather, the fuel used.

A pure function of the trip file's contents (triplog.TripLog.read), so the rules here can change at any time and every trip,
old ones included, can be worked out again. A summary is also stored on the trip's closing line (under "sum") so the trip list
does not have to read every file; STATS_VERSION says which rules produced it, and a stored summary from other rules is
worked out again.

Things the phase-0 recording of a real drive taught us, and what this module does about them:

* Only about 11% of the speed limits BeamNG.Earth reports are POSTED (mapped is true); the rest are estimated from the kind of
  road. "Over the limit" is therefore worked out only where the limit is posted, and the summary says how much of the trip
  that covered (checked_s) so a figure is never presented as the whole story.
* The place name can lag a jump by several seconds, so the trip's start is named from the first place reported once the game
  has settled (START_SETTLE_S), not the first one.
* A street can come back as a road number ("2"). That is not a name; the suburb or town is used instead.
* The fuel fraction can wobble in the first seconds of a trip; fuel used is taken from the volume, counting only decreases, so
  a refill part way through does not give a negative amount.
"""
import math
import re
from collections import Counter

STATS_VERSION = 1

START_SETTLE_S = 12.0  # place names reported before this long into a trip may still describe where the car came from
MOVE_START_MS = 1.0  # at least this fast counts as moving (m/s); the same figure the recorder uses to start a trip
SPEEDING_TOLERANCE_MS = 1.0  # over the limit by more than this (3.6 km/h): the game's speed is within a few percent of true
LIMIT_HOLD_S = 15.0  # a limit read at one moment is taken to hold for the next ten seconds (the interval between readings), no more
INFO_INTERVAL_S = 10.0  # how long the last reading of a trip is taken to cover
ALT_STEP_M = 3.0  # climbing or descending is counted only once the road has risen or fallen this far (the altitude is noisy)
MIN_FUEL_DISTANCE_M = 500.0  # no consumption figure for a trip shorter than this: the tank reading is too coarse
MAX_PLACES = 12
MERGE_GAP_S = 1.0  # speeding stretches closer than this are drawn as one
MAX_SPANS = 300
_ROAD_NUMBER_RE = re.compile(r"^[A-Za-z]{0,3}\s?[0-9]{1,4}[A-Za-z]?$")
_FUEL_UNIT = {"liquid": "L", "electric": "kWh"}


def _num(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def place_label(place):
    """A short name for where the car is: its street, or the town when the street is only a road number. None if nothing."""
    if not isinstance(place, dict):
        return None
    street, town = place.get("street"), place.get("place") or place.get("region")
    if street and not _ROAD_NUMBER_RE.match(street):
        return street
    return town or street or place.get("country") or None


def _trip_name(start, end):
    if start and end:
        return f"Around {start}" if start == end else f"{start} to {end}"
    return start or end


def _place_at_start(info):
    """The first place reported once the game has settled (or the last one, for a trip too short to settle)."""
    named = [i for i in info if isinstance(i.get("place"), dict)]
    for i in named:
        if i.get("t", 0) >= START_SETTLE_S:
            return i["place"]
    return named[-1]["place"] if named else None


def _info_spans(info, duration, key=None):
    """Each info line with the number of seconds it stands for: until the next one (at most LIMIT_HOLD_S), the last one a
    typical interval. With `key`, only lines that have it count, and a line stands until the next such line (the weather
    comes with only some lines)."""
    if key is not None:
        info = [i for i in info if isinstance(i.get(key), dict)]
    spans = []
    for n, i in enumerate(info):
        t = i.get("t")
        if not _num(t):
            continue
        nxt = info[n + 1].get("t") if n + 1 < len(info) else None
        hold = (nxt - t) if _num(nxt) else min(INFO_INTERVAL_S, max(0.0, duration - t))
        spans.append((i, t, max(0.0, min(hold, LIMIT_HOLD_S))))
    return spans


def _merge(spans):
    """Stretches of time that touch or nearly touch joined into one, so a long speeding stretch is one span, not a dozen."""
    out = []
    for lo, hi in sorted(spans):
        if out and lo - out[-1][1] <= MERGE_GAP_S:
            out[-1][1] = max(out[-1][1], hi)
        else:
            out.append([lo, hi])
    return out


def _speeding(points, spans, want_spans=False):
    """Time and distance over the posted limits, and how much of the trip had a posted limit to check against. With
    `want_spans`, also the stretches over the limit ([start, end] seconds into the trip) and where the posted limits were read
    ([start, end, m/s]), for drawing them."""
    checked = over_s = over_m = max_over = 0.0
    over_spans, posted = [], []
    for i, t0, hold in spans:
        lim = i.get("lim")
        if not isinstance(lim, dict) or lim.get("m") is not True or not _num(lim.get("ms")) or hold <= 0:
            continue
        limit, t1 = lim["ms"], t0 + hold
        checked += hold
        if posted and posted[-1][2] == limit and t0 - posted[-1][1] <= MERGE_GAP_S:
            posted[-1][1] = t1
        else:
            posted.append([t0, t1, limit])
        for a, b in zip(points, points[1:]):
            lo, hi = max(a["t"], t0), min(b["t"], t1)
            if hi <= lo:
                continue
            speed = ((a.get("s") or 0.0) + (b.get("s") or 0.0)) / 2
            if speed > limit + SPEEDING_TOLERANCE_MS:
                over_s += hi - lo
                over_m += (hi - lo) * speed
                over_spans.append((lo, hi))
                max_over = max(max_over, max(a.get("s") or 0.0, b.get("s") or 0.0) - limit)
    if checked <= 0:
        return None
    out = {"checked_s": round(checked, 1), "over_s": round(over_s, 1), "over_m": round(over_m, 1), "max_over_ms": round(max_over, 2)}
    if want_spans:
        out["spans"] = [[round(lo, 1), round(hi, 1)] for lo, hi in _merge(over_spans)[:MAX_SPANS]]
        out["posted"] = [[round(lo, 1), round(hi, 1), round(ms, 2)] for lo, hi, ms in posted[:MAX_SPANS]]  # where a posted limit was read, and what it was
    return out


def _weather(spans):
    temps, wind, gust, vis, codes = [], [], [], [], Counter()
    rain_s = snow_s = 0.0
    for i, _, hold in spans:
        wx = i.get("wx")
        if not isinstance(wx, dict):
            continue
        if _num(wx.get("tempC")):
            temps.append(wx["tempC"])
        if _num(wx.get("windMs")):
            wind.append(wx["windMs"])
        if _num(wx.get("gustMs")):
            gust.append(wx["gustMs"])
        if _num(wx.get("visibilityM")):
            vis.append(wx["visibilityM"])
        if _num(wx.get("code")):
            codes[int(wx["code"])] += hold or 1
        if (wx.get("rainMmH") or 0) > 0:
            rain_s += hold
        if (wx.get("snowCmH") or 0) > 0:
            snow_s += hold
    if not (temps or wind or codes):
        return None
    out = {}
    if temps:
        out.update(temp_min=round(min(temps), 1), temp_max=round(max(temps), 1), temp_avg=round(sum(temps) / len(temps), 1))
    if wind:
        out["wind_max_ms"] = round(max(wind), 1)
    if gust:
        out["gust_max_ms"] = round(max(gust), 1)
    if vis:
        out["visibility_min_m"] = round(min(vis))
    if codes:
        out["code"] = codes.most_common(1)[0][0]
    out["rain_s"], out["snow_s"] = round(rain_s), round(snow_s)
    return out


def _altitude(points):
    alts = [p["al"] for p in points if _num(p.get("al"))]
    if not alts:
        return None
    gain = loss = 0.0
    anchor = alts[0]
    for a in alts[1:]:
        if a - anchor >= ALT_STEP_M:
            gain += a - anchor
            anchor = a
        elif anchor - a >= ALT_STEP_M:
            loss += anchor - a
            anchor = a
    return {"min": round(min(alts), 1), "max": round(max(alts), 1), "gain": round(gain), "loss": round(loss)}


def _fuel(points, kind, distance):
    volumes = [p["fv"] for p in points if _num(p.get("fv"))]
    unit = _FUEL_UNIT.get(kind)
    if len(volumes) < 2 or unit is None:
        return None
    used = sum(max(0.0, a - b) for a, b in zip(volumes, volumes[1:]))
    out = {"unit": unit, "used": round(used, 3)}
    if distance >= MIN_FUEL_DISTANCE_M and used > 0:
        out["per_100km"] = round(used / (distance / 100000.0), 2)
    return out


def _damage(points):
    values = [p["dm"] for p in points if _num(p.get("dm"))]
    if not values:
        return None
    return {"start": round(values[0], 2), "end": round(values[-1], 2), "max": round(max(values), 2)}


def summarise(data, end=None, spans=False):
    """The summary of a trip. `data` is what TripLog.read returns ({"header","points","info","events","end"}); `end` is the
    closing line when it is not in `data` yet (a trip being closed). Returns a dict that is safe to put in JSON, or None when
    there is nothing to summarise. With `spans`, the speeding stretches are included under limits["spans"] (they are for the
    trip page, not for the summary stored with the trip).""" 
    points = [p for p in data.get("points", []) if _num(p.get("t")) and _num(p.get("la")) and _num(p.get("lo"))]
    if not points:
        return None
    header = data.get("header") or {}
    end = end or data.get("end") or {}
    info = [i for i in data.get("info", []) if isinstance(i, dict)]
    duration = end.get("t") if _num(end.get("t")) else points[-1]["t"]
    distance = end.get("dist") if _num(end.get("dist")) else (points[-1].get("d") or 0.0)
    top = end.get("top") if _num(end.get("top")) and end.get("top") > 0 else max((p.get("s") or 0.0) for p in points)

    moving = 0.0
    for a, b in zip(points, points[1:]):
        if max(a.get("s") or 0.0, b.get("s") or 0.0) >= MOVE_START_MS:
            moving += b["t"] - a["t"]
    moving = min(moving, duration) if duration else moving

    start_place, end_place = _place_at_start(info), None
    named = [i["place"] for i in info if isinstance(i.get("place"), dict)]
    if named:
        end_place = named[-1]
    start, finish = place_label(start_place), place_label(end_place)

    countries, towns = [], []
    for place in named:
        if place.get("country") and place["country"] not in countries:
            countries.append(place["country"])
        town = place.get("place") or place.get("region")
        if town and town not in towns:
            towns.append(town)

    info_spans = _info_spans(info, duration)
    lats, lons = [p["la"] for p in points], [p["lo"] for p in points]
    events = Counter(e.get("name") for e in data.get("events", []) if isinstance(e, dict))

    out = {"v": STATS_VERSION, "name": _trip_name(start, finish), "from": start, "to": finish,
           "countries": countries[:MAX_PLACES], "places": towns[:MAX_PLACES],
           "dist": round(distance, 1), "dur": round(duration, 1), "moving_s": round(moving, 1), "stopped_s": round(max(0.0, duration - moving), 1),
           "avg_ms": round(distance / duration, 2) if duration > 0 else 0.0,
           "avg_moving_ms": round(distance / moving, 2) if moving > 0 else 0.0,
           "top_ms": round(top, 2), "bbox": [round(min(lats), 6), round(min(lons), 6), round(max(lats), 6), round(max(lons), 6)]}
    for key, value in (("alt", _altitude(points)), ("fuel", _fuel(points, header.get("energy"), distance)),
                       ("damage", _damage(points)), ("limits", _speeding(points, info_spans, spans)), ("wx", _weather(_info_spans(info, duration, "wx")))):
        if value is not None:
            out[key] = value
    if events.get("reset"):
        out["resets"] = events["reset"]
    if events.get("routeArrived"):
        out["arrivals"] = events["routeArrived"]
    return out


def route_line(points, max_points=300, tolerance_m=8.0):
    """The route thinned to its shape, as [[lat, lon], ...] with at most `max_points` entries, for drawing many trips on one map.
    Ramer-Douglas-Peucker: a point is dropped when the line without it stays within `tolerance_m` of where it was; if that
    still leaves too many, the tolerance doubles until it fits. The first and last points are always kept."""
    pts = [(p["la"], p["lo"]) for p in points if _num(p.get("la")) and _num(p.get("lo"))]
    if len(pts) <= 2:
        return [[round(a, 5), round(b, 5)] for a, b in pts]
    k_lat = 111195.0  # metres per degree of latitude
    k_lon = k_lat * math.cos(math.radians(sum(a for a, _ in pts) / len(pts)))  # (flat map maths: exact enough over one trip)
    xy = [(b * k_lon, a * k_lat) for a, b in pts]
    tol = tolerance_m
    while True:
        keep = [False] * len(xy)
        keep[0] = keep[-1] = True
        stack = [(0, len(xy) - 1)]
        while stack:
            lo, hi = stack.pop()
            (x1, y1), (x2, y2) = xy[lo], xy[hi]
            dx, dy = x2 - x1, y2 - y1
            length = math.hypot(dx, dy)
            far, far_i = -1.0, -1
            for i in range(lo + 1, hi):
                x, y = xy[i]
                d = math.hypot(x - x1, y - y1) if length == 0 else abs(dy * (x - x1) - dx * (y - y1)) / length
                if d > far:
                    far, far_i = d, i
            if far > tol:
                keep[far_i] = True
                stack.append((lo, far_i))
                stack.append((far_i, hi))
        if sum(keep) <= max_points or tol > 100000:
            return [[round(a, 5), round(b, 5)] for (a, b), k in zip(pts, keep) if k]
        tol *= 2

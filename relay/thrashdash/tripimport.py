"""Bringing a GPX file (a drive recorded by a phone, a GPS or another program) in as trips, so it can be replayed and compared on the same map.

An imported trip is an ordinary trip file (see triplog.py) with `"imported": "gpx"` in its header, a closing line that says it was
imported, and no readings of the world (the file has no place names, speed limits or weather). It is a real drive, not a game drive, so
the Stats view leaves imported trips out of its totals.

A GPX file comes from anywhere, so it is treated as untrusted: it is refused if it declares entities (the way a small file is made to
expand into a huge one), it is capped in size and in points, and every coordinate is checked. Nothing in it is run or fetched.

Each track in the file (`<trk>`) becomes one trip; a file with only a route (`<rte>`) becomes one. Times come from the file; where it
has none, they are estimated from the distance at a steady speed and the trip says so (`"est": true`).
"""
import math
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

from .tripedit import EditError, _write_trip
from .triplog import KEEP_EVERY_S, KEEP_MOVED_M, KEEP_TURN_DEG, MAX_NAME_CHARS, MIN_TRIP_M, clean_name, haversine_m

MAX_BYTES = 20 * 1024 * 1024
MAX_POINTS = 300_000
MAX_TRIPS = 100
EST_SPEED_MS = 13.9  # 50 km/h: the pace times are made up at when the file has none
MAX_PLAUSIBLE_MS = 120.0  # a step that implies faster than this (432 km/h) is a GPS glitch and is dropped
MIN_TIMED = 0.9  # a track needs times on this share of its points to use them
_DANGEROUS = re.compile(rb"<!\s*(DOCTYPE|ENTITY)", re.I)


def _local(tag):
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _time(text):
    """An ISO 8601 time as seconds since 1970, or None. A time with no zone is taken as UTC."""
    if not text:
        return None
    t = text.strip()
    if t.endswith(("Z", "z")):
        t = t[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(t)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _float(text):
    try:
        v = float(text)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _read_points(parent, point_tag):
    """The points under a track or route, in file order: [{la, lo, al, ts (seconds or None), sp (m/s or None)}]. Invalid ones are left out."""
    out = []
    for el in parent.iter():
        if _local(el.tag) != point_tag:
            continue
        la, lo = _float(el.get("lat")), _float(el.get("lon"))
        if la is None or lo is None or not (-90.0 <= la <= 90.0 and -180.0 <= lo <= 180.0):
            continue
        p = {"la": la, "lo": lo, "al": None, "ts": None, "sp": None}
        for child in el.iter():
            name = _local(child.tag)
            if name == "ele":
                p["al"] = _float(child.text)
            elif name == "time":
                p["ts"] = _time(child.text)
            elif name == "speed":  # (Garmin's extension, in metres per second)
                p["sp"] = _float(child.text)
        out.append(p)
        if len(out) > MAX_POINTS:
            raise EditError(f"that file has more than {MAX_POINTS} points")
    return out


def parse(data):
    """The tracks in a GPX file: [{"name": str|None, "points": [...]}]. Raises EditError for something that is not a usable GPX file."""
    if len(data) > MAX_BYTES:
        raise EditError("that file is too big to be a GPX track")
    if _DANGEROUS.search(data):
        raise EditError("that file declares entities, which a GPX track does not need; it was not read")
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        raise EditError("that is not a valid GPX file") from None
    if _local(root.tag) != "gpx":
        raise EditError("that is not a GPX file")
    tracks = []
    for trk in (el for el in root if _local(el.tag) == "trk"):
        name = next((c.text for c in trk if _local(c.tag) == "name" and c.text), None)
        tracks.append({"name": name, "points": _read_points(trk, "trkpt")})
    if not tracks:
        for rte in (el for el in root if _local(el.tag) == "rte"):
            name = next((c.text for c in rte if _local(c.tag) == "name" and c.text), None)
            tracks.append({"name": name, "points": _read_points(rte, "rtept")})
    if not tracks:
        raise EditError("that GPX file has no track in it")
    return tracks[:MAX_TRIPS]


def _bearing(a, b):
    p1, p2, dl = math.radians(a["la"]), math.radians(b["la"]), math.radians(b["lo"] - a["lo"])
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


def _turn(a, b):
    return abs((a - b + 180.0) % 360.0 - 180.0)


def _build(points):
    """A track's points as a trip's: (body lines, end line, header extras) or None when it is too short to be a trip. Distance is summed
    from every point (so thinning loses none), times come from the file or are estimated, and the points kept are those the recorder
    would keep (moved 4 m, or 10 s, or turned 12 degrees)."""
    if len(points) < 2:
        return None
    timed = [p for p in points if p["ts"] is not None]
    use_times = len(timed) >= MIN_TIMED * len(points) and all(b["ts"] >= a["ts"] for a, b in zip(timed, timed[1:]))
    # distance, dropping glitches
    kept, d = [], 0.0
    for p in points:
        if kept:
            step = haversine_m(kept[-1]["la"], kept[-1]["lo"], p["la"], p["lo"])
            dt = (p["ts"] - kept[-1]["ts"]) if use_times and p["ts"] is not None and kept[-1]["ts"] is not None else None
            if dt is not None and dt > 0 and step / dt > MAX_PLAUSIBLE_MS:
                continue
            d += step
        kept.append({**p, "d": d})
    if len(kept) < 2 or d < MIN_TRIP_M:
        return None
    if use_times:
        t0 = next(p["ts"] for p in kept if p["ts"] is not None)
        last = t0
        for p in kept:
            if p["ts"] is None:
                p["ts"] = last  # (a point with no time sits at the previous one's)
            last = p["ts"]
            p["t"] = round(p["ts"] - t0, 2)
        start_ts = int(t0)
    else:
        for p in kept:
            p["t"] = round(p["d"] / EST_SPEED_MS, 2)
        start_ts = int(time.time() - kept[-1]["t"])
    for i, p in enumerate(kept):
        p["h"] = round(_bearing(kept[i], kept[i + 1]) if i + 1 < len(kept) else _bearing(kept[i - 1], kept[i]), 1)
        if p["sp"] is not None and p["sp"] >= 0:
            p["s"] = round(p["sp"], 2)
        elif i > 0 and p["t"] > kept[i - 1]["t"]:
            p["s"] = round(min(MAX_PLAUSIBLE_MS, (p["d"] - kept[i - 1]["d"]) / (p["t"] - kept[i - 1]["t"])), 2)
        else:
            p["s"] = round(EST_SPEED_MS if not use_times else 0.0, 2)
    # thin the way the recorder does
    body, last_kept = [], None
    for i, p in enumerate(kept):
        keep = last_kept is None or i == len(kept) - 1
        if not keep:
            moved = haversine_m(last_kept["la"], last_kept["lo"], p["la"], p["lo"])
            keep = moved >= KEEP_MOVED_M or p["t"] - last_kept["t"] >= KEEP_EVERY_S or _turn(p["h"], last_kept["h"]) >= KEEP_TURN_DEG
        if keep:
            line = {"k": "p", "t": p["t"], "la": round(p["la"], 7), "lo": round(p["lo"], 7)}
            if p["al"] is not None:
                line["al"] = round(p["al"], 1)
            line.update({"s": p["s"], "h": p["h"], "d": round(p["d"], 1)})
            body.append(line)
            last_kept = p
    top = max(p["s"] for p in kept)
    end = {"k": "end", "reason": "imported", "t": kept[-1]["t"], "ts": start_ts + int(kept[-1]["t"]), "dist": round(d, 1), "top": round(top, 2), "n": len(body)}
    return body, end, {"ts": start_ts, "est": not use_times}


def import_gpx(log, data):
    """Bring in the tracks of a GPX file as trips. Returns {"ids": [new trip ids], "skipped": tracks too short to be a trip,
    "duplicates": tracks already imported (same start and length)}. Raises EditError."""
    tracks = parse(data)
    existing = {(t["ts"], round(t.get("distance") or 0)) for t in log.list() if t.get("imported")}
    ids, skipped, duplicates = [], 0, 0
    for track in tracks:
        built = _build(track["points"])
        if built is None:
            skipped += 1
            continue
        body, end, extra = built
        if (extra["ts"], round(end["dist"])) in existing:
            duplicates += 1
            continue
        header = {"k": "trip", "fmt": 1, "id": log._new_id(extra["ts"]), "ts": extra["ts"], "vehicle": None, "veh": None, "energy": None, "imported": "gpx"}
        if extra["est"]:
            header["est"] = True
        title = clean_name(track["name"])[:MAX_NAME_CHARS] if track["name"] else ""
        ids.append(_write_trip(log, header, body, end, title or None))
        existing.add((extra["ts"], round(end["dist"])))
    if not ids and not duplicates:
        raise EditError("none of the tracks in that file is long enough to be a trip (a trip is at least 50 m)")
    return {"ids": ids, "skipped": skipped, "duplicates": duplicates}

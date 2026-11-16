"""Trip log: where the car has been, kept as one file per trip.

This is the relay's side of the BeamNG.Earth trip log. The game extension (mod/lua/ge/extensions/thrashdash/trips.lua) sends
a position sample about once a second, a slower packet with the place name, speed limit and weather, and a few events. The
relay (not the game) decides where one trip ends and the next begins, so the rules can change without restarting the game.

What the phase-0 recording of a real drive taught us, and how it shapes the rules here:

* While the world is not ready (loading after a teleport or a reset to the spawn point) the game reports a speed of about
  99,900 m/s. Samples that are not ready are dropped.
* A reset back to the spawn point jumped 1.4 km WITHOUT a teleport event, so a trip is also split on a jump in position
  (implied speed far beyond any car, or any distance after a loading gap), not only on the events.
* After a teleport the game's x/y change (a new origin), but latitude and longitude stay true. Only lat/lon is stored.
* The R key's reset fires an event too, with no jump. It never splits a trip.
* The place name can lag a jump by several seconds, so names are kept as "info" lines through the trip, and the first and
  last are read once things have settled (that is later work: this module only records them).

File format, one JSON object per line (JSON Lines), so a crash loses at most the last line and a trip can be appended to
while it is driven:

    {"k":"trip","fmt":1,"id":"20261114T081500Z","ts":1794600900,"vehicle":"etkc","veh":56965,"energy":"liquid"}
    {"k":"p","t":0.0,"la":-41.2258,"lo":174.8803,"al":2.5,"s":0.0,"h":101.8,"d":0.0,"f":0.83,"fv":41.5,"dm":0.0}
    {"k":"i","t":10.0,"place":{...},"lim":{"ms":13.89,"m":true,"u":"kmh"},"wx":{...},"gps":{...}}
    {"k":"e","t":30.2,"name":"routeArrived","la":..,"lo":..}
    {"k":"end","reason":"idle","t":601.0,"ts":1794601501,"dist":8123.4,"top":31.2,"n":412,"sum":{...}}

t is seconds since the trip began, d is the metres driven so far (summed from EVERY sample, so thinning the points that are
written loses no distance), s is m/s, f the fuel or charge fraction, fv litres or kWh, dm the body damage share. "sum" is the
trip's summary (tripstats.py), worked out once when the trip ends; it can always be worked out again from the lines above it.

A name the user gives a trip is one more line appended after the end line (the last one wins; an empty name clears it):

    {"k":"name","name":"Sunday run to the coast"}

and marking a trip as a favourite is another (the last one wins; false takes the mark away):

    {"k":"star","v":true}

so renaming and starring never rewrite the file. A deleted trip is moved to the "trash" folder inside the trip log folder, not erased.
"""
import json
import math
import re
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from . import tripstats
from .trip import energy_kind

# ---- the rules (all in one place so they are easy to tune) -----------------------------------------------------------
JUMP_SPEED_MS = 100.0  # an implied speed above this (360 km/h) between two samples is a jump, not driving
JUMP_MIN_M = 30.0  # ...but only if the points are also this far apart (tiny steps over a tiny time are just noise)
GAP_JUMP_M = 150.0  # after a loading gap (not-ready samples, or a stall longer than MAX_STEP_GAP_S) any move this far is a jump
MAX_STEP_GAP_S = 3.0  # a longer gap between samples adds no distance (a stall or a loading screen, not driving)
MOVE_START_MS = 1.0  # a trip begins when the car is going at least this fast (m/s)
IDLE_END_S = 120.0  # a trip ends after the car has been parked this long (the default: the user chooses on the Trips page, see set_idle_end)
LOST_AFTER_S = 90.0  # a trip ends when no sample has arrived for this long (the game closed, or the free camera)
MIN_TRIP_M = 50.0  # a trip shorter than this, or...
MIN_TRIP_S = 20.0  # ...briefer than this, is a nudge of the car, not a trip, and is not kept
KEEP_MOVED_M = 4.0  # a point is written when the car moved this far since the last one written,
KEEP_EVERY_S = 10.0  # or this long passed (so a parked car still leaves a trace),
KEEP_TURN_DEG = 12.0  # or it turned this much (so corners keep their shape)
MAX_PLAUSIBLE_SPEED = 400.0  # m/s; anything faster is a glitch and is not used (the game sends 99,900 while loading)
MAX_EVENTS_PER_TRIP = 500
HEARTBEAT_S = 10.0  # a place/limit/weather reading is written at least this often, changed or not (so a reading holds for at most this long)
MAX_NAME_CHARS = 80
TRASH_DIR = "trash"
EVENTS = ("teleported", "worldReady", "routeArrived", "reset", "vehicleSwitched", "levelStart", "levelEnd")
ENDS_TRIP = {"teleported": "teleport", "vehicleSwitched": "vehicle", "levelEnd": "level"}

TRIP_ID_RE = re.compile(r"^[0-9]{8}T[0-9]{6}Z(-[0-9]{1,3})?$")
_MODEL_RE = re.compile(r"^[A-Za-z0-9_.\- ]{1,64}$")
_TAIL_BYTES = 8192
_MAX_READ_BYTES = 64 * 1024 * 1024  # a trip file bigger than this is not read back into memory

PLACE_KEYS = ("street", "place", "region", "country")
WEATHER_KEYS = ("code", "tempC", "windMs", "gustMs", "rainMmH", "snowCmH", "visibilityM")
GPS_KEYS = ("remaining", "eta", "off", "routeVersion")


def _num(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def haversine_m(lat1, lon1, lat2, lon2):
    """Metres along the surface between two points (a sphere is exact enough for trip distances)."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * 6371008.8 * math.asin(min(1.0, math.sqrt(a)))


def _turn_deg(a, b):
    """The smallest angle between two headings in degrees."""
    return abs((a - b + 180.0) % 360.0 - 180.0)


class NotFound(KeyError):
    """There is no such trip."""


class Busy(RuntimeError):
    """The trip is being recorded: it cannot be renamed or deleted until it ends."""


def clean_name(value):
    """A trip name as it is stored: one line, no control characters, at most MAX_NAME_CHARS. Raises ValueError if it is not text."""
    if not isinstance(value, str):
        raise ValueError("the name must be text")
    return " ".join("".join(c if c.isprintable() else " " for c in value).split())[:MAX_NAME_CHARS].strip()


def _short(value, limit=80):
    return value[:limit] if isinstance(value, str) else None


class _Sample:
    """One validated position sample. Built by parse_sample; never from a raw packet directly."""

    __slots__ = ("rt", "ts", "lat", "lon", "alt", "spd", "hdg", "ready", "veh", "model")


def parse_sample(packet):
    """Validate a "geo" packet. Returns a _Sample, or None when it is malformed (never raises)."""
    rt, lat, lon = packet.get("rt"), packet.get("lat"), packet.get("lon")
    if not (_num(rt) and _num(lat) and _num(lon)) or not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        return None
    s = _Sample()
    s.rt, s.lat, s.lon = float(rt), float(lat), float(lon)
    ts = packet.get("ts")
    s.ts = int(ts) if _num(ts) and 0 < ts < 4e9 else int(time.time())
    s.ready = packet.get("ready") is True
    s.alt = float(packet["alt"]) if _num(packet.get("alt")) and abs(packet["alt"]) < 100000 else None
    spd = packet.get("spd")
    s.spd = float(spd) if _num(spd) and 0 <= spd <= MAX_PLAUSIBLE_SPEED else None
    hdg = packet.get("hdg")
    s.hdg = float(hdg) % 360.0 if _num(hdg) else None
    veh = packet.get("veh")
    s.veh = veh if isinstance(veh, int) and not isinstance(veh, bool) else None
    model = packet.get("model")
    s.model = model if isinstance(model, str) and _MODEL_RE.match(model) else None
    return s


class _Trip:
    """The trip being recorded."""

    def __init__(self, tid, path, fh, first, veh):
        self.id, self.path, self.fh, self.veh = tid, path, fh, veh
        self.start_rt, self.start_ts = first.rt, first.ts
        self.last_rt, self.last_ts = first.rt, first.ts
        self.last_move_rt, self.last_move_ts = first.rt, first.ts
        self.dist = 0.0
        self.top = 0.0
        self.points = 0
        self.events = 0
        self.written = None  # (lat, lon, rt, heading) of the last point written
        self.world = {}  # the place and speed limit last written down (the readings come often; only a change is worth a line)
        self.announced = False  # whether "a trip started" has been said (only once it has proved to be a trip, not a nudge)
        self.model = None
        self.info_t = None  # when (seconds into the trip) the last reading was written


class TripLog:
    def __init__(self, directory, clock=time.monotonic, idle_end_s=IDLE_END_S, say=None, on_notice=None):
        self.dir = Path(directory)
        self.clock = clock  # monotonic seconds, to notice that samples stopped arriving
        self.idle_end_s = idle_end_s
        self.say = say or (lambda text: print(text, file=sys.stderr))
        self.on_change = None  # called as on_change("started" | "ended", trip_id) the moment a trip file is made or finished or thrown away (see hub.py)
        self.on_notice = on_notice  # called as on_notice("started" | "ended", info) when a trip really starts or ends (see tripnotice.py)
        self.stats = Counter()
        self._trip = None
        self._prev = None  # the last ready sample: what the next one is compared with
        self._gap = False  # not-ready samples were seen since the last ready one (the game was loading)
        self._seen_at = None  # clock() when the last sample arrived
        self._warned_write = False
        self._route_cache = {}  # trip id -> ((size, mtime), thinned route), for the all-trips map
        self._stats_cache = {}  # trip id -> ((size, mtime), summary), for summaries worked out again for the list
        self._recover()

    # ---- intake ------------------------------------------------------------------------------------------------------

    def ingest(self, packet, car=None):
        """Take one "geo", "geoinfo" or "geoevent" packet. `car` is what the dashboard knows of the car right now (the hub's
        channels), or None/empty when the car is not sending. Never raises on bad input."""
        kind = packet.get("type")
        if kind == "geo":
            self._on_sample(packet, car or {})
        elif kind == "geoinfo":
            self._on_info(packet)
        elif kind == "geoevent":
            self._on_event(packet)
        else:
            self.stats["rejected_type"] += 1

    def set_idle_end(self, seconds):
        """How long the car may be parked before the trip ends. Takes effect at once, for the trip being driven too."""
        self.idle_end_s = float(seconds)

    def tick(self):
        """Called every few seconds: ends the trip when samples stopped arriving (the game was closed)."""
        if self._trip is not None and self._seen_at is not None and self.clock() - self._seen_at > LOST_AFTER_S:
            self._close("lost")

    def close_all(self, reason="shutdown"):
        self._close(reason)

    # ---- the rules ---------------------------------------------------------------------------------------------------

    def _on_sample(self, packet, car):
        s = parse_sample(packet)
        if s is None:
            self.stats["rejected_sample"] += 1
            return
        self._seen_at = self.clock()
        if not s.ready:
            # Loading (after a teleport, or a reset to the spawn point). Its speed is nonsense and its position is the
            # old one or the new one: neither is evidence of driving.
            self.stats["not_ready"] += 1
            self._gap = True
            return
        prev = self._prev
        step, jumped = 0.0, False
        if prev is not None:
            dt = s.rt - prev.rt
            dist = haversine_m(prev.lat, prev.lon, s.lat, s.lon)
            if dt <= 0:  # the game side restarted its clock: not a continuation
                jumped = True
            else:
                gap = self._gap or dt > MAX_STEP_GAP_S
                if dist >= JUMP_MIN_M and dist / dt > JUMP_SPEED_MS:
                    jumped = True
                elif gap and dist >= GAP_JUMP_M:
                    jumped = True
                elif not gap:
                    step = dist
        self._gap = False
        if jumped:
            self.stats["jumps"] += 1
            self._close("jump")
            prev = None
        self._prev = s
        moving = s.spd is not None and s.spd >= MOVE_START_MS

        trip = self._trip
        if trip is not None and s.veh != trip.veh and None not in (s.veh, trip.veh):
            self._close("vehicle")
            trip = None
        if trip is None:
            if moving:
                self._open(s, prev, car)
            return

        trip.last_rt, trip.last_ts = s.rt, s.ts
        trip.dist += step
        if s.spd is not None:
            trip.top = max(trip.top, s.spd)
        if moving:
            trip.last_move_rt, trip.last_move_ts = s.rt, s.ts
        elif s.rt - trip.last_move_rt >= self.idle_end_s:
            self._close("idle")
            return
        if self._worth_writing(trip, s):
            self._write_point(trip, s, car)
        if not trip.announced and self._trip is trip and trip.dist >= MIN_TRIP_M and trip.last_rt - trip.start_rt >= MIN_TRIP_S:
            trip.announced = True  # (a trip the log would throw away as a nudge is never announced)
            self._notify("started", {"vehicle": trip.model})

    def _worth_writing(self, trip, s):
        w = trip.written
        if w is None:
            return True
        if s.rt - w[2] >= KEEP_EVERY_S:
            return True
        if haversine_m(w[0], w[1], s.lat, s.lon) >= KEEP_MOVED_M:
            return True
        return s.hdg is not None and w[3] is not None and _turn_deg(s.hdg, w[3]) >= KEEP_TURN_DEG

    def _open(self, s, prev, car):
        """Start a trip. The sample before it (the car still parked) becomes its first point, so the trip starts where the
        car stood and not a metre or two on."""
        first = prev if prev is not None and (prev.veh == s.veh or None in (prev.veh, s.veh)) else s
        tid = self._new_id(first.ts)
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            path = self.dir / f"{tid}.jsonl"
            fh = open(path, "a", encoding="utf-8", newline="\n")  # (LF on every system: a trip file shared between computers is the same file)
        except OSError as e:
            self._write_failed(e)
            return
        trip = _Trip(tid, path, fh, first, s.veh)
        trip.model = s.model
        self._trip = trip
        header = {"k": "trip", "fmt": 1, "id": tid, "ts": first.ts, "vehicle": s.model, "veh": s.veh,
                  "energy": energy_kind(car.get("energy.electric"), car.get("energy.liquid"))}
        if not self._write(header):
            return
        self.stats["trips_opened"] += 1
        self._changed("started", tid)
        if first is not s:
            self._write_point(trip, first, car)
        trip.last_rt, trip.last_ts = s.rt, s.ts
        trip.last_move_rt, trip.last_move_ts = s.rt, s.ts
        if s.spd is not None:
            trip.top = s.spd
        trip.dist += haversine_m(first.lat, first.lon, s.lat, s.lon) if first is not s else 0.0
        self._write_point(trip, s, car)

    def _write_point(self, trip, s, car):
        p = {"k": "p", "t": round(s.rt - trip.start_rt, 2), "la": round(s.lat, 7), "lo": round(s.lon, 7)}
        if s.alt is not None:
            p["al"] = round(s.alt, 1)
        if s.spd is not None:
            p["s"] = round(s.spd, 2)
        if s.hdg is not None:
            p["h"] = round(s.hdg, 1)
        p["d"] = round(trip.dist, 1)
        for key, channel in (("f", "fuel"), ("fv", "fuelVolume"), ("dm", "damage.brokenPct")):
            v = car.get(channel)
            if _num(v):
                p[key] = round(v, 4)
        if self._write(p):
            trip.points += 1
            trip.written = (s.lat, s.lon, s.rt, s.hdg)

    def _on_info(self, packet):
        """A reading of the world: the speed limit about every second, the place name every couple of seconds, the weather and
        route every ten. Written down only when the place or limit changed or something slow arrived or HEARTBEAT_S passed, so
        reading often costs the file little. Every line is a full picture (place, limit and whatever else came with it), and a
        reading holds until the next line, which tripstats relies on."""
        trip = self._trip
        if trip is None or not _num(packet.get("rt")):
            return
        t = round(packet["rt"] - trip.start_rt, 2)
        place = packet.get("place")
        if isinstance(place, dict):
            place = {k: _short(place.get(k)) for k in PLACE_KEYS if _short(place.get(k))} or None
        else:
            place = trip.world.get("place")  # (a reading without a place keeps the last one: the game asks for it less often)
        limit = packet.get("limit")
        lim = None
        if isinstance(limit, dict) and _num(limit.get("ms")):
            lim = {"ms": round(limit["ms"], 2)}
            if isinstance(limit.get("mapped"), bool):
                lim["m"] = limit["mapped"]  # true: a posted limit; absent: estimated from the kind of road
            if limit.get("unit") in ("kmh", "mph"):
                lim["u"] = limit["unit"]
        wx = packet.get("wx")
        wx = {k: round(wx[k], 2) for k in WEATHER_KEYS if _num(wx.get(k))} or None if isinstance(wx, dict) else None
        gps = packet.get("gps")
        gps = {k: round(gps[k], 1) for k in GPS_KEYS if _num(gps.get(k))} or None if isinstance(gps, dict) else None
        changed = place != trip.world.get("place") or lim != trip.world.get("lim")
        due = trip.info_t is None or t - trip.info_t >= HEARTBEAT_S
        if not (changed or wx or gps or due):
            return
        line = {"k": "i", "t": t}
        for key, value in (("place", place), ("lim", lim), ("wx", wx), ("gps", gps)):
            if value:
                line[key] = value
        if len(line) > 2 and self._write(line):
            trip.world = {"place": place, "lim": lim}
            trip.info_t = t

    def _on_event(self, packet):
        name = packet.get("name")
        if name not in EVENTS:
            self.stats["rejected_event"] += 1
            return
        if name in ENDS_TRIP:
            self._close(ENDS_TRIP[name])
            self._prev = None
            self._gap = False
            return
        trip = self._trip
        if trip is None or trip.events >= MAX_EVENTS_PER_TRIP or not _num(packet.get("rt")):
            return
        trip.events += 1
        line = {"k": "e", "t": round(packet["rt"] - trip.start_rt, 2), "name": name}
        if _num(packet.get("lat")) and _num(packet.get("lon")):
            line["la"], line["lo"] = round(packet["lat"], 7), round(packet["lon"], 7)
        self._write(line)

    # ---- the file ----------------------------------------------------------------------------------------------------

    def _new_id(self, ts):
        base = datetime.fromtimestamp(ts, timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        tid, n = base, 1
        while (self.dir / f"{tid}.jsonl").exists():
            n += 1
            tid = f"{base}-{n}"
        return tid

    def _changed(self, kind, trip_id):
        """Say at once that a trip appeared or is over, so a page watching can act without waiting for its next look. Never raises."""
        if self.on_change is None:
            return
        try:
            self.on_change(kind, trip_id)
        except Exception as e:  # noqa: BLE001
            self.stats["change_errors"] += 1
            self.say(f"Trip log: a trip change could not be passed on: {e}")

    def _notify(self, kind, info):
        """Tell whoever listens that a trip started or ended. Whatever goes wrong there must never touch the trip log."""
        if self.on_notice is None:
            return
        try:
            self.on_notice(kind, info)
        except Exception as e:  # noqa: BLE001
            self.stats["notice_errors"] += 1
            self.say(f"Trip log: a trip notice failed: {e}")

    def _write(self, obj):
        trip = self._trip
        try:
            trip.fh.write(json.dumps(obj, separators=(",", ":"), allow_nan=False) + "\n")
            trip.fh.flush()
            return True
        except (OSError, ValueError) as e:  # (a full disk, a removed folder): drop this trip, never break the relay
            self._write_failed(e)
            self._abandon()
            return False

    def _write_failed(self, error):
        self.stats["write_errors"] += 1
        if not self._warned_write:
            self._warned_write = True
            self.say(f"Trip log: cannot write to {self.dir}: {getattr(error, 'strerror', None) or error}")

    def _abandon(self):
        trip, self._trip = self._trip, None
        if trip is not None:
            try:
                trip.fh.close()
            except OSError:
                pass

    def _close(self, reason):
        trip = self._trip
        if trip is None:
            return
        if reason == "idle":  # the parked time after the last movement is not part of the trip
            end_rt, end_ts = trip.last_move_rt, trip.last_move_ts
        else:
            end_rt, end_ts = trip.last_rt, trip.last_ts
        duration = end_rt - trip.start_rt
        if trip.dist < MIN_TRIP_M or duration < MIN_TRIP_S:
            self._abandon()
            try:
                trip.path.unlink()
            except OSError:
                pass
            self.stats["trips_discarded"] += 1
            self._changed("ended", trip.id)
            return
        end = {"k": "end", "reason": reason, "t": round(duration, 2), "ts": end_ts, "dist": round(trip.dist, 1),
               "top": round(trip.top, 2), "n": trip.points}
        summary = self._work_out(trip.id, end)
        if summary is not None:
            end["sum"] = summary
        if self._write(end):
            self._abandon()
            self.stats["trips_closed"] += 1
            self._changed("ended", trip.id)
            self._notify("ended", summary)
        # (a failed write has already abandoned the trip)

    def _recover(self):
        """A trip still open when the relay was last stopped without closing (a crash, a power cut): end it where it stopped."""
        try:
            files = sorted(self.dir.glob("*.jsonl")) if self.dir.is_dir() else []
        except OSError:
            return
        for path in files:
            try:
                head, last_point, has_end, count = self._scan(path)
                if has_end:
                    continue
                if head is None or last_point is None:
                    path.unlink()  # nothing usable was ever written
                    continue
                with open(path, "a+b") as f:
                    f.seek(0, 2)
                    size = f.tell()
                    if size:
                        f.seek(size - 1)
                        if f.read(1) != b"\n":  # a half-written last line: start the end line on a fresh one
                            f.write(b"\n")
                end = {"k": "end", "reason": "interrupted", "t": last_point.get("t", 0), "ts": head.get("ts", 0) + int(last_point.get("t", 0)),
                       "dist": last_point.get("d", 0), "top": 0, "n": count}
                summary = self._work_out(head.get("id"), end)
                if summary is not None:
                    end["sum"] = summary
                    end["top"] = summary.get("top_ms", 0)  # (the speed record was lost with the crash; the points still have it)
                with open(path, "a", encoding="utf-8", newline="\n") as f:
                    f.write(json.dumps(end, separators=(",", ":")) + "\n")
                self.stats["trips_recovered"] += 1
            except (OSError, ValueError):
                self.stats["recover_errors"] += 1

    @staticmethod
    def _scan(path):
        """(header, last point, whether an end line exists, number of points) from a whole file: only used on the few files left open."""
        head = last = None
        has_end = False
        count = 0
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    obj = json.loads(line)
                except ValueError:
                    continue  # a half-written last line
                kind = obj.get("k") if isinstance(obj, dict) else None
                if kind == "trip" and head is None:
                    head = obj
                elif kind == "p":
                    last = obj
                    count += 1
                elif kind == "end":
                    has_end = True
        return head, last, has_end, count

    # ---- reading back ------------------------------------------------------------------------------------------------

    def status(self):
        trip = self._trip
        if trip is None:
            return {"recording": False}
        return {"recording": True, "id": trip.id, "distance": round(trip.dist, 1), "duration": round(trip.last_rt - trip.start_rt, 1)}

    def list(self):
        """A summary of every trip, newest first. Reads only the first and last lines of each file, so it stays quick with
        hundreds of trips."""
        out = []
        try:
            files = [p for p in self.dir.glob("*.jsonl") if TRIP_ID_RE.match(p.stem)] if self.dir.is_dir() else []
        except OSError:
            return out
        for path in files:
            summary = self._summary(path)
            if summary is not None:
                summary["recording"] = self._trip is not None and self._trip.id == summary["id"]
                out.append(summary)
        out.sort(key=lambda t: (t["ts"], t["id"]), reverse=True)
        return out

    def _summary(self, path):
        try:
            with open(path, "rb") as f:
                first = f.readline()
                size = path.stat().st_size
                f.seek(max(0, size - _TAIL_BYTES))
                tail = f.read().splitlines()
            head = json.loads(first)
            if not isinstance(head, dict) or head.get("k") != "trip":
                return None
            out = {"id": head["id"], "ts": head.get("ts", 0), "vehicle": head.get("vehicle"), "energy": head.get("energy"), "ended": False, "title": None, "starred": False}
            titled = starred = False
            for raw in reversed(tail):
                try:
                    obj = json.loads(raw)
                except ValueError:
                    continue  # a cut line at the start of the tail, or a half-written last line
                if not isinstance(obj, dict):
                    continue
                if obj.get("k") == "name":
                    if not titled:  # (the last name line is the one that counts)
                        titled = True
                        out["title"] = obj.get("name") if isinstance(obj.get("name"), str) and obj.get("name") else None
                    continue
                if obj.get("k") == "star":
                    if not starred:  # (the last star line is the one that counts)
                        starred = True
                        out["starred"] = obj.get("v") is True
                    continue
                if obj.get("k") == "end":
                    out.update(ended=True, reason=obj.get("reason"), duration=obj.get("t"), distance=obj.get("dist"), top=obj.get("top"), points=obj.get("n"))
                    stats = obj.get("sum")
                    if not (isinstance(stats, dict) and stats.get("v") == tripstats.STATS_VERSION):
                        stats = self._stats_again(path, out["id"], obj)  # (a trip from older rules, or one without a summary)
                    if stats is not None:
                        out["stats"] = stats
                    return out
                if obj.get("k") == "p":
                    out.update(duration=obj.get("t"), distance=obj.get("d"))
                    return out
            return out
        except (OSError, ValueError, KeyError):
            return None

    def _path_of(self, trip_id):
        """The file of an ended trip. Raises NotFound for an unknown or malformed id, Busy for the trip being recorded."""
        if not isinstance(trip_id, str) or not TRIP_ID_RE.match(trip_id):
            raise NotFound(trip_id)
        if self._trip is not None and self._trip.id == trip_id:
            raise Busy(trip_id)
        path = self.dir / f"{trip_id}.jsonl"
        if not path.is_file():
            raise NotFound(trip_id)
        return path

    def rename(self, trip_id, name):
        """Give an ended trip a name of the user's own (an empty one takes it away). Returns the name as stored, or None.
        Raises NotFound, Busy, or ValueError for a name that is not text."""
        path = self._path_of(trip_id)
        title = clean_name(name)
        self._append(path, {"k": "name", "name": title})
        return title or None

    def star(self, trip_id, starred):
        """Mark an ended trip as a favourite, or take the mark away. Like a name, it is a line added to the end of the file (the last
        one counts), so nothing is rewritten. Returns the state as stored. Raises NotFound, Busy, or ValueError for a non-boolean."""
        if not isinstance(starred, bool):
            raise ValueError("starred must be true or false")
        self._append(self._path_of(trip_id), {"k": "star", "v": starred})
        return starred

    @staticmethod
    def _append(path, line):
        """Add one line to the end of an ended trip's file."""
        with open(path, "a+b") as f:
            f.seek(0, 2)
            size = f.tell()
            if size:
                f.seek(size - 1)
                if f.read(1) != b"\n":  # (a half-written last line: start ours on a fresh one)
                    f.write(b"\n")
            f.write((json.dumps(line, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8"))

    def delete(self, trip_id):
        """Move an ended trip to the trash folder. Raises NotFound or Busy."""
        path = self._path_of(trip_id)
        trash = self.dir / TRASH_DIR
        trash.mkdir(exist_ok=True)
        target, n = trash / path.name, 1
        while target.exists():
            n += 1
            target = trash / f"{path.stem}-{n}.jsonl"
        path.replace(target)
        self._stats_cache.pop(trip_id, None)
        self._route_cache.pop(trip_id, None)

    def _work_out(self, trip_id, end):
        """The summary of a trip from its file, or None if it cannot be worked out (a summary is never worth losing a trip for)."""
        try:
            data = self.read(trip_id)
            return tripstats.summarise(data, end) if data is not None else None
        except Exception as e:  # noqa: BLE001 - whatever is wrong with one trip must not stop the relay ending it
            self.stats["summary_errors"] += 1
            self.say(f"Trip log: could not summarise {trip_id}: {e}")
            return None

    def _stats_again(self, path, trip_id, end):
        """A summary worked out afresh for the list, remembered until the file changes."""
        try:
            st = path.stat()
        except OSError:
            return None
        key = (st.st_size, st.st_mtime_ns)
        cached = self._stats_cache.get(trip_id)
        if cached is not None and cached[0] == key:
            return cached[1]
        summary = self._work_out(trip_id, end)
        self._stats_cache[trip_id] = (key, summary)
        return summary

    def routes(self):
        """Every trip's route, thinned, with what a filter needs (when, which car, which countries) and its totals: what the
        Trips page draws when it shows all trips on one map. Newest first. A finished trip's route is worked out once and
        remembered until its file changes; the trip being recorded is read each time."""
        out = []
        for item in self.list():
            path = self.dir / f"{item['id']}.jsonl"
            try:
                st = path.stat()
            except OSError:
                continue
            key = (st.st_size, st.st_mtime_ns)
            cached = self._route_cache.get(item["id"])
            if cached is not None and cached[0] == key and not item["recording"]:
                line = cached[1]
            else:
                data = self.read(item["id"])
                if data is None:
                    continue
                line = tripstats.route_line(data["points"])
                if not item["recording"]:
                    self._route_cache[item["id"]] = (key, line)
            stats = item.get("stats") or {}
            out.append({"id": item["id"], "ts": item["ts"], "vehicle": item.get("vehicle"), "title": item.get("title"), "name": stats.get("name"),
                        "countries": stats.get("countries", []), "dist": item.get("distance") or 0, "dur": item.get("duration") or 0,
                        "top": item.get("top") or 0, "recording": item["recording"], "line": line})
        return out

    def live(self, trip_id, after):
        """What a trip has gained since `after` seconds into it: {"points", "info", "events", "end"}. The Trips page asks for this
        about once a second while it follows a trip being driven, so it can move the car smoothly without fetching the whole trip
        (or its summary) each time. None if there is no such trip."""
        data = self.read(trip_id)
        if data is None:
            return None
        return {"points": [p for p in data["points"] if p["t"] > after], "info": [i for i in data["info"] if i.get("t", 0) > after],
                "events": [e for e in data["events"] if e.get("t", 0) > after], "end": data["end"]}

    def detail(self, trip_id):
        """read() plus the summary worked out now, so a change to the rules shows at once. None if there is no such trip."""
        data = self.read(trip_id)
        if data is None:
            return None
        try:
            data["stats"] = tripstats.summarise(data, spans=True)
        except Exception as e:  # noqa: BLE001
            self.stats["summary_errors"] += 1
            self.say(f"Trip log: could not summarise {trip_id}: {e}")
            data["stats"] = None
        return data

    def read(self, trip_id):
        """The whole trip as {"header", "points", "info", "events", "end", "title"}, or None if there is no such trip."""
        if not isinstance(trip_id, str) or not TRIP_ID_RE.match(trip_id):
            return None
        path = self.dir / f"{trip_id}.jsonl"
        try:
            if path.stat().st_size > _MAX_READ_BYTES:
                return None
            out = {"header": None, "points": [], "info": [], "events": [], "end": None, "title": None, "starred": False}
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    try:
                        obj = json.loads(line)
                    except ValueError:
                        continue
                    kind = obj.get("k") if isinstance(obj, dict) else None
                    if kind == "trip" and out["header"] is None:
                        out["header"] = obj
                    elif kind == "p":
                        out["points"].append(obj)
                    elif kind == "i":
                        out["info"].append(obj)
                    elif kind == "e":
                        out["events"].append(obj)
                    elif kind == "end":
                        out["end"] = obj
                    elif kind == "name":
                        out["title"] = obj.get("name") if isinstance(obj.get("name"), str) and obj.get("name") else None
                    elif kind == "star":
                        out["starred"] = obj.get("v") is True
            return out if out["header"] is not None else None
        except OSError:
            return None

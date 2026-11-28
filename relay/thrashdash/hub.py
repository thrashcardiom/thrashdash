"""The Hub: the single in-memory place where "what the car is doing right now" lives.

Data sources (the UDP listener or the simulator) push packets in; each connected browser pulls
changes out. Keeping it transport-agnostic means the simulator exercises exactly the same code
path as the real game, so what we test is what ships.
"""
import json
import math
import time
from collections import Counter, deque

from . import PROTOCOL_VERSION
from .trip import TripComputer, energy_kind

# Limits so a malformed or hostile packet cannot make the relay allocate without bound.
MAX_CHANNELS = 2000
MAX_DEPTH = 5
MAX_STRING = 64
MAX_NAME = 200

# Slowly changing data the mod sends in separate "slow" packets: tyres, body damage, component states, and what kind of
# energy the car carries. Each becomes a namespace of channels: tire.FL.temp, damage.zone.RR, state.engine.oilOverheating,
# energy.electric ...
SLOW_SECTIONS = ("tire", "damage", "state", "energy")

# Packets from the game-side trip-log extension rather than from a car: where the player is, the place name / speed limit /
# weather there, and events such as a teleport. They go to the trip log, not to the dashboard's channels.
GEO_TYPES = ("geo", "geoinfo", "geoevent", "geopoi")

# What the dashboards may know about the world (the posted speed limit, the route's progress) is published as channels too:
#   geo.limit (m/s) and geo.limitPosted   from each `geoinfo` (every second), only while a limit is known
#   geo.route.active, .remaining (m), .eta (s), .off (m from the route), .name   from each `georoute` (every second)
#   geo.route.planning and .replanning    true while Earth is making a plan (replanning: a new one for a route it already had)
#   geo.route.stop.distance (m), .eta (s), .name, .number, .ahead    the next stop before the destination, while there is one
#   lap.*                                 from the lap timer (laps.py), with every live position (ten a second)
# They are the game's latest word and nothing more: a reading older than this is dropped rather than shown (the game may have
# been paused, or closed), so a stale limit can never raise an alert.
GEO_TTL_S = 4.0
LIMIT_MAX_MS = 100.0  # 360 km/h: anything above is not a limit
ROUTE_MAX_M = 2.0e7  # half way round the world
ROUTE_MAX_S = 1.0e7
ROUTE_NAME_MAX = 60
PLAN_SHOW_S = 3.0  # a plan is shown for at least this long: Earth is often done within a second, too quick to read
# The route's steps (turn here, take exit 2...) come once per route, in a `geosteps` packet; with the car's progress along the route
# (its length minus what remains) they give the next maneuver: geo.route.next.distance (m) and .type, .modifier, .street, .exit; and the one
# after it: geo.route.then.gap (m after the next one) and .type, .modifier, .street, .exit.
MAX_STEPS = 200
MAX_PATH_POINTS = 1500  # the route's line (trips.lua sends no more)
MAX_STOPS = 12
MAX_FUEL_PLACES = 30  # the most filling stations and chargers taken from one answer (trips.lua sends 15)
STEP_TEXT_MAX = 60
STEP_PASSED_M = 15.0  # a maneuver stays "next" until the car is this far past it, so it does not vanish the moment it is reached
MAX_EXIT = 30

# A source counts as "connected" if a frame arrived within this many seconds. The game sends 30/s,
# so 2 s of silence reliably means paused-in-menu, car unseated, or game closed.
STALE_AFTER_S = 2.0


def _finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _type_name(value):
    # bool must be tested before number: in Python, bool is a subclass of int.
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    return "string"


def flatten(node, prefix="", out=None, depth=0):
    """Flatten nested electrics tables into dotted channel names: wheelThermals.FL.brakeCoreTemperature.

    Only numbers, booleans and short strings survive: those are the only things a gauge can show.
    Non-finite floats become None because the game encodes inf/nan as 9e999, which Python parses
    to inf, and json.dumps would then emit the invalid token `Infinity` that browsers reject.
    """
    if out is None:
        out = {}
    if depth > MAX_DEPTH:
        return out
    for key, value in node.items():
        if len(out) >= MAX_CHANNELS:
            break
        name = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, dict):
            flatten(value, name, out, depth + 1)
        elif isinstance(value, bool):
            out[name] = value
        elif isinstance(value, (int, float)):
            out[name] = value if math.isfinite(value) else None
        elif isinstance(value, str):
            out[name] = value[:MAX_STRING]
        # lists, nulls and anything else: skipped, not displayable
    return out


class Hub:
    def __init__(self, simulated=False, trip_path=None, triplog=None, lap_timer=None):
        self.simulated = simulated
        self.lap_timer = lap_timer  # laps and sectors from the live position (laps.LapTimer), or None
        self.triplog = triplog  # the BeamNG.Earth trip log (triplog.TripLog), or None when this run keeps none
        self.trips = TripComputer(trip_path)  # trip meters A and B (kept across restarts when trip_path is given)
        self._trip = {}  # their current channels (trip.A.distance ...)
        self.vehicle = None
        self.values = {}  # everything current: the latest electrics frame plus the latest slow data
        self._electrics = {}  # latest flattened electrics frame
        self._extra = {}  # latest flattened slow data (tyres, damage, states)
        self.channels = {}  # channel name -> "number" | "boolean" | "string"
        self.catalog_version = 0  # bumped when the channel set or the vehicle changes
        self.frame_seq = 0  # bumped on every accepted frame
        self.last_frame_at = None  # time.monotonic() of the last frame
        self.stats = Counter()
        self.live = deque(maxlen=128)  # the game's live positions (geolive), newest last, for the Trips page's live map
        self.live_seq = 0
        self._geo_limit = ({}, None)  # (channels, time.monotonic() it arrived): the speed limit here
        self._geo_route = ({}, None)  # the same for the route's progress
        self._plan_version = None  # (the route version in the latest progress packet and when it came), to notice a new plan of the same route
        self._plan_shown = None  # (replanning: bool, until) while a plan being made, or just made, is still to be shown
        self._route_raw = None  # the latest progress packet's numbers: {"version", "remaining"}, to work out the next maneuver from
        self._route_steps = None  # {"version", "dist", "steps"} from the latest `geosteps`
        self._route_path = None  # {"version", "points", "dest", "waypoints"} from the latest `georoutepath`: the planned route's line, for the maps
        self.fuel = None  # the game's latest answer to a search for filling stations and chargers: {"id", "origin", "places", "done", "why"}
        self.route_ack = {"seq": 0, "ok": None, "why": None}  # the game's answer to the latest command to set or clear the route
        self._g_peak = 0.0  # the strongest force on the car (m/s^2) seen since the last position sample
        self._frame_json = (None, "")  # (seq, serialized) cache so N browsers share one json.dumps

    # ---- intake -----------------------------------------------------------------------------

    def ingest_packet(self, packet):
        """Accept one decoded packet from the mod (or the simulator). Never raises on bad input."""
        if not isinstance(packet, dict):
            self.stats["rejected_not_object"] += 1
            return
        if packet.get("v") != PROTOCOL_VERSION:
            self.stats["rejected_version"] += 1
            return

        vehicle = packet.get("vehicle")
        vehicle = vehicle[:MAX_NAME] if isinstance(vehicle, str) else None

        kind = packet.get("type")
        if kind == "frame":
            electrics = packet.get("electrics")
            if not isinstance(electrics, dict):
                self.stats["rejected_frame"] += 1
                return
            self._accept_frame(vehicle, flatten(electrics))
        elif kind == "slow":
            self._accept_slow(vehicle, packet)
        elif kind in GEO_TYPES:
            if kind == "geoinfo":
                self._accept_limit(packet)
            elif kind == "geoevent" and self.lap_timer is not None and isinstance(packet.get("name"), str):
                self.lap_timer.event(packet["name"])  # a reset or a teleport spoils the lap in progress
            self._accept_geo(packet)
        elif kind == "georoute":
            self._accept_route(packet)
        elif kind == "geosteps":
            self._accept_steps(packet)
        elif kind == "georoutepath":
            self._accept_path(packet)
        elif kind == "georouteack":
            self._accept_ack(packet)
        elif kind == "geofuel":
            self._accept_fuel(packet)
        elif kind == "geolive":
            self._accept_live(packet)
        elif kind == "catalog":
            # The mod's catalog only lists top-level names. We derive the real (flattened) catalog
            # from frames, so a catalog packet is used only to learn the vehicle quickly.
            self.stats["catalogs"] += 1
            self._set_vehicle(vehicle)
        else:
            self.stats["rejected_type"] += 1

    def _accept_live(self, packet):
        """A live position (ten a second): handed on to the Trips page as it is, and not recorded (the trip is made from the
        once-a-second `geo` packets). Only a plausible position is passed on."""
        lat, lon, rt = packet.get("lat"), packet.get("lon"), packet.get("rt")
        num = lambda v: isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)  # noqa: E731
        if not (num(lat) and num(lon) and num(rt)) or not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
            self.stats["rejected_live"] += 1
            return
        self.stats["live"] += 1
        self.live_seq += 1
        message = {"type": "geo", "seq": self.live_seq, "rt": float(rt), "lat": float(lat), "lon": float(lon)}
        if num(packet.get("hdg")):
            message["hdg"] = float(packet["hdg"]) % 360.0
        if num(packet.get("spd")) and 0 <= packet["spd"] <= 400:
            message["spd"] = float(packet["spd"])
        self.live.append(message)
        if self.lap_timer is not None:
            clock = packet["st"] if num(packet.get("st")) else rt  # game time when the game sends it (see laps.py), else real time
            self.lap_timer.feed(float(lat), float(lon), float(clock), self.vehicle)
            if self.lap_timer.display is not None:  # (the clock on a dashboard moves with every position)
                self._refresh()

    def add_trip_event(self, kind, trip_id):
        """A trip started or ended: told to the Trips page over the same feed as the live positions, so it can open a new trip the
        moment it begins, even when its window is behind the game (a hidden page's timers are slowed right down, but a message
        from the relay still arrives at once)."""
        self.live_seq += 1
        self.live.append({"type": "trip", "seq": self.live_seq, "event": kind, "id": trip_id})

    def live_since(self, seq):
        """The live positions newer than number `seq`, oldest first."""
        return [m for m in self.live if m["seq"] > seq]

    def _accept_limit(self, packet):
        """The speed limit at the car, from each `geoinfo`. A packet with no (or an unbelievable) limit means "none known", which
        clears the channels, so a limit never outlives the road it belonged to."""
        channels = {}
        limit = packet.get("limit")
        if isinstance(limit, dict):
            ms = limit.get("ms")
            if _finite(ms) and 0 < ms <= LIMIT_MAX_MS:
                channels["geo.limit"] = float(ms)
                # Only a limit read from a sign counts as "posted"; the rest is a guess from the kind of road (see triplog.py).
                channels["geo.limitPosted"] = limit.get("mapped") is True
        self._geo_limit = (channels, time.monotonic())
        self._refresh()

    def _accept_route(self, packet):
        """The route's progress, from each `georoute`. No `gps` means "no route set", said as geo.route.active = false: for a
        dashboard, silence would be ambiguous."""
        gps = packet.get("gps")
        channels = {"geo.route.active": isinstance(gps, dict)}
        if isinstance(gps, dict):
            remaining, eta, off, name = gps.get("remaining"), gps.get("eta"), gps.get("off"), gps.get("name")
            if _finite(remaining) and 0 <= remaining <= ROUTE_MAX_M:
                channels["geo.route.remaining"] = float(remaining)
            if _finite(eta) and 0 <= eta <= ROUTE_MAX_S:
                channels["geo.route.eta"] = float(eta)
            if _finite(off) and 0 <= off <= ROUTE_MAX_M:
                channels["geo.route.off"] = float(off)
            if isinstance(name, str) and name.strip():
                channels["geo.route.name"] = name.strip()[:ROUTE_NAME_MAX]
        self._add_planning(channels, gps)
        self._add_stop(channels, gps)
        self._route_raw = None
        if isinstance(gps, dict):
            version, remaining = gps.get("routeVersion"), gps.get("remaining")
            self._route_raw = {"version": version if _finite(version) else None, "remaining": float(remaining) if _finite(remaining) and 0 <= remaining <= ROUTE_MAX_M else None}
        self._geo_route = (channels, time.monotonic())
        self._add_maneuvers()
        self._refresh()

    def _add_planning(self, channels, gps):
        """geo.route.planning and .replanning, from Earth's `planning` flag and the route's version (0 while there is no plan yet).
        A plan for a route that already has one is a re-route (a wrong turn). Either is held for PLAN_SHOW_S, and a re-route done
        between two packets, so never seen being made, still shows by its version having changed."""
        now = time.monotonic()
        if not isinstance(gps, dict):
            self._plan_version = self._plan_shown = None  # (cleared: the next route starts afresh)
            return
        version = gps.get("routeVersion")
        version = version if _finite(version) else 0
        before, at = self._plan_version or (0, 0.0)
        if now - at > GEO_TTL_S:
            before = 0  # (the game went quiet: what it had then says nothing about now)
        self._plan_version = (version, now)
        if self._plan_shown is not None and now >= self._plan_shown[1]:
            self._plan_shown = None
        if gps.get("planning") is True:
            self._plan_shown = (version > 0, now + PLAN_SHOW_S)
        elif self._plan_shown is None and before > 0 and version > 0 and version != before:
            self._plan_shown = (True, now + PLAN_SHOW_S)
        shown = self._plan_shown
        channels["geo.route.planning"] = shown is not None
        channels["geo.route.replanning"] = shown is not None and shown[0]

    @staticmethod
    def _add_stop(channels, gps):
        """geo.route.stop.*: the next stop before the destination. The planned route's legs run from its start through each stop;
        how far along it the car is comes from what remains, and the legs already passed from how many stops are still ahead."""
        stops = gps.get("stops") if isinstance(gps, dict) else None
        remaining = channels.get("geo.route.remaining")
        if not isinstance(stops, dict) or remaining is None:
            return
        ahead, legs = stops.get("ahead"), stops.get("legs")
        if not _finite(ahead) or ahead != int(ahead) or not 1 <= ahead < MAX_STOPS or not isinstance(legs, list) or not 2 <= len(legs) <= MAX_STOPS:
            return
        if not all(isinstance(leg, list) and len(leg) == 2 and all(_finite(v) and 0 <= v <= ROUTE_MAX_M for v in leg) for leg in legs):
            return
        passed = len(legs) - 1 - int(ahead)
        if passed < 0:
            return  # (more stops than the plan has legs for: a new stop not planned yet)
        travelled = sum(leg[0] for leg in legs) - remaining
        distance = max(0.0, sum(leg[0] for leg in legs[: passed + 1]) - travelled)
        length, duration = legs[passed]
        channels["geo.route.stop.distance"] = round(distance, 1)
        channels["geo.route.stop.eta"] = round(min(ROUTE_MAX_S, duration * distance / length), 1) if length > 0 else 0.0
        channels["geo.route.stop.number"] = passed + 1
        channels["geo.route.stop.ahead"] = int(ahead)
        name = stops.get("name")
        if isinstance(name, str) and name.strip():
            channels["geo.route.stop.name"] = name.strip()[:ROUTE_NAME_MAX]

    def _accept_fuel(self, packet):
        """The game's answer to a search for filling stations and chargers: {"id", "lat", "lon" (where it searched from),
        "places": [{"n", "kind", "lat", "lon"}...], "done", "why"?}. Only named places on Earth are kept."""
        search, places = packet.get("id"), packet.get("places")
        if not _finite(search):
            self.stats["rejected_fuel"] += 1
            return
        found = []
        for item in (places if isinstance(places, list) else [])[:MAX_FUEL_PLACES]:
            place = self._place({"lat": item.get("lat"), "lon": item.get("lon"), "name": item.get("n")}) if isinstance(item, dict) else None
            if place and "name" in place:
                place["kind"] = "charger" if item.get("kind") == "charger" else "fuel"
                found.append(place)
        why = packet.get("why")
        self.fuel = {"id": search, "origin": self._place(packet), "places": found, "done": packet.get("done") is True,
                     "why": why[:120] if isinstance(why, str) else None}

    def _accept_steps(self, packet):
        """The steps of the route, once per route (and now and then again). Only plausible ones are kept: in order, in range, with short text."""
        version, dist, steps = packet.get("version"), packet.get("dist"), packet.get("steps")
        if not _finite(version) or not isinstance(steps, list) or not (dist is None or (_finite(dist) and 0 < dist <= ROUTE_MAX_M)):
            self.stats["rejected_steps"] += 1
            return
        clean = []
        for step in steps[:MAX_STEPS]:
            offset = step.get("offset") if isinstance(step, dict) else None
            if not _finite(offset) or not 0 <= offset <= ROUTE_MAX_M or (clean and offset < clean[-1]["offset"]):
                continue
            item = {"offset": float(offset)}
            for key in ("type", "modifier", "name"):
                value = step.get(key)
                if isinstance(value, str) and value.strip():
                    item[key] = " ".join("".join(c if c.isprintable() else " " for c in value).split())[:STEP_TEXT_MAX]
            exit_no = step.get("exit")
            if _finite(exit_no) and 1 <= exit_no <= MAX_EXIT:
                item["exit"] = int(exit_no)
            clean.append(item)
        self._route_steps = {"version": version, "dist": float(dist) if dist is not None else None, "steps": clean}
        self.stats["steps"] += 1
        if self._add_maneuvers():
            self._refresh()

    @staticmethod
    def _place(item):
        """{"lat", "lon", "name"?} from something the game sent, or None if it is not a place on Earth."""
        if not isinstance(item, dict) or not _finite(item.get("lat")) or not _finite(item.get("lon")):
            return None
        if not (-90.0 <= item["lat"] <= 90.0 and -180.0 <= item["lon"] <= 180.0):
            return None
        place = {"lat": round(float(item["lat"]), 6), "lon": round(float(item["lon"]), 6)}
        name = item.get("name")
        if isinstance(name, str) and name.strip():
            place["name"] = " ".join("".join(c if c.isprintable() else " " for c in name).split())[:ROUTE_NAME_MAX]
        return place

    def _accept_path(self, packet):
        """The planned route's line (once per route, and now and then again): its points, the destination and the stops still ahead. Only
        plausible points are kept; a line of fewer than two is not a line."""
        version, points = packet.get("version"), packet.get("points")
        if not _finite(version) or not isinstance(points, list):
            self.stats["rejected_path"] += 1
            return
        line = []
        for p in points[:MAX_PATH_POINTS]:
            if isinstance(p, (list, tuple)) and len(p) == 2 and _finite(p[0]) and _finite(p[1]) and -90.0 <= p[0] <= 90.0 and -180.0 <= p[1] <= 180.0:
                line.append([round(float(p[0]), 6), round(float(p[1]), 6)])
        if len(line) < 2:
            self.stats["rejected_path"] += 1
            return
        waypoints = [w for w in (self._place(x) for x in (packet.get("waypoints") if isinstance(packet.get("waypoints"), list) else [])[:MAX_STOPS]) if w]
        self._route_path = {"version": version, "points": line, "dest": self._place(packet.get("dest")), "waypoints": waypoints}
        self.stats["paths"] += 1

    def _accept_ack(self, packet):
        """The game's answer to a command to set or clear the route: {"ok", "why"}. Counted, so a caller can tell a new answer from an old one."""
        why = packet.get("why")
        self.route_ack = {"seq": self.route_ack["seq"] + 1, "ok": packet.get("ok") is True, "why": why[:120] if isinstance(why, str) else None}

    def route_state(self):
        """The GPS route as the dashboards and maps want it: {"active": False}, or {"active": True, name, remaining (m), eta (s), off (m), version, and,
        once the game has sent the line for this version of the route: points [[lat, lon]...], dest {lat, lon, name?}, waypoints [...]}. A route
        is only as current as the game's latest word (see GEO_TTL_S)."""
        channels, at = self._geo_route
        if at is None or time.monotonic() - at > GEO_TTL_S or channels.get("geo.route.active") is not True:
            return {"active": False}
        raw = self._route_raw or {}
        state = {"active": True, "version": raw.get("version")}
        for key, name in (("name", "geo.route.name"), ("remaining", "geo.route.remaining"), ("eta", "geo.route.eta"), ("off", "geo.route.off")):
            if name in channels:
                state[key] = channels[name]
        path = self._route_path
        if path is not None and raw.get("version") is not None and path["version"] == raw["version"]:
            state.update(points=path["points"], dest=path["dest"], waypoints=path["waypoints"])
        return state

    def _add_maneuvers(self):
        """Add the next maneuver's channels to the route's. Returns whether there are any. They go in the same record as the progress, so
        they expire with it (a maneuver is only as fresh as the car's progress along the route)."""
        channels, at = self._geo_route
        for key in [k for k in channels if k.startswith(("geo.route.next.", "geo.route.then."))]:
            del channels[key]
        raw, held = self._route_raw, self._route_steps
        if at is None or raw is None or held is None or held["dist"] is None or raw["remaining"] is None or raw["version"] != held["version"]:
            return False  # (steps are only believed for the route they belong to: after a re-plan the old ones are wrong)
        traveled = max(0.0, held["dist"] - raw["remaining"])
        upcoming = [st for st in held["steps"] if st["offset"] >= traveled - STEP_PASSED_M and st.get("type") != "depart"]
        if not upcoming:
            return False
        nxt = upcoming[0]
        channels["geo.route.next.distance"] = round(max(0.0, nxt["offset"] - traveled), 1)
        self._put_step(channels, "geo.route.next", nxt)
        if len(upcoming) > 1:
            then = upcoming[1]
            channels["geo.route.then.gap"] = round(max(0.0, then["offset"] - nxt["offset"]), 1)
            self._put_step(channels, "geo.route.then", then)
        return True

    @staticmethod
    def _put_step(channels, prefix, step):
        for key, channel in (("type", "type"), ("modifier", "modifier"), ("name", "street"), ("exit", "exit")):
            if key in step:
                channels[f"{prefix}.{channel}"] = step[key]

    def _refresh(self):
        """Show a change at once, even if no car frame is due."""
        self._rebuild()
        self.frame_seq += 1

    def _geo_channels(self):
        """The world's channels that are still fresh."""
        now = time.monotonic()
        out = {}
        for channels, at in (self._geo_limit, self._geo_route):
            if at is not None and now - at <= GEO_TTL_S:
                out.update(channels)
        if self.lap_timer is not None:
            out.update(self.lap_timer.channels(now))  # lap.*: laps.py (it drops them itself when the positions stop)
        return out

    def _accept_geo(self, packet):
        """A position sample, place/limit/weather snapshot or event from the game's trip-log extension (BeamNG.Earth)."""
        if self.triplog is None:
            self.stats["geo_ignored"] += 1
            return
        self.stats["geo"] += 1
        # The car's own numbers (fuel, damage) go into the trip with each position, but only while the car is sending: stale
        # values from a paused game would be wrong.
        car = dict(self.values) if self.connected else {}
        if packet.get("type") == "geo":
            # The strongest force on the car since the last position sample (they come once a second, a hit lasts a fraction of one):
            # watched in every frame, taken with the sample, and started again.
            if self._g_peak > 0 and self.connected:
                car["gPeak"] = self._g_peak
            self._g_peak = 0.0
        self.triplog.ingest(packet, car)

    def _set_vehicle(self, vehicle):
        if vehicle != self.vehicle:
            self.vehicle = vehicle
            self._extra = {}  # another car: the old car's tyres, damage and states must not linger
            self.catalog_version += 1

    def _rebuild(self):
        """Combine the electrics frame and the slow data into the current values, and announce a changed channel set."""
        values = {**self._electrics, **self._extra, **self._trip, **self._geo_channels()}
        # Channels whose value is None (a non-finite number) are listed nowhere, so compare like with like.
        names = {name for name, v in values.items() if v is not None}
        if names != self.channels.keys():
            self.channels = {name: _type_name(v) for name, v in values.items() if v is not None}
            self.catalog_version += 1
        self.values = values

    def _accept_frame(self, vehicle, flat):
        self._set_vehicle(vehicle)
        ax, ay = flat.get("accXSmooth"), flat.get("accYSmooth")
        if isinstance(ax, (int, float)) and isinstance(ay, (int, float)) and not isinstance(ax, bool) and not isinstance(ay, bool):
            g = math.hypot(ax, ay)
            if math.isfinite(g) and g < 1e4:  # (a glitch of thousands of g is not a reading)
                self._g_peak = max(self._g_peak, g)
        self._electrics = flat
        self._trip = self.trips.update(flat, vehicle, self._energy_kind())
        self._rebuild()
        self.frame_seq += 1
        self.last_frame_at = time.monotonic()
        self.stats["frames"] += 1

    def _accept_slow(self, vehicle, packet):
        """A slow packet is a complete snapshot of tyres, damage and states: a section that is missing (or empty) means
        "none right now", so a repaired car does not keep showing old warnings."""
        self._set_vehicle(vehicle)
        extra = {}
        for name in SLOW_SECTIONS:
            section = packet.get(name)
            # (An empty Lua table is encoded as [] rather than {}, so an empty list is just "empty".)
            if isinstance(section, dict):
                flatten(section, name, extra)
            elif section not in (None, []):
                self.stats["rejected_slow_section"] += 1
        self._extra = extra
        self._rebuild()
        self.frame_seq += 1  # so browsers get the new values even if no electrics frame is due
        self.stats["slow"] += 1

    def _energy_kind(self):
        return energy_kind(self._extra.get("energy.electric"), self._extra.get("energy.liquid"))

    def reset_trip(self, name):
        """Zero trip A or B (raises KeyError for any other name) and show it at once."""
        self.trips.reset(name)
        self._trip = self.trips.channels(self._electrics, self._energy_kind())
        self._rebuild()
        self.frame_seq += 1

    # ---- output -----------------------------------------------------------------------------

    @property
    def connected(self):
        return self.last_frame_at is not None and (time.monotonic() - self.last_frame_at) < STALE_AFTER_S

    def status_message(self):
        return {"type": "status", "connected": self.connected, "vehicle": self.vehicle, "simulated": self.simulated}

    def catalog_message(self):
        return {"type": "catalog", "vehicle": self.vehicle, "channels": self.channels}

    def frame_json(self):
        """Serialized frame message, cached per frame sequence number."""
        seq, text = self._frame_json
        if seq != self.frame_seq:
            text = json.dumps({"type": "frame", "seq": self.frame_seq, "d": self.values}, allow_nan=False, separators=(",", ":"))
            self._frame_json = (self.frame_seq, text)
        return text

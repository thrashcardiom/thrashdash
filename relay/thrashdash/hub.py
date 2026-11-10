"""The Hub: the single in-memory place where "what the car is doing right now" lives.

Data sources (the UDP listener or the simulator) push packets in; each connected browser pulls
changes out. Keeping it transport-agnostic means the simulator exercises exactly the same code
path as the real game, so what we test is what ships.
"""
import json
import math
import time
from collections import Counter

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

# A source counts as "connected" if a frame arrived within this many seconds. The game sends 30/s,
# so 2 s of silence reliably means paused-in-menu, car unseated, or game closed.
STALE_AFTER_S = 2.0


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
    def __init__(self, simulated=False, trip_path=None):
        self.simulated = simulated
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
        elif kind == "catalog":
            # The mod's catalog only lists top-level names. We derive the real (flattened) catalog
            # from frames, so a catalog packet is used only to learn the vehicle quickly.
            self.stats["catalogs"] += 1
            self._set_vehicle(vehicle)
        else:
            self.stats["rejected_type"] += 1

    def _set_vehicle(self, vehicle):
        if vehicle != self.vehicle:
            self.vehicle = vehicle
            self._extra = {}  # another car: the old car's tyres, damage and states must not linger
            self.catalog_version += 1

    def _rebuild(self):
        """Combine the electrics frame and the slow data into the current values, and announce a changed channel set."""
        values = {**self._electrics, **self._extra, **self._trip}
        # Channels whose value is None (a non-finite number) are listed nowhere, so compare like with like.
        names = {name for name, v in values.items() if v is not None}
        if names != self.channels.keys():
            self.channels = {name: _type_name(v) for name, v in values.items() if v is not None}
            self.catalog_version += 1
        self.values = values

    def _accept_frame(self, vehicle, flat):
        self._set_vehicle(vehicle)
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

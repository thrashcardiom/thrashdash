"""Trip computer: two trip meters, A and B, kept by the relay.

They live here and not in the browser so every window and phone shows the same numbers, they survive closing the
dashboard, and nothing depends on a page being open while you drive. Each trip counts distance, time, top speed and
the energy used, until it is reset by hand. It is saved to disk so it also survives restarting ThrashDash.

Distance is the car's speed integrated over the time between frames (as the game's own trip app does). A gap of more
than a second between frames (the game paused, a menu, a loading screen) counts as nothing, so it never adds distance.

Energy is tracked per KIND of energy (litres of fuel, kWh of battery) because a trip can span different cars and
adding litres to kWh would be nonsense. The game's channels: `fuelVolume` is litres for a tank but kWh for a battery,
`fuel` is the remaining fraction of either. The mod reports which kind the car has (`energy.liquid` / `energy.electric`);
a hybrid mixes the two, and an older mod reports nothing, so for those only the percentage used is tracked (it means the
same for any tank or battery).
"""
import json
import math
import time

from .store import _atomic_write_json

TRIPS = ("A", "B")
# A longer gap between frames than this is a pause, not driving.
MAX_GAP_S = 1.0
# More than this share of the tank or battery changing between two frames is a refuel, a respawn or a repair, not use.
MAX_JUMP_RATIO = 0.05
# Averages need some driving behind them to mean anything.
MIN_DISTANCE_FOR_ECONOMY_M = 500.0
MIN_SECONDS_FOR_AVERAGE = 5.0
# Nothing on a road goes faster than this (m/s); a larger value is a glitch and must not become the "top speed".
MAX_PLAUSIBLE_SPEED = 400.0
SAVE_EVERY_S = 10.0
KINDS = ("liquid", "electric")  # the kinds whose volume (litres, kWh) is tracked
PERCENT_ONLY = ("mixed", "unknown")  # a hybrid, or a car whose mod does not say: only the share used is tracked


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def energy_kind(electric, liquid):
    """The kind of energy from the mod's two flags: 'liquid', 'electric', 'mixed', or None when unknown (an older mod)."""
    if electric is True and liquid is True:
        return "mixed"
    if electric is True:
        return "electric"
    if liquid is True:
        return "liquid"
    return None


class _Energy:
    """What a trip has used of one kind of energy, and how far it went doing so."""

    def __init__(self, distance=0.0, volume=0.0, ratio=0.0):
        self.distance = distance  # metres driven while this kind was being tracked
        self.volume = volume  # litres or kWh used
        self.ratio = ratio  # fraction of a full tank or battery used (1.0 = one whole tank)

    def to_json(self):
        return {"distance": self.distance, "volume": self.volume, "ratio": self.ratio}


class _Trip:
    def __init__(self):
        self.distance = 0.0  # metres
        self.seconds = 0.0
        self.max_speed = 0.0  # m/s
        self.energy = {}  # kind -> _Energy

    def to_json(self):
        return {"distance": self.distance, "seconds": self.seconds, "maxSpeed": self.max_speed,
                "energy": {k: e.to_json() for k, e in self.energy.items()}}

    @classmethod
    def from_json(cls, raw):
        trip = cls()
        if not isinstance(raw, dict):
            return trip
        # Loaded data is trusted no further than the rest: numbers only, finite, never negative.
        def positive(key, source=raw):
            value = source.get(key)
            return float(value) if _number(value) and value >= 0 else 0.0
        trip.distance, trip.seconds, trip.max_speed = positive("distance"), positive("seconds"), min(positive("maxSpeed"), MAX_PLAUSIBLE_SPEED)
        energy = raw.get("energy")
        if isinstance(energy, dict):
            for kind in (*KINDS, *PERCENT_ONLY):
                entry = energy.get(kind)
                if isinstance(entry, dict):
                    trip.energy[kind] = _Energy(positive("distance", entry), positive("volume", entry), positive("ratio", entry))
        return trip


class TripComputer:
    def __init__(self, path=None, clock=time.monotonic):
        self.path = path
        self.clock = clock
        self.trips = {name: _Trip() for name in TRIPS}
        self._last_time = None
        self._vehicle = None
        self._prev = None  # (fuelVolume, fuel) of the previous frame, for the energy used between frames
        self._dirty = False
        self._saved_at = clock()
        self.load()

    # ---- keeping them ----------------------------------------------------------------------

    def load(self):
        if self.path is None:
            return
        try:
            with open(self.path, encoding="utf-8") as f:
                raw = json.load(f)
        except (OSError, ValueError):
            return  # none yet, or unreadable: start from zero rather than failing to start
        trips = raw.get("trips") if isinstance(raw, dict) else None
        if isinstance(trips, dict):
            for name in TRIPS:
                self.trips[name] = _Trip.from_json(trips.get(name))

    def save(self):
        if self.path is None:
            return
        try:
            _atomic_write_json(self.path, {"v": 1, "trips": {name: t.to_json() for name, t in self.trips.items()}})
            self._dirty = False
            self._saved_at = self.clock()
        except OSError:
            pass  # a full disk must not stop the dashboard; try again at the next opportunity

    def reset(self, name):
        if name not in self.trips:
            raise KeyError(name)
        self.trips[name] = _Trip()
        self._dirty = True
        self.save()

    # ---- counting --------------------------------------------------------------------------

    def update(self, flat, vehicle, kind):
        """Account for one electrics frame (flattened channels). `kind` is energy_kind() of the car being driven."""
        now = self.clock()
        dt = 0.0 if self._last_time is None else now - self._last_time
        self._last_time = now
        if not 0 < dt <= MAX_GAP_S:
            dt = 0.0
        new_vehicle = vehicle != self._vehicle
        if new_vehicle:
            self._vehicle = vehicle
            self._prev = None  # nothing to compare a different car's tank with
            dt = 0.0

        speed = flat.get("wheelspeed")
        if not _number(speed):
            speed = flat.get("airspeed")
        speed = abs(speed) if _number(speed) and abs(speed) <= MAX_PLAUSIBLE_SPEED else 0.0

        volume, ratio = flat.get("fuelVolume"), flat.get("fuel")
        used_volume = used_ratio = 0.0
        if self._prev is not None and _number(volume) and _number(ratio):
            prev_volume, prev_ratio = self._prev
            drop_ratio = prev_ratio - ratio
            if abs(drop_ratio) <= MAX_JUMP_RATIO:  # a bigger step is a refuel or a respawn, which is not use
                drop_volume = prev_volume - volume
                if kind == "electric":
                    # a battery gains a little back going downhill (regeneration): count the net change
                    used_volume, used_ratio = drop_volume, drop_ratio
                else:
                    used_volume, used_ratio = max(drop_volume, 0.0), max(drop_ratio, 0.0)
        self._prev = (volume, ratio) if _number(volume) and _number(ratio) else None

        driven = speed * dt
        for trip in self.trips.values():
            trip.distance += driven
            trip.seconds += dt
            trip.max_speed = max(trip.max_speed, speed)
            entry = trip.energy.setdefault(kind or "unknown", _Energy())
            entry.distance += driven
            entry.ratio = max(0.0, entry.ratio + used_ratio)
            if kind in KINDS:
                entry.volume = max(0.0, entry.volume + used_volume)  # (a net negative is possible for a moment: regeneration)
        if driven or used_volume or used_ratio:
            self._dirty = True
        if self._dirty and now - self._saved_at >= SAVE_EVERY_S:
            self.save()
        return self.channels(flat, kind)

    # ---- reporting -------------------------------------------------------------------------

    def channels(self, flat, kind):
        """The trip numbers as channels (trip.A.distance ...). Values that cannot be known yet are left out."""
        out = {"trip.energyKind": kind or "unknown"}
        remaining = flat.get("fuelVolume") if kind in KINDS and _number(flat.get("fuelVolume")) else None
        for name, trip in self.trips.items():
            prefix = f"trip.{name}."
            out[prefix + "distance"] = trip.distance
            out[prefix + "seconds"] = trip.seconds
            out[prefix + "maxSpeed"] = trip.max_speed
            if trip.seconds >= MIN_SECONDS_FOR_AVERAGE:
                out[prefix + "avgSpeed"] = trip.distance / trip.seconds
            entry = trip.energy.get(kind or "unknown")
            if entry is None:
                continue
            out[prefix + "percentUsed"] = entry.ratio * 100
            if kind in KINDS:
                out[prefix + "energyUsed"] = entry.volume
                if entry.distance >= MIN_DISTANCE_FOR_ECONOMY_M and entry.volume > 0:
                    per_metre = entry.volume / entry.distance
                    out[prefix + "consumption"] = per_metre * 100_000  # litres or kWh per 100 km
                    if remaining is not None:
                        out[prefix + "range"] = remaining / per_metre  # metres left at this trip's average
        return out

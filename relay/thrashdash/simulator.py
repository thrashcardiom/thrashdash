"""Fake telemetry, so dashboards can be designed and tested without BeamNG running.

It emits packets with the same shape as the Lua mod (same channel names and units as the real
captures in docs/phase0-findings.md) and feeds them through Hub.ingest_packet, i.e. the exact
path real data takes. Plays a looping drive: accelerate through the gears, cruise, brake, repeat.
"""
import asyncio
import math
import time

from . import PROTOCOL_VERSION

RATE_HZ = 30
CYCLE_S = 40.0
MAX_RPM = 6500
IDLE_RPM = 800
# Upper speed (m/s) of each gear, an automatic-ish ladder.
GEAR_TOPS = [0, 9, 17, 27, 39, 53, 70]


def _speed_at(t):
    """Piecewise drive cycle in m/s over CYCLE_S seconds."""
    p = (t % CYCLE_S) / CYCLE_S
    if p < 0.45:  # accelerate
        return 60 * math.sin(p / 0.45 * math.pi / 2)
    if p < 0.65:  # cruise with a little wobble
        return 60 + 2 * math.sin(t * 2)
    if p < 0.85:  # brake
        return 60 * (1 - (p - 0.65) / 0.2)
    return 0.0  # stopped


def _distance_at(t):
    """Metres driven by time t, the integral of _speed_at (closed form per phase; the cruise wobble, about a metre, is left out)."""
    cycles, local = divmod(t, CYCLE_S)
    accel_s, cruise_s, brake_s = 0.45 * CYCLE_S, 0.2 * CYCLE_S, 0.2 * CYCLE_S

    def accelerate(x):
        return 60 * (2 * accel_s / math.pi) * (1 - math.cos(math.pi * x / (2 * accel_s)))

    def brake(x):
        return 60 * (x - x * x / (2 * brake_s))

    per_cycle = accelerate(accel_s) + 60 * cruise_s + brake(brake_s)
    d = cycles * per_cycle
    d += accelerate(min(local, accel_s))
    d += 60 * max(0.0, min(local - accel_s, cruise_s))
    d += brake(max(0.0, min(local - accel_s - cruise_s, brake_s)))
    return d


def _gear_for(speed):
    for gear in range(1, len(GEAR_TOPS)):
        if speed <= GEAR_TOPS[gear]:
            return gear
    return len(GEAR_TOPS) - 1


def vehicle_at(t, vehicles, period):
    """Which simulated vehicle is "being driven" t seconds in: they take turns, `period` seconds each."""
    return vehicles[int(t // period) % len(vehicles)]


def make_frame(t, frame_n, vehicle="simulator"):
    speed = _speed_at(t)
    prev = _speed_at(t - 0.1)
    accel = (speed - prev) / 0.1
    gear = _gear_for(speed) if speed > 0.3 else 0
    if gear:
        lo, hi = GEAR_TOPS[gear - 1], GEAR_TOPS[gear]
        rpm = 1800 + (MAX_RPM - 2300) * min(1.0, max(0.0, (speed - lo) / max(1.0, hi - lo)))
    else:
        rpm = IDLE_RPM + 20 * math.sin(t * 9)
    throttle = max(0.0, min(1.0, accel / 4 + 0.15)) if gear else 0.0
    brake = max(0.0, min(1.0, -accel / 6))
    warmup = min(1.0, t / 60)
    left = int(t * 1.6) % 2 if 8 < (t % 30) < 14 else 0
    right = int(t * 1.6) % 2 if 22 < (t % 30) < 26 else 0
    hot = 120 * brake + 40
    steer = 0.7 * math.sin(t / 2.2) * min(1.0, speed / 25)  # weaving, more as the car goes faster
    fuel = max(0.0, 0.8 - _distance_at(t) * 1.6e-6)  # 8 L per 100 km driven, from a 50 L tank
    return {
        "v": PROTOCOL_VERSION,
        "type": "frame",
        "n": frame_n,
        "vehicle": f"/vehicles/{vehicle}/",
        "electrics": {
            "rpm": rpm,
            "maxrpm": MAX_RPM,
            "idlerpm": IDLE_RPM,
            "wheelspeed": speed,
            "airspeed": speed,
            "gear": ("N" if gear == 0 else str(gear)),
            "gearIndex": gear,
            "throttle": throttle,
            "brake": brake,
            "clutch": 0,
            "throttle_input": throttle,
            "brake_input": brake,
            "clutch_input": 0,
            "steering_input": steer,
            "accXSmooth": -steer * speed * 0.25,  # the car's sideways acceleration in m/s^2; negative in a right-hand corner (measured in the real game)
            "accYSmooth": -accel,  # m/s^2 along the car; negative when accelerating, positive when braking (measured in the real game)
            "accZSmooth": -9.81,
            "fuel": fuel,
            "fuelVolume": fuel * 50,  # litres
            "fuelCapacity": 50,
            "watertemp": 40 + 52 * warmup + 2 * math.sin(t / 3),
            "oiltemp": 35 + 60 * warmup,
            "engineLoad": throttle,
            "ignition": True,
            "engineRunning": 1,
            "signal_L": left,
            "signal_R": right,
            "hazard": 1 if 34 < (t % 50) < 38 and int(t * 1.6) % 2 else 0,
            "escActive": brake > 0.9 or 2 < (t % 40) < 3,
            "odometer": 48213.0 + _distance_at(t),  # metres, as the real channel is assumed to be
            "highbeam": 1 if 30 < (t % 40) < 38 else 0,
            "lowbeam": 1,
            "parkingbrake": 1 if speed < 0.3 and (t % CYCLE_S) / CYCLE_S > 0.9 else 0,
            "absActive": brake > 0.85,
            "checkengine": (t % 50) > 44,
            "lowfuel": fuel < 0.1,
            "oil": 0,
            "wheelThermals": {w: {"brakeCoreTemperature": hot, "brakeSurfaceTemperature": hot + 10} for w in ("FL", "FR", "RL", "RR")},
        },
    }


WHEELS = ("FL", "FR", "RL", "RR")
ZONES = ("FL", "FR", "ML", "MR", "RL", "RR")


def make_slow(t, vehicle="simulator"):
    """The slow data (tyres, damage, component states) in the shape the Lua mod sends it. A repeating story over a minute:
    tyres warm up with use, the front-right tyre punctures, the body takes two impacts, then the car is "repaired"."""
    cycle = t % 60
    speed = _speed_at(t)
    warm = min(1.0, (t % 600) / 90)
    brake = max(0.0, min(1.0, -(speed - _speed_at(t - 0.1)) / 0.6))
    tires = {}
    for i, w in enumerate(WHEELS):
        front = w in ("FL", "FR")
        temp = 22 + 55 * warm * (0.75 + 0.25 * math.sin(t / 7 + i)) + (10 if front else 0) * (speed / 60) + 25 * brake * (1 if front else 0.5)
        flat = w == "FR" and 40 < cycle < 52
        pressure = 5.0 if flat else 215 + (temp - 22) * 0.9 + 4 * math.sin(t / 5 + i)
        tires[w] = {"temp": temp, "coreTemp": temp + 8, "pressure": pressure, "deflated": flat, "punctured": flat,
                    "load": 3600 + 400 * math.sin(t / 3 + i), "slip": abs(math.sin(t / 2 + i)) * 0.4}
    zones = {z: 0.0 for z in ZONES}
    if cycle > 18:
        zones["FL"] = min(0.12, (cycle - 18) / 160)  # the real game: a wreck's worst zone is only about 0.12
        zones["ML"] = zones["FL"] * 0.3
    if cycle > 34:
        zones["RR"] = min(0.15, (cycle - 34) / 70)
        zones["MR"] = zones["RR"] * 0.4
    # The same story as the real game: a scrape dents the car (5-6% of beams deformed, none broken); a wreck has
    # about a quarter of its beams broken.
    total_zone_damage = sum(zones.values())
    broken_pct = max(0.0, total_zone_damage - 0.06) * 75
    deformed_pct = (6 + total_zone_damage * 60) if total_zone_damage > 0 else 0.0
    broken = broken_pct / 100 * 2400
    deformed = deformed_pct / 100 * 2400
    rpm_high = (_speed_at(t) / 70) > 0.93
    hot = warm > 0.95 and speed > 40
    state = {
        "engine": {"oilOverheating": hot, "coolantOverheating": hot and cycle > 25, "overRevDanger": rpm_high, "radiatorLeak": zones["FL"] > 0.06,
                   "engineReducedTorque": zones["FL"] > 0.09, "oilLevelCritical": False},
        "wheels": {"brakeOverHeatFL": min(1.0, brake * 1.4), "brakeFL": False, "brakeFR": False},
    }
    return {
        "v": PROTOCOL_VERSION,
        "type": "slow",
        "vehicle": f"/vehicles/{vehicle}/",
        "tire": tires,
        "damage": {"total": sum(zones.values()) * 1500, "zone": zones, "beamsTotal": 2400, "beamsBroken": int(broken),
                   "beamsDeformed": int(deformed), "brokenPct": broken_pct, "deformedPct": deformed_pct},
        "state": state,
        "energy": {"electric": False, "liquid": True},  # a petrol car, so the trip computer shows litres
    }


# The simulated car goes round a circular track (so the lap timer can be tried without the game): 160 m radius, counter-clockwise, the
# distance it drives in _distance_at read off round the circle. Origin in Wellington, like the real captures.
TRACK_LAT, TRACK_LON = -41.2865, 174.7762
TRACK_RADIUS_M = 160.0
EARTH_M = 6371008.8


def _ll(x, y):
    """Metres east and north of the track's centre -> (lat, lon)."""
    return (TRACK_LAT + math.degrees(y / EARTH_M), TRACK_LON + math.degrees(x / (EARTH_M * math.cos(math.radians(TRACK_LAT)))))


def track_position(t):
    """Where the simulated car is t seconds in, as a (lat, lon, heading degrees)."""
    angle = _distance_at(t) / TRACK_RADIUS_M
    x, y = TRACK_RADIUS_M * math.cos(angle), TRACK_RADIUS_M * math.sin(angle)
    # Counter-clockwise on a map: facing north (heading 0) at angle 0, then turning towards the west, so the heading falls as the angle grows.
    return (*_ll(x, y), (-math.degrees(angle)) % 360)


def make_live(t):
    """A `geolive` packet (ten a second in the game): the car's place on the track and its speed."""
    lat, lon, heading = track_position(t)
    return {"v": PROTOCOL_VERSION, "type": "geolive", "rt": t, "lat": lat, "lon": lon, "hdg": heading, "spd": _speed_at(t)}


def track_circuit(sectors=True):
    """A circuit for the simulated track, in the shape the circuits API takes: the start line across the road at angle 0 (due east of
    the centre, where the car is heading north), and a sector line on the far side."""
    r = TRACK_RADIUS_M
    gate = lambda a, b: {"a": list(_ll(*a)), "b": list(_ll(*b))}  # noqa: E731
    circuit = {"name": "Simulated track", "kind": "circuit", "enabled": True, "start": gate((r - 25, 0), (r + 25, 0)), "sectors": []}
    if sectors:
        circuit["sectors"] = [gate((-(r - 25), 0), (-(r + 25), 0))]
    return circuit


def limit_at(t):
    """The speed limit the simulated road has t seconds in, as the game's feed gives it: (m/s, posted?) or None where none is known.
    Some stretches are posted, some only a guess from the kind of road, and the stop has none, so every state can be seen."""
    p = (t % CYCLE_S) / CYCLE_S
    if p < 0.15:
        return 13.89, True
    if p < 0.30:
        return 22.22, False
    if p < 0.65:
        return 27.78, True
    if p < 0.85:
        return 13.89, True
    return None


# The simulated route and its steps, in the shape BeamNG.Earth gives them (a real one is in docs/route-steps-sample.json).
ROUTE_LENGTH_M = 4200
ROUTE_STEPS = [
    {"type": "depart", "name": "Hutt Road", "offset": 0, "distance": 600},
    {"type": "turn", "modifier": "left", "name": "Oriental Parade", "offset": 600, "distance": 900},
    {"type": "roundabout", "exit": 2, "offset": 1500, "distance": 30},
    {"type": "exit roundabout", "name": "Evans Bay Parade", "offset": 1530, "distance": 870},
    {"type": "turn", "modifier": "slight right", "name": "Rongotai Road", "offset": 2400, "distance": 1500},
    {"type": "fork", "modifier": "slight left", "name": "Cobham Drive", "offset": 3900, "distance": 300},
    {"type": "arrive", "modifier": "left", "offset": 4200, "distance": 0},
]


SIM_STOP_M = 1800.0  # how far along the simulated route its stop is


def make_geo(t):
    """The world packets (limit, route progress, and now and then the route's steps), once a second, in the shape trips.lua sends them. The route is set for the
    first 30 s of each minute and then cleared, with a short spell off the route."""
    limit = limit_at(t)
    info = {"v": PROTOCOL_VERSION, "type": "geoinfo", "rt": t}
    if limit is not None:
        info["limit"] = {"ms": limit[0], "mapped": limit[1], "unit": "kmh"}
    route = {"v": PROTOCOL_VERSION, "type": "georoute", "rt": t}
    cycle = t % 60
    packets = [info, route]
    if cycle < 30:
        left = ROUTE_LENGTH_M - cycle * 120
        route["gps"] = {"remaining": left, "eta": left / 14.0, "off": 35.0 if 12 < cycle < 16 else 0.0, "name": "Oriental Bay", "planning": False,
                        "routeVersion": 1 + int(t // 60)}
        if ROUTE_LENGTH_M - left < SIM_STOP_M:  # a stop on the way, until the simulated car has passed it
            route["gps"]["stops"] = {"ahead": 1, "name": "Evans Bay Fuel", "legs": [[SIM_STOP_M, SIM_STOP_M / 14.0], [ROUTE_LENGTH_M - SIM_STOP_M, (ROUTE_LENGTH_M - SIM_STOP_M) / 14.0]]}
        if int(cycle) % 20 == 0:  # the steps come when the route appears and now and then after, like trips.lua
            packets.append({"v": PROTOCOL_VERSION, "type": "geosteps", "rt": t, "version": 1 + int(t // 60), "dist": ROUTE_LENGTH_M, "steps": ROUTE_STEPS})
    return tuple(packets)


async def run(hub, rate=RATE_HZ, vehicles=("simulator",), period=15.0):
    """Feed the hub forever. With several `vehicles`, they take turns, so vehicle-switching can be tried without the game."""
    start = time.monotonic()
    n = 0
    interval = 1.0 / rate
    while True:
        n += 1
        elapsed = time.monotonic() - start
        vehicle = vehicle_at(elapsed, vehicles, period)
        hub.ingest_packet(make_frame(elapsed, n, vehicle))
        if n % 8 == 0:  # ~4 per second, like the mod
            hub.ingest_packet(make_slow(elapsed, vehicle))
        if n % RATE_HZ == 0:  # once a second, like trips.lua
            for packet in make_geo(elapsed):
                hub.ingest_packet(packet)
        if n % (RATE_HZ // 10) == 0:  # ten a second, like the live position in the game
            hub.ingest_packet(make_live(elapsed))
        await asyncio.sleep(interval)

"""Planning a GPS route from the Trips page: the stops, saved destinations, sending the route to the game, and finding fuel nearby.

BeamNG.Earth plans the route (on the roads, in its own app); ThrashDash only says where to go. The Trips page's Route view collects stops (a
map click, a saved destination, or a place from an earlier trip) and the relay passes them to the game's extension (trips.lua), which hands
them to BeamNG.Earth's API (`gpsSet` for one stop, `gpsSetRoute` for several, the last the destination). The game answers whether it took
them; the planned route then comes back like any other (the line and its progress: hub.route_state()) and is drawn on the maps.

Only this computer may set or clear a route (the API's write rule, like every change), and the relay only ever sends plain places.
"""
import asyncio
import json
import math
import re
import secrets
from pathlib import Path

from .store import NotFound, _atomic_write_json
from .validate import ValidationError

MAX_STOPS = 11  # what BeamNG.Earth's gpsSetRoute takes: up to 10 stops on the way and the destination
MAX_NAME = 60
MAX_DESTINATIONS = 100
MAX_DESTINATION_NAME = 40
DESTINATION_ID_RE = re.compile(r"^d[0-9a-f]{8}$")
ACK_WAIT_S = 3.0  # how long to wait for the game to say whether it took a route
FUEL_WAIT_S = 8.0  # how long to wait for the whole answer to a search for fuel (BeamNG.Earth may have to fetch map data first)
EARTH_M = 6371008.8
NOT_CONNECTED = "The game is not connected: start BeamNG, load the BeamNG.Earth level and drive a moment, then try again."


def _clean_text(value, limit, what):
    if not isinstance(value, str):
        raise ValidationError(f"{what}: must be text")
    return " ".join("".join(c if c.isprintable() else " " for c in value).split())[:limit].strip()


def clean_place(raw, what="place"):
    """{"lat", "lon", "name"?} as stored or sent: numbers on Earth (to about a decimetre), a short one-line name. Raises ValidationError."""
    if not isinstance(raw, dict):
        raise ValidationError(f"{what}: must be a place with lat and lon")
    out = {}
    for key, low, high in (("lat", -90.0, 90.0), ("lon", -180.0, 180.0)):
        v = raw.get(key)
        if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v or not low <= v <= high:
            raise ValidationError(f"{what}.{key}: must be a number from {low:g} to {high:g}")
        out[key] = round(float(v), 6)
    if raw.get("name") not in (None, ""):
        name = _clean_text(raw["name"], MAX_NAME, f"{what}.name")
        if name:
            out["name"] = name
    return out


def clean_stops(raw):
    """The stops of a route: one to MAX_STOPS places, the last the destination. Raises ValidationError."""
    if not isinstance(raw, list) or not 1 <= len(raw) <= MAX_STOPS:
        raise ValidationError(f"stops: give 1 to {MAX_STOPS} places, the last the destination")
    return [clean_place(stop, f"stops[{i}]") for i, stop in enumerate(raw)]


class DestinationStore:
    """Saved destinations: a name and a place, kept in destinations.json (written whole through a temporary file)."""

    def __init__(self, path):
        self.path = Path(path) if path is not None else None
        self._items = self._load()

    def _load(self):
        if self.path is None:
            return []
        try:
            with open(self.path, encoding="utf-8") as f:
                raw = json.load(f)
        except (OSError, ValueError):
            return []  # missing or damaged: start empty rather than break ThrashDash
        items, seen = [], set()
        for item in raw.get("destinations", []) if isinstance(raw, dict) else []:
            try:
                dest_id = item["id"]
                if not DESTINATION_ID_RE.match(dest_id) or dest_id in seen:
                    continue
                place = clean_place(item)
                name = _clean_text(item["name"], MAX_DESTINATION_NAME, "name")
                if name:
                    items.append({"id": dest_id, "name": name, **{k: place[k] for k in ("lat", "lon")}})
                    seen.add(dest_id)
            except (KeyError, TypeError, ValidationError):
                continue  # one damaged entry must not lose the others
        return items[:MAX_DESTINATIONS]

    def _commit(self, items):
        if self.path is not None:
            _atomic_write_json(self.path, {"destinations": items})
        self._items = items

    def list(self):
        return [dict(item) for item in self._items]

    def create(self, name, lat, lon):
        """Save a place under a name (a name already in use gets "(2)" and so on). Returns the saved destination."""
        if len(self._items) >= MAX_DESTINATIONS:
            raise ValidationError(f"too many saved destinations (limit {MAX_DESTINATIONS}): delete one first")
        place = clean_place({"lat": lat, "lon": lon})
        name = _clean_text(name, MAX_DESTINATION_NAME, "name")
        if not name:
            raise ValidationError("name: must not be empty")
        taken = {d["name"].lower() for d in self._items}
        unique, n = name, 2
        while unique.lower() in taken:
            unique = f"{name} ({n})"
            n += 1
        item = {"id": "d" + secrets.token_hex(4), "name": unique, **place}
        self._commit([*self._items, item])
        return dict(item)

    def delete(self, dest_id):
        items = [d for d in self._items if d["id"] != dest_id]
        if len(items) == len(self._items):
            raise NotFound(dest_id)
        self._commit(items)


class RoutePlanner:
    """Sends routes to the game and says whether it took them. One command at a time: the game's answers carry no number, so two at once
    could be mistaken for each other."""

    def __init__(self, notices, hub, wait_s=ACK_WAIT_S, sleep=asyncio.sleep):
        self.notices = notices
        self.hub = hub
        self.wait_s = wait_s
        self._sleep = sleep
        self._lock = asyncio.Lock()

    async def set_route(self, stops):
        """Set the route through `stops` (cleaned here). Returns {"ok": bool, "why": str | None}; raises ValidationError for bad stops."""
        return await self._command({"type": "route", "action": "set", "stops": clean_stops(stops)})

    async def clear_route(self):
        return await self._command({"type": "route", "action": "clear"})

    async def _command(self, payload):
        if self.notices is None:
            return {"ok": False, "why": "This run of ThrashDash cannot talk to the game."}
        async with self._lock:
            before = self.hub.route_ack["seq"]
            if not self.notices.send_to_game(payload):
                return {"ok": False, "why": NOT_CONNECTED}
            waited = 0.0
            while waited < self.wait_s:
                if self.hub.route_ack["seq"] != before:
                    ack = self.hub.route_ack
                    return {"ok": bool(ack["ok"]), "why": ack["why"]}
                await self._sleep(0.05)
                waited += 0.05
            return {"ok": False, "why": "The game did not answer. Is the BeamNG.Earth level loaded, and BeamNG restarted since ThrashDash was updated?"}


def metres_between(a, b):
    """The straight-line distance between two {"lat", "lon"} places (good over the few tens of kilometres a search covers)."""
    across = math.radians(b["lon"] - a["lon"]) * math.cos(math.radians((a["lat"] + b["lat"]) / 2))
    return math.hypot(across, math.radians(b["lat"] - a["lat"])) * EARTH_M


class FuelFinder:
    """Asks the game for the filling stations and chargers near the car (BeamNG.Earth's own place data). One search at a time."""

    def __init__(self, notices, hub, wait_s=FUEL_WAIT_S, sleep=asyncio.sleep):
        self.notices = notices
        self.hub = hub
        self.wait_s = wait_s
        self._sleep = sleep
        self._lock = asyncio.Lock()
        self._number = secrets.randbelow(1_000_000)  # (so an answer to a search of an earlier run is not taken for this one's)

    async def search(self):
        """{"ok": True, "places": [{name, kind, lat, lon, metres}...] nearest first, "done": bool} or {"ok": False, "why": str}.
        `done` is False when BeamNG.Earth was still fetching map data after the wait: asking again later may find more."""
        if self.notices is None:
            return {"ok": False, "why": "This run of ThrashDash cannot talk to the game."}
        async with self._lock:
            self._number += 1
            number = self._number
            if not self.notices.send_to_game({"type": "places", "action": "fuel", "id": number}):
                return {"ok": False, "why": NOT_CONNECTED}
            answer, waited = None, 0.0
            while waited < self.wait_s:
                latest = self.hub.fuel
                if latest is not None and latest["id"] == number:
                    answer = latest
                    if answer["done"]:
                        break
                await self._sleep(0.05)
                waited += 0.05
        if answer is None:
            return {"ok": False, "why": "The game did not answer. Is the BeamNG.Earth level loaded, and BeamNG restarted since ThrashDash was updated?"}
        if answer["why"] and not answer["places"]:
            return {"ok": False, "why": f"The search failed: {answer['why']}."}
        origin = answer["origin"]
        places = [{**p, "metres": round(metres_between(origin, p)) if origin else None} for p in answer["places"]]
        places.sort(key=lambda p: p["metres"] if p["metres"] is not None else 0)
        return {"ok": True, "places": places, "done": answer["done"]}

"""Lap and sector timing.

BeamNG has no lap timer in the world BeamNG.Earth streams, so ThrashDash times laps itself, from where the car is (ten positions a
second, in latitude and longitude). A **circuit** is a few lines drawn across the road:

* a **start line** (and, for a closed circuit, that same line is the finish: each time the car crosses it a lap ends and the next begins);
* for a **stage** (a point to point run), a separate **finish line**: the run starts at the start line and ends at the finish line;
* optional **sector lines** between them, which split a lap into sectors (two lines make three sectors).

A line is two points. It counts only when crossed **one way**: from the right of the line as it runs from its first point to its second
to the left of it. (The Trips page draws every line from the direction you were driving, so you never think about this; it keeps a
car coming back the other way, or a neighbouring road, from starting a lap.)

How exact is it? The car's position arrives ten times a second, which is 3 m apart at 100 km/h and 6 m at 200 km/h. The moment a line is
crossed is worked out between the two positions either side of it (the crossing's place along the step, times the step's time), so the
timing does not wobble by whole tenths of a second. It is still a position fed to a clock by a game: **good to a few hundredths of a
second, not to the millisecond**, and the dashboard shows tenths for that reason.

Rules, kept here so they are in one place:

* The first crossing of a start line only *starts* the timing: that lap is not timed (it began wherever you were). The first timed lap
  is the one after.
* A lap needs every sector line crossed in order. One missed (a shortcut, or a line the car did not cross) still records the lap, but
  marked not valid, and it never counts as a best.
* Time is the game's own (it stops when the game is paused, and runs slow in slow motion), so pausing mid-lap does not lengthen it.
* A reset (R), a teleport, a change of car or level, a position that jumps, or the game going quiet for 2 s **cancels the run**: the lap
  in progress is dropped, and the next crossing of the start line starts again. A lap a reset helped is not a lap.
* A line crossed again within 3 s of the last is ignored (no lap is that short; it is the same crossing seen twice).
* Bests are kept **per vehicle**: a hatchback and a supercar do not share a best lap.
* Changing a circuit's lines clears its laps (they were timed over different lines). Renaming or switching it off does not.

Circuits are kept in circuits.json and each circuit's laps in laps/<id>.jsonl (one line per lap, written the moment it ends), with
that circuit's bests and the best lap's trace (distance against time, for the live gap) in laps/<id>.best.json.
"""
import bisect
import json
import math
import re
import secrets
import time
from pathlib import Path

from .store import NotFound, _atomic_write_json
from .validate import ValidationError

EARTH_RADIUS_M = 6371008.8
MIN_GATE_M, MAX_GATE_M = 3.0, 200.0  # how long a line may be: shorter is a point, longer is a road away
MAX_SECTOR_LINES = 8  # (so at most 9 sectors)
MAX_CIRCUITS = 50
MAX_NAME = 40
MIN_LAP_S = 3.0
MAX_GAP_S = 2.0  # no position for longer than this and the run is dropped
MAX_SPEED_MS = 150.0  # an implied speed above this, over at least JUMP_MIN_M, is a jump (a reset or a teleport), not driving
JUMP_MIN_M = 30.0
TRACE_STEP_M = 5.0
MAX_TRACE = 4000  # points kept of the best lap (a 20 km lap at 5 m)
MAX_LAP_LINES = 5000  # laps kept per circuit; the oldest go first
FRESH_S = 2.0  # lap channels are dropped when no position has arrived for this long
CIRCUIT_ID_RE = re.compile(r"^k[0-9a-f]{8}$")
KINDS = ("circuit", "stage")
VEHICLE_RE = re.compile(r"/vehicles/([^/]+)/")
# Events from the game that end a run: the car was moved, or the world changed under it.
CANCEL_EVENTS = {"reset", "teleported", "vehicleSwitched", "levelStart", "levelEnd"}


# ---- geometry ------------------------------------------------------------------------------------------------------------------


def _xy(lat, lon, lat0, lon0):
    """Metres east and north of (lat0, lon0): a flat map, which is exact enough over the few hundred metres between two positions."""
    return (math.radians(lon - lon0) * EARTH_RADIUS_M * math.cos(math.radians(lat0)), math.radians(lat - lat0) * EARTH_RADIUS_M)


def distance_m(a, b):
    """Metres between two (lat, lon) points."""
    x, y = _xy(b[0], b[1], a[0], a[1])
    return math.hypot(x, y)


def cross_gate(p0, p1, gate):
    """Where along the step p0 -> p1 (0 up to, not including, 1) it crosses `gate` the right way, or None. The right way is from the
    right of the line a -> b to the left of it. Points are (lat, lon); `gate` is {"a": (lat, lon), "b": (lat, lon)}."""
    rx, ry = _xy(p1[0], p1[1], p0[0], p0[1])
    ax, ay = _xy(gate["a"][0], gate["a"][1], p0[0], p0[1])
    bx, by = _xy(gate["b"][0], gate["b"][1], p0[0], p0[1])
    sx, sy = bx - ax, by - ay
    denom = rx * sy - ry * sx  # r x s
    if denom >= 0:  # parallel, or the wrong way across
        return None
    t = (ax * sy - ay * sx) / denom
    u = (ax * ry - ay * rx) / denom
    if 0.0 <= t < 1.0 and 0.0 <= u <= 1.0:
        return t
    return None


def vehicle_key(path):
    """"/vehicles/etkc/" -> "etkc"; anything else -> "unknown"."""
    m = VEHICLE_RE.search(path) if isinstance(path, str) else None
    return m.group(1) if m else "unknown"


# ---- circuits ------------------------------------------------------------------------------------------------------------------


def _clean_point(value, what):
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValidationError(f"{what}: must be [latitude, longitude]")
    lat, lon = value
    for v in (lat, lon):
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
            raise ValidationError(f"{what}: must be numbers")
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        raise ValidationError(f"{what}: not a place on Earth")
    return [round(float(lat), 7), round(float(lon), 7)]  # (7 places is a centimetre or so)


def clean_gate(raw, what):
    if not isinstance(raw, dict):
        raise ValidationError(f"{what}: must be a line with two points, a and b")
    a, b = _clean_point(raw.get("a"), f"{what}.a"), _clean_point(raw.get("b"), f"{what}.b")
    length = distance_m(a, b)
    if not MIN_GATE_M <= length <= MAX_GATE_M:
        raise ValidationError(f"{what}: a line must be between {MIN_GATE_M:g} and {MAX_GATE_M:g} metres long (this one is {length:.0f})")
    return {"a": a, "b": b}


def clean_name(value):
    if not isinstance(value, str):
        raise ValidationError("name: must be text")
    name = " ".join("".join(c if c.isprintable() else " " for c in value).split())[:MAX_NAME].strip()
    if not name:
        raise ValidationError("name: must not be empty")
    return name


def clean_circuit(raw):
    """A circuit as stored: {name, kind, enabled, start, finish (a stage only), sectors: [line...]}. Raises ValidationError."""
    if not isinstance(raw, dict):
        raise ValidationError("a circuit must be an object")
    kind = raw.get("kind", "circuit")
    if kind not in KINDS:
        raise ValidationError('kind: must be "circuit" or "stage"')
    enabled = raw.get("enabled", True)
    if not isinstance(enabled, bool):
        raise ValidationError("enabled: must be true or false")
    circuit = {"name": clean_name(raw.get("name")), "kind": kind, "enabled": enabled, "start": clean_gate(raw.get("start"), "start")}
    if kind == "stage":
        circuit["finish"] = clean_gate(raw.get("finish"), "finish")
    sectors = raw.get("sectors", [])
    if not isinstance(sectors, list) or len(sectors) > MAX_SECTOR_LINES:
        raise ValidationError(f"sectors: a list of at most {MAX_SECTOR_LINES} lines")
    circuit["sectors"] = [clean_gate(g, f"sectors[{i}]") for i, g in enumerate(sectors)]
    return circuit


def geometry(circuit):
    """What the laps depend on: if this changes, old laps no longer mean anything."""
    return json.dumps([circuit["kind"], circuit["start"], circuit.get("finish"), circuit["sectors"]], sort_keys=True)


class CircuitStore:
    """The saved circuits, kept in memory and written whole through a temporary file on every change. Nothing here reads the file while
    timing: the timer asks for `enabled()`, a list that is replaced (never changed in place) when anything changes."""

    def __init__(self, path, laps=None):
        self.path = Path(path) if path is not None else None
        self.laps = laps
        self.version = 0  # bumped by every change: a timer comparing it knows when its circuits changed under it
        self._items = self._load()
        self._enabled = [c for c in self._items if c["enabled"]]

    def _load(self):
        if self.path is None:
            return []
        try:
            with open(self.path, encoding="utf-8") as f:
                raw = json.load(f)
        except (OSError, ValueError):
            return []  # missing or damaged: start empty rather than break ThrashDash
        items, seen = [], set()
        for item in raw.get("circuits", []) if isinstance(raw, dict) else []:
            try:
                circuit_id = item["id"]
                if not CIRCUIT_ID_RE.match(circuit_id) or circuit_id in seen:
                    continue
                items.append({"id": circuit_id, **clean_circuit(item)})
                seen.add(circuit_id)
            except (KeyError, TypeError, ValidationError):
                continue  # one damaged circuit must not lose the others
        return items[:MAX_CIRCUITS]

    def _commit(self, items):
        if self.path is not None:
            _atomic_write_json(self.path, {"circuits": items})
        self._items = items
        self._enabled = [c for c in items if c["enabled"]]
        self.version += 1

    def list(self):
        return json.loads(json.dumps(self._items))  # (a copy: callers may not change the stored ones)

    def enabled(self):
        return self._enabled

    def get(self, circuit_id):
        for c in self._items:
            if c["id"] == circuit_id:
                return json.loads(json.dumps(c))
        raise NotFound(circuit_id)

    @staticmethod
    def _unique_name(name, items, ignore=None):
        taken = {c["name"].lower() for c in items if c["id"] != ignore}
        if name.lower() not in taken:
            return name
        n = 2
        while f"{name} ({n})".lower() in taken:
            n += 1
        return f"{name} ({n})"

    def create(self, raw):
        if len(self._items) >= MAX_CIRCUITS:
            raise ValidationError(f"too many circuits (limit {MAX_CIRCUITS}): delete one first")
        circuit = clean_circuit(raw)
        circuit["name"] = self._unique_name(circuit["name"], self._items)
        circuit_id = "k" + secrets.token_hex(4)
        self._commit([*self._items, {"id": circuit_id, **circuit}])
        return circuit_id

    def update(self, circuit_id, raw):
        """Replace a circuit. Returns True when its lines changed, in which case its laps were cleared."""
        old = next((c for c in self._items if c["id"] == circuit_id), None)
        if old is None:
            raise NotFound(circuit_id)
        circuit = clean_circuit(raw)
        circuit["name"] = self._unique_name(circuit["name"], self._items, ignore=circuit_id)
        changed = geometry(circuit) != geometry(old)
        self._commit([{"id": circuit_id, **circuit} if c["id"] == circuit_id else c for c in self._items])
        if changed and self.laps is not None:
            self.laps.clear(circuit_id)
        return changed

    def delete(self, circuit_id):
        items = [c for c in self._items if c["id"] != circuit_id]
        if len(items) == len(self._items):
            raise NotFound(circuit_id)
        self._commit(items)
        if self.laps is not None:
            self.laps.clear(circuit_id)


# ---- laps ----------------------------------------------------------------------------------------------------------------------


class LapStore:
    """Finished laps and the bests, per circuit. `folder` None keeps them in memory only (a simulated run is not worth keeping)."""

    def __init__(self, folder=None):
        self.folder = Path(folder) if folder is not None else None
        self._laps = {}  # circuit id -> [lap...], oldest first
        self._bests = {}  # circuit id -> {vehicle: {"t", "s", "ts", "trace"}}

    def _lap_path(self, circuit_id):
        return self.folder / f"{circuit_id}.jsonl"

    def _best_path(self, circuit_id):
        return self.folder / f"{circuit_id}.best.json"

    def _load_laps(self, circuit_id):
        if circuit_id in self._laps:
            return self._laps[circuit_id]
        laps = []
        if self.folder is not None and CIRCUIT_ID_RE.match(circuit_id):
            try:
                with open(self._lap_path(circuit_id), encoding="utf-8") as f:
                    for line in f:
                        try:
                            lap = json.loads(line)
                        except ValueError:
                            continue  # a half-written last line after a crash
                        if isinstance(lap, dict) and isinstance(lap.get("t"), (int, float)):
                            laps.append(lap)
            except OSError:
                pass
        self._laps[circuit_id] = laps[-MAX_LAP_LINES:]
        return self._laps[circuit_id]

    def _load_bests(self, circuit_id):
        if circuit_id in self._bests:
            return self._bests[circuit_id]
        bests = {}
        if self.folder is not None and CIRCUIT_ID_RE.match(circuit_id):
            try:
                with open(self._best_path(circuit_id), encoding="utf-8") as f:
                    raw = json.load(f)
                if isinstance(raw, dict):
                    bests = {k: v for k, v in raw.items() if isinstance(k, str) and isinstance(v, dict) and isinstance(v.get("t"), (int, float))}
            except (OSError, ValueError):
                pass
        self._bests[circuit_id] = bests
        return bests

    def best(self, circuit_id, vehicle):
        return self._load_bests(circuit_id).get(vehicle)

    def record(self, circuit_id, vehicle, lap, trace):
        """Keep a finished lap. `lap` is {n, t, s, ok, ts, r}. Returns {"newBest": bool, "sectorBests": [bool...]}: whether it is the
        vehicle's best lap, and which of its sectors are that vehicle's best."""
        lap = {**lap, "v": vehicle}
        laps = self._load_laps(circuit_id)
        laps.append(lap)
        if self.folder is not None:
            self.folder.mkdir(parents=True, exist_ok=True)
            with open(self._lap_path(circuit_id), "a", encoding="utf-8") as f:
                f.write(json.dumps(lap, separators=(",", ":"), allow_nan=False) + "\n")
        if len(laps) > MAX_LAP_LINES + 200:  # trim in batches, not on every lap
            del laps[: len(laps) - MAX_LAP_LINES]
            self._rewrite_laps(circuit_id)
        result = {"newBest": False, "sectorBests": [False] * len(lap["s"])}
        if not lap["ok"]:
            return result
        bests = self._load_bests(circuit_id)
        best = bests.get(vehicle)
        # (a best from before the circuit had its present number of sectors cannot be compared sector by sector)
        before = list(best["s"]) if best is not None and len(best.get("s", [])) == len(lap["s"]) else None
        sectors = list(before) if before is not None else list(lap["s"])
        for i, s in enumerate(lap["s"]):
            if before is None or s < before[i]:
                result["sectorBests"][i] = True
                sectors[i] = s
        result["newBest"] = best is None or lap["t"] < best["t"]
        if result["newBest"]:
            bests[vehicle] = {"t": lap["t"], "s": sectors, "ts": lap["ts"], "trace": trace}
        elif any(result["sectorBests"]):
            best["s"] = sectors
        else:
            return result
        if self.folder is not None:
            _atomic_write_json(self._best_path(circuit_id), bests)
        return result

    def _rewrite_laps(self, circuit_id):
        if self.folder is None:
            return
        text = "".join(json.dumps(lap, separators=(",", ":"), allow_nan=False) + "\n" for lap in self._laps[circuit_id])
        tmp = self._lap_path(circuit_id).with_suffix(".tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(self._lap_path(circuit_id))

    def laps(self, circuit_id, limit=300):
        return list(self._load_laps(circuit_id)[-limit:])

    def bests(self, circuit_id):
        """Each vehicle's best lap and sectors (not the trace, which is for the live gap and is large)."""
        return {v: {"t": b["t"], "s": b.get("s", []), "ts": b.get("ts")} for v, b in self._load_bests(circuit_id).items()}

    def clear(self, circuit_id):
        self._laps.pop(circuit_id, None)
        self._bests.pop(circuit_id, None)
        if self.folder is not None and CIRCUIT_ID_RE.match(circuit_id):
            for path in (self._lap_path(circuit_id), self._best_path(circuit_id)):
                try:
                    path.unlink()
                except OSError:
                    pass
        self._laps[circuit_id] = []
        self._bests[circuit_id] = {}


# ---- the timer -----------------------------------------------------------------------------------------------------------------


class _Run:
    __slots__ = ("circuit", "vehicle", "n", "start_t", "dist", "marks", "trace", "last_trace_d", "id")

    def __init__(self, circuit, vehicle, n, start_t, dist, run_id):
        self.circuit, self.vehicle, self.n, self.start_t, self.dist = circuit, vehicle, n, start_t, dist
        self.marks = []  # when each sector line was crossed (the game's clock)
        self.trace = [(0.0, 0.0)]  # (metres into the lap, seconds into the lap)
        self.last_trace_d = 0.0
        self.id = run_id


def _gap_at(trace, d):
    """Seconds the best lap took to reach `d` metres, from its trace; None if it is not that long."""
    if not trace or d < 0 or d > trace[-1][0]:
        return None
    i = bisect.bisect_left(trace, d, key=lambda point: point[0])
    if i == 0:
        return trace[0][1]
    (d0, t0), (d1, t1) = trace[i - 1], trace[min(i, len(trace) - 1)]
    return t0 if d1 <= d0 else t0 + (t1 - t0) * (d - d0) / (d1 - d0)


class LapTimer:
    """Takes the car's positions and turns them into laps. Pure logic: feed() is given every position, in order, and says whether
    anything it publishes changed."""

    def __init__(self, circuits, laps):
        self.circuits = circuits
        self.laps = laps
        self.run = None
        self.display = None  # the circuit last timed, kept for the dashboard after the run ends
        self.last = None  # the lap that ended last: {"t", "ok", "delta", "sectors", "isBest", "sectorBests"}
        self._prev = None
        self._seen_version = circuits.version
        self.last_vehicle = None  # the vehicle of the latest run: whose best the dashboard shows between runs
        self._attempts = {}  # circuit id -> runs started (a stage's number)
        self._run_counter = 0
        self.updated_at = None  # time.monotonic() of the latest position

    # -- what ends a run --

    def cancel(self):
        """Drop the lap in progress and forget the last position (the next step starts afresh)."""
        self.run = None
        self._prev = None

    def event(self, name):
        if name in CANCEL_EVENTS:
            self.cancel()

    # -- intake --

    def feed(self, lat, lon, rt, vehicle):
        """One position: where, when (GAME time in seconds, which stands still while the game is paused and runs slow in slow motion, so
        a pause mid-lap is not part of the lap) and in which vehicle (the path the game gave)."""
        self.updated_at = time.monotonic()
        if self.circuits.version != self._seen_version:  # circuits were edited: whatever was running may not exist now
            self._seen_version = self.circuits.version
            self.cancel()
            self.display = None
            self.last = None
        key = vehicle_key(vehicle)
        if self.run is not None and self.run.vehicle != key:
            self.cancel()
        cur = (lat, lon, rt)
        prev = self._prev
        if prev is not None and rt == prev[2]:
            return  # the same instant of game time: the game is paused, and the lap clock stands still with it
        self._prev = cur
        circuits = self.circuits.enabled()
        if prev is None or not circuits:
            if not circuits:
                self.run = None
            return
        dt = rt - prev[2]
        if not 0 < dt <= MAX_GAP_S:
            self.cancel()
            self._prev = cur
            return
        step = distance_m(prev, cur)
        if step >= JUMP_MIN_M and step / dt > MAX_SPEED_MS:
            self.cancel()
            self._prev = cur
            return
        self._step(prev, cur, dt, step, circuits, key)

    def _step(self, prev, cur, dt, step, circuits, key):
        run = self.run
        if run is not None and not any(c["id"] == run.circuit["id"] for c in circuits):
            run = self.run = None  # switched off while running
        if run is None:
            for c in circuits:
                t = cross_gate((prev[0], prev[1]), (cur[0], cur[1]), c["start"])
                if t is not None:
                    self._begin(c, key, prev[2] + t * dt, step * (1 - t))
                    return
            return
        c = run.circuit
        p0, p1 = (prev[0], prev[1]), (cur[0], cur[1])
        # sector lines, in order (only the next one counts: crossing a later one first is not a sector)
        index = len(run.marks)
        if index < len(c["sectors"]):
            t = cross_gate(p0, p1, c["sectors"][index])
            if t is not None and prev[2] + t * dt > run.start_t:
                run.marks.append(prev[2] + t * dt)
        finish = c["finish"] if c["kind"] == "stage" else c["start"]
        t = cross_gate(p0, p1, finish)
        if t is not None and prev[2] + t * dt - run.start_t >= MIN_LAP_S:
            self._finish(run, prev[2] + t * dt, run.dist + step * t)
            if c["kind"] == "circuit":  # the lap that ended is the start of the next, at the same instant
                self._begin(c, key, prev[2] + t * dt, step * (1 - t), n=run.n + 1, run_id=run.id)
            else:
                self.run = None
            return
        if c["kind"] == "stage" and prev[2] - run.start_t >= MIN_LAP_S:
            t = cross_gate(p0, p1, c["start"])  # back across the start line: a new attempt
            if t is not None:
                self._begin(c, key, prev[2] + t * dt, step * (1 - t))
                return
        run.dist += step
        if run.dist - run.last_trace_d >= TRACE_STEP_M and len(run.trace) < MAX_TRACE:
            run.trace.append((run.dist, cur[2] - run.start_t))
            run.last_trace_d = run.dist

    def _begin(self, circuit, key, start_t, dist, n=None, run_id=None):
        if n is None:
            self._attempts[circuit["id"]] = self._attempts.get(circuit["id"], 0) + 1
            n = 1 if circuit["kind"] == "circuit" else self._attempts[circuit["id"]]
        if run_id is None:
            self._run_counter += 1
            run_id = int(time.time()) * 1000 + self._run_counter % 1000
        self.run = _Run(circuit, key, n, start_t, dist, run_id)
        self.display = circuit
        self.last_vehicle = key

    def _finish(self, run, end_t, dist):
        c = run.circuit
        total = end_t - run.start_t
        marks = [m - run.start_t for m in run.marks]
        sectors = []
        if c["sectors"]:
            edges = [0.0, *marks, total]
            sectors = [round(edges[i + 1] - edges[i], 3) for i in range(len(edges) - 1)] if len(marks) == len(c["sectors"]) else []
        ok = len(marks) == len(c["sectors"])
        best_before = self.laps.best(c["id"], run.vehicle)
        trace = [*run.trace, (dist, total)]
        lap = {"n": run.n, "t": round(total, 3), "s": sectors, "ok": ok, "ts": int(time.time()), "r": run.id}
        result = self.laps.record(c["id"], run.vehicle, lap, [[round(d, 1), round(t, 3)] for d, t in trace])
        self.last = {"t": lap["t"], "ok": ok, "sectors": sectors, "isBest": result["newBest"], "sectorBests": result["sectorBests"],
                     "delta": round(total - best_before["t"], 3) if ok and best_before else None, "circuit": c["id"]}

    # -- what the dashboards see --

    def channels(self, now=None):
        """The lap channels: lap.active, lap.circuit, lap.number, lap.time, lap.delta, lap.sector, lap.sectors, lap.best, lap.last,
        lap.lastValid, lap.lastDelta, lap.lastIsBest, lap.s<N>.last / .best / .isBest. Empty when nothing is being timed, or when no
        position has arrived lately (a stale clock must not show)."""
        now = time.monotonic() if now is None else now
        circuit = self.display
        if circuit is None or self.updated_at is None or now - self.updated_at > FRESH_S or self._prev is None:
            return {}
        run = self.run
        out = {"lap.active": run is not None, "lap.circuit": circuit["name"]}
        vehicle = run.vehicle if run is not None else self.last_vehicle
        if run is not None:
            elapsed = max(0.0, self._prev[2] - run.start_t)
            out["lap.number"] = run.n
            out["lap.time"] = round(elapsed, 3)
            out["lap.sector"] = len(run.marks) + 1
            ahead = self.laps.best(circuit["id"], vehicle)
            ref = _gap_at(ahead.get("trace") or [], run.dist) if ahead else None
            if ref is not None:
                out["lap.delta"] = round(elapsed - ref, 3)
        if circuit["sectors"]:
            out["lap.sectors"] = len(circuit["sectors"]) + 1
        last = self.last if self.last and self.last["circuit"] == circuit["id"] else None
        best = self.laps.best(circuit["id"], vehicle) if vehicle else None
        if best:
            out["lap.best"] = best["t"]
        if last:
            out["lap.last"] = last["t"]
            out["lap.lastValid"] = last["ok"]
            out["lap.lastIsBest"] = last["isBest"]
            if last["delta"] is not None:
                out["lap.lastDelta"] = last["delta"]
            for i, s in enumerate(last["sectors"]):
                out[f"lap.s{i + 1}.last"] = s
                out[f"lap.s{i + 1}.isBest"] = bool(last["sectorBests"][i])
        if best:
            for i, s in enumerate(best.get("s", [])):
                out[f"lap.s{i + 1}.best"] = s
        return out

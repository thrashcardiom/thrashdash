"""Lap and sector timing: geometry, the timer's rules, the circuit and lap stores. A round test track of known size gives laps of known time."""
import json
import math
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from thrashdash import laps as L  # noqa: E402
from thrashdash.store import NotFound  # noqa: E402
from thrashdash.validate import ValidationError  # noqa: E402

LAT0, LON0 = -41.29, 174.78
CAR = "/vehicles/etkc/"


def ll(x, y):
    """Metres east/north of the origin -> (lat, lon)."""
    return (LAT0 + math.degrees(y / L.EARTH_RADIUS_M), LON0 + math.degrees(x / (L.EARTH_RADIUS_M * math.cos(math.radians(LAT0)))))


def gate(a, b):
    return {"a": list(ll(*a)), "b": list(ll(*b))}


RADIUS = 200.0
SPEED = 40.0
LAP_S = 2 * math.pi * RADIUS / SPEED  # 31.416 s


def point(angle):
    return RADIUS * math.cos(angle), RADIUS * math.sin(angle)


# Counter-clockwise from angle 0 (heading north there). Radial gates: one at angle 0 (the start), one at angle pi (a sector line, heading south there).
START = gate((170, 0), (230, 0))
SECTOR_PI = gate((-170, 0), (-230, 0))


def circuit(**kw):
    base = {"name": "Round", "kind": "circuit", "enabled": True, "start": START, "sectors": []}
    base.update(kw)
    return base


def drive(timer, t0, seconds, speed=SPEED, hz=10, ccw=True, offset=0.0, vehicle=CAR, start_angle=-0.3, radius=RADIUS):
    """Drive the round track: positions every 1/hz s from t0 for `seconds`. Returns the end time."""
    t = t0
    steps = int(seconds * hz)
    for i in range(steps + 1):
        t = t0 + i / hz
        angle = start_angle + (1 if ccw else -1) * (speed * (i / hz + offset) / radius)
        x, y = radius * math.cos(angle), radius * math.sin(angle)
        timer.feed(*ll(x, y), t, vehicle)
    return t


class Env:
    """A circuit store and timer on a temporary folder."""

    def __init__(self, *circuits):
        self.dir = tempfile.TemporaryDirectory()
        base = Path(self.dir.name)
        self.laps = L.LapStore(base / "laps")
        self.store = L.CircuitStore(base / "circuits.json", self.laps)
        self.ids = [self.store.create(c) for c in circuits]
        self.timer = L.LapTimer(self.store, self.laps)

    def close(self):
        self.dir.cleanup()


class GeometryTests(unittest.TestCase):
    def test_crossing_is_found_at_the_right_place_along_the_step(self):
        g = {"a": ll(0, -10), "b": ll(0, 10)}  # a line along north; a car heading east crosses from its right to its left?
        # cross(s, r) > 0 needed: s = (0, 20) (north), r east = (1, 0): s.x*r.y - s.y*r.x = -20 < 0: the wrong way
        self.assertIsNone(L.cross_gate(ll(-3, 0), ll(7, 0), g))
        flipped = {"a": g["b"], "b": g["a"]}
        t = L.cross_gate(ll(-3, 0), ll(7, 0), flipped)
        self.assertAlmostEqual(t, 0.3, places=3)

    def test_misses(self):
        g = {"a": ll(0, 10), "b": ll(0, -10)}
        self.assertIsNone(L.cross_gate(ll(-3, 15), ll(7, 15), g))  # passes beyond the end of the line
        self.assertIsNone(L.cross_gate(ll(-3, 0), ll(-1, 0), g))  # stops short
        self.assertIsNone(L.cross_gate(ll(3, 0), ll(7, 0), g))  # already across
        self.assertIsNone(L.cross_gate(ll(0, 0), ll(0, 5), g))  # runs along it

    def test_a_step_ending_exactly_on_the_line_is_counted_once(self):
        g = {"a": ll(0, 10), "b": ll(0, -10)}
        first = L.cross_gate(ll(-5, 0), ll(0, 0), g)
        second = L.cross_gate(ll(0, 0), ll(5, 0), g)
        self.assertEqual([first is not None, second is not None], [False, True])

    def test_distance_and_vehicle_key(self):
        self.assertAlmostEqual(L.distance_m(ll(0, 0), ll(30, 40)), 50.0, places=2)
        self.assertEqual(L.vehicle_key("/vehicles/etkc/"), "etkc")
        self.assertEqual(L.vehicle_key("/vehicles/midtruck/x.pc"), "midtruck")
        self.assertEqual(L.vehicle_key(None), "unknown")
        self.assertEqual(L.vehicle_key("nonsense"), "unknown")


class ValidationTests(unittest.TestCase):
    def test_a_good_circuit_is_cleaned(self):
        c = L.clean_circuit({"name": "  Hill \n climb ", "start": START, "kind": "stage", "finish": SECTOR_PI, "sectors": [START]})
        self.assertEqual((c["name"], c["kind"], c["enabled"], len(c["sectors"])), ("Hill climb", "stage", True, 1))

    def test_bad_circuits_are_refused(self):
        base = circuit()
        bad = [None, [], {**base, "name": ""}, {**base, "name": 5}, {**base, "kind": "oval"}, {**base, "enabled": "yes"},
               {**base, "start": None}, {**base, "start": {"a": [0, 0]}}, {**base, "start": {"a": [0, 0], "b": [0, 0]}},  # a point
               {**base, "start": {"a": [95, 0], "b": [95.0001, 0]}}, {**base, "start": {"a": [0, 200], "b": [0, 200.0001]}},
               {**base, "start": {"a": [float("nan"), 0], "b": [0, 0.0001]}}, {**base, "start": {"a": ["0", 0], "b": [0, 0.0001]}},
               {**base, "start": {"a": [True, 0], "b": [0, 0.0001]}},
               {**base, "start": gate((0, 0), (500, 0))},  # 500 m long
               {**base, "kind": "stage"},  # a stage needs a finish
               {**base, "sectors": [START] * 9}, {**base, "sectors": "no"}, {**base, "sectors": [5]}]
        for raw in bad:
            with self.assertRaises(ValidationError, msg=repr(raw)[:70]):
                L.clean_circuit(raw)

    def test_a_circuit_ignores_a_finish_it_does_not_use(self):
        self.assertNotIn("finish", L.clean_circuit({**circuit(), "finish": START}))


class CircuitStoreTests(unittest.TestCase):
    def test_create_list_update_delete_and_persist(self):
        env = Env()
        self.addCleanup(env.close)
        a = env.store.create(circuit(name="A"))
        b = env.store.create(circuit(name="a"))  # a name may not repeat (even in another case)
        self.assertEqual([c["name"] for c in env.store.list()], ["A", "a (2)"])
        again = L.CircuitStore(env.store.path)
        self.assertEqual([c["id"] for c in again.list()], [a, b])
        env.store.update(b, circuit(name="Renamed", enabled=False))
        self.assertEqual([c["id"] for c in env.store.enabled()], [a])
        env.store.delete(a)
        self.assertEqual([c["name"] for c in L.CircuitStore(env.store.path).list()], ["Renamed"])
        with self.assertRaises(NotFound):
            env.store.delete(a)
        with self.assertRaises(NotFound):
            env.store.update(a, circuit())
        with self.assertRaises(NotFound):
            env.store.get(a)

    def test_the_version_changes_with_every_change_and_the_enabled_list_is_replaced_not_edited(self):
        env = Env()
        self.addCleanup(env.close)
        v, before = env.store.version, env.store.enabled()
        env.store.create(circuit())
        self.assertGreater(env.store.version, v)
        self.assertEqual(before, [])  # the old list was not changed under whoever held it

    def test_a_limit_on_how_many(self):
        env = Env()
        self.addCleanup(env.close)
        for i in range(L.MAX_CIRCUITS):
            env.store.create(circuit(name=f"C{i}"))
        with self.assertRaises(ValidationError):
            env.store.create(circuit(name="one more"))

    def test_a_damaged_file_starts_empty_and_a_damaged_entry_loses_only_itself(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "circuits.json"
            path.write_text("{not json")
            self.assertEqual(L.CircuitStore(path).list(), [])
            good = {"id": "k0000000a", **L.clean_circuit(circuit())}
            path.write_text(json.dumps({"circuits": [good, {"id": "bad id", **circuit()}, {"id": "k0000000b"}, 7, good]}))
            self.assertEqual([c["id"] for c in L.CircuitStore(path).list()], ["k0000000a"])

    def test_changing_the_lines_clears_the_laps_but_renaming_does_not(self):
        env = Env(circuit())
        self.addCleanup(env.close)
        cid = env.ids[0]
        env.laps.record(cid, "etkc", {"n": 1, "t": 30.0, "s": [], "ok": True, "ts": 1, "r": 1}, [[0, 0], [10, 1]])
        self.assertFalse(env.store.update(cid, circuit(name="New name", enabled=False)))
        self.assertEqual(len(env.laps.laps(cid)), 1)
        self.assertTrue(env.store.update(cid, circuit(sectors=[SECTOR_PI])))
        self.assertEqual(env.laps.laps(cid), [])
        self.assertEqual(env.laps.bests(cid), {})
        env.laps.record(cid, "etkc", {"n": 1, "t": 30.0, "s": [10, 20], "ok": True, "ts": 1, "r": 1}, [])
        self.assertTrue(env.store.update(cid, circuit(kind="stage", finish=SECTOR_PI, sectors=[SECTOR_PI])))  # a different kind is a different thing
        self.assertEqual(env.laps.laps(cid), [])


class TimingTests(unittest.TestCase):
    def setUp(self):
        self.env = Env(circuit())
        self.addCleanup(self.env.close)
        self.timer = self.env.timer
        self.cid = self.env.ids[0]

    def lap_times(self):
        return [lap["t"] for lap in self.env.laps.laps(self.cid)]

    def test_laps_are_timed_accurately_and_the_first_crossing_only_starts_the_timing(self):
        drive(self.timer, 100.0, 2.9 * LAP_S)  # starts a little before the line: crosses it at about 1.5 s
        times = self.lap_times()
        self.assertEqual(len(times), 2)  # (the lap in progress at the end is not one)
        for t in times:
            self.assertAlmostEqual(t, LAP_S, delta=0.02)
        self.assertEqual([lap["n"] for lap in self.env.laps.laps(self.cid)], [1, 2])

    def test_timing_is_not_tied_to_the_sample_times(self):
        for offset in (0.0, 0.013, 0.05, 0.0999):
            env = Env(circuit())
            try:
                drive(env.timer, 50.0, 1.9 * LAP_S, offset=offset)
                times = [lap["t"] for lap in env.laps.laps(env.ids[0])]
                self.assertEqual(len(times), 1, offset)
                self.assertAlmostEqual(times[0], LAP_S, delta=0.02, msg=offset)
            finally:
                env.close()

    def test_a_slower_pace_a_different_sample_rate(self):
        env = Env(circuit())
        self.addCleanup(env.close)
        drive(env.timer, 0.0, 2.3 * 2 * math.pi * RADIUS / 25.0, speed=25.0, hz=5)
        times = [lap["t"] for lap in env.laps.laps(env.ids[0])]
        self.assertEqual(len(times), 2)
        self.assertAlmostEqual(times[0], 2 * math.pi * RADIUS / 25.0, delta=0.03)

    def test_going_round_the_wrong_way_times_nothing(self):
        drive(self.timer, 0.0, 3 * LAP_S, ccw=False)
        self.assertEqual(self.lap_times(), [])
        self.assertIsNone(self.timer.run)

    def test_the_lap_numbers_and_channels_follow_the_run(self):
        drive(self.timer, 0.0, 2.5 * LAP_S)
        ch = self.timer.channels()
        self.assertIs(ch["lap.active"], True)
        self.assertEqual((ch["lap.circuit"], ch["lap.number"]), ("Round", 3))
        self.assertAlmostEqual(ch["lap.last"], LAP_S, delta=0.02)
        self.assertIs(ch["lap.lastValid"], True)
        self.assertAlmostEqual(ch["lap.best"], LAP_S, delta=0.02)
        self.assertGreater(ch["lap.time"], 0)
        self.assertNotIn("lap.sectors", ch)  # no sector lines

    def test_no_position_for_a_while_drops_the_channels(self):
        drive(self.timer, 0.0, 1.4 * LAP_S)
        self.assertTrue(self.timer.channels())
        self.assertEqual(self.timer.channels(now=self.timer.updated_at + L.FRESH_S + 0.1), {})
        self.assertTrue(self.timer.channels(now=self.timer.updated_at + L.FRESH_S - 0.1))

    def test_nothing_before_a_circuit_has_been_crossed(self):
        self.assertEqual(self.timer.channels(), {})
        drive(self.timer, 0.0, 1.0)  # (the line is 1.5 s away)
        self.assertEqual(self.timer.channels(), {})

    def test_a_jump_cancels_the_lap_and_the_next_crossing_starts_again(self):
        drive(self.timer, 0.0, 1.5 * LAP_S)  # one lap done, half way round the second
        self.assertEqual(len(self.lap_times()), 1)
        x, y = point(2.0)
        self.timer.feed(*ll(x + 5000, y), 100.0, CAR)  # a teleport: 5 km in a tenth of a second
        self.assertIsNone(self.timer.run)
        drive(self.timer, 100.1, 2.4 * LAP_S, start_angle=-0.3)
        self.assertEqual(len(self.lap_times()), 3)  # the lap the jump spoiled was not recorded; two came after the restart
        self.assertEqual([lap["n"] for lap in self.env.laps.laps(self.cid)], [1, 1, 2])  # numbering restarts with the new run

    def test_a_game_event_that_moves_the_car_cancels_the_run(self):
        for name in ("reset", "teleported", "vehicleSwitched", "levelStart", "levelEnd"):
            env = Env(circuit())
            try:
                drive(env.timer, 0.0, 1.5 * LAP_S)
                self.assertIsNotNone(env.timer.run, name)
                env.timer.event(name)
                self.assertIsNone(env.timer.run, name)
            finally:
                env.close()
        drive(self.timer, 0.0, 1.5 * LAP_S)
        self.timer.event("worldReady")  # nothing to do with the car
        self.assertIsNotNone(self.timer.run)

    def test_a_gap_in_the_positions_cancels_the_run(self):
        drive(self.timer, 0.0, 1.5 * LAP_S)
        self.assertIsNotNone(self.timer.run)
        self.timer.feed(*ll(*point(1.0)), 1000.0, CAR)  # the game was paused for ages
        self.assertIsNone(self.timer.run)

    def test_a_paused_game_neither_cancels_nor_lengthens_the_lap(self):
        drive(self.timer, 0.0, 0.8 * LAP_S)
        self.assertIsNotNone(self.timer.run)
        stuck_at = self.timer._prev
        for _ in range(50):  # positions keep coming while the game is paused, all at the same instant of game time
            self.timer.feed(stuck_at[0], stuck_at[1], stuck_at[2], CAR)
        self.assertIsNotNone(self.timer.run)
        self.assertEqual(self.timer._prev, stuck_at)
        self.assertTrue(self.timer.channels())  # still shown, frozen

    def test_the_game_clock_going_backwards_cancels_the_run(self):
        drive(self.timer, 1000.0, 1.5 * LAP_S)
        self.timer.feed(*ll(*point(2.0)), 5.0, CAR)  # a new level: the extension's clock started again
        self.assertIsNone(self.timer.run)

    def test_changing_car_cancels_the_run(self):
        drive(self.timer, 0.0, 1.5 * LAP_S)
        self.timer.feed(*ll(*point(1.0)), 1000.0 + 0.0, "/vehicles/midtruck/")
        self.assertIsNone(self.timer.run)

    def test_the_same_crossing_seen_twice_is_one_crossing(self):
        # Two positions a hair either side of the line, then a stop and a tiny reversal: less than 3 s after the start, so ignored.
        t = 0.0
        self.timer.feed(*ll(215, -2), t, CAR)
        self.timer.feed(*ll(215, 2), t + 0.1, CAR)  # crosses: starts the run
        self.assertIsNotNone(self.timer.run)
        self.timer.feed(*ll(215, -2), t + 0.2, CAR)  # (backwards: not valid)
        self.timer.feed(*ll(215, 2), t + 0.3, CAR)  # forwards again 0.2 s after the start: too soon to be a lap
        self.assertEqual(self.lap_times(), [])
        self.assertIsNotNone(self.timer.run)

    def test_disabling_the_circuit_mid_run_stops_the_timing(self):
        drive(self.timer, 0.0, 1.5 * LAP_S)
        self.env.store.update(self.cid, circuit(enabled=False))
        drive(self.timer, 100.0, 3 * LAP_S)
        self.assertEqual(len(self.lap_times()), 1)
        self.assertEqual(self.timer.channels(), {})

    def test_editing_the_circuit_cancels_and_forgets(self):
        drive(self.timer, 0.0, 1.5 * LAP_S)
        self.env.store.update(self.cid, circuit(sectors=[SECTOR_PI]))
        self.timer.feed(*ll(*point(1.0)), 100.0, CAR)
        self.assertIsNone(self.timer.run)
        self.assertEqual(self.timer.channels(), {})


class SectorTests(unittest.TestCase):
    def setUp(self):
        self.env = Env(circuit(sectors=[SECTOR_PI]))
        self.addCleanup(self.env.close)
        self.cid = self.env.ids[0]

    def test_a_sector_line_splits_the_lap(self):
        drive(self.env.timer, 0.0, 2.4 * LAP_S)
        laps = self.env.laps.laps(self.cid)
        self.assertEqual(len(laps), 2)
        for lap in laps:
            self.assertTrue(lap["ok"])
            self.assertEqual(len(lap["s"]), 2)
            self.assertAlmostEqual(lap["s"][0], LAP_S / 2, delta=0.03)
            self.assertAlmostEqual(sum(lap["s"]), lap["t"], delta=0.002)
        ch = self.env.timer.channels()
        self.assertEqual(ch["lap.sectors"], 2)
        self.assertAlmostEqual(ch["lap.s1.last"], LAP_S / 2, delta=0.03)
        self.assertIn("lap.s2.best", ch)

    def test_the_sector_being_driven_is_reported(self):
        timer = self.env.timer
        seen = {}
        for i in range(0, 400):  # 40 s: the start is crossed at 1.5 s, the sector line (half way round) at about 17 s, the finish at 33 s
            t = i / 10
            timer.feed(*ll(*point(-0.3 + SPEED * t / RADIUS)), t, CAR)
            if i in (100, 250):
                seen[i] = timer.channels()
        self.assertEqual((seen[100]["lap.sector"], seen[250]["lap.sector"]), (1, 2))
        self.assertEqual(seen[250]["lap.sectors"], 2)

    def test_a_missed_sector_line_makes_the_lap_not_valid_and_never_a_best(self):
        env = Env(circuit(sectors=[gate((0, 150), (0, 90))]))  # a line inside the track that the car never crosses
        self.addCleanup(env.close)
        drive(env.timer, 0.0, 2.4 * LAP_S)
        laps = env.laps.laps(env.ids[0])
        self.assertEqual(len(laps), 2)
        self.assertTrue(all(not lap["ok"] and lap["s"] == [] for lap in laps))
        self.assertEqual(env.laps.bests(env.ids[0]), {})
        ch = env.timer.channels()
        self.assertIs(ch["lap.lastValid"], False)
        self.assertNotIn("lap.best", ch)

    def test_a_sector_line_reached_out_of_order_is_not_a_sector(self):
        # The lines are listed (-200, 0) then (0, 200), but a car going round counter-clockwise reaches (0, 200) first. Only the NEXT line in
        # the list counts, so the early one is ignored, then (-200, 0) is taken, and the second never comes before the finish: not valid.
        env = Env(circuit(sectors=[gate((-170, 0), (-230, 0)), gate((0, 170), (0, 230))]))
        self.addCleanup(env.close)
        drive(env.timer, 0.0, 1.9 * LAP_S)
        laps = env.laps.laps(env.ids[0])
        self.assertEqual(len(laps), 1)
        self.assertEqual((laps[0]["ok"], laps[0]["s"]), (False, []))
        # ...and the same two lines, listed in the order the car meets them, make a valid lap, so the gate shapes are right
        env2 = Env(circuit(sectors=[gate((0, 170), (0, 230)), gate((-170, 0), (-230, 0))]))
        self.addCleanup(env2.close)
        drive(env2.timer, 0.0, 1.9 * LAP_S)
        lap = env2.laps.laps(env2.ids[0])[0]
        self.assertTrue(lap["ok"])
        self.assertEqual(len(lap["s"]), 3)
        self.assertAlmostEqual(lap["s"][0] + lap["s"][1] + lap["s"][2], lap["t"], delta=0.002)


class DeltaAndBestTests(unittest.TestCase):
    def setUp(self):
        self.env = Env(circuit(sectors=[SECTOR_PI]))
        self.addCleanup(self.env.close)
        self.cid = self.env.ids[0]

    def test_the_live_gap_to_the_best_lap(self):
        drive(self.env.timer, 0.0, 1.9 * LAP_S, speed=40.0)  # a best lap of 31.4 s is set on lap 1
        t = self.env.timer
        self.assertNotIn("lap.lastDelta", t.channels())  # the first lap had nothing to be compared with
        # now a slower lap (36 m/s): half way round, it is about 1.5 s behind
        t.cancel()
        deltas = []
        tm = 200.0
        for i in range(0, 700):
            angle = -0.3 + 36.0 * (i / 10) / RADIUS
            t.feed(*ll(*point(angle)), tm + i / 10, CAR)
            ch = t.channels()
            if "lap.delta" in ch and ch.get("lap.number") == 1 and 14.0 < ch["lap.time"] < 16.0:
                deltas.append(ch["lap.delta"])
        self.assertTrue(deltas)
        self.assertTrue(all(d > 0.3 for d in deltas), deltas)  # behind, and getting further behind

    def test_a_faster_lap_is_a_new_best_with_a_negative_last_delta(self):
        t = self.env.timer
        drive(t, 0.0, 1.9 * LAP_S, speed=40.0)
        drive(t, 1000.0, 1.9 * 2 * math.pi * RADIUS / 44.0, speed=44.0)
        ch = t.channels()
        self.assertLess(ch["lap.lastDelta"], -2.0)
        self.assertIs(ch["lap.lastIsBest"], True)
        self.assertAlmostEqual(ch["lap.best"], 2 * math.pi * RADIUS / 44.0, delta=0.03)
        self.assertIs(ch["lap.s1.isBest"], True)
        self.assertEqual(self.env.laps.bests(self.cid)["etkc"]["t"], ch["lap.best"])

    def test_a_slower_lap_is_not_a_best(self):
        t = self.env.timer
        drive(t, 0.0, 2.3 * LAP_S, speed=40.0)
        drive(t, 1000.0, 2.5 * 2 * math.pi * RADIUS / 30.0, speed=30.0)
        ch = t.channels()
        self.assertGreater(ch["lap.lastDelta"], 5.0)
        self.assertIs(ch["lap.lastIsBest"], False)
        self.assertAlmostEqual(ch["lap.best"], LAP_S, delta=0.03)

    def test_bests_are_per_vehicle(self):
        t = self.env.timer
        drive(t, 0.0, 1.9 * LAP_S, speed=40.0, vehicle="/vehicles/etkc/")
        drive(t, 1000.0, 1.9 * 2 * math.pi * RADIUS / 30.0, speed=30.0, vehicle="/vehicles/midtruck/")
        bests = self.env.laps.bests(self.cid)
        self.assertEqual(set(bests), {"etkc", "midtruck"})
        ch = t.channels()
        self.assertAlmostEqual(ch["lap.best"], 2 * math.pi * RADIUS / 30.0, delta=0.05)  # the truck's best, not the hatchback's
        self.assertNotIn("lap.lastDelta", ch)  # (the truck had no best to beat)

    def test_the_bests_and_laps_survive_a_restart(self):
        drive(self.env.timer, 0.0, 2.3 * LAP_S)
        laps2 = L.LapStore(self.env.laps.folder)
        self.assertEqual([lap["t"] for lap in laps2.laps(self.cid)], [lap["t"] for lap in self.env.laps.laps(self.cid)])
        self.assertAlmostEqual(laps2.best(self.cid, "etkc")["t"], LAP_S, delta=0.03)
        self.assertTrue(laps2.best(self.cid, "etkc")["trace"])  # the trace for the live gap

    def test_the_trace_is_thinned_and_ends_at_the_finish(self):
        drive(self.env.timer, 0.0, 2.3 * LAP_S)
        trace = self.env.laps.best(self.cid, "etkc")["trace"]
        self.assertLess(len(trace), 2 * math.pi * RADIUS / L.TRACE_STEP_M + 5)
        self.assertGreater(len(trace), 100)
        self.assertAlmostEqual(trace[-1][0], 2 * math.pi * RADIUS, delta=4)
        self.assertAlmostEqual(trace[-1][1], LAP_S, delta=0.03)
        self.assertEqual(sorted(trace), trace)  # in order, for the search


class StageTests(unittest.TestCase):
    """A straight run from a start line to a finish line 1 km east, at 30 m/s."""

    def setUp(self):
        start = {"a": ll(0, -20), "b": ll(0, 20)}  # north: from the car's right going... east needs a line whose cross(s, r) > 0
        start = {"a": ll(0, 20), "b": ll(0, -20)}  # s points south; r east: s.x*r.y - s.y*r.x = 0 - (-40)(1) = 40 > 0
        finish = {"a": ll(1000, 20), "b": ll(1000, -20)}
        self.env = Env({"name": "Hill", "kind": "stage", "start": start, "finish": finish, "sectors": [{"a": ll(500, 20), "b": ll(500, -20)}]})
        self.addCleanup(self.env.close)
        self.cid = self.env.ids[0]

    def run_stage(self, t0, speed=30.0, x0=-100.0, x1=1100.0, hz=10):
        steps = int((x1 - x0) / speed * hz)
        for i in range(steps + 1):
            self.env.timer.feed(*ll(x0 + speed * i / hz, 0.0), t0 + i / hz, CAR)

    def test_a_stage_runs_from_the_start_to_the_finish_and_ends(self):
        self.run_stage(0.0)
        laps = self.env.laps.laps(self.cid)
        self.assertEqual(len(laps), 1)
        self.assertAlmostEqual(laps[0]["t"], 1000 / 30.0, delta=0.02)
        self.assertEqual(len(laps[0]["s"]), 2)
        self.assertAlmostEqual(laps[0]["s"][0], 500 / 30.0, delta=0.02)
        self.assertIsNone(self.env.timer.run)  # over: the next attempt begins at the start line
        ch = self.env.timer.channels()
        self.assertIs(ch["lap.active"], False)
        self.assertAlmostEqual(ch["lap.last"], 1000 / 30.0, delta=0.02)

    def test_each_attempt_is_numbered_and_the_best_is_kept(self):
        self.run_stage(0.0, speed=30.0)
        self.run_stage(500.0, speed=35.0)
        self.run_stage(900.0, speed=25.0)
        laps = self.env.laps.laps(self.cid)
        self.assertEqual([lap["n"] for lap in laps], [1, 2, 3])
        self.assertAlmostEqual(self.env.laps.bests(self.cid)["etkc"]["t"], 1000 / 35.0, delta=0.02)

    def test_the_finish_line_alone_does_nothing(self):
        for i in range(0, 200):
            self.env.timer.feed(*ll(900 + i, 0.0), i / 10.0 + 0.1 * 0, CAR)  # starts after the start line: no run
        self.assertEqual(self.env.laps.laps(self.cid), [])

    def test_going_back_over_the_start_line_begins_a_new_attempt(self):
        t = self.env.timer
        for i in range(0, 120):  # drive out to x = 190 at 30 m/s, 10 Hz
            t.feed(*ll(-30 + 3.0 * i, 0.0), i / 10.0, CAR)
        first_start = t.run.start_t
        # a U-turn (a jump back would cancel), then across the start line again, the right way
        for i in range(0, 80):
            t.feed(*ll(330 - 3.0 * i, 20.0), 12.0 + i / 10.0, CAR)
        for i in range(0, 120):
            t.feed(*ll(-100 + 3.0 * i, 0.0), 20.0 + i / 10.0, CAR)
        self.assertIsNotNone(t.run)
        self.assertGreater(t.run.start_t, first_start + 5)


class StoreTests(unittest.TestCase):
    def test_laps_are_trimmed_to_the_newest(self):
        with tempfile.TemporaryDirectory() as d:
            store = L.LapStore(Path(d))
            for i in range(L.MAX_LAP_LINES + 250):
                store.record("k00000001", "etkc", {"n": i, "t": 30.0 + i, "s": [], "ok": True, "ts": i, "r": 1}, [])
            kept = store.laps("k00000001", limit=10**6)
            self.assertLessEqual(len(kept), L.MAX_LAP_LINES + 200)
            self.assertEqual(kept[-1]["n"], L.MAX_LAP_LINES + 249)
            again = L.LapStore(Path(d))
            self.assertEqual(again.laps("k00000001", limit=10**6)[-1]["n"], L.MAX_LAP_LINES + 249)
            self.assertLessEqual(len(again.laps("k00000001", limit=10**6)), L.MAX_LAP_LINES + 200)

    def test_a_half_written_last_line_after_a_crash_is_ignored(self):
        with tempfile.TemporaryDirectory() as d:
            store = L.LapStore(Path(d))
            store.record("k00000001", "etkc", {"n": 1, "t": 30.0, "s": [], "ok": True, "ts": 1, "r": 1}, [])
            with open(Path(d) / "k00000001.jsonl", "a") as f:
                f.write('{"n":2,"t":3')
            self.assertEqual(len(L.LapStore(Path(d)).laps("k00000001")), 1)

    def test_a_memory_only_store_keeps_nothing_on_disk(self):
        store = L.LapStore(None)
        result = store.record("k00000001", "etkc", {"n": 1, "t": 30.0, "s": [10.0, 20.0], "ok": True, "ts": 1, "r": 1}, [[0, 0]])
        self.assertTrue(result["newBest"])
        self.assertEqual(store.laps("k00000001")[0]["v"], "etkc")

    def test_the_first_lap_makes_every_sector_a_best_and_a_worse_one_does_not(self):
        store = L.LapStore(None)
        first = store.record("k00000001", "etkc", {"n": 1, "t": 30.0, "s": [10.0, 20.0], "ok": True, "ts": 1, "r": 1}, [])
        self.assertEqual(first, {"newBest": True, "sectorBests": [True, True]})
        worse = store.record("k00000001", "etkc", {"n": 2, "t": 31.0, "s": [11.0, 20.0], "ok": True, "ts": 2, "r": 1}, [])
        self.assertEqual(worse, {"newBest": False, "sectorBests": [False, False]})
        mixed = store.record("k00000001", "etkc", {"n": 3, "t": 30.5, "s": [9.5, 21.0], "ok": True, "ts": 3, "r": 1}, [])
        self.assertEqual(mixed, {"newBest": False, "sectorBests": [True, False]})
        self.assertEqual(store.bests("k00000001")["etkc"]["s"], [9.5, 20.0])  # the best of each, though no lap had both
        self.assertEqual(store.bests("k00000001")["etkc"]["t"], 30.0)

    def test_not_valid_laps_are_kept_but_never_a_best(self):
        store = L.LapStore(None)
        result = store.record("k00000001", "etkc", {"n": 1, "t": 20.0, "s": [], "ok": False, "ts": 1, "r": 1}, [])
        self.assertEqual(result, {"newBest": False, "sectorBests": []})
        self.assertEqual(store.bests("k00000001"), {})
        self.assertEqual(len(store.laps("k00000001")), 1)

    def test_clearing_removes_the_files(self):
        with tempfile.TemporaryDirectory() as d:
            store = L.LapStore(Path(d))
            store.record("k00000001", "etkc", {"n": 1, "t": 30.0, "s": [], "ok": True, "ts": 1, "r": 1}, [[0, 0]])
            self.assertTrue((Path(d) / "k00000001.jsonl").exists() and (Path(d) / "k00000001.best.json").exists())
            store.clear("k00000001")
            self.assertEqual(list(Path(d).iterdir()), [])
            self.assertEqual((store.laps("k00000001"), store.bests("k00000001")), ([], {}))

    def test_a_circuit_id_that_is_not_one_is_never_turned_into_a_path(self):
        with tempfile.TemporaryDirectory() as d:
            store = L.LapStore(Path(d))
            self.assertEqual(store.laps("../../etc/passwd"), [])
            store.clear("../../etc/passwd")  # (no error, and nothing outside the folder is touched)


if __name__ == "__main__":
    unittest.main()

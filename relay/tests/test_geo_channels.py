"""The world the game knows (speed limit, route progress) reaches the dashboards as channels: geo.limit, geo.route.* ."""
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from thrashdash import PROTOCOL_VERSION, simulator  # noqa: E402
from thrashdash.hub import GEO_TTL_S, Hub  # noqa: E402


def info(limit=None):
    packet = {"v": PROTOCOL_VERSION, "type": "geoinfo", "rt": 1.0}
    if limit is not None:
        packet["limit"] = limit
    return packet


def route(gps=None):
    packet = {"v": PROTOCOL_VERSION, "type": "georoute", "rt": 1.0}
    if gps is not None:
        packet["gps"] = gps
    return packet


def hub_with_car():
    hub = Hub()  # no trip log: the channels must not depend on one
    hub.ingest_packet({"v": PROTOCOL_VERSION, "type": "frame", "n": 1, "vehicle": "/vehicles/test/", "electrics": {"wheelspeed": 10.0}})
    return hub


class LimitTests(unittest.TestCase):
    def test_a_posted_limit_becomes_two_channels(self):
        hub = hub_with_car()
        hub.ingest_packet(info({"ms": 13.89, "mapped": True, "unit": "kmh"}))
        self.assertEqual(hub.values["geo.limit"], 13.89)
        self.assertIs(hub.values["geo.limitPosted"], True)
        self.assertEqual(hub.channels["geo.limit"], "number")
        self.assertEqual(hub.channels["geo.limitPosted"], "boolean")

    def test_a_guess_from_the_kind_of_road_is_not_posted(self):
        for mapped in (False, None, "yes", 1):
            hub = hub_with_car()
            hub.ingest_packet(info({"ms": 22.2, "mapped": mapped}))
            self.assertIs(hub.values["geo.limitPosted"], False, mapped)

    def test_no_limit_known_clears_the_channels(self):
        hub = hub_with_car()
        hub.ingest_packet(info({"ms": 13.89, "mapped": True}))
        hub.ingest_packet(info())  # the game answered with no limit
        self.assertNotIn("geo.limit", hub.values)
        self.assertNotIn("geo.limitPosted", hub.values)
        self.assertNotIn("geo.limit", hub.channels)

    def test_unbelievable_limits_are_refused(self):
        for bad in (0, -5, 100.1, 9e9, float("inf"), float("nan"), "50", True, None, [50]):
            hub = hub_with_car()
            hub.ingest_packet(info({"ms": bad, "mapped": True}))
            self.assertNotIn("geo.limit", hub.values, bad)
        hub = hub_with_car()
        hub.ingest_packet(info("fast"))
        hub.ingest_packet(info([1, 2]))
        self.assertNotIn("geo.limit", hub.values)

    def test_the_channels_join_the_frame_and_survive_new_frames(self):
        hub = hub_with_car()
        hub.ingest_packet(info({"ms": 13.89, "mapped": True}))
        hub.ingest_packet({"v": PROTOCOL_VERSION, "type": "frame", "n": 2, "vehicle": "/vehicles/test/", "electrics": {"wheelspeed": 11.0}})
        self.assertEqual((hub.values["wheelspeed"], hub.values["geo.limit"]), (11.0, 13.89))
        self.assertIn('"geo.limit":13.89', hub.frame_json())

    def test_a_new_reading_reaches_browsers_even_without_a_car_frame(self):
        hub = hub_with_car()
        before = hub.frame_seq
        hub.ingest_packet(info({"ms": 13.89, "mapped": True}))
        self.assertGreater(hub.frame_seq, before)

    def test_a_limit_is_dropped_when_the_game_goes_quiet(self):
        hub = hub_with_car()
        with mock.patch("thrashdash.hub.time.monotonic", return_value=1000.0):
            hub.ingest_packet(info({"ms": 13.89, "mapped": True}))
            self.assertIn("geo.limit", hub.values)
        with mock.patch("thrashdash.hub.time.monotonic", return_value=1000.0 + GEO_TTL_S - 0.1):
            hub._rebuild()
            self.assertIn("geo.limit", hub.values)
        with mock.patch("thrashdash.hub.time.monotonic", return_value=1000.0 + GEO_TTL_S + 0.1):
            hub._rebuild()
            self.assertNotIn("geo.limit", hub.values)

    def test_the_channels_do_not_need_a_trip_log_and_are_not_given_to_one(self):
        class Spy:
            def __init__(self):
                self.seen = []

            def ingest(self, packet, car=None):
                self.seen.append(packet["type"])

        spy = Spy()
        hub = Hub(triplog=spy)
        hub.ingest_packet(info({"ms": 13.89, "mapped": True}))
        hub.ingest_packet(route({"remaining": 100.0}))
        self.assertEqual(spy.seen, ["geoinfo"])  # the route packet is for dashboards only: the trip log keeps its own, slower reading
        self.assertEqual(hub.values["geo.limit"], 13.89)


class RouteTests(unittest.TestCase):
    def test_route_progress_becomes_channels(self):
        hub = hub_with_car()
        hub.ingest_packet(route({"remaining": 1234.5, "eta": 300, "off": 12, "name": "Home", "planning": False}))
        self.assertIs(hub.values["geo.route.active"], True)
        self.assertEqual((hub.values["geo.route.remaining"], hub.values["geo.route.eta"], hub.values["geo.route.off"]), (1234.5, 300.0, 12.0))
        self.assertEqual(hub.values["geo.route.name"], "Home")
        self.assertEqual(hub.channels["geo.route.name"], "string")

    def test_no_route_says_so_and_leaves_the_numbers_out(self):
        hub = hub_with_car()
        hub.ingest_packet(route({"remaining": 100.0, "eta": 10.0}))
        hub.ingest_packet(route())  # the route was cleared
        self.assertIs(hub.values["geo.route.active"], False)
        for gone in ("remaining", "eta", "off", "name"):
            self.assertNotIn(f"geo.route.{gone}", hub.values)

    def test_a_route_still_being_planned_is_active_without_numbers(self):
        hub = hub_with_car()
        hub.ingest_packet(route({"planning": True}))
        self.assertIs(hub.values["geo.route.active"], True)
        self.assertNotIn("geo.route.remaining", hub.values)

    def test_bad_numbers_and_names_are_left_out(self):
        hub = hub_with_car()
        hub.ingest_packet(route({"remaining": -1, "eta": float("inf"), "off": "far", "name": "   "}))
        self.assertIs(hub.values["geo.route.active"], True)
        for gone in ("remaining", "eta", "off", "name"):
            self.assertNotIn(f"geo.route.{gone}", hub.values)
        hub.ingest_packet(route({"remaining": 2.1e7}))
        self.assertNotIn("geo.route.remaining", hub.values)

    def test_a_long_name_is_cut(self):
        hub = hub_with_car()
        hub.ingest_packet(route({"remaining": 5, "name": "x" * 500}))
        self.assertEqual(len(hub.values["geo.route.name"]), 60)

    def test_a_route_that_is_not_an_object_means_none(self):
        hub = hub_with_car()
        hub.ingest_packet(route("home"))
        self.assertIs(hub.values["geo.route.active"], False)

    def test_the_route_is_dropped_when_the_game_goes_quiet(self):
        hub = hub_with_car()
        with mock.patch("thrashdash.hub.time.monotonic", return_value=50.0):
            hub.ingest_packet(route({"remaining": 100.0}))
        with mock.patch("thrashdash.hub.time.monotonic", return_value=50.0 + GEO_TTL_S + 1):
            hub._rebuild()
        self.assertNotIn("geo.route.active", hub.values)

    def test_the_limit_and_the_route_expire_separately(self):
        hub = hub_with_car()
        with mock.patch("thrashdash.hub.time.monotonic", return_value=10.0):
            hub.ingest_packet(info({"ms": 13.89, "mapped": True}))
        with mock.patch("thrashdash.hub.time.monotonic", return_value=13.0):
            hub.ingest_packet(route({"remaining": 100.0}))
        with mock.patch("thrashdash.hub.time.monotonic", return_value=15.0):
            hub._rebuild()
        self.assertNotIn("geo.limit", hub.values)
        self.assertIn("geo.route.remaining", hub.values)


class PlanningTests(unittest.TestCase):
    """geo.route.planning / .replanning: from Earth's `planning` flag and the route's version, held long enough to be read."""

    def setUp(self):
        self.hub = hub_with_car()
        self.now = 100.0
        patcher = mock.patch("thrashdash.hub.time.monotonic", side_effect=lambda: self.now)
        patcher.start()
        self.addCleanup(patcher.stop)

    def at(self, seconds, version=None, planning=False, gone=False):
        self.now = 100.0 + seconds
        gps = None if gone else {"remaining": 900.0, "off": 0.0, "routeVersion": version, "planning": planning}
        self.hub.ingest_packet(route(gps))
        channels = self.hub._geo_route[0]
        return channels.get("geo.route.planning"), channels.get("geo.route.replanning")

    def test_a_settled_route_is_not_being_planned(self):
        self.assertEqual(self.at(0, version=1), (False, False))
        self.assertEqual(self.at(1, version=1), (False, False))

    def test_the_first_plan_is_planning_not_replanning(self):
        self.assertEqual(self.at(0, version=0, planning=True), (True, False))
        self.assertEqual(self.at(1, version=0, planning=True), (True, False))
        self.assertEqual(self.at(5, version=1), (False, False))  # (arriving: not taken for a re-route)

    def test_a_new_plan_for_a_route_there_already_is_a_reroute(self):
        self.at(0, version=1)
        self.assertEqual(self.at(1, version=1, planning=True), (True, True))

    def test_it_is_held_for_three_seconds_then_goes(self):
        self.at(0, version=1)
        self.at(1, version=1, planning=True)
        self.assertEqual(self.at(2, version=2), (True, True))
        self.assertEqual(self.at(3, version=2), (True, True))
        self.assertEqual(self.at(4, version=2), (False, False))

    def test_a_long_plan_stays_up_as_long_as_it_takes(self):
        self.at(0, version=1)
        for second in range(1, 12):
            self.assertEqual(self.at(second, version=1, planning=True), (True, True))
        self.assertEqual(self.at(12, version=2), (True, True))
        self.assertEqual(self.at(13, version=2), (True, True))
        self.assertEqual(self.at(14, version=2), (False, False))

    def test_a_reroute_finished_between_two_packets_still_shows(self):
        self.at(0, version=1)
        self.assertEqual(self.at(1, version=2), (True, True))
        self.assertEqual(self.at(5, version=2), (False, False))

    def test_a_failed_reroute_goes_too(self):
        self.at(0, version=1)
        self.at(1, version=1, planning=True)
        self.assertEqual(self.at(6, version=1), (False, False))

    def test_a_cleared_route_forgets_the_old_one(self):
        self.at(0, version=3)
        self.at(1, version=3, planning=True)
        self.assertEqual(self.at(2, gone=True), (None, None))
        self.assertEqual(self.at(3, version=4), (False, False))  # (a different route, not a re-plan of the cleared one)

    def test_after_the_game_has_been_quiet_a_different_version_is_not_a_reroute(self):
        self.at(0, version=3)
        self.assertEqual(self.at(60, version=1), (False, False))

    def test_only_a_true_flag_counts_and_a_bad_version_is_no_version(self):
        for flag in ("yes", 1, None):
            self.assertEqual(self.at(0, version=1, planning=flag), (False, False))
        self.assertEqual(self.at(1, version="x", planning=True), (True, False))


class SimulatorTests(unittest.TestCase):
    def test_the_simulators_world_packets_are_accepted_and_cover_every_state(self):
        hub = hub_with_car()
        seen = {"limit": set(), "posted": set(), "route": set()}
        for t in range(0, 240):
            for packet in simulator.make_geo(float(t)):
                hub.ingest_packet(packet)
            seen["limit"].add("geo.limit" in hub.values)
            if "geo.limitPosted" in hub.values:
                seen["posted"].add(hub.values["geo.limitPosted"])
            seen["route"].add(hub.values["geo.route.active"])
            self.assertEqual(hub.stats["rejected_type"], 0)
        self.assertEqual(seen["limit"], {True, False})
        self.assertEqual(seen["posted"], {True, False})
        self.assertEqual(seen["route"], {True, False})


if __name__ == "__main__":
    unittest.main()


# ---- the next maneuver, from the route's steps and the car's progress ---------------------------------------------------------------------

import json as _json  # noqa: E402

SAMPLE = _json.loads((Path(__file__).resolve().parents[2] / "docs" / "route-steps-sample.json").read_text())  # (a real route from BeamNG.Earth)


def steps(version=1, dist=SAMPLE["dist"], items=None):
    return {"v": PROTOCOL_VERSION, "type": "geosteps", "rt": 1.0, "version": version, "dist": dist, "steps": SAMPLE["steps"] if items is None else items}


def progress(traveled, version=1, dist=SAMPLE["dist"]):
    return route({"remaining": dist - traveled, "eta": 100.0, "routeVersion": version})


def nxt(hub):
    return {k[len("geo.route."):]: v for k, v in hub.values.items() if k.startswith(("geo.route.next.", "geo.route.then."))}


class ManeuverTests(unittest.TestCase):
    def test_the_next_maneuver_is_the_next_step_ahead(self):
        hub = hub_with_car()
        hub.ingest_packet(steps())
        hub.ingest_packet(progress(219))
        self.assertEqual(nxt(hub), {"next.distance": 266.0, "next.type": "fork", "next.modifier": "slight left", "next.street": "Aotea Quay",
                                    "then.gap": 810.0, "then.type": "roundabout", "then.exit": 1})
        self.assertEqual(hub.channels["geo.route.next.distance"], "number")
        self.assertEqual(hub.channels["geo.route.next.street"], "string")

    def test_the_departure_is_never_the_next_turn(self):
        hub = hub_with_car()
        hub.ingest_packet(steps())
        hub.ingest_packet(progress(0))
        self.assertEqual(nxt(hub)["next.type"], "fork")

    def test_the_distance_counts_down_as_the_car_goes_on(self):
        hub = hub_with_car()
        hub.ingest_packet(steps())
        seen = []
        for traveled in (300, 400, 480):
            hub.ingest_packet(progress(traveled))
            seen.append(hub.values["geo.route.next.distance"])
        self.assertEqual(seen, [185.0, 85.0, 5.0])

    def test_a_maneuver_stays_next_until_the_car_is_a_little_past_it(self):
        hub = hub_with_car()
        hub.ingest_packet(steps())
        hub.ingest_packet(progress(490))  # five metres past the fork
        self.assertEqual((nxt(hub)["next.type"], nxt(hub)["next.distance"]), ("fork", 0.0))
        hub.ingest_packet(progress(505))  # twenty past: on to the roundabout
        self.assertEqual(nxt(hub)["next.type"], "roundabout")
        self.assertEqual(nxt(hub)["next.exit"], 1)
        self.assertNotIn("next.street", nxt(hub))  # (a roundabout entry has no street: it has an exit number)

    def test_the_last_step_has_no_step_after_it(self):
        hub = hub_with_car()
        hub.ingest_packet(steps())
        hub.ingest_packet(progress(SAMPLE["dist"] - 100))
        got = nxt(hub)
        self.assertEqual((got["next.type"], got["next.modifier"], got["next.distance"]), ("arrive", "left", 97.0))  # (the arrival is 3 m before the route's very end)
        self.assertFalse([k for k in got if k.startswith("then.")])

    def test_after_the_last_step_there_is_nothing_next(self):
        hub = hub_with_car()
        hub.ingest_packet(steps())
        hub.ingest_packet(progress(SAMPLE["dist"] + 50))
        self.assertEqual(nxt(hub), {})

    def test_steps_are_only_believed_for_the_route_they_belong_to(self):
        hub = hub_with_car()
        hub.ingest_packet(steps(version=1))
        hub.ingest_packet(progress(219, version=2))  # a re-planned route: the old steps are wrong
        self.assertEqual(nxt(hub), {})
        hub.ingest_packet(steps(version=2, items=[{"type": "turn", "modifier": "left", "name": "New Road", "offset": 500}]))
        self.assertEqual(nxt(hub)["next.street"], "New Road")

    def test_steps_arriving_after_the_progress_show_at_once(self):
        hub = hub_with_car()
        hub.ingest_packet(progress(219))
        self.assertEqual(nxt(hub), {})
        seq = hub.frame_seq
        hub.ingest_packet(steps())
        self.assertEqual(nxt(hub)["next.type"], "fork")
        self.assertGreater(hub.frame_seq, seq)

    def test_the_maneuver_goes_when_the_route_does(self):
        hub = hub_with_car()
        hub.ingest_packet(steps())
        hub.ingest_packet(progress(219))
        hub.ingest_packet(route())  # the route was cleared
        self.assertEqual(nxt(hub), {})
        self.assertIs(hub.values["geo.route.active"], False)

    def test_the_maneuver_goes_when_the_game_goes_quiet(self):
        hub = hub_with_car()
        with mock.patch("thrashdash.hub.time.monotonic", return_value=100.0):
            hub.ingest_packet(steps())
            hub.ingest_packet(progress(219))
        with mock.patch("thrashdash.hub.time.monotonic", return_value=100.0 + GEO_TTL_S + 1):
            hub._rebuild()
        self.assertEqual(nxt(hub), {})

    def test_progress_without_a_version_or_remaining_gives_no_maneuver(self):
        hub = hub_with_car()
        hub.ingest_packet(steps())
        hub.ingest_packet(route({"remaining": 9000.0}))  # (no routeVersion)
        self.assertEqual(nxt(hub), {})
        hub.ingest_packet(route({"routeVersion": 1}))  # (no remaining)
        self.assertEqual(nxt(hub), {})
        hub.ingest_packet(steps(dist=None))  # (no length to work the progress from)
        hub.ingest_packet(progress(219))
        self.assertEqual(nxt(hub), {})

    def test_bad_steps_are_refused_or_cleaned(self):
        hub = hub_with_car()
        for bad in ({"version": "x"}, {"version": 1, "steps": "turn"}, {"version": 1, "steps": None}, {"version": float("nan"), "steps": []},
                    {"version": 1, "dist": -5, "steps": []}, {"version": 1, "dist": 9e9, "steps": []}):
            hub.ingest_packet({"v": PROTOCOL_VERSION, "type": "geosteps", **bad})
        self.assertEqual(hub.stats["rejected_steps"], 6)
        messy = [{"type": "turn", "offset": "far"}, {"offset": -3}, "x", None, {"offset": 100, "type": "turn", "modifier": "left", "name": "  A\x00B  ", "exit": 99},
                 {"offset": 50, "type": "turn"}, {"offset": 200, "type": 5, "name": "x" * 500, "exit": 2}]
        hub.ingest_packet(steps(items=messy, dist=1000))
        hub.ingest_packet(progress(0, dist=1000))
        self.assertEqual(hub._route_steps["steps"], [
            {"offset": 100.0, "type": "turn", "modifier": "left", "name": "A B"},  # (an exit number out of range is dropped, text tidied)
            {"offset": 200.0, "name": "x" * 60, "exit": 2},  # (and out-of-order ones are skipped: the 50 after the 100)
        ])

    def test_at_most_two_hundred_steps_are_kept(self):
        hub = hub_with_car()
        hub.ingest_packet(steps(items=[{"offset": float(i), "type": "turn"} for i in range(500)], dist=1000))
        self.assertEqual(len(hub._route_steps["steps"]), 200)

    def test_the_simulator_gives_steps_the_hub_accepts_and_a_maneuver_follows(self):
        hub = hub_with_car()
        for t in range(0, 30):
            for packet in simulator.make_geo(float(t)):
                hub.ingest_packet(packet)
        self.assertEqual(hub.stats["rejected_steps"], 0)
        self.assertGreaterEqual(hub.stats["steps"], 2)  # (at the start, and again twenty seconds in)
        self.assertIn("geo.route.next.type", hub.values)

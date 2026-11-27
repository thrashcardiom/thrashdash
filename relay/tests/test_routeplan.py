"""Planning a GPS route from the Trips page: stops, saved destinations, the command to the game and its answer, and the planned route's line."""
import asyncio
import json
import socket
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import aiohttp
from aiohttp import web

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from thrashdash import PROTOCOL_VERSION  # noqa: E402
from thrashdash.hub import GEO_TTL_S, Hub  # noqa: E402
from thrashdash.paths import web_root  # noqa: E402
from thrashdash.routeplan import MAX_DESTINATIONS, MAX_STOPS, DestinationStore, RoutePlanner, clean_place, clean_stops  # noqa: E402
from thrashdash.server import make_app  # noqa: E402
from thrashdash.store import LayoutStore, NotFound, SettingsStore  # noqa: E402
from thrashdash.tripnotice import TripNotices  # noqa: E402
from thrashdash.udp import start_listener  # noqa: E402
from thrashdash.validate import ValidationError  # noqa: E402
from test_triplog import free_port  # noqa: E402

V = PROTOCOL_VERSION
HOME = {"lat": -41.2865, "lon": 174.7762, "name": "Home"}


class PlaceTests(unittest.TestCase):
    def test_a_place_is_numbers_on_earth_and_a_short_name(self):
        self.assertEqual(clean_place({"lat": -41.28654321, "lon": 174.77621, "name": "  My \n place  "}), {"lat": -41.286543, "lon": 174.77621, "name": "My place"})
        self.assertEqual(clean_place({"lat": 0, "lon": 0}), {"lat": 0.0, "lon": 0.0})
        self.assertEqual(clean_place({"lat": 1, "lon": 2, "name": ""}), {"lat": 1.0, "lon": 2.0})
        self.assertEqual(len(clean_place({"lat": 1, "lon": 2, "name": "x" * 500})["name"]), 60)

    def test_anything_else_is_refused(self):
        for bad in (None, "home", [], {}, {"lat": 1}, {"lon": 1}, {"lat": 91, "lon": 0}, {"lat": 0, "lon": 181}, {"lat": "1", "lon": 2}, {"lat": True, "lon": 2},
                    {"lat": float("nan"), "lon": 0}, {"lat": float("inf"), "lon": 0}, {"lat": 1, "lon": 2, "name": 5}):
            with self.assertRaises(ValidationError, msg=repr(bad)):
                clean_place(bad)

    def test_a_route_is_one_to_eleven_places(self):
        self.assertEqual(len(clean_stops([HOME])), 1)
        self.assertEqual(len(clean_stops([HOME] * MAX_STOPS)), MAX_STOPS)
        for bad in ([], [HOME] * (MAX_STOPS + 1), "home", None, {"stops": []}, [HOME, "x"], [HOME, {"lat": 99, "lon": 0}]):
            with self.assertRaises(ValidationError, msg=repr(bad)[:50]):
                clean_stops(bad)


class DestinationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "destinations.json"
        self.store = DestinationStore(self.path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_save_list_delete_and_keep_across_a_restart(self):
        a = self.store.create("Home", -41.2865, 174.7762)
        b = self.store.create("Work", -41.3, 174.8)
        self.assertEqual([d["name"] for d in self.store.list()], ["Home", "Work"])
        self.assertRegex(a["id"], r"^d[0-9a-f]{8}$")
        self.assertEqual([d["id"] for d in DestinationStore(self.path).list()], [a["id"], b["id"]])
        self.store.delete(a["id"])
        self.assertEqual([d["name"] for d in DestinationStore(self.path).list()], ["Work"])
        with self.assertRaises(NotFound):
            self.store.delete(a["id"])

    def test_a_name_in_use_gets_a_number(self):
        self.store.create("Home", 1, 2)
        self.assertEqual(self.store.create("home", 3, 4)["name"], "home (2)")
        self.assertEqual(self.store.create("HOME", 5, 6)["name"], "HOME (3)")

    def test_bad_destinations_are_refused(self):
        for name, lat, lon in (("", 1, 2), ("   ", 1, 2), (None, 1, 2), (5, 1, 2), ("x", 91, 0), ("x", 0, 200), ("x", "1", 2), ("x", None, None)):
            with self.assertRaises(ValidationError, msg=repr((name, lat, lon))):
                self.store.create(name, lat, lon)
        self.assertEqual(self.store.list(), [])

    def test_a_name_is_one_short_line(self):
        self.assertEqual(self.store.create("  Aunt \n Jo's  ", 1, 2)["name"], "Aunt Jo's")
        self.assertEqual(len(self.store.create("y" * 300, 1, 2)["name"]), 40)

    def test_a_limit_on_how_many(self):
        for i in range(MAX_DESTINATIONS):
            self.store.create(f"D{i}", 1, 2)
        with self.assertRaises(ValidationError):
            self.store.create("one more", 1, 2)

    def test_a_damaged_file_starts_empty_and_a_damaged_entry_loses_only_itself(self):
        self.path.write_text("{not json")
        self.assertEqual(DestinationStore(self.path).list(), [])
        good = {"id": "d0000000a", "name": "Good", "lat": 1, "lon": 2}
        self.path.write_text(json.dumps({"destinations": [good, {"id": "bad id", "name": "x", "lat": 1, "lon": 2}, {"id": "d0000000b", "name": "", "lat": 1, "lon": 2},
                                                          {"id": "d0000000c", "name": "Far", "lat": 99, "lon": 2}, 7, good]}))
        self.assertEqual([d["name"] for d in DestinationStore(self.path).list()], ["Good"])

    def test_without_a_file_nothing_is_kept(self):
        store = DestinationStore(None)
        store.create("Home", 1, 2)
        self.assertEqual(len(store.list()), 1)

    def test_the_list_is_a_copy(self):
        self.store.create("Home", 1, 2)
        self.store.list()[0]["name"] = "changed"
        self.assertEqual(self.store.list()[0]["name"], "Home")


class FakeNotices:
    """Stands in for TripNotices: records what is sent to the game, and can answer it (the game's `georouteack`) as the real game would."""

    def __init__(self, hub, answer=None, connected=True):
        self.hub, self.answer, self.connected, self.sent = hub, answer, connected, []

    def game_connected(self):
        return self.connected

    def send_to_game(self, payload):
        if not self.connected:
            return False
        self.sent.append(payload)
        if self.answer is not None:
            asyncio.get_running_loop().call_later(0.02, lambda: self.hub.ingest_packet({"v": V, "type": "georouteack", **self.answer}))
        return True


class PlannerTests(unittest.IsolatedAsyncioTestCase):
    async def test_the_stops_go_to_the_game_and_its_answer_comes_back(self):
        hub = Hub()
        notices = FakeNotices(hub, {"ok": True})
        result = await RoutePlanner(notices, hub).set_route([{"lat": -41.3, "lon": 174.8, "name": "A"}, {"lat": -41.4, "lon": 174.9}])
        self.assertEqual(result, {"ok": True, "why": None})
        self.assertEqual(notices.sent, [{"type": "route", "action": "set", "stops": [{"lat": -41.3, "lon": 174.8, "name": "A"}, {"lat": -41.4, "lon": 174.9}]}])

    async def test_a_refusal_carries_the_games_reason(self):
        hub = Hub()
        result = await RoutePlanner(FakeNotices(hub, {"ok": False, "why": "too far apart"}), hub).set_route([HOME])
        self.assertEqual(result, {"ok": False, "why": "too far apart"})

    async def test_clearing(self):
        hub = Hub()
        notices = FakeNotices(hub, {"ok": True})
        self.assertTrue((await RoutePlanner(notices, hub).clear_route())["ok"])
        self.assertEqual(notices.sent, [{"type": "route", "action": "clear"}])

    async def test_a_game_that_is_not_connected_is_told_plainly_and_nothing_is_sent(self):
        hub = Hub()
        notices = FakeNotices(hub, {"ok": True}, connected=False)
        result = await RoutePlanner(notices, hub).set_route([HOME])
        self.assertFalse(result["ok"])
        self.assertIn("not connected", result["why"])
        self.assertEqual(notices.sent, [])

    async def test_no_answer_is_reported_after_a_wait_not_for_ever(self):
        hub = Hub()
        slept = []

        async def sleep(s):
            slept.append(s)

        result = await RoutePlanner(FakeNotices(hub, None), hub, wait_s=1.0, sleep=sleep).set_route([HOME])
        self.assertFalse(result["ok"])
        self.assertIn("did not answer", result["why"])
        self.assertAlmostEqual(sum(slept), 1.0, delta=0.06)

    async def test_bad_stops_are_refused_before_anything_is_sent(self):
        hub = Hub()
        notices = FakeNotices(hub, {"ok": True})
        with self.assertRaises(ValidationError):
            await RoutePlanner(notices, hub).set_route([{"lat": 100, "lon": 0}])
        self.assertEqual(notices.sent, [])

    async def test_an_old_answer_is_not_taken_for_the_new_one(self):
        hub = Hub()
        hub.ingest_packet({"v": V, "type": "georouteack", "ok": True})  # (an answer to an earlier command)
        notices = FakeNotices(hub, {"ok": False, "why": "no"})
        self.assertEqual((await RoutePlanner(notices, hub).set_route([HOME]))["ok"], False)

    async def test_commands_go_one_at_a_time(self):
        hub = Hub()
        notices = FakeNotices(hub, {"ok": True})
        planner = RoutePlanner(notices, hub)
        results = await asyncio.gather(planner.set_route([HOME]), planner.clear_route(), planner.set_route([HOME]))
        self.assertTrue(all(r["ok"] for r in results))
        self.assertEqual([m["action"] for m in notices.sent], ["set", "clear", "set"])

    async def test_without_a_channel_to_the_game_it_says_so(self):
        result = await RoutePlanner(None, Hub()).set_route([HOME])
        self.assertFalse(result["ok"])

    async def test_the_real_notices_channel_sends_json_to_the_game_address(self):
        sent = []

        class Transport:
            def sendto(self, data, addr):
                sent.append((json.loads(data), addr))

        notices = TripNotices(SettingsStore(Path(tempfile.mkdtemp()) / "s.json"))
        self.assertFalse(notices.game_connected())
        self.assertFalse(notices.send_to_game({"type": "route"}))  # (no game yet)
        notices.attach(Transport())
        notices.seen_game(("127.0.0.1", 40000))
        self.assertTrue(notices.game_connected())
        self.assertTrue(notices.send_to_game({"type": "route", "action": "clear"}))
        self.assertEqual(sent, [({"type": "route", "action": "clear"}, ("127.0.0.1", 40000))])


def car_hub():
    hub = Hub()
    hub.ingest_packet({"v": V, "type": "frame", "n": 1, "vehicle": "/vehicles/x/", "electrics": {"rpm": 1}})
    return hub


def path(version=1, points=None, **extra):
    return {"v": V, "type": "georoutepath", "version": version, "points": points if points is not None else [[-41.3, 174.8], [-41.31, 174.81], [-41.32, 174.82]],
            "dest": {"lat": -41.32, "lon": 174.82, "name": "Home"}, "waypoints": [], **extra}


def progress(version=1, **gps):
    return {"v": V, "type": "georoute", "gps": {"remaining": 900.0, "eta": 120.0, "off": 3.0, "name": "Home", "routeVersion": version, **gps}}


class LongLineDatagramTests(unittest.IsolatedAsyncioTestCase):
    async def test_the_longest_line_the_game_sends_arrives_in_one_datagram(self):
        # (1,500 points with every digit a double has: longer than the game writes them, so this is the worst case)
        points = [[-41.123456789012345 - i * 1e-5, -174.12345678901234 + i * 1e-5] for i in range(1500)]
        data = json.dumps(path(points=points), separators=(",", ":")).encode()
        self.assertLess(len(data), 65507)  # the most a UDP datagram over IPv4 can carry
        hub = car_hub()
        transport = await start_listener(hub, "127.0.0.1", 0)
        sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sender.sendto(data, transport.get_extra_info("sockname"))
            for _ in range(100):
                if hub.stats["paths"]:
                    break
                await asyncio.sleep(0.02)
            hub.ingest_packet(progress())
            self.assertEqual(len(hub.route_state()["points"]), 1500)
        finally:
            sender.close()
            transport.close()


class HubRouteLineTests(unittest.TestCase):
    def test_no_route_is_inactive(self):
        hub = car_hub()
        self.assertEqual(hub.route_state(), {"active": False})
        hub.ingest_packet({"v": V, "type": "georoute"})
        self.assertEqual(hub.route_state(), {"active": False})

    def test_a_route_has_its_progress_and_its_line(self):
        hub = car_hub()
        hub.ingest_packet(path(version=2))
        hub.ingest_packet(progress(version=2))
        state = hub.route_state()
        self.assertEqual((state["active"], state["version"], state["remaining"], state["eta"], state["off"], state["name"]), (True, 2, 900.0, 120.0, 3.0, "Home"))
        self.assertEqual(len(state["points"]), 3)
        self.assertEqual(state["dest"], {"lat": -41.32, "lon": 174.82, "name": "Home"})
        self.assertEqual(state["waypoints"], [])

    def test_a_line_is_only_given_for_the_version_it_belongs_to(self):
        hub = car_hub()
        hub.ingest_packet(path(version=1))
        hub.ingest_packet(progress(version=2))  # (re-planned: the old line is wrong)
        state = hub.route_state()
        self.assertTrue(state["active"])
        self.assertNotIn("points", state)
        hub.ingest_packet(path(version=2))
        self.assertIn("points", hub.route_state())

    def test_progress_without_a_version_has_no_line(self):
        hub = car_hub()
        hub.ingest_packet(path(version=1))
        hub.ingest_packet({"v": V, "type": "georoute", "gps": {"remaining": 5.0}})
        self.assertNotIn("points", hub.route_state())

    def test_the_route_goes_when_cleared_or_when_the_game_goes_quiet(self):
        hub = car_hub()
        with mock.patch("thrashdash.hub.time.monotonic", return_value=10.0):
            hub.ingest_packet(path())
            hub.ingest_packet(progress())
            self.assertTrue(hub.route_state()["active"])
            hub.ingest_packet({"v": V, "type": "georoute"})
            self.assertFalse(hub.route_state()["active"])
            hub.ingest_packet(progress())
        with mock.patch("thrashdash.hub.time.monotonic", return_value=10.0 + GEO_TTL_S + 1):
            self.assertEqual(hub.route_state(), {"active": False})

    def test_bad_lines_are_refused_and_cleaned(self):
        hub = car_hub()
        for bad in ({"version": "x", "points": []}, {"version": 1, "points": "line"}, {"version": 1, "points": [[1, 2]]}, {"version": 1, "points": [[95, 0], [0, 200]]},
                    {"version": 1, "points": [["a", 1], [None, 2]]}, {"version": float("nan"), "points": [[1, 2], [3, 4]]}):
            hub.ingest_packet({"v": V, "type": "georoutepath", **bad})
        self.assertEqual(hub.stats["rejected_path"], 6)
        hub.ingest_packet(path(points=[[1, 2], "x", [95, 3], [3.123456789, 4], [5, 6, 7], [7, 8]], waypoints=[{"lat": 1, "lon": 2, "name": "A"}, {"lat": 99, "lon": 0}, "x"],
                               dest={"lat": "far"}))
        hub.ingest_packet(progress())
        state = hub.route_state()
        self.assertEqual(state["points"], [[1.0, 2.0], [3.123457, 4.0], [7.0, 8.0]])
        self.assertEqual(state["waypoints"], [{"lat": 1.0, "lon": 2.0, "name": "A"}])
        self.assertIsNone(state["dest"])

    def test_the_line_is_capped(self):
        hub = car_hub()
        hub.ingest_packet(path(points=[[-41 + i * 1e-5, 174] for i in range(5000)]))
        hub.ingest_packet(progress())
        self.assertEqual(len(hub.route_state()["points"]), 1500)

    def test_the_games_answers_are_counted(self):
        hub = Hub()
        self.assertEqual(hub.route_ack["seq"], 0)
        hub.ingest_packet({"v": V, "type": "georouteack", "ok": True})
        hub.ingest_packet({"v": V, "type": "georouteack", "ok": False, "why": "x" * 500})
        self.assertEqual((hub.route_ack["seq"], hub.route_ack["ok"], len(hub.route_ack["why"])), (2, False, 120))
        hub.ingest_packet({"v": V, "type": "georouteack", "ok": "yes"})
        self.assertIs(hub.route_ack["ok"], False)  # (only a real true counts)

    def test_the_simulator_needs_no_line(self):
        hub = car_hub()
        hub.ingest_packet(progress())
        self.assertNotIn("points", hub.route_state())


class ApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        data = Path(self.tmp.name)
        self.hub = car_hub()
        self.notices = FakeNotices(self.hub, {"ok": True})
        self.destinations = DestinationStore(data / "destinations.json")
        self.port = free_port()
        app = make_app(self.hub, self.port, LayoutStore(data / "layouts", web_root() / "layouts"), SettingsStore(data / "settings.json"), notices=self.notices,
                       route_planner=RoutePlanner(self.notices, self.hub), destinations=self.destinations)
        self.runner = web.AppRunner(app, access_log=None)
        await self.runner.setup()
        await web.TCPSite(self.runner, "127.0.0.1", self.port).start()
        self.base = f"http://127.0.0.1:{self.port}/api"
        self.http = aiohttp.ClientSession()
        self.h = {"X-ThrashDash": "1"}

    async def asyncTearDown(self):
        await self.http.close()
        await self.runner.cleanup()
        self.tmp.cleanup()

    async def test_the_route_can_be_looked_at_by_anyone(self):
        async with self.http.get(f"{self.base}/route") as r:
            self.assertEqual(await r.json(), {"active": False, "game": True})
        self.hub.ingest_packet(path())
        self.hub.ingest_packet(progress())
        async with self.http.get(f"{self.base}/route") as r:
            got = await r.json()
        self.assertTrue(got["active"] and len(got["points"]) == 3 and got["dest"]["name"] == "Home" and got["game"] is True)

    async def test_setting_a_route_sends_the_stops_and_waits_for_the_games_word(self):
        async with self.http.post(f"{self.base}/route", json={"stops": [{"lat": -41.3, "lon": 174.8}, HOME]}, headers=self.h) as r:
            self.assertEqual((r.status, await r.json()), (200, {"ok": True}))
        self.assertEqual(self.notices.sent[0]["stops"][1]["name"], "Home")

    async def test_the_game_refusing_is_a_409_with_its_reason(self):
        self.notices.answer = {"ok": False, "why": "too far apart"}
        async with self.http.post(f"{self.base}/route", json={"stops": [HOME]}, headers=self.h) as r:
            self.assertEqual(r.status, 409)
            self.assertEqual((await r.json())["error"], "too far apart")

    async def test_no_game_is_a_409_saying_so(self):
        self.notices.connected = False
        async with self.http.post(f"{self.base}/route", json={"stops": [HOME]}, headers=self.h) as r:
            self.assertEqual(r.status, 409)
            self.assertIn("not connected", (await r.json())["error"])
        async with self.http.get(f"{self.base}/route") as r:
            self.assertFalse((await r.json())["game"])

    async def test_bad_stops_are_a_400(self):
        for body in ({}, {"stops": []}, {"stops": [{"lat": 95, "lon": 0}]}, {"stops": "home"}, [], {"stops": [HOME] * 12}):
            async with self.http.post(f"{self.base}/route", json=body, headers=self.h) as r:
                self.assertEqual(r.status, 400 if isinstance(body, dict) else 404, str(body)[:40])
        self.assertEqual(self.notices.sent, [])

    async def test_clearing_the_route(self):
        async with self.http.delete(f"{self.base}/route", headers=self.h) as r:
            self.assertEqual((r.status, await r.json()), (200, {"ok": True}))
        self.assertEqual(self.notices.sent, [{"type": "route", "action": "clear"}])

    async def test_changes_need_the_header(self):
        async with self.http.post(f"{self.base}/route", json={"stops": [HOME]}) as r:
            self.assertEqual(r.status, 403)
        async with self.http.delete(f"{self.base}/route") as r:
            self.assertEqual(r.status, 403)
        async with self.http.post(f"{self.base}/destinations", json={"name": "x", "lat": 1, "lon": 2}) as r:
            self.assertEqual(r.status, 403)
        self.assertEqual(self.notices.sent, [])
        self.assertEqual(self.destinations.list(), [])

    async def test_saved_destinations(self):
        async with self.http.post(f"{self.base}/destinations", json={"name": "Home", "lat": -41.2865, "lon": 174.7762}, headers=self.h) as r:
            saved = (await r.json())["destination"]
        async with self.http.get(f"{self.base}/destinations") as r:
            self.assertEqual(await r.json(), {"enabled": True, "destinations": [saved]})
        async with self.http.delete(f"{self.base}/destinations/{saved['id']}", headers=self.h) as r:
            self.assertEqual(r.status, 200)
        async with self.http.delete(f"{self.base}/destinations/{saved['id']}", headers=self.h) as r:
            self.assertEqual(r.status, 404)
        for bad in ({}, {"name": "", "lat": 1, "lon": 2}, {"name": "x", "lat": 99, "lon": 2}, {"name": "x"}):
            async with self.http.post(f"{self.base}/destinations", json=bad, headers=self.h) as r:
                self.assertEqual(r.status, 400, str(bad))

    async def test_a_run_without_route_planning_says_so(self):
        port = free_port()
        app = make_app(Hub(), port, LayoutStore(Path(self.tmp.name) / "l2", web_root() / "layouts"), SettingsStore(Path(self.tmp.name) / "s2.json"))
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        await web.TCPSite(runner, "127.0.0.1", port).start()
        try:
            async with self.http.get(f"http://127.0.0.1:{port}/api/destinations") as r:
                self.assertEqual(await r.json(), {"enabled": False, "destinations": []})
            async with self.http.post(f"http://127.0.0.1:{port}/api/route", json={"stops": [HOME]}, headers=self.h) as r:
                self.assertEqual(r.status, 404)
            async with self.http.get(f"http://127.0.0.1:{port}/api/route") as r:
                self.assertEqual((await r.json())["game"], False)
        finally:
            await runner.cleanup()


if __name__ == "__main__":
    unittest.main()

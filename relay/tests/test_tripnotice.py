"""Tests for trip popups: the text, who gets it (the game, the dashboard windows), and the settings that choose."""
import asyncio
import json
import socket
import sys
import tempfile
import unittest
from pathlib import Path

import aiohttp
from aiohttp import web

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from thrashdash.hub import Hub  # noqa: E402
from thrashdash.paths import web_root  # noqa: E402
from thrashdash.server import make_app  # noqa: E402
from thrashdash.store import LayoutStore, SettingsStore  # noqa: E402
from thrashdash.tripnotice import GAME_FRESH_S, KEEP, TripNotices, format_ended, format_started  # noqa: E402
from thrashdash.triplog import TripLog  # noqa: E402
from thrashdash.udp import start_listener  # noqa: E402
from thrashdash.validate import ValidationError, validate_settings  # noqa: E402
from test_triplog import Driver, free_port  # noqa: E402

SUMMARY = {"name": "Courtenay Place to Hutt Road", "dist": 2124.3, "dur": 146.0, "top_ms": 30.46,
           "limits": {"over_s": 79.0, "checked_s": 137.0}, "fuel": {"unit": "L", "used": 0.45, "per_100km": 21.2}}


class FakeTransport:
    def __init__(self):
        self.sent = []
        self.fail = False

    def sendto(self, data, addr):
        if self.fail:
            raise OSError("closed")
        self.sent.append((json.loads(data), addr))


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class TextTests(unittest.TestCase):
    def test_a_finished_trip_says_where_how_far_how_long_and_how_fast(self):
        title, msg = format_ended(SUMMARY, {"distance": "km", "speed": "kmh"})
        self.assertEqual(title, "Trip finished")
        self.assertEqual(msg, "Courtenay Place to Hutt Road: 2.12 km, 2 min 26 s, top 110 km/h, 1 min 19 s over the limit, 0.45 L")

    def test_it_follows_the_users_units(self):
        _, msg = format_ended(SUMMARY, {"distance": "mi", "speed": "mph"})
        self.assertIn("1.32 mi", msg)
        self.assertIn("top 68 mph", msg)

    def test_parts_with_nothing_to_say_are_left_out(self):
        _, msg = format_ended({"name": "A to B", "dist": 15000.0, "dur": 3700.0, "top_ms": 0, "limits": {"over_s": 0}}, {})
        self.assertEqual(msg, "A to B: 15.0 km, 1 h 01 min")
        _, bare = format_ended(None, None)
        self.assertEqual(bare, "Trip: 0.00 km, 0 s")

    def test_a_long_name_cannot_make_a_huge_message(self):
        _, msg = format_ended({"name": "x" * 5000, "dist": 1.0, "dur": 1.0}, {})
        self.assertLessEqual(len(msg), 200)

    def test_a_started_trip_names_the_vehicle_when_known(self):
        self.assertEqual(format_started({"vehicle": "etkc"}), ("Trip started", "Recording a new trip in the etkc"))
        self.assertEqual(format_started(None)[1], "Recording a new trip")


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.settings = SettingsStore(Path(self.tmp.name) / "settings.json")
        self.clock = Clock()
        self.notices = TripNotices(self.settings, clock=self.clock)
        self.transport = FakeTransport()
        self.notices.attach(self.transport)
        self.notices.seen_game(("127.0.0.1", 54321))

    def tearDown(self):
        self.tmp.cleanup()

    def test_by_default_the_game_hears_when_a_trip_ends_but_not_when_it_starts(self):
        self.notices.publish("started", {"vehicle": "etkc"})
        self.assertEqual(self.transport.sent, [])
        self.notices.publish("ended", SUMMARY)
        (packet, addr), = self.transport.sent
        self.assertEqual(addr, ("127.0.0.1", 54321))
        self.assertEqual(packet["type"], "toast")
        self.assertEqual(packet["seconds"], 12)  # (long enough to be seen while driving)
        self.assertEqual(packet["title"], "Trip finished")
        self.assertIn("2.12 km", packet["msg"])

    def test_the_start_popup_is_there_when_asked_for(self):
        self.settings.update({"tripNotices": {"gameStart": True}})
        self.notices.publish("started", {"vehicle": "etkc"})
        self.assertEqual(self.transport.sent[0][0]["title"], "Trip started")

    def test_each_popup_can_be_switched_off(self):
        self.settings.update({"tripNotices": {"game": False, "window": False}})
        self.notices.publish("ended", SUMMARY)
        self.assertEqual(self.transport.sent, [])
        self.assertEqual(self.notices.since(0), [])

    def test_the_windows_get_a_notice_only_for_the_end_and_numbered(self):
        self.notices.publish("started", {})
        self.assertEqual(self.notices.since(0), [])
        self.notices.publish("ended", SUMMARY)
        self.notices.publish("ended", SUMMARY)
        first, second = self.notices.since(0)
        self.assertEqual((first["id"], second["id"]), (1, 2))
        self.assertEqual(first["type"], "notice")
        self.assertEqual(self.notices.since(1), [second])
        self.assertEqual(self.notices.since(2), [])

    def test_only_the_latest_few_are_kept(self):
        for _ in range(KEEP + 5):
            self.notices.publish("ended", SUMMARY)
        self.assertEqual(len(self.notices.since(0)), KEEP)
        self.assertEqual(self.notices.seq, KEEP + 5)

    def test_no_message_goes_to_a_game_that_has_not_been_heard_from_or_has_gone_quiet(self):
        quiet = TripNotices(self.settings, clock=self.clock)
        quiet.attach(self.transport)
        quiet.publish("ended", SUMMARY)  # never heard from
        self.assertEqual(self.transport.sent, [])
        self.clock.t += GAME_FRESH_S + 1  # it was closed: its old port may belong to something else now
        self.notices.publish("ended", SUMMARY)
        self.assertEqual(self.transport.sent, [])
        self.notices.seen_game(("127.0.0.1", 60000))  # it came back on another port
        self.notices.publish("ended", SUMMARY)
        self.assertEqual(self.transport.sent[0][1], ("127.0.0.1", 60000))

    def test_a_socket_that_fails_does_not_stop_the_banner(self):
        self.transport.fail = True
        self.notices.publish("ended", SUMMARY)
        self.assertEqual(len(self.notices.since(0)), 1)

    def test_unreadable_settings_make_it_quiet_not_broken(self):
        class Broken:
            def get(self):
                raise RuntimeError("disk")
        TripNotices(Broken()).publish("ended", SUMMARY)  # no exception


    def test_what_happened_to_the_popups_is_counted(self):
        self.notices.publish("ended", SUMMARY)
        self.transport.fail = True
        self.notices.publish("ended", SUMMARY)
        self.clock.t += GAME_FRESH_S + 1
        self.notices.publish("ended", SUMMARY)
        self.assertEqual(dict(self.notices.stats), {"game_sent": 1, "windows": 3, "game_errors": 1, "game_unreachable": 1})

    def test_a_test_popup_ignores_the_settings_and_says_whether_the_game_got_it(self):
        self.settings.update({"tripNotices": {"game": False, "window": False}})
        self.assertEqual(self.notices.test(), {"game": True})
        self.assertEqual(self.transport.sent[0][0]["title"], "Test popup")
        self.assertEqual(self.notices.since(0)[0]["kind"], "test")
        self.clock.t += GAME_FRESH_S + 1
        self.assertEqual(self.notices.test(), {"game": False})  # (the dashboard windows still hear it)
        self.assertEqual(len(self.notices.since(0)), 2)


class TripLogHookTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.events = []
        self.log = TripLog(Path(self.tmp.name) / "triplog", say=lambda t: None, on_notice=lambda kind, info: self.events.append((kind, info)))
        self.d = Driver(self.log)

    def tearDown(self):
        self.log.close_all()
        self.tmp.cleanup()

    def test_a_trip_starting_and_ending_is_announced_once_each_with_its_summary(self):
        self.d.drive(60, speed=15)
        self.assertEqual([k for k, _ in self.events], ["started"])
        self.assertEqual(self.events[0][1], {"vehicle": "etkc"})
        self.log.close_all()
        self.assertEqual([k for k, _ in self.events], ["started", "ended"])
        self.assertGreater(self.events[1][1]["dist"], 800)

    def test_a_nudge_that_is_thrown_away_is_not_announced_as_finished(self):
        self.d.drive(5, speed=15)  # too short to keep
        self.log.close_all()
        self.assertEqual(self.events, [])  # (neither started nor finished: it was only a nudge)

    def test_a_trip_is_announced_as_started_only_once_it_is_a_real_one(self):
        self.d.drive(10, speed=15)  # 150 m but under 20 s
        self.assertEqual(self.events, [])
        self.d.drive(15, speed=15)
        self.assertEqual([k for k, _ in self.events], ["started"])
        self.d.drive(30, speed=15)
        self.assertEqual([k for k, _ in self.events], ["started"], "once, not on every sample")

    def test_a_listener_that_fails_never_breaks_the_trip_log(self):
        said = []
        log = TripLog(Path(self.tmp.name) / "other", say=said.append, on_notice=lambda *a: 1 / 0)
        Driver(log).drive(60, speed=15)
        log.close_all()
        self.assertEqual(len(log.list()), 1)
        self.assertEqual(log.stats["notice_errors"], 2)
        self.assertTrue(said)


class SettingsTests(unittest.TestCase):
    def test_validation(self):
        self.assertEqual(validate_settings({"tripNotices": {"game": False}})["tripNotices"], {"game": False})
        self.assertEqual(validate_settings({"tripNotices": {"game": True, "gameStart": True, "window": False, "other": 1}})["tripNotices"],
                         {"game": True, "gameStart": True, "window": False})
        for bad in ({"tripNotices": []}, {"tripNotices": {"game": "yes"}}, {"tripNotices": {"window": 1}}):
            with self.assertRaises(ValidationError):
                validate_settings(bad)

    def test_the_parked_wait_is_one_of_the_offered_minutes(self):
        for good in (1, 2, 5, 10, 30):
            self.assertEqual(validate_settings({"tripIdleEndMin": good})["tripIdleEndMin"], good)
        for bad in (0, 3, 60, -1, "5", 2.5, True, None.__class__, [2]):
            with self.assertRaises(ValidationError):
                validate_settings({"tripIdleEndMin": bad})

    def test_the_parked_wait_defaults_to_two_minutes_and_is_kept(self):
        with tempfile.TemporaryDirectory() as d:
            store = SettingsStore(Path(d) / "settings.json")
            self.assertEqual(store.get()["tripIdleEndMin"], 2)
            store.update({"tripIdleEndMin": 5})
            self.assertEqual(SettingsStore(Path(d) / "settings.json").get()["tripIdleEndMin"], 5)

    def test_partial_updates_keep_the_rest(self):
        with tempfile.TemporaryDirectory() as d:
            store = SettingsStore(Path(d) / "settings.json")
            store.update({"tripNotices": {"gameStart": True}})
            self.assertEqual(SettingsStore(Path(d) / "settings.json").get()["tripNotices"], {"game": True, "gameStart": True, "window": True})


class UdpRoundTripTests(unittest.IsolatedAsyncioTestCase):
    async def test_the_relay_answers_the_game_at_the_address_its_positions_came_from(self):
        with tempfile.TemporaryDirectory() as d:
            settings = SettingsStore(Path(d) / "settings.json")
            notices = TripNotices(settings)
            hub = Hub()
            port = free_port()
            transport = await start_listener(hub, "127.0.0.1", port, notices)
            game = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            game.bind(("127.0.0.1", 0))
            game.setblocking(False)
            try:
                notices.publish("ended", SUMMARY)  # before the game has been heard from: goes nowhere
                game.sendto(json.dumps({"v": 1, "type": "frame", "electrics": {"rpm": 1}}).encode(), ("127.0.0.1", port))
                await asyncio.sleep(0.1)
                self.assertIsNone(notices.game_addr, "telemetry from the car is not the trip-log extension")
                game.sendto(json.dumps({"v": 1, "type": "geo", "rt": 1.0, "lat": 1.0, "lon": 1.0, "ready": True}).encode(), ("127.0.0.1", port))
                await asyncio.sleep(0.1)
                self.assertEqual(notices.game_addr, game.getsockname())
                notices.publish("ended", SUMMARY)
                await asyncio.sleep(0.1)
                data, source = game.recvfrom(65535)
                self.assertEqual(source, ("127.0.0.1", port))  # (the extension only believes this address)
                self.assertEqual(json.loads(data)["title"], "Trip finished")
            finally:
                game.close()
                transport.close()


class TestPopupHttpTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        data = Path(self.tmp.name)
        self.settings = SettingsStore(data / "settings.json")
        self.notices = TripNotices(self.settings)
        self.transport = FakeTransport()
        self.notices.attach(self.transport)
        self.port = free_port()
        log = TripLog(data / "triplog", say=lambda t: None)
        self.log = log
        app = make_app(Hub(triplog=log), self.port, LayoutStore(data / "layouts", web_root() / "layouts"), self.settings, notices=self.notices)
        self.runner = web.AppRunner(app, access_log=None)
        await self.runner.setup()
        await web.TCPSite(self.runner, "127.0.0.1", self.port).start()
        self.base = f"http://127.0.0.1:{self.port}"
        self.http = aiohttp.ClientSession()

    async def asyncTearDown(self):
        await self.http.close()
        await self.runner.cleanup()
        self.tmp.cleanup()

    async def test_the_test_popup_button_reaches_the_game_and_the_windows(self):
        self.notices.seen_game(("127.0.0.1", 5555))
        async with self.http.post(f"{self.base}/api/triplog/test-popup", json={}, headers={"X-ThrashDash": "1"}) as r:
            self.assertEqual(r.status, 200)
            self.assertEqual(await r.json(), {"ok": True, "game": True})
        self.assertEqual(self.transport.sent[0][1], ("127.0.0.1", 5555))
        self.assertEqual(self.notices.since(0)[0]["title"], "Test popup")

    async def test_it_says_when_the_game_was_not_reached(self):
        async with self.http.post(f"{self.base}/api/triplog/test-popup", json={}, headers={"X-ThrashDash": "1"}) as r:
            self.assertEqual((await r.json())["game"], False)

    async def test_changing_the_parked_wait_applies_to_the_trip_log_at_once(self):
        self.assertEqual(self.log.idle_end_s, 120.0)
        async with self.http.put(f"{self.base}/api/settings", json={"tripIdleEndMin": 10}, headers={"X-ThrashDash": "1"}) as r:
            self.assertEqual(r.status, 200)
            self.assertEqual((await r.json())["tripIdleEndMin"], 10)
        self.assertEqual(self.log.idle_end_s, 600.0)
        async with self.http.put(f"{self.base}/api/settings", json={"tripIdleEndMin": 4}, headers={"X-ThrashDash": "1"}) as r:
            self.assertEqual(r.status, 400)
        self.assertEqual(self.log.idle_end_s, 600.0)

    async def test_it_needs_the_write_header_like_every_change(self):
        async with self.http.post(f"{self.base}/api/triplog/test-popup", json={}) as r:
            self.assertEqual(r.status, 403)

    async def test_the_trip_list_reports_what_happened_to_popups(self):
        self.notices.publish("ended", SUMMARY)
        async with self.http.get(f"{self.base}/api/triplog") as r:
            body = await r.json()
        self.assertEqual(body["notices"], {"game_unreachable": 1, "windows": 1})


class WebSocketTests(unittest.IsolatedAsyncioTestCase):
    async def test_dashboard_windows_get_the_notice_but_not_old_ones(self):
        with tempfile.TemporaryDirectory() as d:
            data = Path(d)
            settings = SettingsStore(data / "settings.json")
            notices = TripNotices(settings)
            notices.publish("ended", SUMMARY)  # before anyone is watching
            port = free_port()
            app = make_app(Hub(), port, LayoutStore(data / "layouts", web_root() / "layouts"), settings, notices=notices)
            runner = web.AppRunner(app, access_log=None)
            await runner.setup()
            await web.TCPSite(runner, "127.0.0.1", port).start()
            try:
                async with aiohttp.ClientSession() as http, http.ws_connect(f"http://127.0.0.1:{port}/ws") as ws:
                    await asyncio.sleep(0.4)
                    notices.publish("ended", {**SUMMARY, "name": "Second"})
                    seen = []
                    for _ in range(20):
                        msg = await asyncio.wait_for(ws.receive_json(), timeout=3)
                        seen.append(msg)
                        if msg.get("type") == "notice":
                            break
                    notice = seen[-1]
                    self.assertEqual(notice["type"], "notice")
                    self.assertIn("Second", notice["msg"])
                    self.assertEqual(notice["id"], 2)
                    self.assertEqual([m for m in seen if m.get("type") == "notice"], [notice], "the one from before it connected was not replayed")
            finally:
                await runner.cleanup()


if __name__ == "__main__":
    unittest.main()

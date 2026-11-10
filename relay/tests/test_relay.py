"""Relay tests. Run:  .venv/bin/python -m unittest discover -s relay/tests -v   (from the repo root)"""
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

from thrashdash import PROTOCOL_VERSION, simulator  # noqa: E402
from thrashdash.hub import Hub, flatten  # noqa: E402
from thrashdash.paths import web_root  # noqa: E402
from thrashdash.server import make_app  # noqa: E402
from thrashdash.store import LayoutStore, SettingsStore  # noqa: E402
from thrashdash.udp import start_listener  # noqa: E402


def free_port(kind=socket.SOCK_STREAM):
    with socket.socket(socket.AF_INET, kind) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def frame(electrics, vehicle="/vehicles/test/", v=PROTOCOL_VERSION):
    return {"v": v, "type": "frame", "n": 1, "vehicle": vehicle, "electrics": electrics}



def without_trip(channels):
    """The channels minus the trip computer's (trip.*), which every frame now carries."""
    return {k: v for k, v in channels.items() if not k.startswith("trip.")}


class HubTests(unittest.TestCase):
    def test_flatten_nested_and_types(self):
        flat = flatten({"rpm": 3000, "ign": True, "gear": "N", "wt": {"FL": {"core": 12.5}}, "junk": [1, 2], "nothing": None})
        self.assertEqual(flat, {"rpm": 3000, "ign": True, "gear": "N", "wt.FL.core": 12.5})

    def test_non_finite_numbers_become_none(self):
        flat = flatten({"a": float("inf"), "b": float("nan"), "c": 1})
        self.assertEqual(flat, {"a": None, "b": None, "c": 1})

    def test_frame_json_is_strict_json(self):
        hub = Hub()
        hub.ingest_packet(frame({"a": float("inf"), "c": 1}))

        def refuse(token):
            raise ValueError(token)

        decoded = json.loads(hub.frame_json(), parse_constant=refuse)  # raises on Infinity/NaN
        self.assertEqual(without_trip(decoded["d"]), {"a": None, "c": 1})

    def test_rejects_unknown_protocol_version_and_garbage(self):
        hub = Hub()
        hub.ingest_packet(frame({"rpm": 1}, v=99))
        hub.ingest_packet([1, 2, 3])
        hub.ingest_packet({"v": PROTOCOL_VERSION, "type": "frame", "electrics": "nope"})
        hub.ingest_packet({"v": PROTOCOL_VERSION, "type": "mystery"})
        self.assertEqual(hub.frame_seq, 0)
        self.assertFalse(hub.connected)
        self.assertEqual(hub.stats["rejected_version"], 1)

    def test_catalog_version_bumps_only_when_channels_or_vehicle_change(self):
        hub = Hub()
        hub.ingest_packet(frame({"rpm": 1}))
        v1 = hub.catalog_version
        hub.ingest_packet(frame({"rpm": 2}))
        self.assertEqual(hub.catalog_version, v1)
        hub.ingest_packet(frame({"rpm": 2, "fuel": 0.5}))
        self.assertGreater(hub.catalog_version, v1)
        v2 = hub.catalog_version
        hub.ingest_packet(frame({"rpm": 2, "fuel": 0.5}, vehicle="/vehicles/other/"))
        self.assertGreater(hub.catalog_version, v2)
        self.assertEqual(without_trip(hub.channels), {"rpm": "number", "fuel": "number"})

    def test_channel_count_is_capped(self):
        flat = flatten({f"c{i}": i for i in range(5000)})
        self.assertLessEqual(len(flat), 2000)

    def test_simulator_vehicles_take_turns(self):
        names = ("a", "b", "c")
        self.assertEqual([simulator.vehicle_at(t, names, 10) for t in (0, 9.9, 10, 25, 31, 100)], ["a", "a", "b", "c", "a", "b"])
        self.assertEqual(simulator.vehicle_at(123, ("only",), 5), "only")
        self.assertEqual(simulator.make_frame(1.0, 1, "etkc")["vehicle"], "/vehicles/etkc/")
        self.assertEqual(simulator.make_frame(1.0, 1)["vehicle"], "/vehicles/simulator/")

    def test_simulator_frames_are_accepted_by_the_hub(self):
        hub = Hub(simulated=True)
        for i in range(0, 4000, 7):
            hub.ingest_packet(simulator.make_frame(i / 100, i))
        self.assertTrue(hub.connected)
        for needed in ("rpm", "wheelspeed", "gear", "gearIndex", "fuel", "watertemp", "wheelThermals.FL.brakeCoreTemperature"):
            self.assertIn(needed, hub.channels)


def slow(vehicle="/vehicles/test/", **sections):  # same default vehicle as frame(), or the hub rightly sees a vehicle change
    return {"v": PROTOCOL_VERSION, "type": "slow", "vehicle": vehicle, **sections}


class SlowDataTests(unittest.TestCase):
    """Tyres, damage and component states arrive in separate low-rate packets and merge with the electrics frame."""

    def hub_with_frame(self):
        hub = Hub()
        hub.ingest_packet(frame({"rpm": 3000, "gear": "N"}))
        return hub

    def test_slow_data_joins_the_frame_under_its_own_names(self):
        hub = self.hub_with_frame()
        hub.ingest_packet(slow(tire={"FL": {"temp": 80.5, "pressure": 220, "deflated": False}}, damage={"zone": {"FR": 0.5}, "total": 12},
                               state={"engine": {"oilOverheating": True}}))
        self.assertEqual(hub.values["rpm"], 3000)  # electrics untouched
        self.assertEqual((hub.values["tire.FL.temp"], hub.values["tire.FL.pressure"], hub.values["tire.FL.deflated"]), (80.5, 220, False))
        self.assertEqual((hub.values["damage.zone.FR"], hub.values["damage.total"], hub.values["state.engine.oilOverheating"]), (0.5, 12, True))
        self.assertEqual(hub.channels["tire.FL.temp"], "number")
        self.assertEqual(hub.channels["state.engine.oilOverheating"], "boolean")

    def test_new_electrics_frames_keep_the_slow_data(self):
        hub = self.hub_with_frame()
        hub.ingest_packet(slow(tire={"FL": {"temp": 80}}))
        hub.ingest_packet(frame({"rpm": 4100, "gear": "N"}))
        self.assertEqual((hub.values["rpm"], hub.values["tire.FL.temp"]), (4100, 80))

    def test_a_slow_packet_is_a_complete_snapshot_so_missing_sections_clear(self):
        hub = self.hub_with_frame()
        hub.ingest_packet(slow(tire={"FL": {"temp": 80}}, state={"engine": {"oilOverheating": True}}))
        hub.ingest_packet(slow(tire={"FL": {"temp": 81}}))  # the car was repaired: no states any more
        self.assertEqual(hub.values["tire.FL.temp"], 81)
        self.assertNotIn("state.engine.oilOverheating", hub.values)
        self.assertNotIn("state.engine.oilOverheating", hub.channels)

    def test_an_empty_lua_table_arrives_as_an_empty_list_and_means_none(self):
        hub = self.hub_with_frame()
        hub.ingest_packet(slow(state={"engine": {"a": True}}))
        hub.ingest_packet(slow(tire={"FL": {"temp": 1}}, state=[]))
        self.assertNotIn("state.engine.a", hub.values)
        self.assertEqual(hub.stats["rejected_slow_section"], 0)

    def test_bad_sections_are_ignored_and_counted(self):
        hub = self.hub_with_frame()
        hub.ingest_packet(slow(tire="nonsense", damage=[1, 2], state={"engine": {"ok": True}}))
        self.assertEqual(hub.values["state.engine.ok"], True)
        self.assertEqual(hub.stats["rejected_slow_section"], 2)
        self.assertEqual(hub.values["rpm"], 3000)

    def test_another_vehicle_clears_the_old_ones_slow_data(self):
        hub = self.hub_with_frame()
        hub.ingest_packet(slow(tire={"FL": {"temp": 80}}))
        hub.ingest_packet(frame({"rpm": 900}, vehicle="/vehicles/other/"))
        self.assertNotIn("tire.FL.temp", hub.values)
        hub.ingest_packet(slow(vehicle="/vehicles/other/", tire={"FL": {"temp": 30}}))
        self.assertEqual(hub.values["tire.FL.temp"], 30)

    def test_a_slow_packet_refreshes_browsers_but_does_not_count_as_a_live_connection(self):
        hub = Hub()
        hub.ingest_packet(slow(tire={"FL": {"temp": 80}}))
        self.assertFalse(hub.connected)
        self.assertGreater(hub.frame_seq, 0)
        self.assertEqual(hub.values["tire.FL.temp"], 80)

    def test_non_finite_slow_values_become_none_and_do_not_break_the_json(self):
        hub = self.hub_with_frame()
        hub.ingest_packet(slow(tire={"FL": {"temp": float("inf"), "pressure": 200}}))
        text = hub.frame_json()
        json.loads(text, parse_constant=lambda tok: (_ for _ in ()).throw(ValueError(tok)))  # strict JSON: no Infinity
        self.assertNotIn("tire.FL.temp", hub.channels)
        self.assertEqual(hub.channels["tire.FL.pressure"], "number")

    def test_the_catalog_only_changes_when_the_channels_do(self):
        hub = self.hub_with_frame()
        hub.ingest_packet(slow(tire={"FL": {"temp": 80}}))
        version = hub.catalog_version
        for i in range(5):
            hub.ingest_packet(frame({"rpm": 3000 + i, "gear": "N"}))
            hub.ingest_packet(slow(tire={"FL": {"temp": 80 + i}}))
        self.assertEqual(hub.catalog_version, version)
        hub.ingest_packet(slow(tire={"FL": {"temp": 80}, "FR": {"temp": 80}}))
        self.assertGreater(hub.catalog_version, version)

    def test_a_non_finite_electrics_value_no_longer_makes_the_catalog_churn(self):
        # regression: a None-valued channel used to make the "did the channels change?" comparison fail on EVERY frame
        hub = Hub()
        hub.ingest_packet(frame({"rpm": 1, "weird": float("inf")}))
        version = hub.catalog_version
        for _ in range(5):
            hub.ingest_packet(frame({"rpm": 2, "weird": float("inf")}))
        self.assertEqual(hub.catalog_version, version)

    def test_the_simulators_slow_data_is_accepted_and_plausible(self):
        hub = self.hub_with_frame()
        names = set()
        for i in range(0, 6000, 25):
            hub.ingest_packet(simulator.make_slow(i / 10))
            names |= set(hub.channels)
            for w in ("FL", "FR", "RL", "RR"):
                p = hub.values[f"tire.{w}.pressure"]
                self.assertTrue(0 <= p < 400, (i, w, p))
            for z in ("FL", "FR", "ML", "MR", "RL", "RR"):
                self.assertTrue(0 <= hub.values[f"damage.zone.{z}"] <= 1, (i, z))
        for needed in ("tire.FL.temp", "tire.RR.coreTemp", "tire.FR.deflated", "damage.total", "damage.brokenPct", "state.engine.oilOverheating", "state.wheels.brakeOverHeatFL"):
            self.assertIn(needed, names)


class ServerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.hub = Hub()
        self.http_port = free_port()
        self.udp_port = free_port(socket.SOCK_DGRAM)
        self.tmp = tempfile.TemporaryDirectory()
        data = Path(self.tmp.name)
        app = make_app(self.hub, self.http_port, LayoutStore(data / "layouts", web_root() / "layouts"), SettingsStore(data / "settings.json"))
        self.runner = web.AppRunner(app, access_log=None)
        await self.runner.setup()
        await web.TCPSite(self.runner, "127.0.0.1", self.http_port).start()
        self.transport = await start_listener(self.hub, "127.0.0.1", self.udp_port)
        self.base = f"http://127.0.0.1:{self.http_port}"
        self.session = aiohttp.ClientSession()

    async def asyncTearDown(self):
        await self.session.close()
        self.transport.close()
        await self.runner.cleanup()
        self.tmp.cleanup()

    async def test_index_served_with_security_headers(self):
        async with self.session.get(self.base + "/") as r:
            self.assertEqual(r.status, 200)
            self.assertIn("text/html", r.headers["Content-Type"])
            self.assertIn("default-src 'none'", r.headers["Content-Security-Policy"])
            self.assertEqual(r.headers["X-Content-Type-Options"], "nosniff")
            self.assertIn("ThrashDash", await r.text())

    async def test_static_assets_served_and_traversal_refused(self):
        async with self.session.get(self.base + "/js/app.js") as r:
            self.assertEqual(r.status, 200)
        async with self.session.get(self.base + "/%2e%2e/relay/run.py") as r:
            self.assertIn(r.status, (403, 404))

    async def test_wrong_host_header_refused(self):
        async with self.session.get(self.base + "/api/status", headers={"Host": "evil.example"}) as r:
            self.assertEqual(r.status, 403)

    async def test_cross_origin_websocket_refused(self):
        with self.assertRaises(aiohttp.WSServerHandshakeError) as cm:
            await self.session.ws_connect(self.base + "/ws", headers={"Origin": "http://evil.example"})
        self.assertEqual(cm.exception.status, 403)

    async def test_udp_to_websocket_end_to_end(self):
        async with self.session.ws_connect(self.base + "/ws", headers={"Origin": self.base}) as ws:
            self.assertEqual((await ws.receive_json(timeout=2))["type"], "hello")
            sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            # json.dumps writes the literal Infinity the way a hostile/odd sender might.
            datagram = json.dumps(frame({"rpm": 4200, "gear": "N", "bad": float("inf")})).encode()
            got = {}
            for _ in range(20):  # UDP can drop; resend until the pipeline has delivered
                sender.sendto(datagram, ("127.0.0.1", self.udp_port))
                try:
                    msg = await ws.receive_json(timeout=0.3)
                except asyncio.TimeoutError:
                    continue
                got[msg["type"]] = msg
                if got.get("frame", {}).get("d") and got.get("catalog", {}).get("channels"):
                    break
            sender.close()
            self.assertEqual(without_trip(got["frame"]["d"]), {"rpm": 4200, "gear": "N", "bad": None})
            self.assertEqual(without_trip(got["catalog"]["channels"]), {"rpm": "number", "gear": "string"})
            self.assertEqual(got["catalog"]["vehicle"], "/vehicles/test/")

    async def test_udp_garbage_is_counted_not_fatal(self):
        sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sender.sendto(b"\xff\xfe not json", ("127.0.0.1", self.udp_port))
        sender.sendto(b"[" * 60000, ("127.0.0.1", self.udp_port))  # deeply nested: must not crash the parser
        sender.close()
        for _ in range(20):
            await asyncio.sleep(0.05)
            if self.hub.stats["rejected_json"] >= 2:
                break
        self.assertEqual(self.hub.stats["rejected_json"], 2)
        async with self.session.get(self.base + "/api/status") as r:
            self.assertEqual(r.status, 200)


if __name__ == "__main__":
    unittest.main()

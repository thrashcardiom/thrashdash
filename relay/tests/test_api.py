"""Tests for layout validation, storage and the REST API."""
import copy
import json
import socket
import sys
import tempfile
import unittest
from pathlib import Path

import aiohttp
from aiohttp import web

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from thrashdash.api import is_loopback  # noqa: E402
from thrashdash.hub import Hub  # noqa: E402
from thrashdash.paths import web_root  # noqa: E402
from thrashdash.server import make_app  # noqa: E402
from thrashdash.store import MAX_USER_LAYOUTS, LayoutStore, NotFound, ReadOnly, SettingsStore  # noqa: E402
from thrashdash.validate import ValidationError, validate_layout, validate_settings  # noqa: E402

DEFAULT = json.loads((web_root() / "layouts" / "default.json").read_text(encoding="utf-8"))
H = {"X-ThrashDash": "1"}


def small_layout(**over):
    layout = {"format": 1, "name": "Mine", "width": 800, "height": 300,
              "widgets": [{"id": "g1", "type": "gear", "x": 1, "y": 2, "w": 100, "h": 100, "channel": "gear"}]}
    layout.update(over)
    return layout


class ValidateTests(unittest.TestCase):
    def test_default_layout_is_valid_and_unchanged(self):
        self.assertEqual(validate_layout(DEFAULT), DEFAULT)

    def test_rejects_structural_problems(self):
        bad = [
            None, [], {"format": 2, "name": "x", "width": 800, "height": 300, "widgets": []},
            small_layout(width=1), small_layout(width="800"), small_layout(width=float("nan")), small_layout(height=99999),
            small_layout(widgets="nope"), small_layout(widgets=[1]), small_layout(widgets=[{"type": "gear"}]),
            small_layout(widgets=[{"type": "gear", "x": 0, "y": 0, "w": 0, "h": 10}]),
            small_layout(widgets=[{"type": "gear", "x": 0, "y": 0, "w": 10, "h": 10, "__proto__": 1}]),
            small_layout(widgets=[{"type": "gear", "x": 0, "y": 0, "w": 10, "h": 10, "1bad": 1}]),
            small_layout(widgets=[{"type": "gear", "x": 0, "y": 0, "w": 10, "h": 10, "label": "a\x00b"}]),
            small_layout(widgets=[{"type": "gear", "x": 0, "y": 0, "w": 10, "h": 10, "label": "x" * 201}]),
            small_layout(widgets=[{"type": "gear", "x": 0, "y": 0, "w": 10, "h": 10, "deep": {"a": {"b": 1}}}]),
            small_layout(widgets=[{"type": "gear", "x": 0, "y": 0, "w": 10, "h": 10, "many": list(range(21))}]),
            small_layout(widgets=[{"type": "gear", "x": 0, "y": 0, "w": 10, "h": 10, "id": "has space"}]),
            small_layout(widgets=[{"id": "a", "type": "gear", "x": 0, "y": 0, "w": 10, "h": 10}] * 2),
            small_layout(widgets=[{"type": "gear", "x": 0, "y": 0, "w": 10, "h": 10}] * 201),
            small_layout(name="n" * 61),
        ]
        for raw in bad:
            with self.assertRaises(ValidationError, msg=repr(raw)[:120]):
                validate_layout(raw)

    def test_error_messages_name_the_path(self):
        with self.assertRaisesRegex(ValidationError, r"widgets\[1\]\.w"):
            validate_layout(small_layout(widgets=[small_layout()["widgets"][0], {"type": "gear", "x": 0, "y": 0, "w": "x", "h": 1}]))

    def test_unknown_top_level_keys_are_dropped(self):
        self.assertNotIn("evil", validate_layout(small_layout(evil="x")))

    def test_settings(self):
        self.assertEqual(validate_settings({"units": {"speed": "mph", "mystery": "x"}, "junk": 1}), {"units": {"speed": "mph"}})
        self.assertEqual(validate_settings({"defaultLayout": "u-0123456789ab"}), {"defaultLayout": "u-0123456789ab"})
        for bad in (None, [], {"units": []}, {"units": {"speed": "furlongs"}}, {"defaultLayout": "../x"}, {"defaultLayout": 5}):
            with self.assertRaises(ValidationError, msg=repr(bad)):
                validate_settings(bad)


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.skipped = []
        self.store = LayoutStore(self.dir / "user", web_root() / "layouts", on_skip=lambda p, why: self.skipped.append((p.name, why)))

    def tearDown(self):
        self.tmp.cleanup()

    def test_listing_order_default_then_your_dashboards_then_templates(self):
        mine = [self.store.create(small_layout(name=n)) for n in ("Zebra", "apple")]
        listing = self.store.list()
        ids = [i["id"] for i in listing]
        self.assertEqual(ids[0], "default")
        self.assertEqual([i["name"] for i in listing[1:3]], ["apple", "Zebra"])  # user dashboards by name, ignoring case
        self.assertEqual(set(ids[1:3]), set(mine))
        self.assertTrue(all(i["builtin"] for i in listing[3:]))  # then only templates
        self.assertEqual([i["id"] for i in listing[3:]], sorted(i["id"] for i in listing[3:]))

    def test_builtin_listed_and_readonly(self):
        listing = self.store.list()
        self.assertEqual([i["id"] for i in listing if i["builtin"]], ["default", "cluster", "drift", "driver", "health", "minimal", "offroad", "phone", "telemetry", "trip"])  # the plain default leads, then A-Z
        layout, builtin = self.store.get("default")
        self.assertTrue(builtin)
        with self.assertRaises(ReadOnly):
            self.store.save("default", small_layout())
        with self.assertRaises(ReadOnly):
            self.store.delete("default")

    def test_create_get_save_delete_roundtrip(self):
        lid = self.store.create(small_layout())
        self.assertRegex(lid, r"^u-[0-9a-f]{12}$")
        self.assertEqual(self.store.get(lid)[0]["name"], "Mine")
        self.store.save(lid, small_layout(name="Renamed"))
        self.assertEqual(self.store.get(lid)[0]["name"], "Renamed")
        self.assertIn(lid, [i["id"] for i in self.store.list()])
        self.store.delete(lid)
        with self.assertRaises(NotFound):
            self.store.get(lid)

    def test_writes_are_atomic_and_leave_no_temp_files(self):
        lid = self.store.create(small_layout())
        for i in range(5):
            self.store.save(lid, small_layout(name=f"v{i}"))
        self.assertEqual([p.name for p in (self.dir / "user").iterdir()], [f"{lid}.json"])

    def test_invalid_layout_never_touches_disk(self):
        with self.assertRaises(ValidationError):
            self.store.create(small_layout(width=1))
        self.assertFalse((self.dir / "user").exists() and any((self.dir / "user").iterdir()))

    def test_ids_cannot_escape_the_layouts_folder(self):
        (self.dir / "user").mkdir()  # must exist, or "user/../x" cannot resolve and the test proves nothing
        (self.dir / "secret.json").write_text(json.dumps(small_layout()))
        for evil in ["../secret", "..\\secret", "u-../../secret", "/etc/passwd", "default/../../x", "u-" + "a" * 12 + "/../x", "", "DEFAULT", "a" * 41, "x.json"]:
            with self.assertRaises(NotFound, msg=evil):
                self.store.get(evil)
            with self.assertRaises((NotFound, ReadOnly), msg=evil):
                self.store.save(evil, small_layout())
            with self.assertRaises((NotFound, ReadOnly), msg=evil):
                self.store.delete(evil)
        self.assertTrue((self.dir / "secret.json").exists())

    def test_cannot_save_or_delete_nonexistent_user_layout(self):
        with self.assertRaises(NotFound):
            self.store.save("u-000000000000", small_layout())
        with self.assertRaises(NotFound):
            self.store.delete("u-000000000000")

    def test_corrupt_files_are_skipped_not_fatal(self):
        user = self.dir / "user"
        user.mkdir()
        (user / "u-aaaaaaaaaaaa.json").write_text("{not json")
        (user / "u-bbbbbbbbbbbb.json").write_text(json.dumps({"format": 1, "name": "bad", "width": 1, "height": 1, "widgets": []}))
        good = self.store.create(small_layout())
        ids = [i["id"] for i in self.store.list()]
        self.assertIn(good, ids)
        self.assertNotIn("u-aaaaaaaaaaaa", ids)
        self.assertEqual(len(self.skipped), 2)

    def test_layout_count_is_limited(self):
        user = self.dir / "user"
        user.mkdir()
        for i in range(MAX_USER_LAYOUTS):
            (user / f"u-{i:012x}.json").write_text("{}")
        with self.assertRaisesRegex(ValidationError, "too many"):
            self.store.create(small_layout())


class SettingsStoreTests(unittest.TestCase):
    def test_the_impact_rule_validates_merges_and_persists(self):
        from thrashdash.validate import validate_settings, ValidationError
        self.assertEqual(validate_settings({"impactRule": {"damage": 2, "g": 0}})["impactRule"], {"damage": 2.0, "g": 0.0})
        for bad in ({"damage": 0}, {"damage": 101}, {"damage": "1"}, {"damage": True}, {"g": 1}, {"g": 21}, {"g": float("nan")}, {"g": None}, 5):
            with self.assertRaises(ValidationError, msg=bad):
                validate_settings({"impactRule": bad})
        with tempfile.TemporaryDirectory() as d:
            store = SettingsStore(Path(d) / "settings.json")
            self.assertEqual(store.get()["impactRule"], {"damage": 1.0, "g": 4.0})
            store.update({"impactRule": {"g": 6}})
            self.assertEqual(SettingsStore(Path(d) / "settings.json").get()["impactRule"], {"damage": 1.0, "g": 6.0}, "one half changes, the other stays")

    def test_defaults_update_persist_and_recover_from_corruption(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "settings.json"
            store = SettingsStore(path)
            self.assertEqual(store.get(), {"units": {"speed": "kmh", "temp": "c", "distance": "km", "pressure": "psi", "economy": "auto"}, "defaultLayout": "default", "skins": {}, "vehicleDashboards": {}, "autoSwitch": True, "lan": False, "alerts": {"enabled": True, "off": []},
                                         "tripNotices": {"game": True, "gameStart": False, "window": True}, "tripIdleEndMin": 2, "restoreWindows": True, "exactWindowPlacement": False, "closeWindowsOnExit": True, "earthPath": "", "impactRule": {"damage": 1.0, "g": 4.0}, "fuelPrice": {"liquid": 0.0, "electric": 0.0, "currency": ""}})
            store.update({"units": {"speed": "mph"}})
            self.assertEqual(SettingsStore(path).get()["units"], {"speed": "mph", "temp": "c", "distance": "km", "pressure": "psi", "economy": "auto"})  # partial update keeps the rest
            path.write_text("garbage{")
            self.assertEqual(store.get()["units"]["speed"], "kmh")  # corrupt file falls back to defaults


class LoopbackTests(unittest.TestCase):
    def test_is_loopback(self):
        for ok in ("127.0.0.1", "127.5.5.5", "::1", "::ffff:127.0.0.1"):
            self.assertTrue(is_loopback(ok), ok)
        for bad in ("192.168.1.5", "10.0.0.2", "::ffff:192.168.1.5", "2001:db8::1", "", None, "garbage"):
            self.assertFalse(is_loopback(bad), str(bad))


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class ApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        data = Path(self.tmp.name)
        self.port = free_port()
        app = make_app(Hub(), self.port, LayoutStore(data / "layouts", web_root() / "layouts"), SettingsStore(data / "settings.json"))
        self.runner = web.AppRunner(app, access_log=None)
        await self.runner.setup()
        await web.TCPSite(self.runner, "127.0.0.1", self.port).start()
        self.base = f"http://127.0.0.1:{self.port}"
        self.http = aiohttp.ClientSession()
        self.data = data

    async def asyncTearDown(self):
        await self.http.close()
        await self.runner.cleanup()
        self.tmp.cleanup()

    async def test_list_and_get_builtin(self):
        async with self.http.get(self.base + "/api/layouts") as r:
            self.assertEqual(r.status, 200)
            self.assertEqual([x["id"] for x in (await r.json())["layouts"]][:3], ["default", "cluster", "drift"])
        async with self.http.get(self.base + "/api/layouts/default") as r:
            body = await r.json()
            self.assertTrue(body["builtin"])
            self.assertEqual(body["layout"]["name"], "Default")

    async def test_full_crud_cycle(self):
        async with self.http.post(self.base + "/api/layouts", json=small_layout(), headers=H) as r:
            self.assertEqual(r.status, 201)
            lid = (await r.json())["id"]
        async with self.http.put(f"{self.base}/api/layouts/{lid}", json=small_layout(name="Second"), headers=H) as r:
            self.assertEqual((await r.json())["layout"]["name"], "Second")
        async with self.http.get(f"{self.base}/api/layouts/{lid}") as r:
            self.assertEqual((await r.json())["layout"]["name"], "Second")
        async with self.http.delete(f"{self.base}/api/layouts/{lid}", headers=H) as r:
            self.assertEqual(r.status, 200)
        async with self.http.get(f"{self.base}/api/layouts/{lid}") as r:
            self.assertEqual(r.status, 404)

    async def test_writes_need_the_custom_header(self):
        for method, path in (("POST", "/api/layouts"), ("PUT", "/api/layouts/u-000000000000"), ("DELETE", "/api/layouts/u-000000000000"), ("PUT", "/api/settings")):
            async with self.http.request(method, self.base + path, json=small_layout()) as r:
                self.assertEqual(r.status, 403, f"{method} {path}")
        self.assertFalse((self.data / "layouts").exists())

    async def test_cross_origin_writes_refused_even_with_header(self):
        async with self.http.post(self.base + "/api/layouts", json=small_layout(), headers={**H, "Origin": "http://evil.example"}) as r:
            self.assertEqual(r.status, 403)
        self.assertFalse((self.data / "layouts").exists())

    async def test_options_preflight_is_not_answered(self):
        async with self.http.options(self.base + "/api/layouts", headers={"Origin": "http://evil.example", "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "x-thrashdash"}) as r:
            self.assertEqual(r.status, 403)
            self.assertNotIn("Access-Control-Allow-Origin", r.headers)

    async def test_builtin_cannot_be_modified(self):
        async with self.http.put(self.base + "/api/layouts/default", json=small_layout(), headers=H) as r:
            self.assertEqual(r.status, 403)
            self.assertIn("read-only", (await r.json())["error"])
        async with self.http.delete(self.base + "/api/layouts/default", headers=H) as r:
            self.assertEqual(r.status, 403)

    async def test_bad_input_gets_clear_errors(self):
        async with self.http.post(self.base + "/api/layouts", json=small_layout(width=1), headers=H) as r:
            self.assertEqual(r.status, 400)
            self.assertIn("width", (await r.json())["error"])
        async with self.http.post(self.base + "/api/layouts", data=b"{broken", headers={**H, "Content-Type": "application/json"}) as r:
            self.assertEqual(r.status, 400)
        async with self.http.post(self.base + "/api/layouts", data=b"x" * (300 * 1024), headers={**H, "Content-Type": "application/json"}) as r:
            self.assertEqual(r.status, 413)
        async with self.http.get(self.base + "/api/layouts/..%2f..%2fetc%2fpasswd") as r:
            self.assertEqual(r.status, 404)

    async def test_error_responses_carry_security_headers(self):
        async with self.http.get(self.base + "/does-not-exist") as r:
            self.assertEqual(r.status, 404)
            self.assertIn("default-src 'none'", r.headers["Content-Security-Policy"])
            self.assertEqual(r.headers["X-Content-Type-Options"], "nosniff")

    async def test_settings_roundtrip_and_validation(self):
        async with self.http.get(self.base + "/api/settings") as r:
            self.assertEqual((await r.json())["units"]["speed"], "kmh")
        async with self.http.put(self.base + "/api/settings", json={"units": {"speed": "mph"}}, headers=H) as r:
            self.assertEqual((await r.json())["units"], {"speed": "mph", "temp": "c", "distance": "km", "pressure": "psi", "economy": "auto"})
        async with self.http.put(self.base + "/api/settings", json={"units": {"speed": "furlongs"}}, headers=H) as r:
            self.assertEqual(r.status, 400)
        async with self.http.get(self.base + "/api/settings") as r:
            self.assertEqual((await r.json())["units"]["speed"], "mph")  # the bad request changed nothing

    async def test_deleting_a_dashboard_cleans_up_settings(self):
        async with self.http.post(self.base + "/api/layouts", json=small_layout(), headers=H) as r:
            lid = (await r.json())["id"]
        async with self.http.put(self.base + "/api/settings", json={"defaultLayout": lid, "skins": {lid: "backlit"}, "vehicleDashboards": {"etkc": lid}}, headers=H) as r:
            self.assertEqual(r.status, 200)
        async with self.http.delete(f"{self.base}/api/layouts/{lid}", headers=H) as r:
            self.assertEqual(r.status, 200)
        async with self.http.get(self.base + "/api/settings") as r:
            s = await r.json()
        self.assertEqual((s["defaultLayout"], s["skins"], s["vehicleDashboards"]), ("default", {}, {}))

    async def test_default_layout_file_is_served_as_a_static_asset_too(self):
        # The editor imports/exports layout JSON; make sure the shipped file is what the API returns.
        async with self.http.get(self.base + "/api/layouts/default") as r:
            served = (await r.json())["layout"]
        self.assertEqual(served, validate_layout(copy.deepcopy(DEFAULT)))


if __name__ == "__main__":
    unittest.main()


class ScaleFieldTests(unittest.TestCase):
    def test_scale_fields_validated_and_kept(self):
        out = validate_layout(small_layout(scaleMode="fixed", scalePercent=60))
        self.assertEqual((out["scaleMode"], out["scalePercent"]), ("fixed", 60))

    def test_layouts_without_scale_fields_stay_valid_and_get_none_added(self):
        out = validate_layout(small_layout())
        self.assertNotIn("scaleMode", out)
        self.assertNotIn("scalePercent", out)

    def test_bad_scale_values_rejected(self):
        for bad in (dict(scaleMode="banana"), dict(scaleMode=5), dict(scalePercent=5), dict(scalePercent=401),
                    dict(scalePercent="60"), dict(scalePercent=float("nan"))):
            with self.assertRaises(ValidationError, msg=repr(bad)):
                validate_layout(small_layout(**bad))


class SkinSettingTests(unittest.TestCase):
    """A skin is a per-dashboard DISPLAY setting (settings.skins), not part of a layout."""

    def test_layouts_carry_no_skin(self):
        # An old file with a "skin" key still loads; the key is just ignored like any unknown one.
        self.assertNotIn("skin", validate_layout(small_layout(skin="backlit")))
        self.assertNotIn("skin", validate_layout(small_layout(skin="not-even-a-skin")))

    def test_settings_validation(self):
        ok = validate_settings({"skins": {"default": "backlit", "u-0123456789ab": "classicWhite"}})
        self.assertEqual(ok["skins"], {"default": "backlit", "u-0123456789ab": "classicWhite"})
        for bad in ({"skins": []}, {"skins": {"default": "neon"}}, {"skins": {"../x": "backlit"}}, {"skins": {"default": 5}},
                    {"skins": {f"d{i}": "backlit" for i in range(201)}}):
            with self.assertRaises(ValidationError, msg=repr(bad)[:60]):
                validate_settings(bad)

    def test_alert_settings(self):
        self.assertEqual(validate_settings({"alerts": {"enabled": False, "off": ["fuel", "fuel", "overRev"]}})["alerts"], {"enabled": False, "off": ["fuel", "overRev"]})
        self.assertEqual(validate_settings({"alerts": {}})["alerts"], {})
        self.assertEqual(validate_settings({"alerts": {"on": ["offRoute", "offRoute"]}})["alerts"], {"on": ["offRoute"]})
        for bad in ({"alerts": []}, {"alerts": {"enabled": "yes"}}, {"alerts": {"off": "fuel"}}, {"alerts": {"off": [5]}},
                    {"alerts": {"off": ["../x"]}}, {"alerts": {"off": ["a" + str(i) for i in range(41)]}}, {"alerts": {"on": "offRoute"}}, {"alerts": {"on": [5]}}, {"alerts": {"on": ["../x"]}}):
            with self.assertRaises(ValidationError, msg=repr(bad)[:60]):
                validate_settings(bad)
        with tempfile.TemporaryDirectory() as d:
            store = SettingsStore(Path(d) / "settings.json")
            self.assertEqual(store.get()["alerts"], {"enabled": True, "off": []})  # alerts are on by default
            store.update({"alerts": {"off": ["fuel"]}})
            store.update({"alerts": {"enabled": False}})  # a partial update keeps the other half
            self.assertEqual(SettingsStore(Path(d) / "settings.json").get()["alerts"], {"enabled": False, "off": ["fuel"]})
            store.update({"units": {"speed": "mph"}})  # unrelated updates leave alerts alone
            self.assertEqual(store.get()["alerts"], {"enabled": False, "off": ["fuel"]})
            store.update({"alerts": {"on": ["offRoute"]}})  # an alert that is off unless asked for is remembered too
            self.assertEqual(SettingsStore(Path(d) / "settings.json").get()["alerts"], {"enabled": False, "off": ["fuel"], "on": ["offRoute"]})

    def test_alert_limit_margin_setting(self):
        self.assertEqual(validate_settings({"alerts": {"limitMargin": 1.5}})["alerts"], {"limitMargin": 1.5})
        self.assertEqual(validate_settings({"alerts": {"limitMargin": 0}})["alerts"], {"limitMargin": 0.0})  # 0 = any excess
        self.assertEqual(validate_settings({"alerts": {"limitMargin": 14}})["alerts"], {"limitMargin": 14.0})
        for bad in (-0.1, 14.1, float("nan"), float("inf"), "5", True, None, [1]):
            with self.assertRaises(ValidationError, msg=repr(bad)):
                validate_settings({"alerts": {"limitMargin": bad}})
        with tempfile.TemporaryDirectory() as d:
            store = SettingsStore(Path(d) / "settings.json")
            store.update({"alerts": {"off": ["overLimit"]}})
            store.update({"alerts": {"limitMargin": 2.0}})  # the margin and the switches are saved independently
            saved = SettingsStore(Path(d) / "settings.json").get()["alerts"]
            self.assertEqual((saved["off"], saved["limitMargin"]), (["overLimit"], 2.0))

    def test_store_merges_and_clean_removes_the_entry(self):
        with tempfile.TemporaryDirectory() as d:
            store = SettingsStore(Path(d) / "settings.json")
            self.assertEqual(store.get()["skins"], {})
            store.update({"skins": {"default": "backlit"}})
            store.update({"skins": {"u-0123456789ab": "classicBlack"}})  # a second dashboard does not disturb the first
            self.assertEqual(store.get()["skins"], {"default": "backlit", "u-0123456789ab": "classicBlack"})
            store.update({"skins": {"default": "clean"}})
            self.assertEqual(store.get()["skins"], {"u-0123456789ab": "classicBlack"})
            store.update({"units": {"speed": "mph"}})  # unrelated updates leave skins alone
            self.assertEqual(store.get()["skins"], {"u-0123456789ab": "classicBlack"})

    def test_skin_list_matches_the_front_end(self):
        # The skin ids live in two places (Python allow-list, JS themes.js); keep them from drifting apart.
        import re
        from thrashdash.validate import SKIN_IDS
        js = (web_root() / "js" / "skins" / "themes.js").read_text(encoding="utf-8")
        ids = set(re.findall(r'^    id: "(\w+)",', js, re.M)) | {"clean"}
        self.assertEqual(ids, SKIN_IDS)


class VehicleRuleTests(unittest.TestCase):
    def test_validation(self):
        ok = validate_settings({"vehicleDashboards": {"etkc": "cluster", "midtruck": "u-0123456789ab", "old": None}, "autoSwitch": False})
        self.assertEqual(ok["vehicleDashboards"], {"etkc": "cluster", "midtruck": "u-0123456789ab", "old": None})
        self.assertIs(ok["autoSwitch"], False)
        bad = [{"vehicleDashboards": []}, {"vehicleDashboards": {"../x": "cluster"}}, {"vehicleDashboards": {"": "cluster"}},
               {"vehicleDashboards": {"etkc": "../etc"}}, {"vehicleDashboards": {"etkc": 5}}, {"autoSwitch": "yes"}, {"autoSwitch": 1},
               {"vehicleDashboards": {f"v{i}": "default" for i in range(101)}}]
        for raw in bad:
            with self.assertRaises(ValidationError, msg=repr(raw)[:70]):
                validate_settings(raw)

    def test_store_merges_removes_and_defaults(self):
        with tempfile.TemporaryDirectory() as d:
            store = SettingsStore(Path(d) / "settings.json")
            self.assertEqual((store.get()["vehicleDashboards"], store.get()["autoSwitch"]), ({}, True))
            store.update({"vehicleDashboards": {"etkc": "cluster"}})
            store.update({"vehicleDashboards": {"midtruck": "u-0123456789ab"}})
            self.assertEqual(store.get()["vehicleDashboards"], {"etkc": "cluster", "midtruck": "u-0123456789ab"})
            store.update({"vehicleDashboards": {"etkc": None}, "autoSwitch": False})
            self.assertEqual(store.get()["vehicleDashboards"], {"midtruck": "u-0123456789ab"})
            self.assertIs(store.get()["autoSwitch"], False)

    def test_forget_layout_removes_every_reference(self):
        with tempfile.TemporaryDirectory() as d:
            store = SettingsStore(Path(d) / "settings.json")
            gone = "u-0123456789ab"
            store.update({"defaultLayout": gone, "skins": {gone: "backlit", "default": "classicBlack"},
                          "vehicleDashboards": {"etkc": gone, "midtruck": "cluster", "pickup": gone}})
            store.forget_layout(gone)
            s = store.get()
            self.assertEqual(s["defaultLayout"], "default")
            self.assertEqual(s["skins"], {"default": "classicBlack"})
            self.assertEqual(s["vehicleDashboards"], {"midtruck": "cluster"})
            store.forget_layout("u-ffffffffffff")  # unknown id: harmless no-op



class EarthSettingTests(unittest.TestCase):
    def test_the_earth_folder_is_a_setting_that_is_kept_trimmed_and_checked(self):
        with tempfile.TemporaryDirectory() as d:
            store = SettingsStore(Path(d) / "settings.json")
            self.assertEqual(store.get()["earthPath"], "")
            self.assertEqual(store.update({"earthPath": "  /home/me/Downloads  "})["earthPath"], "/home/me/Downloads")
            self.assertEqual(store.get()["earthPath"], "/home/me/Downloads")
            self.assertEqual(store.update({"earthPath": ""})["earthPath"], "", "empty means ThrashDash does not start Earth")
            for bad in (5, None, ["x"], "a\nb", "x" * 501):
                with self.assertRaises(ValidationError, msg=repr(bad)):
                    store.update({"earthPath": bad})


class ThemeValidationTests(unittest.TestCase):
    """A dashboard's theme (colours for the flat look) is saved in the layout: only plain #rrggbb colours of known names may be."""

    @staticmethod
    def layout(**extra):
        return {"format": 1, "name": "T", "width": 800, "height": 300, "widgets": [], **extra}

    def test_a_theme_is_kept_with_its_colours_in_lower_case(self):
        out = validate_layout(self.layout(theme={"bg": "#0B0E13", "accent": "#35c2ff"}))
        self.assertEqual(out["theme"], {"bg": "#0b0e13", "accent": "#35c2ff"})

    def test_no_theme_or_an_empty_one_leaves_none(self):
        self.assertNotIn("theme", validate_layout(self.layout()))
        self.assertNotIn("theme", validate_layout(self.layout(theme={})))
        self.assertNotIn("theme", validate_layout(self.layout(theme={"unknown": "#ffffff"})))  # (an unknown colour is dropped, and nothing is left)

    def test_an_unknown_colour_name_is_dropped_and_the_rest_kept(self):
        self.assertEqual(validate_layout(self.layout(theme={"bg": "#111111", "sparkle": "#ffffff"}))["theme"], {"bg": "#111111"})

    def test_a_colour_that_is_not_a_plain_hex_colour_is_refused(self):
        for bad in ("red", "#fff", "#12345g", "#1234567", "rgb(1,2,3)", "url(//x)", "#ffffff; background: url(//x)", "", None, 5, ["#ffffff"]):
            with self.assertRaises(ValidationError, msg=repr(bad)):
                validate_layout(self.layout(theme={"bg": bad}))

    def test_a_theme_that_is_not_an_object_is_refused(self):
        for bad in ("#ffffff", 5, ["bg"], None):
            with self.assertRaises(ValidationError, msg=repr(bad)):
                validate_layout(self.layout(theme=bad))

    def test_a_theme_comes_back_from_the_store(self):
        with tempfile.TemporaryDirectory() as d:
            store = LayoutStore(Path(d) / "layouts", web_root() / "layouts")
            lid = store.create(self.layout(theme={"panel": "#202020"}))
            self.assertEqual(store.get(lid)[0]["theme"], {"panel": "#202020"})

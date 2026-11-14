"""REST endpoints for layouts and settings (everything the editor persists)."""
import asyncio
import ipaddress

from aiohttp import web

from .auth import COOKIE_NAME, SESSION_SECONDS, TOKEN_SECONDS, valid_pin
from .lan import LanController, LanError
from .paths import web_root
from .store import LayoutStore, NotFound, ReadOnly, SettingsStore
from . import tripexport, tripshare
from .configs import windows_differ
from .triplog import Busy, NotFound as TripNotFound
from .validate import ValidationError

LAYOUTS = web.AppKey("layouts", LayoutStore)
SETTINGS = web.AppKey("settings", SettingsStore)
LAN_CONTROLLER = web.AppKey("lan_controller", LanController)
LOCAL_CHECK = web.AppKey("local_check", object)  # remote address -> is this client this computer?
CONFIGS = web.AppKey("configs", object)  # the ConfigStore (saved sets of windows), or None
QUIT = web.AppKey("quit", dict)  # {"stop": fn} once ThrashDash can be asked to stop (set by __main__), else empty
WINDOW_OPENER = web.AppKey("window_opener", dict)  # {"open": async fn(windows) -> int} once windows can be launched, else empty
WINDOWS = web.AppKey("windows", object)  # the WindowRegistry (which dashboard windows are open)
AUTH = web.AppKey("auth", object)  # the AuthStore: the PIN for devices on the network
NOTICES = web.AppKey("notices", object)  # trip popups (tripnotice.TripNotices), or None
TRIPLOG = web.AppKey("triplog", object)  # the TripLog (where the car has been), or None when this run keeps none
HUB = web.AppKey("hub", object)  # the Hub (live data and the trip meters); defined here so both modules share one key

# Browsers refuse to attach a custom header to a cross-origin request without a CORS preflight, and
# we never answer preflights, so only pages served by this relay can set it. Together with the
# Origin/Host checks in the guard middleware this stops other websites from writing layouts.
WRITE_HEADER = "X-ThrashDash"


def is_loopback(remote):
    if not remote:
        return False
    try:
        addr = ipaddress.ip_address(remote.split("%")[0])
    except ValueError:
        return False
    if getattr(addr, "ipv4_mapped", None):
        addr = addr.ipv4_mapped
    return addr.is_loopback


def check_write_allowed(request, allow_remote=False):
    """Raise HTTPForbidden unless this request may change data. `allow_remote` is only for signing in with the PIN, which a
    phone must be able to do (it still has to carry the header)."""
    if request.headers.get(WRITE_HEADER) != "1":
        raise web.HTTPForbidden(text=f"missing {WRITE_HEADER} header")
    # Other devices on the network may VIEW dashboards but only this computer may change anything.
    if not allow_remote and not request.app[LOCAL_CHECK](request.remote):
        raise web.HTTPForbidden(text="changes are only allowed from the computer running ThrashDash")


def set_session_cookie(response, auth):
    """Remember a signed-in device: HttpOnly (page scripts cannot read it) and SameSite=Strict (other sites cannot make the
    browser send it). Not Secure: the relay speaks plain http on the local network."""
    response.set_cookie(COOKIE_NAME, auth.issue_cookie(), max_age=SESSION_SECONDS, httponly=True, samesite="Strict", path="/")


def _error(status, message):
    return web.json_response({"error": message}, status=status)


async def _body(request):
    try:
        return await request.json()
    except (ValueError, UnicodeDecodeError):
        raise web.HTTPBadRequest(text="body is not valid JSON") from None


def _guard_store_errors(func):
    async def wrapper(request):
        try:
            return await func(request)
        except ValidationError as e:
            return _error(400, str(e))
        except NotFound:
            return _error(404, "layout not found")
        except ReadOnly as e:
            return _error(403, str(e))
        except OSError as e:
            return _error(500, f"could not write to disk: {e.strerror or e}")

    return wrapper


@_guard_store_errors
async def list_layouts(request):
    return web.json_response({"layouts": request.app[LAYOUTS].list()})


@_guard_store_errors
async def get_layout(request):
    layout_id = request.match_info["id"]
    layout, builtin = request.app[LAYOUTS].get(layout_id)
    return web.json_response({"id": layout_id, "builtin": builtin, "layout": layout})


@_guard_store_errors
async def create_layout(request):
    layout_id = request.app[LAYOUTS].create(await _body(request))
    layout, _ = request.app[LAYOUTS].get(layout_id)
    return web.json_response({"id": layout_id, "builtin": False, "layout": layout}, status=201)


@_guard_store_errors
async def save_layout(request):
    layout_id = request.match_info["id"]
    request.app[LAYOUTS].save(layout_id, await _body(request))
    layout, _ = request.app[LAYOUTS].get(layout_id)
    return web.json_response({"id": layout_id, "builtin": False, "layout": layout})


@_guard_store_errors
async def delete_layout(request):
    layout_id = request.match_info["id"]
    request.app[LAYOUTS].delete(layout_id)
    request.app[SETTINGS].forget_layout(layout_id)  # its look, vehicle rules and default status go with it
    return web.json_response({"deleted": layout_id})


@_guard_store_errors
async def get_settings(request):
    return web.json_response(request.app[SETTINGS].get())


@_guard_store_errors
async def put_settings(request):
    body = await _body(request)
    if isinstance(body, dict) and body.get("lan") is True and not request.app[AUTH].has_pin:
        return _error(409, "Choose a PIN first: network access needs one.")  # (nothing is changed)
    settings = request.app[SETTINGS].update(body)
    if "lan" in body:  # the network switch takes effect immediately, no restart
        try:
            await request.app[LAN_CONTROLLER].apply(settings["lan"])
        except LanError as e:
            request.app[SETTINGS].update({"lan": False})  # never leave a setting on that could not be honoured
            return _error(409, str(e))
    log = request.app[TRIPLOG]
    if log is not None:
        log.set_idle_end(settings["tripIdleEndMin"] * 60)  # (at once, for the trip being driven too)
    return web.json_response(request.app[SETTINGS].get())


async def get_client(request):
    """What the asking device may do, so the page can hide controls that would only fail (phones cannot edit)."""
    local = request.app[LOCAL_CHECK](request.remote)
    lan = request.app[LAN_CONTROLLER]
    return web.json_response({"local": local, "pinSet": request.app[AUTH].has_pin, "lan": {"enabled": lan.enabled, "urls": lan.urls() if local else []}})


def _require_local(request):
    if not request.app[LOCAL_CHECK](request.remote):
        raise web.HTTPForbidden(text="only the computer running ThrashDash may do this")


async def pin_login(request):
    """A device on the network signs in with the PIN. Wrong guesses are slowed down per device, and across all devices."""
    auth = request.app[AUTH]
    if not auth.has_pin:
        return _error(503, "No PIN has been set on the computer running ThrashDash yet.")
    key = request.remote
    wait = auth.throttle.wait_seconds(key)
    if wait:
        response = _error(429, f"Too many wrong PINs. Try again in {wait} seconds.")
        response.headers["Retry-After"] = str(wait)
        return response
    body = await _body(request)
    if auth.check_pin(body.get("pin") if isinstance(body, dict) else None):
        auth.throttle.succeeded(key)
        response = web.json_response({"ok": True})
        set_session_cookie(response, auth)
        return response
    auth.throttle.failed(key)
    return _error(403, "Wrong PIN")


async def get_lan_pin(request):
    """The current PIN, for this computer's own menu (a phone that has signed in must never be able to read it)."""
    _require_local(request)
    auth = request.app[AUTH]
    return web.json_response({"set": auth.has_pin, "pin": auth.pin or ""})


async def put_lan_pin(request):
    check_write_allowed(request)
    body = await _body(request)
    pin = body.get("pin") if isinstance(body, dict) else None
    if not valid_pin(pin):
        return _error(400, "The PIN must be 4 to 8 digits.")
    request.app[AUTH].set_pin(pin)
    lan = request.app[LAN_CONTROLLER]
    await lan.on_disable()  # every device that was signed in is signed out: drop their live connections now
    error = None
    if (request.app[SETTINGS].get()["lan"] or lan.start_when_pin_set) and not lan.enabled:  # network access was wanted but had to wait for a PIN
        lan.start_when_pin_set = False
        try:
            await lan.apply(True)
        except LanError as e:
            error = str(e)
    return web.json_response({"set": True, "lan": lan.enabled, "error": error})


async def lan_token(request):
    """A single-use sign-in token for the QR code (good for a few minutes), so scanning it needs no typing."""
    check_write_allowed(request)
    auth = request.app[AUTH]
    if not auth.has_pin:
        return _error(409, "Choose a PIN first.")
    return web.json_response({"token": auth.new_token(), "expiresIn": TOKEN_SECONDS})


@_guard_store_errors
async def put_window(request):
    """A dashboard window on this computer reports its dashboard, size and position (see windows.py)."""
    check_write_allowed(request)
    body = await _body(request)
    if not isinstance(body, dict):
        return _error(400, "body must be an object")
    request.app[WINDOWS].update(request.match_info["id"], body, body.get("stamp"))
    return web.json_response({"ok": True})


@_guard_store_errors
async def delete_window(request):
    """A dashboard window was closed. `t` is the page's clock in ms, so a reload's close cannot undo its own re-report."""
    check_write_allowed(request)
    try:
        stamp = float(request.query.get("t", ""))
    except ValueError:
        return _error(400, "t must be a number")
    request.app[WINDOWS].remove(request.match_info["id"], stamp)
    return web.json_response({"ok": True})


async def get_windows(request):
    registry = request.app[WINDOWS]
    return web.json_response({"windows": registry.entries, "live": sorted(registry.live)})


async def reset_trip(request):
    check_write_allowed(request)
    name = request.match_info["name"]
    try:
        request.app[HUB].reset_trip(name)
    except KeyError:
        return _error(404, "there is no such trip (use A or B)")
    return web.json_response({"ok": True})


async def list_triplog(request):
    """Every recorded trip (newest first) and whether one is being recorded now."""
    log = request.app[TRIPLOG]
    if log is None:
        return web.json_response({"enabled": False, "status": {"recording": False}, "trips": []})
    notices = request.app[NOTICES]
    return web.json_response({"enabled": True, "status": log.status(), "trips": log.list(), "notices": dict(notices.stats) if notices is not None else {}})


async def quit_thrashdash(request):
    """Quit ThrashDash (the Quit button). The answer goes out first; then it stops the same way Ctrl-C does, which closes its windows."""
    stop = request.app[QUIT].get("stop")
    if stop is None:
        return _error(501, "this run of ThrashDash cannot be stopped from the page")
    asyncio.get_running_loop().call_later(0.25, stop)
    return web.json_response({"ok": True})


async def test_popup(request):
    """Send a popup now (to the game and the dashboard windows), whatever the settings say, to see which ways work."""
    notices = request.app[NOTICES]
    if notices is None:
        return _error(404, "popups are not available in this run")
    return web.json_response({"ok": True, **notices.test()})


def _configs(request):
    store = request.app[CONFIGS]
    if store is None:
        raise web.HTTPNotFound(text="saved configs are not available in this run")
    return store


def _configs_state(request):
    """Everything the Dashboards menu shows about configs: which one is loaded, whether the windows have changed since it was saved,
    and a summary of each (so it can say what it holds)."""
    data = _configs(request).get()
    live = request.app[WINDOWS].live_entries()
    active = next((c for c in data["configs"] if c["id"] == data["active"]), None)
    summary = lambda ws: [{"kind": e.get("kind", "dashboard"), "dashboard": e.get("dashboard"), "width": e.get("width"), "height": e.get("height")} for e in ws.values()]  # noqa: E731
    return {"active": data["active"], "open": len(live), "changed": bool(active) and windows_differ(active["windows"], live),
            "configs": [{"id": c["id"], "name": c["name"], "windows": summary(c["windows"])} for c in data["configs"]]}


async def list_configs(request):
    return web.json_response(_configs_state(request))


async def create_config(request):
    """Save as: {name} makes a new config of the windows open now, which becomes the active one."""
    body = await _body(request)
    if not isinstance(body, dict):
        return _error(400, "body must be an object")
    try:
        _configs(request).create(body.get("name"), request.app[WINDOWS].live_entries())
    except ValidationError as e:
        return _error(400, str(e))
    return web.json_response({"ok": True, **_configs_state(request)})


async def save_config(request):
    """Save: overwrite a config with the windows open now. `PUT /api/configs/active` with {id} picks the config ThrashDash opens with
    and opens its windows that are not open yet."""
    body = await _body(request)
    if not isinstance(body, dict):
        return _error(400, "body must be an object")
    store = _configs(request)
    config_id = request.match_info["id"]
    opened = 0
    try:
        if config_id == "active":
            chosen = body.get("id")
            store.activate(chosen)
            windows = store.active_windows() if chosen is not None else None
            opener = request.app[WINDOW_OPENER].get("open")
            if windows and opener is not None:
                opened = await opener(windows)
        else:
            store.save(config_id, request.app[WINDOWS].live_entries())
    except ValidationError as e:
        return _error(400, str(e))
    except NotFound:
        return _error(404, "there is no such config")
    return web.json_response({"ok": True, "opened": opened, **_configs_state(request)})


async def delete_config(request):
    try:
        _configs(request).delete(request.match_info["id"])
    except NotFound:
        return _error(404, "there is no such config")
    return web.json_response({"ok": True, **_configs_state(request)})


async def get_triplog_routes(request):
    """Every trip's thinned route for the all-trips map. Worked out off the main loop: reading many trip files must not
    hold up the live telemetry."""
    log = request.app[TRIPLOG]
    routes = await asyncio.to_thread(log.routes) if log is not None else []
    return web.json_response({"enabled": log is not None, "trips": routes})


async def get_triplog_trip(request):
    log = request.app[TRIPLOG]
    trip = log.detail(request.match_info["id"]) if log is not None else None
    if trip is None:
        return _error(404, "there is no such trip")
    return web.json_response(trip)


EXPORTS = {"gpx": ("application/gpx+xml", tripexport.to_gpx, "gpx"), "kml": ("application/vnd.google-earth.kml+xml", tripexport.to_kml, "kml")}


async def live_triplog_trip(request):
    """What a trip has gained since `after` (seconds into it), for following a trip being driven."""
    log = request.app[TRIPLOG]
    try:
        after = float(request.query.get("after", "-1"))
    except ValueError:
        return _error(400, "after must be a number")
    update = await asyncio.to_thread(log.live, request.match_info["id"], after) if log is not None else None
    if update is None:
        return _error(404, "there is no such trip")
    return web.json_response(update)


async def share_triplog_trip(request):
    """A finished trip as one html file anyone can open (tripshare.py). It holds the trip's positions, so it is only made on request."""
    log = request.app[TRIPLOG]
    trip = log.detail(request.match_info["id"]) if log is not None else None
    if trip is None:
        return _error(404, "there is no such trip")
    if not trip.get("end"):
        return _error(409, "this trip is still being recorded: share it once it has ended")
    page = tripshare.build_share_page(trip, request.app[SETTINGS].get()["units"], web_root())
    name = tripexport.file_name(trip, trip.get("stats"), "html")
    return web.Response(text=page, content_type="text/html", charset="utf-8", headers={"Content-Disposition": f'attachment; filename="{name}"'})


async def export_triplog_trip(request):
    """A trip as a GPX or KML file to download (read-only, so a phone may do it too)."""
    log = request.app[TRIPLOG]
    kind = request.match_info["kind"]
    trip = log.detail(request.match_info["id"]) if log is not None and kind in EXPORTS else None
    if trip is None:
        return _error(404, "there is no such trip")
    content_type, build, extension = EXPORTS[kind]
    body = build(trip, trip.get("stats"))
    name = tripexport.file_name(trip, trip.get("stats"), extension)
    return web.Response(text=body, content_type=content_type, charset="utf-8", headers={"Content-Disposition": f'attachment; filename="{name}"'})


async def rename_triplog_trip(request):
    """Give an ended trip a name: {"name": "..."} (an empty name takes it away)."""
    log = request.app[TRIPLOG]
    body = await _body(request)
    if log is None or not isinstance(body, dict):
        return _error(404, "there is no such trip")
    try:
        title = log.rename(request.match_info["id"], body.get("name"))
    except TripNotFound:
        return _error(404, "there is no such trip")
    except Busy:
        return _error(409, "this trip is still being recorded")
    except ValueError as e:
        return _error(400, str(e))
    return web.json_response({"ok": True, "title": title})


async def delete_triplog_trip(request):
    """Move an ended trip to the trash folder."""
    log = request.app[TRIPLOG]
    try:
        if log is None:
            raise TripNotFound
        log.delete(request.match_info["id"])
    except TripNotFound:
        return _error(404, "there is no such trip")
    except Busy:
        return _error(409, "this trip is still being recorded")
    return web.json_response({"ok": True})


def add_routes(app):
    r = app.router
    r.add_get("/api/layouts", list_layouts)
    r.add_post("/api/layouts", create_layout)
    r.add_get("/api/layouts/{id}", get_layout)
    r.add_put("/api/layouts/{id}", save_layout)
    r.add_delete("/api/layouts/{id}", delete_layout)
    r.add_post("/api/trips/{name}/reset", reset_trip)
    r.add_get("/api/triplog", list_triplog)
    r.add_get("/api/configs", list_configs)
    r.add_post("/api/configs", create_config)
    r.add_put("/api/configs/{id}", save_config)
    r.add_delete("/api/configs/{id}", delete_config)
    r.add_post("/api/quit", quit_thrashdash)
    r.add_post("/api/triplog/test-popup", test_popup)
    r.add_get("/api/triplog/routes", get_triplog_routes)  # (before {id}: first match wins)
    r.add_get("/api/triplog/{id}", get_triplog_trip)
    r.add_get("/api/triplog/{id}/share", share_triplog_trip)
    r.add_get("/api/triplog/{id}/live", live_triplog_trip)  # (before {kind}: first match wins)
    r.add_get("/api/triplog/{id}/{kind}", export_triplog_trip)
    r.add_patch("/api/triplog/{id}", rename_triplog_trip)
    r.add_delete("/api/triplog/{id}", delete_triplog_trip)
    r.add_get("/api/windows", get_windows)
    r.add_put("/api/windows/{id}", put_window)
    r.add_delete("/api/windows/{id}", delete_window)
    r.add_post("/api/pin/login", pin_login)
    r.add_get("/api/lan/pin", get_lan_pin)
    r.add_put("/api/lan/pin", put_lan_pin)
    r.add_post("/api/lan/token", lan_token)
    r.add_get("/api/client", get_client)
    r.add_get("/api/settings", get_settings)
    r.add_put("/api/settings", put_settings)

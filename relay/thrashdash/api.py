"""REST endpoints for layouts and settings (everything the editor persists)."""
import ipaddress

from aiohttp import web

from .lan import LanController, LanError
from .store import LayoutStore, NotFound, ReadOnly, SettingsStore
from .validate import ValidationError

LAYOUTS = web.AppKey("layouts", LayoutStore)
SETTINGS = web.AppKey("settings", SettingsStore)
LAN_CONTROLLER = web.AppKey("lan_controller", LanController)
LOCAL_CHECK = web.AppKey("local_check", object)  # remote address -> is this client this computer?
WINDOWS = web.AppKey("windows", object)  # the WindowRegistry (which dashboard windows are open)
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


def check_write_allowed(request):
    """Raise HTTPForbidden unless this request may change data."""
    if request.headers.get(WRITE_HEADER) != "1":
        raise web.HTTPForbidden(text=f"missing {WRITE_HEADER} header")
    # Other devices on the network may VIEW dashboards but only this computer may change anything.
    if not request.app[LOCAL_CHECK](request.remote):
        raise web.HTTPForbidden(text="changes are only allowed from the computer running ThrashDash")


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
    settings = request.app[SETTINGS].update(body)
    if "lan" in body:  # the network switch takes effect immediately, no restart
        try:
            await request.app[LAN_CONTROLLER].apply(settings["lan"])
        except LanError as e:
            request.app[SETTINGS].update({"lan": False})  # never leave a setting on that could not be honoured
            return _error(409, str(e))
    return web.json_response(request.app[SETTINGS].get())


async def get_client(request):
    """What the asking device may do, so the page can hide controls that would only fail (phones cannot edit)."""
    local = request.app[LOCAL_CHECK](request.remote)
    lan = request.app[LAN_CONTROLLER]
    return web.json_response({"local": local, "lan": {"enabled": lan.enabled, "urls": lan.urls() if local else []}})


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


def add_routes(app):
    r = app.router
    r.add_get("/api/layouts", list_layouts)
    r.add_post("/api/layouts", create_layout)
    r.add_get("/api/layouts/{id}", get_layout)
    r.add_put("/api/layouts/{id}", save_layout)
    r.add_delete("/api/layouts/{id}", delete_layout)
    r.add_post("/api/trips/{name}/reset", reset_trip)
    r.add_get("/api/windows", get_windows)
    r.add_put("/api/windows/{id}", put_window)
    r.add_delete("/api/windows/{id}", delete_window)
    r.add_get("/api/client", get_client)
    r.add_get("/api/settings", get_settings)
    r.add_put("/api/settings", put_settings)

"""HTTP + WebSocket server: serves the dashboard and streams telemetry to it."""
import asyncio
import json
from urllib.parse import quote, urlencode, urlparse

from aiohttp import web

from . import __version__
from .auth import COOKIE_NAME, AuthStore
from .lifecycle import WindowCounter
from .windows import WindowRegistry
from .api import AUTH, HUB, LAN_CONTROLLER, LAYOUTS, LOCAL_CHECK, SETTINGS, WINDOWS, add_routes, check_write_allowed, set_session_cookie
from .lan import LanController
from .netinfo import host_allowed, is_local_client
from .paths import web_root

PUSH_HZ = 30

# Everything the page needs comes from this origin; nothing is fetched from a CDN, so the
# dashboard works offline and a compromised third-party host cannot inject code into it.
# 'unsafe-inline' is for styles only (SVG presentation attributes the widgets set at runtime).
CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; font-src 'self'; connect-src 'self'; base-uri 'none'; form-action 'none'"
)


PORT = web.AppKey("port", int)
WINDOW_COUNTER = web.AppKey("window_counter", WindowCounter)  # open dashboard windows on this computer (their WebSockets)
REMOTE_SOCKETS = web.AppKey("remote_sockets", set)


# What a device on the network may fetch BEFORE it has signed in: only the PIN page and what it needs.
PIN_PAGE = "/pin.html"
LOGIN_PATH = "/api/pin/login"
SIGN_IN_FILES = {PIN_PAGE, "/js/pin.js", "/css/pin.css"}


def _harden(response):
    response.headers["Content-Security-Policy"] = CSP
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cache-Control"] = "no-store"  # a dashboard edited between runs must never show stale JS
    return response


def _address_without(request, name):
    """This request's path and query with one query parameter taken out (the sign-in token must not stay in the address)."""
    rest = [(k, v) for k, v in request.query.items() if k != name]
    # Always a path on this site: a request for "//other.example/" must not become a redirect to another site
    return "/" + request.path.lstrip("/") + (("?" + urlencode(rest)) if rest else "")


def _wants_a_page(request):
    return request.method == "GET" and "text/html" in request.headers.get("Accept", "")


def _sign_in_gate(request):
    """For a device on the network: let it through if it is signed in; sign it in if it brings a valid QR token; otherwise
    send it to the PIN page (a browser navigation) or refuse (anything else). Raises to answer the request itself."""
    auth = request.app[AUTH]
    if auth.cookie_valid(request.cookies.get(COOKIE_NAME)):
        return
    token = request.query.get("t")
    if token and request.method == "GET":
        wait = auth.throttle.wait_seconds(request.remote)
        if wait:
            raise web.HTTPTooManyRequests(text=f"too many attempts; try again in {wait} seconds", headers={"Retry-After": str(wait)})
        if auth.use_token(token):
            auth.throttle.succeeded(request.remote)
            signed_in = web.HTTPFound(_address_without(request, "t"))  # same page, minus the token
            set_session_cookie(signed_in, auth)
            raise _harden(signed_in)
        auth.throttle.failed(request.remote)
    if request.path in SIGN_IN_FILES or (request.path == LOGIN_PATH and request.method == "POST"):
        return
    if _wants_a_page(request):
        raise _harden(web.HTTPFound(PIN_PAGE + "?next=" + quote(_address_without(request, "t"), safe="")))
    raise web.HTTPUnauthorized(text="PIN required")


@web.middleware
async def guard(request, handler):
    """Reject requests that did not come from a page we served, or from a place that may not use us.

    * DNS rebinding: a hostile site resolves its own name to 127.0.0.1 (or to the PC's LAN address) and then talks to
      us. The Host header would still be the hostile name, so only our own names are answered (netinfo.host_allowed).
    * Cross-site WebSocket hijacking: any web page may open ws://127.0.0.1:port. Browsers always send Origin on
      WebSocket handshakes, and it must match Host.
    * Network access is off by default: until it is switched on, the relay is not even listening beyond this computer,
      and a connection that still arrives from elsewhere is refused here too.
    Devices on the network can only read; changes need this computer (api.check_write_allowed).
    """
    lan = request.app[LAN_CONTROLLER]
    host = request.headers.get("Host", "")
    if not host_allowed(host, request.app[PORT], lan.enabled):
        raise web.HTTPForbidden(text="unexpected Host header")
    if not lan.enabled and not request.app[LOCAL_CHECK](request.remote):
        raise web.HTTPForbidden(text="network access is switched off")
    origin = request.headers.get("Origin")
    if origin is not None and urlparse(origin).netloc.lower() != host.lower():
        raise web.HTTPForbidden(text="cross-origin request refused")
    if not request.app[LOCAL_CHECK](request.remote):
        # A device on the network: it needs the PIN (this computer never does). If network access were somehow on with no PIN
        # set, no device is signed in and none can sign in, so it fails closed.
        try:
            _sign_in_gate(request)
        except web.HTTPException as exc:
            _harden(exc)
            raise
    if request.method not in ("GET", "HEAD"):
        check_write_allowed(request, allow_remote=(request.path == LOGIN_PATH))
    try:
        response = await handler(request)
    except web.HTTPException as exc:  # error responses (404, 413...) get the same hardening headers
        response = exc
    _harden(response)
    if isinstance(response, web.HTTPException):
        raise response
    return response


async def index(request):
    return web.FileResponse(web_root() / "index.html")


async def api_status(request):
    hub = request.app[HUB]
    return web.json_response({**hub.status_message(), "version": __version__, "channels": len(hub.channels), "stats": dict(hub.stats)})


async def ws_handler(request):
    hub = request.app[HUB]
    ws = web.WebSocketResponse(heartbeat=20, max_msg_size=64 * 1024)
    await ws.prepare(request)
    remote = not request.app[LOCAL_CHECK](request.remote)
    if remote:
        request.app[REMOTE_SOCKETS].add(ws)  # so switching network access off can drop them straight away
    else:
        request.app[WINDOW_COUNTER].connected()  # an open dashboard window on this computer (see lifecycle.py)

    async def writer():
        seen_catalog = seen_frame = -1
        last_status = None
        interval = 1.0 / PUSH_HZ
        await ws.send_json({"type": "hello", "relay": __version__, "protocol": 1})
        while not ws.closed:
            status = hub.status_message()
            if status != last_status:
                last_status = status
                await ws.send_json(status)
            if hub.catalog_version != seen_catalog:
                seen_catalog = hub.catalog_version
                await ws.send_json(hub.catalog_message())
            if hub.frame_seq != seen_frame and hub.frame_seq > 0:  # never send the empty pre-data state as a "frame"
                seen_frame = hub.frame_seq
                await ws.send_str(hub.frame_json())
            await asyncio.sleep(interval)

    task = asyncio.create_task(writer())
    try:
        # Reading is what lets aiohttp notice the browser closing the socket. Browser->relay
        # messages are ignored: editing goes through the REST API, not the telemetry socket.
        async for _ in ws:
            pass
    finally:
        request.app[REMOTE_SOCKETS].discard(ws)
        if not remote:
            request.app[WINDOW_COUNTER].disconnected()
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, ConnectionError):
            pass
    return ws


MAX_BODY = 256 * 1024  # a layout is a few KB; this bounds what any write can make us buffer


def make_app(hub, http_port, layouts, settings, lan=None, local_check=is_local_client, windows=None, auth=None):
    """Build the web application. `lan` controls network access (default: an inert controller, i.e. off).
    `local_check(remote_address)` says whether a client is this computer; tests replace it to play a remote device."""
    app = web.Application(middlewares=[guard], client_max_size=MAX_BODY)
    lan = lan or LanController(http_port)
    app[HUB] = hub
    app[PORT] = http_port
    app[LAN_CONTROLLER] = lan
    app[LOCAL_CHECK] = local_check
    app[REMOTE_SOCKETS] = set()
    app[WINDOW_COUNTER] = WindowCounter()
    app[LAYOUTS] = layouts
    app[SETTINGS] = settings
    app[WINDOWS] = windows if windows is not None else WindowRegistry()  # (in memory only unless the caller gives it a file)
    app[AUTH] = auth if auth is not None else AuthStore()  # (no PIN, in memory, unless the caller gives it a file)

    async def close_remote():
        for ws in list(app[REMOTE_SOCKETS]):
            await ws.close(code=1001, message=b"network access switched off")

    lan.on_disable = close_remote

    async def save_state(_app):
        hub.trips.save()  # keep the trip meters and the list of open windows when ThrashDash is closed
        app[WINDOWS].flush()

    app.on_shutdown.append(save_state)
    add_routes(app)
    app.router.add_get("/", index)
    app.router.add_get("/ws", ws_handler)
    app.router.add_get("/api/status", api_status)
    # Registered last so the explicit routes above win. follow_symlinks stays off: the web root
    # is a plain folder and we never want a symlink to lead outside it.
    app.router.add_static("/", web_root(), follow_symlinks=False)
    return app

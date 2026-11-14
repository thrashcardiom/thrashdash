"""A trip as ONE html file that anyone can open, with no ThrashDash and no server: its route on a map, the figures, the speed
graph and the replay, exactly as the Trips page shows them.

Sending someone a trip should not need them to install anything or for your computer to be reachable, so the file holds
everything: the trip's data, the map library, the styles and the page's own program. The program is the Trips page's real
program (web/js/trips/trips.js and what it uses), not a second copy of it, so a fix to the Trips page is a fix to shared files
too. The page's program is written as ES modules, which a file opened from disk cannot load from one another; so they are
joined into one ordinary script here (`bundle`), each module in a scope of its own and its imports turned into reads of the
modules it needs. The only module that differs is `js/api.js`: in a shared file it answers from the embedded data instead of
asking a ThrashDash.

The file contains the trip's positions, so it is only made when someone asks for it.

What the recipient's browser may do is limited by a Content-Security-Policy written into the file: run only the file's own
scripts (named by their hash), draw pictures only from the two map servers, and make no requests of any other kind.

Map pictures: street map tiles come from OpenStreetMap, which refuses requests that carry no Referer, and a page opened from a
disk sends none. So a file opened from disk shows the satellite picture (EOX, which does not mind) to begin with, and the street
map when the file is put on a web server.
"""
import base64
import hashlib
import html
import json
import re
from pathlib import Path

ENTRY = "js/trips/trips.js"

# `js/api.js` as a shared file sees it: the one trip, from the data embedded in the page.
SHARED_API = r"""
const data = JSON.parse(document.getElementById("share-data").textContent);
const trip = data.trip;
const refuse = async () => { throw new Error("not available in a shared page"); };
export const api = {
  getClient: async () => ({ local: false, pinSet: false, lan: { enabled: false, urls: [] } }),
  getSettings: async () => ({ units: data.units, tripNotices: { game: false, gameStart: false, window: false }, tripIdleEndMin: 2 }),
  listTrips: async () => ({ enabled: true, status: { recording: false }, notices: {}, trips: [{
    id: trip.header.id, ts: trip.header.ts, vehicle: trip.header.vehicle, energy: trip.header.energy, ended: true, reason: trip.end && trip.end.reason,
    duration: trip.stats && trip.stats.dur, distance: trip.stats && trip.stats.dist, top: trip.end && trip.end.top, title: trip.title, stats: trip.stats, recording: false }] }),
  getTrip: async () => trip,
  listRoutes: async () => ({ enabled: true, trips: [] }),
  liveTrip: async () => ({ points: [], info: [], events: [], end: null }),
  saveSettings: refuse, renameTrip: refuse, deleteTrip: refuse, testPopup: refuse,
};
"""

_IMPORT_RE = re.compile(r'^import\s*\{([^}]*)\}\s*from\s*"([^"]+)";[ \t]*$', re.M)
_EXPORT_RE = re.compile(r"^export\s+((?:async\s+)?(?:function\*?|const|let|class)\s+)([A-Za-z0-9_$]+)", re.M)
_TILE_HOSTS = "https://tile.openstreetmap.org https://tiles.maps.eox.at"


class BundleError(Exception):
    """A module could not be joined in (an import form this simple joiner does not handle)."""


def _resolve(importer, target):
    """The path (relative to the web root, with / separators) that `target`, imported by the module at `importer`, names."""
    parts = importer.split("/")[:-1]
    for piece in target.split("/"):
        if piece == "..":
            if not parts:
                raise BundleError(f"{importer}: {target} leaves the web folder")
            parts.pop()
        elif piece not in (".", ""):
            parts.append(piece)
    return "/".join(parts)


def bundle(root, entry=ENTRY, overrides=None):
    """The entry module and everything it imports, as one ordinary script. `overrides` maps a module path to source to use in
    place of the file (the shared page's api). Each module becomes a function whose result is its exports."""
    overrides = {"js/api.js": SHARED_API, **(overrides or {})}
    root = Path(root)
    order, seen = [], set()

    def source_of(path):
        if path in overrides:
            return overrides[path]
        file = (root / path).resolve()
        if root.resolve() not in file.parents:
            raise BundleError(f"{path} is outside the web folder")
        return file.read_text(encoding="utf-8")

    def visit(path, trail):
        if path in seen:
            return
        if path in trail:
            raise BundleError("modules import each other in a circle: " + " -> ".join([*trail, path]))
        text = source_of(path)
        for match in _IMPORT_RE.finditer(text):
            visit(_resolve(path, match.group(2)), [*trail, path])
        seen.add(path)
        order.append((path, text))

    visit(entry, [])
    out = ['"use strict";', "const __modules = {};"]
    for path, text in order:
        def read_imports(match, path=path):
            names = [n.strip() for n in match.group(1).split(",") if n.strip()]
            mapped = ", ".join(n.replace(" as ", ": ") for n in names)
            return f"const {{ {mapped} }} = __modules[{json.dumps(_resolve(path, match.group(2)))}];"

        body = _IMPORT_RE.sub(read_imports, text)
        exported = [m.group(2) for m in _EXPORT_RE.finditer(body)]
        body = _EXPORT_RE.sub(lambda m: m.group(1) + m.group(2), body)
        if re.search(r"^(?:import|export)\b", body, re.M):
            raise BundleError(f"{path}: an import or export form the bundler does not handle")
        out.append(f"__modules[{json.dumps(path)}] = (() => {{\n{body}\nreturn {{ {', '.join(exported)} }};\n}})();")
    return "\n".join(out) + "\n"


def _script_json(value):
    """JSON safe to put inside <script type="application/json">: nothing in it can end the element or open a comment."""
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")


def _csp(script_bodies):
    hashes = " ".join("'sha256-" + base64.b64encode(hashlib.sha256(b.encode("utf-8")).digest()).decode() + "'" for b in script_bodies)
    return (f"default-src 'none'; script-src {hashes}; style-src 'unsafe-inline'; img-src data: {_TILE_HOSTS}; "
            "connect-src 'none'; font-src 'none'; base-uri 'none'; form-action 'none'")


def _shared_trip(trip):
    """The trip as the shared page needs it, and nothing else: no car ids, no fuel or damage readings, no raw readings of the
    world (their results are in the summary)."""
    header = trip.get("header") or {}
    keep = ("t", "la", "lo", "al", "s", "h", "d")
    return {"header": {"id": header.get("id"), "ts": header.get("ts"), "vehicle": header.get("vehicle"), "energy": header.get("energy")},
            "points": [{k: p[k] for k in keep if k in p} for p in trip.get("points", [])], "info": [], "events": [],
            "end": {"reason": (trip.get("end") or {}).get("reason"), "top": (trip.get("end") or {}).get("top")},
            "title": trip.get("title"), "stats": trip.get("stats")}


def build_share_page(trip, units, root):
    """The whole shared file as text. `trip` is TripLog.detail's result for a finished trip; `units` the user's unit choices;
    `root` the web folder."""
    root = Path(root)
    page = (root / "trips.html").read_text(encoding="utf-8")
    title = (trip.get("title") or (trip.get("stats") or {}).get("name") or "Trip")
    css = "\n".join((root / name).read_text(encoding="utf-8") for name in ("vendor/leaflet/leaflet.css", "css/trips.css"))
    css = re.sub(r"url\((?:\"|')?images/[^)]*\)", 'url("data:,")', css)  # (Leaflet's own pictures are not used here, and are not in the file)
    leaflet = (root / "vendor/leaflet/leaflet.js").read_text(encoding="utf-8")
    flag = "window.THRASHDASH_SHARE = true;"
    program = bundle(root)
    scripts = [leaflet, flag, program]
    for script in scripts:
        if re.search(r"</script", script, re.I):
            raise BundleError("a script contains an end tag that would cut the page short")
    # The page: the Trips page's own markup, with its styles and scripts put inside, and a note that it is a shared copy.
    page = re.sub(r'<link rel="stylesheet" href="/css/fonts\.css">\s*', "", page)
    page = re.sub(r'<link rel="stylesheet" href="/vendor/leaflet/leaflet\.css">\s*<link rel="stylesheet" href="/css/trips\.css">', lambda m: f"<style>\n{css}\n</style>", page)
    page = re.sub(r'\s*<script src="/vendor/leaflet/leaflet\.js"></script>\s*<script type="module" src="/js/trips/trips\.js"></script>', "", page)
    page = page.replace("<title>ThrashDash: Trips</title>", f"<title>{html.escape(title)} (ThrashDash)</title>", 1)
    page = page.replace('<body data-view="list">', '<body data-view="list" class="static">', 1)
    csp = f'<meta http-equiv="Content-Security-Policy" content="{html.escape(_csp(scripts), quote=True)}">'
    page = page.replace('<meta charset="utf-8">', f'<meta charset="utf-8">\n  {csp}', 1)  # (the charset first: a browser must see it in the first kilobyte)
    # Leaflet's licence asks that its notice travels with every copy of the library; this file is a copy of it.
    notice = (root / "vendor/leaflet/LICENSE").read_text(encoding="utf-8").replace("--", "- -")
    page = page.replace("</head>", f"<!-- Leaflet, bundled in this page:\n{notice}\n-->\n</head>", 1)
    payload = _script_json({"trip": _shared_trip(trip), "units": units})
    tail = (f'<script type="application/json" id="share-data">{payload}</script>\n'
            + "".join(f"<script>{s}</script>\n" for s in scripts))
    return page.replace("</body>", tail + "</body>", 1)

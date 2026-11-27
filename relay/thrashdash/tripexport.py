"""A trip as a GPX, KML or CSV file, to open in other programs.

GPX is what GPS tools and most mapping programs read (a track of timed points, with the speed in the Garmin extension many of
them understand). KML is what Google Earth reads (no key or account needed): the route as a line on the ground, and a timed
`gx:Track` for the time slider of the desktop program. Both are built from a trip as TripLog.read returns it plus its summary (tripstats).

Everything that came from outside the program (the trip's name, the street names the game reported) goes into the file
escaped, and anything XML 1.0 cannot hold is removed, so a strange name can never make a file other programs cannot open.

The times are real: the trip's start (UTC) plus each point's seconds into the trip. The heights are BeamNG.Earth's, and KML
is told to ignore them and lay the path on the ground, so a height that is off cannot bury the route in the terrain.
"""
import csv
import io
import re
from datetime import datetime, timedelta, timezone
from xml.sax.saxutils import escape

# What XML 1.0 forbids: control characters other than tab, newline and carriage return (and the two non-characters).
_BAD_XML = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]")
_UNSAFE_FILE = re.compile(r'[^A-Za-z0-9 ._\-()]+')

GPX_NS = 'xmlns="http://www.topografix.com/GPX/1/1" xmlns:gpxtpx="http://www.garmin.com/xmlschemas/TrackPointExtension/v1"'
KML_NS = 'xmlns="http://www.opengis.net/kml/2.2" xmlns:gx="http://www.google.com/kml/ext/2.2"'


def _text(value):
    return escape(_BAD_XML.sub("", str(value)))


def _num(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def trip_title(data, stats):
    """The name to give the file's track: the user's own, else the worked-out one, else a plain one from the date."""
    return data.get("title") or (stats or {}).get("name") or f"Trip {_start(data):%Y-%m-%d %H:%M}"


def _start(data):
    ts = (data.get("header") or {}).get("ts")
    return datetime.fromtimestamp(ts if _num(ts) and 0 < ts < 4e9 else 0, timezone.utc)


def _iso(start, t):
    return (start + timedelta(seconds=t)).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _points(data):
    return [p for p in data.get("points", []) if _num(p.get("la")) and _num(p.get("lo")) and _num(p.get("t"))
            and -90 <= p["la"] <= 90 and -180 <= p["lo"] <= 180]


def _description(stats):
    """A few plain lines about the trip, for the description a program shows beside the track."""
    if not stats:
        return ""
    lines = [f"{stats['dist'] / 1000:.2f} km in {int(stats['dur'] // 60)} min {int(stats['dur'] % 60)} s"]
    lines.append(f"Top speed {stats['top_ms'] * 3.6:.0f} km/h, average {stats['avg_moving_ms'] * 3.6:.0f} km/h while moving")
    if stats.get("from") or stats.get("to"):
        lines.append(f"From {stats.get('from') or '?'} to {stats.get('to') or '?'}")
    if stats.get("fuel"):
        fuel = stats["fuel"]
        lines.append(f"{fuel['used']:.2f} {fuel['unit']} used" + (f" ({fuel['per_100km']:.1f} {fuel['unit']}/100 km)" if fuel.get("per_100km") else ""))
    if stats.get("limits") and stats["limits"].get("over_s"):
        lines.append(f"Over the posted limit for {stats['limits']['over_s']:.0f} s of {stats['limits']['checked_s']:.0f} s checked")
    lines.append("Recorded by ThrashDash with BeamNG.Earth")
    return "\n".join(lines)


def to_gpx(data, stats):
    """The trip as GPX 1.1: a start and end waypoint and one track of timed points (with speed)."""
    points = _points(data)
    start = _start(data)
    name = _text(trip_title(data, stats))
    out = ['<?xml version="1.0" encoding="UTF-8"?>', f'<gpx version="1.1" creator="ThrashDash" {GPX_NS}>',
           f"<metadata><name>{name}</name><time>{_iso(start, 0)}</time></metadata>"]
    if points:
        for label, p in (("Start", points[0]), ("End", points[-1])):
            out.append(f'<wpt lat="{p["la"]:.7f}" lon="{p["lo"]:.7f}"><name>{label}</name></wpt>')
    out.append(f"<trk><name>{name}</name>")
    description = _description(stats)
    if description:
        out.append(f"<desc>{_text(description)}</desc>")
    out.append("<trkseg>")
    for p in points:
        row = [f'<trkpt lat="{p["la"]:.7f}" lon="{p["lo"]:.7f}">']
        if _num(p.get("al")):
            row.append(f"<ele>{p['al']:.1f}</ele>")
        row.append(f"<time>{_iso(start, p['t'])}</time>")
        if _num(p.get("s")):
            row.append(f"<extensions><gpxtpx:TrackPointExtension><gpxtpx:speed>{p['s']:.2f}</gpxtpx:speed></gpxtpx:TrackPointExtension></extensions>")
        row.append("</trkpt>")
        out.append("".join(row))
    out += ["</trkseg></trk>", "</gpx>"]
    return "\n".join(out) + "\n"


def to_kml(data, stats):
    """The trip as KML for Google Earth: the route as a line on the ground, a pin at each end, and (hidden until ticked, since it
    would draw over the line) a timed gx:Track for Earth's time slider.

    The line is a plain LineString because that is what every Google Earth (the website and phone apps as well as the desktop
    program) draws. The first version had only the gx:Track, which the user's Google Earth read as the two pins and nothing
    else: it does not draw that element, so the route was missing."""
    points = _points(data)
    start = _start(data)
    name = _text(trip_title(data, stats))
    description = _description(stats)
    note = f"<description>{_text(description)}</description>" if description else ""
    out = ['<?xml version="1.0" encoding="UTF-8"?>', f"<kml {KML_NS}>", "<Document>", f"<name>{name}</name>", note]
    if any(_num(p.get("s")) for p in points):
        out.append('<Schema id="speed"><gx:SimpleArrayField name="speed" type="float"><displayName>Speed (m/s)</displayName></gx:SimpleArrayField></Schema>')
    out.append('<Style id="route"><LineStyle><color>fff5c235</color><width>4</width></LineStyle></Style>')  # (aabbggrr: the dashboard's blue)
    out.append('<Style id="start"><IconStyle><color>ff84dc3d</color><scale>1.1</scale></IconStyle></Style>')
    out.append('<Style id="end"><IconStyle><color>ff4d4dff</color><scale>1.1</scale></IconStyle></Style>')
    if points:
        coords = " ".join(f"{p['lo']:.7f},{p['la']:.7f},0" for p in points)
        out.append(f"<Placemark><name>{name}</name>{note}<styleUrl>#route</styleUrl>"
                   f"<LineString><tessellate>1</tessellate><altitudeMode>clampToGround</altitudeMode><coordinates>{coords}</coordinates></LineString></Placemark>")
        for label, style, p in (("Start", "start", points[0]), ("End", "end", points[-1])):
            out.append(f'<Placemark><name>{label}</name><styleUrl>#{style}</styleUrl><Point><coordinates>{p["lo"]:.7f},{p["la"]:.7f},0</coordinates></Point></Placemark>')
        out.append("<Folder><name>Timed track (for the time slider in Google Earth desktop)</name><visibility>0</visibility>")
        out.append(f"<Placemark><name>{name} (timed)</name><visibility>0</visibility><styleUrl>#route</styleUrl>")
        out.append('<gx:Track><altitudeMode>clampToGround</altitudeMode>')
        out.extend(f"<when>{_iso(start, p['t'])}</when>" for p in points)
        out.extend(f"<gx:coord>{p['lo']:.7f} {p['la']:.7f} {p['al'] if _num(p.get('al')) else 0:.1f}</gx:coord>" for p in points)
        if any(_num(p.get("s")) for p in points):
            out.append('<ExtendedData><SchemaData schemaUrl="#speed"><gx:SimpleArrayData name="speed">')
            out.extend(f"<gx:value>{(p.get('s') if _num(p.get('s')) else 0):.2f}</gx:value>" for p in points)
            out.append("</gx:SimpleArrayData></SchemaData></ExtendedData>")
        out.append("</gx:Track></Placemark></Folder>")
    out += ["</Document>", "</kml>"]
    return "\n".join(line for line in out if line) + "\n"


# ---- CSV: one row per recorded point, for a spreadsheet ---------------------------------------------------------------------------------

# A reading from the game (the posted limit, the street) holds until the next one; one older than this is no longer where the car is.
_INFO_HOLD_S = 15.0
# A cell a spreadsheet would take for a formula starts with one of these. The street names come from a map of the world: nothing in
# them may run as anything in someone's spreadsheet, so such a cell is made text with a leading apostrophe.
_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def csv_text(value):
    """A text cell: control characters out, and not something a spreadsheet would run as a formula."""
    text = "".join(c if c.isprintable() else " " for c in str(value))
    return "'" + text if text.startswith(_FORMULA_START) else text


def _cell(value, places=None):
    """A cell: empty for nothing (or a number that is not a number), true/false, a number to `places` decimals, or text."""
    if value is None or value == "":
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if _num(value):
        if value != value or value in (float("inf"), float("-inf")):
            return ""
        return f"{value:.{places}f}" if places is not None else str(value)
    return csv_text(value)


def csv_bytes_text(rows):
    """Rows of cells as CSV text: UTF-8 with a byte order mark (Excel on Windows only reads names like Taupo or Whanganui with their
    macrons right if the file says it is UTF-8), CRLF line ends (as RFC 4180 has it), cells quoted only where they need it."""
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\r\n")
    writer.writerows(rows)
    return "\ufeff" + out.getvalue()


def to_csv(data, stats=None):
    """The trip's points as CSV, one row each (about once a second). Everything is in SI units with the unit in the column's name, so
    a spreadsheet does not depend on the units the person chose on the Trips page. A value the game did not give is an empty cell."""
    header = data.get("header") or {}
    energy = header.get("energy")
    fuel_col = {"liquid": "fuel_volume_l", "electric": "fuel_volume_kwh"}.get(energy, "fuel_volume")
    columns = ["t_s", "time_utc", "lat", "lon", "altitude_m", "speed_ms", "heading_deg", "distance_m", "speed_limit_ms", "limit_is_posted",
               "street", "place", "fuel_fraction", fuel_col, "body_damage_pct", "peak_g"]
    start = _start(data)
    info = sorted((i for i in data.get("info", []) if isinstance(i, dict) and _num(i.get("t"))), key=lambda i: i["t"])
    rows = [columns]
    at = -1  # the latest reading at or before the point
    for p in _points(data):
        while at + 1 < len(info) and info[at + 1]["t"] <= p["t"]:
            at += 1
        reading = info[at] if at >= 0 and p["t"] - info[at]["t"] <= _INFO_HOLD_S else {}
        limit = reading.get("lim") if isinstance(reading.get("lim"), dict) else {}
        place = reading.get("place") if isinstance(reading.get("place"), dict) else {}
        rows.append([
            _cell(p["t"], 2), _iso(start, p["t"]), _cell(p["la"], 7), _cell(p["lo"], 7), _cell(p.get("al"), 1), _cell(p.get("s"), 2),
            _cell(p.get("h"), 1), _cell(p.get("d"), 1), _cell(limit.get("ms"), 2), _cell(limit.get("m") is True if _num(limit.get("ms")) else None),
            _cell(place.get("street")), _cell(place.get("place")), _cell(p.get("f"), 4), _cell(p.get("fv"), 3), _cell(p.get("dm"), 2), _cell(p.get("g"), 2),
        ])
    return csv_bytes_text(rows)


def file_name(data, stats, extension):
    """A safe name for the downloaded file: the trip's name and date, ASCII only, with nothing a file system or a header could
    object to (so it can go into Content-Disposition as it is)."""
    base = _UNSAFE_FILE.sub("", trip_title(data, stats)).strip(" ._-")[:60] or "trip"
    return f"{base} {_start(data):%Y-%m-%d}.{extension}"

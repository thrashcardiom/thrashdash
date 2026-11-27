"""Changing finished trips: backing them all up and restoring them, joining two into one, and cutting one in two.

None of this rewrites a trip in place. A joined or cut trip is written as new files, and the trips it was made from go to the trash
folder (the same place a deleted trip goes), so any of it can be undone by moving the files back. A restore never overwrites a trip
that is already there.

The trip file format and the rules for what a trip is are in triplog.py; the figures stored with each trip are worked out again for
a joined or cut trip (tripstats.py), not copied.
"""
import io
import json
import re
import zipfile
from datetime import datetime, timezone

from . import tripstats
from .triplog import TRASH_DIR, TRIP_ID_RE, Busy, NotFound, _MAX_READ_BYTES, haversine_m, stored_price, stored_tags

JOIN_MAX_GAP_M = 500.0  # two trips this far apart (end of the first to start of the second) are not one drive with a stop in the middle
JOIN_MAX_GAP_S = 3600.0  # ...and neither are two this far apart in time (a stop longer than an hour is not a stop in a drive)
OVERLAP_SLACK_S = 2.0  # a second trip that seems to start up to this long before the first ended is not overlapping: start times are whole seconds, end times are not
CUT_MARGIN_S = 5.0  # a cut this close to either end of a trip would leave a nudge, not a trip
RESTORE_MAX_BYTES = 512 * 1024 * 1024  # the biggest backup that will be read
RESTORE_MAX_FILES = 20000
RESTORE_MAX_TOTAL = 2 * 1024 ** 3  # ...and the most that all its files may add up to once unpacked
_FILE_RE = re.compile(r"^([0-9]{8}T[0-9]{6}Z(-[0-9]{1,3})?)\.jsonl$")


class EditError(ValueError):
    """The change cannot be made, and why (a sentence for the person who asked)."""


def _dump(obj):
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False)


def _lines(data):
    """A trip as the lines of its file: header, then everything in order, then the end line and anything added after it (name, star)."""
    out = [data["header"]]
    out.extend(data["points"])
    out.extend(data["info"])
    out.extend(data["events"])
    return out


def _sorted_body(data):
    """The points, readings and events of a trip, as they were written: in time order (a point and a reading at the same moment keep
    the point first)."""
    rows = ([(p.get("t", 0), 0, p) for p in data["points"]] + [(i.get("t", 0), 1, i) for i in data["info"]]
            + [(q.get("t", 0), 2, q) for q in data.get("pois", [])] + [(e.get("t", 0), 3, e) for e in data["events"]])
    rows.sort(key=lambda r: (r[0], r[1]))
    return [r[2] for r in rows]


def _in_time_order(rows):
    """The lines of a trip body in time order; at the same moment a point comes before a reading, and a reading before an event."""
    rank = {"p": 0, "i": 1, "poi": 2, "e": 3}
    return sorted(rows, key=lambda r: (r.get("t", 0), rank.get(r.get("k"), 3)))


def _write_trip(log, header, body, end, title=None, starred=False, notes=(), tags=(), price=None):
    """Write a new trip file: `header`, the body lines, the closing line (with its summary worked out now) and a name and star if given.
    Returns the id. The file is written whole in one go, to a name that is free."""
    trip_id = header["id"]
    log.dir.mkdir(parents=True, exist_ok=True)  # (an import into a fresh data folder: nothing has made it yet)
    path = log.dir / f"{trip_id}.jsonl"
    data = {"header": header, "points": [r for r in body if r.get("k") == "p"], "info": [r for r in body if r.get("k") == "i"],
            "events": [r for r in body if r.get("k") == "e"], "pois": [r for r in body if r.get("k") == "poi"], "end": end}
    summary = tripstats.summarise(data, end)
    end = {**end, **({"sum": summary} if summary is not None else {})}
    lines = [header, *body, end]
    if title:
        lines.append({"k": "name", "name": title})
    if starred:
        lines.append({"k": "star", "v": True})
    if tags:
        lines.append({"k": "tags", "v": list(tags)})
    if price:
        lines.append({"k": "price", "v": price})  # (a line, so it beats whatever price the header carried over)
    lines.extend({"k": "note", "t": n["t"], "text": n["text"]} for n in notes)
    with open(path, "x", encoding="utf-8", newline="\n") as f:  # ("x": never over a file that is there)
        f.write("\n".join(_dump(line) for line in lines) + "\n")
    return trip_id


def _to_trash(log, trip_id):
    """Move a trip's file to the trash folder (under a free name there). Raises NotFound or Busy."""
    path = log._path_of(trip_id)
    trash = log.dir / TRASH_DIR
    trash.mkdir(exist_ok=True)
    target, n = trash / path.name, 1
    while target.exists():
        n += 1
        target = trash / f"{path.stem}-{n}.jsonl"
    path.replace(target)
    log._stats_cache.pop(trip_id, None)
    log._route_cache.pop(trip_id, None)


def _finished(log, trip_id):
    """A finished trip's data, or an error saying why not."""
    log._path_of(trip_id)  # (raises NotFound for an unknown trip, Busy for the one being recorded)
    data = log.read(trip_id)
    if data is None:
        raise NotFound(trip_id)
    if data["end"] is None or not data["points"]:
        raise EditError("that trip has not finished, or has no route")
    return data


def _id_from(log, ts):
    base = datetime.fromtimestamp(ts, timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    trip_id, n = base, 1
    while (log.dir / f"{trip_id}.jsonl").exists():
        n += 1
        trip_id = f"{base}-{n}"
    return trip_id


# ---- joining two trips ----------------------------------------------------------------------------------------------------------

def check_join(log, first_id, second_id, force=False):
    """Whether two trips may be joined: returns their data (first, second) or raises EditError saying why not (NotFound, Busy as usual).
    The usual rules are about whether it looks like one drive: the same vehicle model, and the second beginning within JOIN_MAX_GAP_S
    (an hour) and JOIN_MAX_GAP_M of where the first ended. How the first trip ended does not matter (a position jump or a teleport that
    landed close by still leaves one drive); where the car actually was does. Someone who knows better can `force` the join past those
    three rules (the drive had a long break, the car was moved, a different car was swapped in): what no one can force is a join that
    makes no sense, two trips that are the same trip, in the wrong order, or overlapping in time."""
    if first_id == second_id:
        raise EditError("choose two different trips")
    a, b = _finished(log, first_id), _finished(log, second_id)
    ha, hb = a["header"], b["header"]
    if hb.get("ts", 0) < ha.get("ts", 0):
        raise EditError("the first trip must be the earlier one")
    gap_s = hb["ts"] - ha["ts"] - a["end"].get("t", 0)
    if gap_s < -OVERLAP_SLACK_S:
        raise EditError("these trips overlap in time")
    if force:
        return a, b
    if ha.get("vehicle") != hb.get("vehicle"):
        raise EditError("these were driven in different vehicles")
    if gap_s > JOIN_MAX_GAP_S:
        raise EditError("these trips are too far apart in time to be one drive")
    la, fb = a["points"][-1], b["points"][0]
    away = haversine_m(la["la"], la["lo"], fb["la"], fb["lo"])
    if away > JOIN_MAX_GAP_M:
        raise EditError(f"the second trip starts {away / 1000:.1f} km from where the first ended, too far to be one drive")
    return a, b


def join_options(log, trip_id):
    """The finished trips either side of `trip_id` (by start time), and whether each could be joined with it:
    {"prev": {id, ts, title, ok, reason, forceable}|None, "next": ...}. `ok` is whether the join would work; when it would not, `reason` says
    why (a sentence), so the page can show the choice greyed out with the reason rather than have the button vanish, and `forceable` says
    whether it could still be done on purpose (see check_join's `force`). None for a side with no finished trip at all."""
    items = sorted((t for t in log.list() if t.get("ended") and not t.get("recording")), key=lambda t: (t["ts"], t["id"]))
    ids = [t["id"] for t in items]
    out = {"prev": None, "next": None}
    if trip_id not in ids:
        return out
    i = ids.index(trip_id)
    for key, other, args in (("prev", items[i - 1] if i > 0 else None, lambda o: (o["id"], trip_id)), ("next", items[i + 1] if i + 1 < len(items) else None, lambda o: (trip_id, o["id"]))):
        if other is None:
            continue
        reason, forceable = None, False
        try:
            check_join(log, *args(other))
        except EditError as e:
            reason = str(e)
            try:
                check_join(log, *args(other), force=True)
                forceable = True
            except (EditError, NotFound, Busy):
                forceable = False
        except (NotFound, Busy):
            reason = "that trip cannot be read"
        out[key] = {"id": other["id"], "ts": other["ts"], "title": other.get("title") or (other.get("stats") or {}).get("name"), "ok": reason is None,
                    "reason": reason, "forceable": forceable}
    return out


def join(log, first_id, second_id, force=False):
    """Make one trip of two: the car drove, stopped for a while (which ended the first trip) and drove on (the second). `first_id` must
    be the earlier. The time between them is kept as time the car stood still, so the new trip's clock is the real one. The rules for what
    may be joined are in check_join, and `force` joins past the "does it look like one drive" ones. The new trip has the first one's id and
    name; both originals go to the trash. Returns the new trip's id."""
    a, b = check_join(log, first_id, second_id, force)
    ha, hb = a["header"], b["header"]
    shift = hb["ts"] - ha["ts"]  # seconds from the first trip's start to the second's start
    gap_s = max(0.0, shift - a["end"].get("t", 0))
    la, fb = a["points"][-1], b["points"][0]
    d_shift = a["end"].get("dist", la.get("d", 0.0))
    body = _sorted_body(a)
    if gap_s > 2.0:
        # The car stood still between the two: say so with two stationary points (where the first ended and where the second began), so the
        # wait counts as time stopped and shows as a stop, rather than as a stretch driven at the last speed.
        still = {"k": "p", "s": 0.0, "d": round(d_shift, 1)}
        body.append({**still, "t": round(la["t"] + 1.0, 2), "la": la["la"], "lo": la["lo"], "al": la.get("al"), "h": la.get("h")})
        body.append({**still, "t": round(fb["t"] + shift - 1.0, 2), "la": fb["la"], "lo": fb["lo"], "al": fb.get("al"), "h": fb.get("h")})
        for p in body[-2:]:
            for key in ("al", "h"):
                if p[key] is None:
                    del p[key]
    for row in _sorted_body(b):
        moved = {**row, "t": round(row.get("t", 0) + shift, 2)}
        if row.get("k") == "p" and isinstance(row.get("d"), (int, float)):
            moved["d"] = round(row["d"] + d_shift, 1)
        body.append(moved)
    body = _in_time_order(body)  # (the two trips may share a moment, to within the rounding of whole-second start times)
    end = {"k": "end", "reason": b["end"].get("reason"), "t": round(b["end"].get("t", 0) + shift, 2), "ts": b["end"].get("ts", hb["ts"]),
           "dist": round(d_shift + b["end"].get("dist", 0.0), 1), "top": round(max(a["end"].get("top", 0), b["end"].get("top", 0)), 2),
           "n": a["end"].get("n", len(a["points"])) + b["end"].get("n", len(b["points"]))}
    title, starred = a.get("title"), bool(a.get("starred"))
    notes = [*a.get("notes", []), *({"t": round(n["t"] + shift, 2), "text": n["text"]} for n in b.get("notes", []))]
    _to_trash(log, first_id)
    _to_trash(log, second_id)
    try:
        tags = stored_tags([*a.get("tags", []), *b.get("tags", [])])  # (both drives' tags, the first's first; at most as many as one trip may have)
        price = a.get("price") or b.get("price")  # (one drive, one price: the first's, or the second's if the first had none)
        return _write_trip(log, dict(ha), body, end, title or b.get("title"), starred or bool(b.get("starred")), notes, tags, price)
    except Exception:
        # (nothing is lost: put the originals back rather than leave the drive in the trash)
        for tid in (first_id, second_id):
            _restore_from_trash(log, tid)
        raise


def _restore_from_trash(log, trip_id):
    trash = log.dir / TRASH_DIR
    for candidate in sorted(trash.glob(f"{trip_id}*.jsonl")):
        target = log.dir / f"{trip_id}.jsonl"
        if not target.exists():
            candidate.replace(target)
            return


# ---- cutting one trip in two ----------------------------------------------------------------------------------------------------

def cut(log, trip_id, at_s):
    """Cut a finished trip in two at `at_s` seconds into it: the first part keeps the trip's id and name, the second is a new trip
    that begins at the cut. Both get their own summaries. The original goes to the trash. Returns [first_id, second_id]."""
    if not isinstance(at_s, (int, float)) or isinstance(at_s, bool) or at_s != at_s:
        raise EditError("say where to cut, in seconds into the trip")
    data = _finished(log, trip_id)
    points = sorted(data["points"], key=lambda p: p.get("t", 0))
    duration = data["end"].get("t", points[-1].get("t", 0))
    if at_s < CUT_MARGIN_S or at_s > duration - CUT_MARGIN_S:
        raise EditError("cut further from the ends: each part must be a trip of its own")
    before = [p for p in points if p.get("t", 0) <= at_s]
    after = [p for p in points if p.get("t", 0) > at_s]
    if not before or not after:
        raise EditError("there is no route on one side of that cut")
    t0, d0 = after[0]["t"], after[0].get("d", 0.0)
    header = data["header"]
    ts2 = header["ts"] + int(round(t0))
    header2 = {**header, "id": _id_from(log, ts2), "ts": ts2}

    body_all = _sorted_body(data)
    first_body = [r for r in body_all if r.get("t", 0) <= before[-1]["t"]]
    second_rows = [r for r in body_all if r.get("t", 0) >= t0]
    # the second part opens knowing where it is: the last reading before the cut (place, limit, weather) is carried over to its start
    carried = [r for r in data["info"] if r.get("t", 0) < t0]
    second_body = []
    if carried:
        second_body.append({**carried[-1], "t": 0.0})
    for row in second_rows:
        moved = {**row, "t": round(row.get("t", 0) - t0, 2)}
        if row.get("k") == "p" and isinstance(row.get("d"), (int, float)):
            moved["d"] = round(row["d"] - d0, 1)
        second_body.append(moved)
    first_end = {"k": "end", "reason": "split", "t": round(before[-1]["t"], 2), "ts": header["ts"] + int(round(before[-1]["t"])),
                 "dist": round(before[-1].get("d", 0.0), 1), "top": round(max((p.get("s") or 0.0) for p in before), 2), "n": len(before)}
    second_end = {**data["end"], "t": round(data["end"].get("t", duration) - t0, 2), "dist": round(data["end"].get("dist", 0.0) - d0, 1),
                  "top": round(max((p.get("s") or 0.0) for p in after), 2), "n": len(after)}
    second_end.pop("sum", None)
    first_end.pop("sum", None)
    title, starred = data.get("title"), bool(data.get("starred"))
    first_notes = [n for n in data.get("notes", []) if n["t"] < t0]
    second_notes = [{"t": round(n["t"] - t0, 2), "text": n["text"]} for n in data.get("notes", []) if n["t"] >= t0]
    _to_trash(log, trip_id)
    try:
        tags = data.get("tags", [])  # (both halves of a drive are still that kind of drive)
        price = data.get("price")
        first = _write_trip(log, dict(header), first_body, first_end, title, starred, first_notes, tags, price)
        second = _write_trip(log, header2, second_body, second_end, notes=second_notes, tags=tags, price=price)
    except Exception:
        for name in (trip_id, header2["id"]):
            try:
                (log.dir / f"{name}.jsonl").unlink()
            except OSError:
                pass
        _restore_from_trash(log, trip_id)
        raise
    return [first, second]


# ---- backing up and restoring all trips ---------------------------------------------------------------------------------------

def backup(log):
    """Every finished trip (not the one being recorded, and not the trash) as a zip, returned as bytes. Each trip is one file in it,
    under its own name, so the zip is also just a folder of trips that can be copied back by hand."""
    out = io.BytesIO()
    recording = log.status().get("id") if log.status().get("recording") else None
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        count = 0
        for path in sorted(log.dir.glob("*.jsonl")) if log.dir.is_dir() else []:
            if not TRIP_ID_RE.match(path.stem) or path.stem == recording:
                continue
            z.write(path, arcname=path.name)
            count += 1
        z.writestr("README.txt", f"ThrashDash trip backup: {count} trips, one .jsonl file each.\n"
                                 "Restore it from the Trips page (Backup and restore), or copy the files into the triplog folder.\n")
    return out.getvalue()


def restore(log, data):
    """Add the trips in a backup zip (`data`, bytes) that are not already here. Never overwrites a trip, and reads only entries that
    are plain trip files: a name that is anything else, or a file that is not a whole trip, is counted as rejected, not extracted.
    Returns {"added": n, "skipped": n (already here), "rejected": n}. Raises EditError for something that is not a usable zip."""
    if len(data) > RESTORE_MAX_BYTES:
        raise EditError("that file is too big to be a trip backup")
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise EditError("that is not a zip file") from None
    infos = z.infolist()
    if len(infos) > RESTORE_MAX_FILES or sum(i.file_size for i in infos) > RESTORE_MAX_TOTAL:
        raise EditError("that zip holds too much to be a trip backup")
    log.dir.mkdir(parents=True, exist_ok=True)
    recording = log.status().get("id") if log.status().get("recording") else None
    counts = {"added": 0, "skipped": 0, "rejected": 0}
    for info in infos:
        m = _FILE_RE.match(info.filename)
        if info.is_dir() or info.filename == "README.txt":
            continue
        if not m or info.file_size > _MAX_READ_BYTES:
            counts["rejected"] += 1
            continue
        trip_id = m.group(1)
        target = log.dir / f"{trip_id}.jsonl"
        if target.exists() or trip_id == recording:
            counts["skipped"] += 1
            continue
        try:
            raw = z.read(info)
            head = json.loads(raw.split(b"\n", 1)[0])
            if not (isinstance(head, dict) and head.get("k") == "trip" and head.get("id") == trip_id):
                raise ValueError("not a trip file")
            text = raw.decode("utf-8")
            for line in text.splitlines():
                if line.strip():
                    json.loads(line)  # (every line must be JSON: a damaged file is not brought in)
        except (ValueError, UnicodeDecodeError, zipfile.BadZipFile, RuntimeError):
            counts["rejected"] += 1
            continue
        with open(target, "x", encoding="utf-8", newline="\n") as f:
            f.write(text if text.endswith("\n") else text + "\n")
        counts["added"] += 1
    return counts

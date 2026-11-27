# Reference

Files, settings, command line, the HTTP and WebSocket API, the packets the game sends, the trip file format, the code, building and security. For what ThrashDash does see the [README](../README.md); for dashboards and widgets see [dashboards](dashboards.md).

## Contents

- [Settings and files](#settings-and-files)
- [Command line](#command-line)
- [HTTP and WebSocket API](#http-and-websocket-api)
- [Telemetry protocol](#telemetry-protocol)
- [The trip file format](#the-trip-file-format)
- [Development](#development)
- [Security](#security)


## Settings and files

### Where things are stored

| | Linux | Windows |
|:---|:---|:---|
| **Data folder** | `~/.local/share/thrashdash/` (or `$XDG_DATA_HOME/thrashdash/`) | `%LOCALAPPDATA%\ThrashDash\` |

Inside the data folder:

| Path | Contents |
|:---|:---|
| `layouts/u-<12 hex>.json` | Your own dashboards |
| `settings.json` | Settings (below) |
| `trips.json` | Trips A and B |
| `configs.json` | Saved window configs: sets of windows (dashboards and the map window) with their sizes and places |
| `destinations.json` | Saved destinations for the Route view: a name and a place each |
| `circuits.json` | The lap timing circuits (their lines) |
| `laps/<id>.jsonl`, `laps/<id>.best.json` | A circuit's finished laps (one line each, the newest 5000 kept), and its bests per vehicle with the best lap's distance-against-time trace (for the live gap) |
| `triplog/<id>.jsonl` | The trip log: one file per drive (BeamNG.Earth) or per imported GPX track; deleted trips, and the originals of a join or cut, are moved to `triplog/trash/` |
| `windows.json` | The remembered windows |
| `auth.json` | The PIN for devices on the network, and the secret that signs their sign-in cookies (readable only by your user) |
| `thrashdash.log` | The program's messages (AppImage only, when started from a desktop; the latest run) |
| `earth.log` | BeamNG.Earth's own messages, when ThrashDash started it (the latest run) |
| `window-profiles/main/` | The dashboard windows' browser profile (so ThrashDash never touches your everyday browser) |

The built-in dashboards live in [`web/layouts/`](../web/layouts/) and are never modified.

If a data folder from an earlier name of this program exists and this one does not, your saved dashboards, settings, trips and windows are **copied** across the first time it runs (the old folder is left as it was, as a backup).

### Settings

`settings.json` is managed through the editor and the menu; you rarely need to touch it. The relay validates everything and ignores unknown keys.

| Key | Default | Meaning |
|:---|:---|:---|
| `units.speed` | `kmh` | `kmh` or `mph` |
| `units.temp` | `c` | `c` or `f` |
| `units.distance` | `km` | `km` or `mi` |
| `units.pressure` | `psi` | `kpa`, `psi` or `bar` |
| `units.economy` | `auto` | `auto` (follow `distance`), `l100`, `kmpl`, `mpgus`, `mpguk` |
| `defaultLayout` | `default` | The dashboard a window shows when nothing else applies |
| `skins` | `{}` | Dashboard id to look, for dashboards that are not flat |
| `vehicleDashboards` | `{}` | Vehicle (model folder) to dashboard id |
| `autoSwitch` | `true` | Windows that are not pinned follow the vehicle rules |
| `lan` | `false` | Serve to other devices on the network |
| `alerts` | `{"enabled": true, "off": []}` | The alert switch, the ids of alerts switched off (`off`), the ids of alerts that are off unless asked for and have been switched on (`on`, such as `offRoute`), and the over-limit margin (`limitMargin`, metres a second) |
| `restoreWindows` | `true` | Reopen the remembered windows at startup |
| `exactWindowPlacement` | `false` | Start dashboard windows in X11 mode so positions can be restored |
| `closeWindowsOnExit` | `true` | Close the dashboard and map windows when ThrashDash closes |
| `tripNotices` | `{"game": true, "gameStart": false, "window": true}` | Popups about trips: in the game when a trip ends, also when one starts, and a banner on the dashboard windows when one ends |
| `fuelPrice` | `{"liquid": 0, "electric": 0, "currency": ""}` | The price of fuel per litre and of electricity per kWh (0 to 1000; 0 means none) and a currency symbol or code (at most 4 characters). Copied into each trip as it starts (see `price` below), so changing it never changes an old trip. Either part may be sent alone |
| `impactRule` | `{"damage": 1, "g": 4}` | What counts as an impact on the Trips page: body damage of at least `damage` percent (0.1 to 100), or a force of at least `g` (2 to 20; 0 ignores force). Either half may be sent alone. A trip keeps every knock (`knocks` in its summary), so changing this never needs a trip to be worked out again |
| `earthPath` | `""` | The folder (or file) BeamNG.Earth is kept in; ThrashDash starts the highest version in it when it starts. Empty: it does not start Earth |
| `tripIdleEndMin` | `2` | Minutes a car may be parked before the trip ends: 1, 2, 5, 10 or 30 |

## Command line

The AppImage and the source take the same options:

```sh
./ThrashDash-0.1.0-x86_64.AppImage [options]                  # the AppImage
.venv/bin/python relay/run.py [options]                       # from the source (Windows: .venv\Scripts\python relay\run.py)
```

The AppImage always adds `--quit-when-windows-close` for you, and prints `--version`, `--help` and `--install-mod` to wherever you ran it.

| Option | Meaning |
|:---|:---|
| `--simulate` | Use fake telemetry instead of listening for the game. Fake trips and windows are not saved. |
| `--sim-vehicles A,B` | With `--simulate`: simulated vehicles that take turns, to try per-vehicle dashboards (implies `--simulate`). |
| `--sim-vehicle-seconds N` | Seconds per simulated vehicle (default 15). |
| `--http-port PORT` | Dashboard port (default 8765). |
| `--udp-port PORT` | Port the mod sends to (default 4568). It must match `PORT` in the mod. |
| `--no-window` | Do not open the dashboard window (and do not restore windows). |
| `--browser PATH` | Chromium-based browser to use for the window. |
| `--lan` | Serve to other devices on your network from the start (it can also be switched on from the menu). It needs a PIN: if none has been chosen yet it stays off until you choose one in the menu. |
| `--with-earth PATH` | Start BeamNG.Earth along with ThrashDash and quit when it closes. PATH is the folder it is kept in (the highest version in it is started) or its file; it overrides the `earthPath` setting. ThrashDash never closes Earth. |
| `--no-earth` | Do not start BeamNG.Earth this time, whatever the setting says. |
| `--keep-running` | Packaged programs (the AppImage and the Windows exe) quit when the last dashboard window closes; this keeps them running until they are stopped. |
| `--quit-when-windows-close` | End ThrashDash a moment after the last dashboard window on this computer is closed (the AppImage uses this). With `--no-window` it only applies once a window has been opened. |
| `--install-mod [PATH]` | Copy the BeamNG mod into the game's `mods/unpacked` folder and exit. `PATH` is the game's user folder if it cannot be found by itself. |
| `--version` | Print the version. |


## HTTP and WebSocket API

The relay serves the dashboard and a small JSON API on `http://127.0.0.1:8765`. The dashboard uses nothing else, and you can script against it.

**Writes** (`PUT`, `POST`, `DELETE`) must carry the header `X-ThrashDash: 1` and come from this computer; anything else is refused with 403. A device on the network (not this computer) must first have signed in with the PIN: until it has, every request except the PIN page and sign-in itself is refused with 401, and a browser is sent to the PIN page. See [Security](#security).

| Method and path | Purpose |
|:---|:---|
| `GET /` | The dashboard page. Static files are served from [`web/`](../web/). |
| `GET /api/status` | Connection state, vehicle, channel count, version and counters. |
| `GET /ws` | WebSocket: live data (below). |
| `GET /ws?feed=geo` | A lighter WebSocket for the Trips page: only the car's live position (ten a second), trip starts and ends, and the quit message (below). It does not count as a dashboard window. |
| `POST /api/quit` | Quit ThrashDash (the Quit button): it answers, then stops the way Ctrl-C does. This computer only. |
| `GET /api/layouts` | List dashboards: `id`, `name`, `builtin`, `width`, `height`, `modified`. |
| `GET /api/layouts/{id}` | One dashboard: `{id, builtin, layout}`. |
| `POST /api/layouts` | Create a dashboard from a layout; returns its new id. |
| `PUT /api/layouts/{id}` | Replace one of your dashboards. Built-ins are read-only (403). |
| `DELETE /api/layouts/{id}` | Delete one of yours, and forget its look, vehicle rules and default status. |
| `GET /api/settings` | All [settings](#settings). |
| `PUT /api/settings` | Change some settings (a partial object; unknown keys ignored). Switching `lan` takes effect immediately. |
| `GET /api/client` | What the asking device may do: `{local, pinSet, lan: {enabled, urls}}`. |
| `GET /api/lan/pin` | The PIN, for this computer's menu only: `{set, pin}`. |
| `PUT /api/lan/pin` | Choose the PIN (`{"pin": "4821"}`, 4 to 8 digits). Signs every phone out. This computer only. |
| `POST /api/lan/token` | A single-use sign-in token for the QR code (`{token, expiresIn}`). This computer only. |
| `POST /api/pin/login` | A device on the network signs in (`{"pin": "4821"}`); the answer sets the sign-in cookie. Wrong guesses are slowed down (429 with `Retry-After`). |
| `POST /api/trips/{A\|B}/reset` | Reset a trip. |
| `GET /api/triplog` | The trip log: `{enabled, status: {recording, id, distance, duration}, presetTags (the fixed set of tags offered to begin with), trips: [...]}`, newest first, each with `id`, `ts`, `vehicle`, `ended`, `reason`, `distance` (m), `duration` (s), `top` (m/s), `points`, `starred`, `tags` (the trip's tags), `price` (the fuel price it is costed at, or null), `imported` (`"gpx"` for a trip brought in from a GPX file), `title` (the name the user gave it), and `stats` (the [summary](#the-trip-summary), once it has ended). |
| `POST /api/triplog/test-popup` | Send a popup now to the game and the dashboard windows: `{ok, game}` (`game` says whether it could be sent to the game). This computer only. |
| `GET /api/configs`, `POST /api/configs` `{name}`, `PUT /api/configs/{id}`, `PUT /api/configs/active` `{id}`, `DELETE /api/configs/{id}` | Saved window configs (see above): the list with which one is loaded and whether the windows changed since it was saved; Save as is POST and Save is PUT (both take the windows open now); picking is PUT active, which opens the config's windows that are not open. Writes are this computer only. |
| `GET /api/triplog/routes` | Every trip's route thinned to its shape for the all-trips map: `{enabled, trips: [{id, ts, vehicle, title, name, countries, dist, dur, top, recording, line: [[lat, lon], ...]}]}`. |
| `GET /api/triplog/{id}/live?after=T` | What a trip has gained since `T` seconds into it: `{points, info, events, end}`, for following a trip being driven. |
| `GET /api/triplog/{id}/share` | A finished trip as one self-contained html page (see Share above). 409 while it is being recorded. |
| `GET /api/triplog/{id}/gpx`, `/kml`, `/csv` | The trip as a download (CSV: one row for each recorded point, SI units with the unit in the column name, UTF-8 with a byte order mark, CRLF line ends, a text cell that could be read as a formula made plain with a leading apostrophe; GPX 1.1, or KML with the route as a line on the ground, a pin at each end and a hidden timed `gx:Track`). 404 for an unknown trip or kind. |
| `GET /api/triplog/{id}` | One trip in full: `{header, points, info, events, pois, notes, end, title, starred, stats}` (`title` is the name the user gave it, or null; `pois` the places found near stops and `notes` the notes written against waypoints; `stats.limits` also has `spans` and `posted` here, the time ranges over the limit and where a posted limit was read). 404 for an unknown or malformed id. |
| `GET /api/earth[?path=...]` | What the BeamNG.Earth setting (or the given text) would start, without starting it: `{configured, chosen, message, running}`. This computer only. |
| `POST /api/triplog/import` | Add the tracks in a GPX file sent as the request body (at most 20 MB): `{ok, ids, skipped, duplicates}`. 400 with a reason for a file that is not usable. This computer only. |
| `GET /api/triplog/backup` | Every finished trip as one zip (`thrashdash-trips-YYYYMMDD.zip`), one `.jsonl` file each. This computer only. |
| `POST /api/triplog/restore` | Add the trips in a backup zip sent as the request body (`Content-Type: application/zip`, at most 512 MB): `{ok, added, skipped, rejected}`. Never overwrites a trip; only plain `<id>.jsonl` entries that are whole trips are used. This computer only. |
| `GET /api/triplog/{id}/joins` | The finished trips either side of this one, and whether each could be joined with it: `{prev, next}`, each `{id, ts, title, ok, reason, forceable}` or `null` when there is no such trip (`reason` says why `ok` is false; `forceable` whether a join can still be forced). |
| `POST /api/triplog/join` | Make one trip of two: `{"first": id, "second": id}` (the first the earlier), and optionally `"force": true` to join them though they do not look like one drive (a different vehicle model, more than an hour or 500 m apart); a trip with itself, in the wrong order, or overlapping in time cannot be forced: `{ok, id}`. 400 with a reason when they cannot be joined (different vehicle models, wrong order, overlapping, more than an hour or 500 m apart). The originals go to the trash. This computer only. |
| `POST /api/triplog/{id}/cut` | Cut a trip in two: `{"at": seconds into the trip}`: `{ok, ids}`. 400 when a part would be shorter than 5 s. The original goes to the trash. This computer only. |
| `PATCH /api/triplog/{id}` | Change an ended trip: `{"name": "..."}` renames it (an empty name takes it away; at most 80 characters), `{"starred": true}` marks it as a favourite (`false` takes the mark away); either or both; `{"price": {"liquid": 2.45, "electric": 0.31, "currency": "$"}}` sets the fuel price it is costed at (`null` takes it away; both prices 0 is the same as none); `{"tags": ["Track", "Hill climb"]}` sets its tags (the whole list; an empty one takes them all away; at most 8, each at most 24 characters, no repeats in any case; a fixed tag is spelled its usual way); `{"note": {"t": seconds, "text": "..."}}` writes a note against the waypoint at that time (empty text clears it). Any of them together. 409 while it is being recorded. This computer only. |
| `DELETE /api/triplog/{id}` | Move an ended trip to the trash folder. 409 while it is being recorded. This computer only. |
| `GET /api/route` | The GPS route now: `{active, game, ...}`; when active also `name`, `remaining` (m), `eta` (s), `off` (m from the route), `version`, and, once the game has sent the line for this version, `points` (the planned route as `[lat, lon]` pairs: BeamNG.Earth's own line, simplified by shape only if it has more than 1,500), `dest` and `waypoints` (the stops still ahead, `{lat, lon, name?}`). `game` says whether the game's extension has sent a position lately. Anyone may look. |
| `POST /api/route` | Set the route: `{"stops": [{lat, lon, name?}, ...]}`, 1 to 11 places, the last the destination. The relay sends them to the game and answers `{ok: true}` when the game says it took them (up to 3 s), not when the route is planned (that comes through `GET /api/route`); 409 with the game's reason when it refuses, did not answer or is not connected; 400 for bad stops. This computer only. |
| `DELETE /api/route` | Clear the GPS route in the game (the same answers). This computer only. |
| `GET /api/destinations` | `{enabled, destinations: [{id, name, lat, lon}]}`: the saved destinations. |
| `POST /api/destinations` | Save one: `{name, lat, lon}` (a name already used gets "(2)"; at most 100): `{ok, destination}`. This computer only. |
| `DELETE /api/destinations/{id}` | Delete one. This computer only. |
| `GET /api/circuits` | The lap timing circuits: `{enabled, circuits: [{id, name, kind: "circuit"\|"stage", enabled, start, finish (a stage), sectors: [line...], lapCount, bests: {vehicle: {t, s, ts}}}]}`. A line is `{a: [lat, lon], b: [lat, lon]}`; it counts when crossed from the right of a to b to the left of it. |
| `POST /api/circuits` | Make a circuit (the body as above, without `id`, `lapCount` or `bests`): `{ok, id}`. 400 says what is wrong (a line shorter than 3 m or longer than 200 m, a stage with no finish, more than 8 sector lines, more than 50 circuits). This computer only. |
| `PUT /api/circuits/{id}` | Replace a circuit: `{ok, cleared}`; `cleared` is true when its lines changed, which clears its laps. This computer only. |
| `DELETE /api/circuits/{id}` | Delete a circuit and its laps. This computer only. |
| `GET /api/circuits/{id}/laps` | `{circuit, laps: [{n, t, s: [sector seconds], ok, v (vehicle), ts, r (run)}] (the newest 300), bests: {vehicle: {t, s, ts}}}`. |
| `DELETE /api/circuits/{id}/laps` | Forget a circuit's laps and bests; the circuit stays. This computer only. |
| `GET /api/windows` | The remembered windows and which are open now. |
| `PUT /api/windows/{id}` | A window reports itself (used by the dashboard). |
| `DELETE /api/windows/{id}?t=<ms>` | A window was closed (used by the dashboard). |

### WebSocket messages

All messages are JSON text. The relay sends:

| `type` | Fields | When |
|:---|:---|:---|
| `hello` | `relay` (version), `protocol` | Once, first. |
| `status` | `connected`, `vehicle`, `simulated` | On connect and whenever it changes. `connected` is false after two seconds with no data. |
| `catalog` | `vehicle`, `channels` (name to `number`, `boolean` or `string`) | On connect and whenever the set of channels or the vehicle changes. |
| `frame` | `seq`, `d` (channel name to current value) | Whenever there is new data, up to 30 times a second. Includes the electrics, tyres, damage, states and trip channels together. |

A dashboard window also receives, when they happen:

| `type` | Fields | When |
|:---|:---|:---|
| `notice` | `id`, `kind`, `title`, `msg` | A trip ended (or a test popup was sent): the banner text. Only sent when the banner setting is on. |
| `quit` | | ThrashDash is closing and asked its windows to close with it (`closeWindowsOnExit`). Only sent to windows on this computer. |

The `?feed=geo` connection sends `hello` (with `"feed": "geo"`), then `geo` (`seq`, `rt`, `lat`, `lon`, `hdg`, `spd`: the car's position as the game reports it, ten a second), `trip` (`seq`, `event`: `started` or `ended`, `id`: the moment a trip file is made or finished or thrown away) and `quit`.

Anything the page sends over the WebSocket is ignored: changes go through the REST API.


## Telemetry protocol

The mod sends JSON datagrams over UDP to `127.0.0.1:4568`, only for the vehicle you are sitting in. Every packet carries `"v": 1` (the protocol version) and the vehicle path. The relay ignores anything with another version and anything malformed, and bounds the number and depth of channels and the length of text, so a bad packet cannot make it allocate without limit.

| Packet | Rate | Contents |
|:---|:---|:---|
| `catalog` | every 2 s | `channels`: the top-level electrics names and their types |
| `frame` | 30 per second | `n` (a counter) and `electrics`: the car's whole electrics table |
| `slow` | 4 per second | `tire`, `damage`, `state` and `energy`, each a complete snapshot |

```json
{"v":1,"type":"frame","n":1234,"vehicle":"/vehicles/etkc/",
 "electrics":{"rpm":3120.4,"wheelspeed":27.8,"gear":"D","watertemp":88.1,"wheelThermals":{"FL":{"brakeSurfaceTemperature":212.0}}}}
```

```json
{"v":1,"type":"slow","vehicle":"/vehicles/etkc/",
 "tire":{"FL":{"temp":64.2,"coreTemp":71.0,"pressure":206.8,"deflated":false,"punctured":false,"load":3600,"slip":0.12}},
 "damage":{"total":311295,"brokenPct":23.6,"deformedPct":31.0,"beamsTotal":4978,"beamsBroken":1177,"beamsDeformed":1543,
           "zone":{"FL":0.12,"FR":0.03,"ML":0.01,"MR":0.0,"RL":0.0,"RR":0.02}},
 "state":{"engine":{"oilOverheating":true}},
 "energy":{"electric":false,"liquid":true}}
```

The trip log's own extension (BeamNG.Earth only; it runs in the game, not in a car) sends more packet types to the same port:

| Packet | Rate | Contents |
|:---|:---|:---|
| `geo` | every second | `lat`, `lon`, `alt`, `spd` (m/s), `hdg`, `ready` (false while the world loads: the game's speed is nonsense then, so the relay ignores the sample), `veh`, `model`, `rt` (real seconds since the extension loaded), `ts` (unix time) |
| `geolive` | 10 per second | `lat`, `lon`, `hdg`, `spd`, `rt` (real seconds since the extension loaded), `st` (**game** seconds since it loaded: stands still while the game is paused, runs slow in slow motion; the lap timer uses it, so a pause mid-lap is not part of the lap): just where the car is, for the live map and the lap timer. Handed straight to the Trips page over `/ws?feed=geo`; never recorded (the trip is made from `geo`). |
| `geoinfo` | every 1 s | `limit` (`ms`, `mapped`, `unit`); with every 2nd one `place` (street, place, region, country); with every 10th `wx` (weather) and `gps` (route progress). A packet that leaves a part out means "not read this time" for `place`, `wx` and `gps`, and "no limit known" for `limit`. |
| `georoute` | every 1 s | `gps`: `remaining` (m), `eta` (s), `off` (m from the route), `name`, `planning`. **No `gps` means no route is set.** For the dashboard only: it is not given to the trip log, which keeps the slower `gps` reading inside `geoinfo`, so trips are not written to more often. |
| `georoutepath` | when a route is set or re-planned, and every 20 s | The planned route's line for the maps: `version` (the route's `routeVersion`), `points` (the route thinned to about 500 `[lat, lon]` pairs, first and last kept), `dest` (`{lat, lon, name}`) and `waypoints` (the stops still ahead). The relay shows a line only with the progress packets of the same version. |
| `route` (relay to game) | when a route is set or cleared on the Trips page | `{type: "route", action: "set", stops: [{lat, lon, name?}...]}` (1 to 11, the last the destination) or `{type: "route", action: "clear"}`, sent to wherever the last position came from; the extension checks every stop and gives them to BeamNG.Earth's `gpsSet` (one stop) or `gpsSetRoute` (several). Only the relay's own address is believed. |
| `georouteack` | the game's answer to `route` | `{ok, why?}`: whether BeamNG.Earth took the route, and why not. |
| `geosteps` | when a route is set or re-planned, and every 20 s | `version` (the route's `routeVersion`, which the `georoute` packets carry too), `dist` (the route's length in metres) and `steps`: up to 120 of `{offset (metres along the route where it begins), distance, type, modifier, name, exit}`. The names are a routing service's: `type` is `depart`, `turn`, `fork`, `roundabout`, `exit roundabout`, `end of road`, `merge`, `on ramp`, `off ramp`, `new name`, `continue` or `arrive`; `modifier` is `left`, `slight left`, `sharp left`, `right`, `slight right`, `sharp right`, `straight` or `uturn`. The car's progress along the route is `dist` minus the `remaining` of the progress packet (checked against a real drive: it matched the steps' positions to within 3 to 30 m). A real route is in [`docs/route-steps-sample.json`](route-steps-sample.json). The relay believes steps only for the route version of the progress packet. |
| `toast` (relay to game) | when a trip ends or starts | the reverse direction: `{type: "toast", title, msg}`, sent to wherever the last `geo` packet came from; the game shows it on screen |
| `geoevent` | when it happens | `name`: `teleported`, `worldReady`, `routeArrived`, `reset`, `vehicleSwitched`, `levelStart`, `levelEnd`, `waypoint` (the driver's key); with `lat`, `lon` |
| `geopoi` | once per stop (the car still for 20 s) | `poi`: up to 3 `{n: name, c: category}` for the places BeamNG.Earth finds within 100 m, nearest first; with `lat`, `lon`. Sent when the answer arrives; only the first answer with names. |

These go to the trip log and the live map, not to the dashboard's channels. The relay learns where the game's extension is from where its packets come from (the extension binds its socket to `127.0.0.1`), and answers there. A packet that is malformed or out of range is counted and dropped.

The relay flattens nested tables into dotted channel names (`wheelThermals.FL.brakeSurfaceTemperature`, `tire.FL.temp`, `damage.zone.FL`). An empty Lua table is encoded as `[]`, which the relay treats as "empty". The mod reads game state defensively: a missing game API loses one section, never the whole stream, and it disables itself after repeated errors rather than flooding the game log.


## The trip file format

A trip is one JSON Lines file in `triplog/` (one JSON object per line, so a crash loses at most the last line and a trip can be appended to while it is driven). The id is the start time in UTC: `20261114T081500Z`. The rules that decide where a trip begins and ends are at the top of [`relay/thrashdash/triplog.py`](../relay/thrashdash/triplog.py); the figures are worked out by [`tripstats.py`](../relay/thrashdash/tripstats.py).

```
{"k":"trip","fmt":1,"id":"20261114T081500Z","ts":1794600900,"vehicle":"etkc","veh":56965,"energy":"liquid"}
{"k":"p","t":0.0,"la":-41.2258,"lo":174.8803,"al":2.5,"s":0.0,"h":101.8,"d":0.0,"f":0.83,"fv":41.5,"dm":0.0}
{"k":"i","t":10.0,"place":{...},"lim":{"ms":13.89,"m":true,"u":"kmh"},"wx":{...},"gps":{...}}
{"k":"e","t":30.2,"name":"routeArrived","la":..,"lo":..}
{"k":"end","reason":"idle","t":601.0,"ts":1794601501,"dist":8123.4,"top":31.2,"n":412,"sum":{...}}
{"k":"name","name":"Sunday run to the coast"}
{"k":"star","v":true}
```

| Line | Meaning |
|:---|:---|
| `trip` | The header: format, id, start time (`ts`, unix), the vehicle model and id, and the energy kind (`liquid`, `electric`, `mixed`). A trip brought in from a GPX file also has `"imported": "gpx"` (and `"est": true` when its times were estimated), and no vehicle. |
| `p` | A point: `t` seconds into the trip, `la` and `lo` (real latitude and longitude: the game moves its own origin when you teleport, so its x and y are never kept), `al` altitude (m), `s` speed (m/s), `h` heading (degrees from north), `d` metres driven so far (summed from **every** sample, so thinning loses none), `f` fuel or charge (0 to 1), `fv` litres or kWh, `dm` body damage (% of beams broken), `g` the strongest force on the car since the previous point, in g (the relay watches the car's acceleration in every frame and takes the largest with each position). |
| `i` | The world around the car: `place` (street, place, region, country), `lim` (`ms` metres per second, `m` true when the limit is a **posted** one and absent when it is a guess from the kind of road, `u` the unit the road uses), `wx` weather, `gps` route progress. Written when the street or limit changes, or every 10 seconds. Each line is a whole picture and holds until the next line. |
| `poi` | The places the game found near where the car stood still: `poi` a list of `{n, c}` (name, category), with `la`, `lo`. Used to name the stop. |
| `e` | An event: `reset` (the R key), `routeArrived`, `worldReady`, `waypoint` (a pin the driver dropped). Events that end a trip (`teleported`, `vehicleSwitched`, `levelEnd`) end it instead. |
| `end` | The closing line: why it ended (`idle`, `teleport`, `jump`, `vehicle`, `level`, `lost`, `shutdown`, `interrupted`, `split` for the first part of a trip that was cut in two, or `imported` for a trip brought in from a GPX file), duration, distance, top speed, number of points, and `sum`, the summary worked out once (and worked out again whenever its rules change; it includes `stops`, where the car stood still for 30 s or more mid-trip, `refuels`, where fuel or charge was taken on, and `knocks`: every hard knock, `{t, la, lo, rise, g?, at}`, damage rising by 0.1 percentage points or more, or a force of 2 g or more, the two within 3 s counting as one; which of them are impacts is the `impactRule` setting). |
| `name` | A name the user gave the trip, appended after the end line (the last one wins; an empty one clears it). Renaming never rewrites the file. |
| `note` | `{"k":"note","t":30.0,"text":"..."}`: a note against the waypoint at `t` seconds into the trip, appended after the end line (the last one for a `t` wins; empty text clears it). |
| `star` | `{"k":"star","v":true}` marks the trip as a favourite, `false` takes the mark away; appended after the end line like a name (the last one wins). |
| `tags` | `{"k":"tags","v":["Track","Hill climb"]}` sets the trip's tags (the whole list; the last one wins; an empty list clears them); appended after the end line like a name. A damaged line gives no tags; more than 8 keeps the first 8. |
| `price` | `{"k":"price","v":{"liquid":2.45,"electric":0.31,"currency":"$"}}` sets the fuel price the trip is costed at (`null` takes it away); appended after the end line like a name, and beats the `price` the trip's first line carries (a copy of the setting as it was when the trip started). A damaged price is ignored. Only the price is stored: the cost is worked out where the trip is shown, from the fuel used in its summary. |

A trip that was open when ThrashDash stopped has no `end` line; the next start ends it as `interrupted`. A deleted trip is moved to `triplog/trash/`, not erased.

### The trip summary

`sum` on the closing line, and `stats` in the API, is worked out by [`tripstats.py`](../relay/thrashdash/tripstats.py) and carries `v`, the version of the rules that produced it: a stored summary from other rules is worked out again when it is read, so a rule can change without touching the files. Fields that do not apply to a trip are left out.

| Field | Meaning |
|:---|:---|
| `name`, `from`, `to`, `countries`, `places` | The name (from the street at the start and at the end), the two street names, and the countries and towns passed through. |
| `dist` (m), `dur`, `moving_s`, `stopped_s` (s), `avg_ms`, `avg_moving_ms`, `top_ms` (m/s), `bbox` | Distance, times, speeds and the box the route fits in. |
| `alt` | `{min, max, gain, loss}` in metres. |
| `fuel` | `{unit: "L" or "kWh", used, per_100km}`. |
| `damage` | `{start, end, max}`: body damage, % of beams broken. |
| `limits` | `{checked_s, over_s, over_m, max_over_ms}`: counted only where a limit is **posted**; `checked_s` is how much of the trip had one. |
| `wx` | Weather: temperature, wind, gusts, visibility, seconds of rain and snow. |
| `stops` | `[{t, dur, la, lo, at, poi?}]`: the car still for 30 s or more mid-trip, with the names of places found within 100 m. |
| `refuels` | `[{t, la, lo, added, unit, at}]`: fuel or charge taken on while stopped (2 L or 1 kWh at least). |
| `knocks` | `[{t, la, lo, rise, g?, at}]`: every hard knock, the candidates for an impact. A knock is body damage rising by 0.1 percentage points or more (a crash over a few samples counting once) or a force of 2 g or more, the two within 3 s being one knock; `rise` is the damage, `g` the strongest force (absent when the car sent none). Which are **impacts** is the `impactRule` setting, applied where the trips are shown, so changing it never needs a trip to be worked out again. |
| `g` | `{max, t, la, lo, at}`: the strongest force on the car, in g, and where. |
| `route` | When a route was set in the game: `{planned_m, eta_s, arrived_s, early_s, replans, off_max_m, left_m}`. |
| `waypoints`, `resets`, `arrivals` | Counts of the waypoints dropped, the R key presses and the routes arrived at. |

## Development

### Code map

| Path | What |
|:---|:---|
| [`mod/lua/vehicle/extensions/auto/thrashdash_telemetry.lua`](../mod/lua/vehicle/extensions/auto/thrashdash_telemetry.lua) | The mod: collects electrics, tyres, damage, states and energy kind, and sends the packets. |
| [`mod/lua/ge/extensions/thrashdash/trips.lua`](../mod/lua/ge/extensions/thrashdash/trips.lua), [`mod/scripts/thrashdash/modScript.lua`](../mod/scripts/thrashdash/modScript.lua) | The trip log's game side (BeamNG.Earth): positions, place names, speed limit, weather and events, sent to the relay. The game maps an extension name's `_` to a folder separator, so `thrashdash_trips` must be the file `thrashdash/trips.lua`. |
| [`mod/lua/ge/extensions/core/input/actions/thrashdash.json`](../mod/lua/ge/extensions/core/input/actions/thrashdash.json) | The game's control for dropping a waypoint (unbound until the player binds a key; it calls `thrashdash_trips.dropWaypoint()`). |
| [`relay/thrashdash/triplog.py`](../relay/thrashdash/triplog.py) | The trip log: turns the stream of positions into trips on disk (the rules for where one ends are at its top). |
| [`web/trips.html`](../web/trips.html), [`web/js/trips/`](../web/js/trips/) | The Trips page: `trips.js` draws it, `model.js` holds the logic Node tests (colours, graph, figures), and `compare.js` the comparison of two trips (including `impactsOf`, the impact rule, in `model.js`); the map library is in `web/vendor/leaflet/`. |
| [`relay/thrashdash/tripshare.py`](../relay/thrashdash/tripshare.py) | A trip as one html file: joins the Trips page's modules into one script and embeds the data. |
| [`relay/thrashdash/tripexport.py`](../relay/thrashdash/tripexport.py) | A trip as a GPX or KML file. |
| [`relay/thrashdash/tripimport.py`](../relay/thrashdash/tripimport.py) | A GPX file brought in as trips (checked first; thinned like a recording; marked `"imported": "gpx"`). |
| [`relay/thrashdash/earthlaunch.py`](../relay/thrashdash/earthlaunch.py) | Starting BeamNG.Earth along with ThrashDash (the highest version in a folder), following it, and quitting when it closes. |
| [`relay/thrashdash/tripedit.py`](../relay/thrashdash/tripedit.py) | Changing finished trips: joining two, cutting one, backing up and restoring (never in place: new files, originals to the trash). |
| [`relay/thrashdash/tripstats.py`](../relay/thrashdash/tripstats.py) | What a recorded trip adds up to: name, distance, speeds, places, speeding, weather, fuel, stops, refuels, knocks, peak G and the route. |
| [`relay/thrashdash/hub.py`](../relay/thrashdash/hub.py) | Where "what the car is doing now" lives; merges electrics, slow data and trips into channels. |
| [`relay/thrashdash/udp.py`](../relay/thrashdash/udp.py), [`simulator.py`](../relay/thrashdash/simulator.py) | Data sources: the real mod, and a simulated drive through the same code path. |
| [`relay/thrashdash/server.py`](../relay/thrashdash/server.py), [`api.py`](../relay/thrashdash/api.py) | The web server, WebSocket and REST API, and the request guard. |
| [`relay/thrashdash/store.py`](../relay/thrashdash/store.py), [`validate.py`](../relay/thrashdash/validate.py) | Layout and settings storage, and structural validation. |
| [`relay/thrashdash/trip.py`](../relay/thrashdash/trip.py) | The trip computer (meters A and B). |
| [`relay/thrashdash/routeplan.py`](../relay/thrashdash/routeplan.py) | Planning a GPS route: the stops, saved destinations, and sending the route to the game and taking its answer. |
| [`web/js/trips/route.js`](../web/js/trips/route.js), [`stops.js`](../web/js/trips/stops.js) | The Route view of the Trips page, and the stop helpers it uses (Node-tested). |
| [`relay/thrashdash/laps.py`](../relay/thrashdash/laps.py) | Lap and sector timing: the line geometry, the circuit and lap stores and the timer's rules (written at its top). |
| [`web/js/trips/circuits.js`](../web/js/trips/circuits.js), [`gates.js`](../web/js/trips/gates.js) | The Circuits view of the Trips page, and the line geometry it uses (Node-tested; the same rule as the relay's). |
| [`web/js/widgets/laptimer.js`](../web/js/widgets/laptimer.js) | The lap timer widget. |
| [`relay/thrashdash/windows.py`](../relay/thrashdash/windows.py), [`configs.py`](../relay/thrashdash/configs.py) | The window memory (dashboard windows and the map window), and the saved configs (named sets of windows). |
| [`relay/thrashdash/tripnotice.py`](../relay/thrashdash/tripnotice.py) | Popups about trips: the text, sending it to the game, and the banner for dashboard windows. |
| [`web/js/windowPlace.js`](../web/js/windowPlace.js), [`tripnotice.js`](../web/js/tripnotice.js) | A window putting itself where it was remembered; the trip-ended banner. |
| [`web/js/widgets/tripmap.js`](../web/js/widgets/tripmap.js) | The live map widget: a frame of the Trips page in its compact form. |
| [`relay/thrashdash/lan.py`](../relay/thrashdash/lan.py), [`netinfo.py`](../relay/thrashdash/netinfo.py) | Network access and deciding who counts as "this computer". |
| [`relay/thrashdash/auth.py`](../relay/thrashdash/auth.py) | The PIN: storage, signed sign-in cookies, QR tokens and guess throttling. The check itself is in `guard` in [`server.py`](../relay/thrashdash/server.py). |
| [`relay/thrashdash/launcher.py`](../relay/thrashdash/launcher.py) | Finds a Chromium-based browser and opens app windows (with a clean environment, so it works from inside a packaged app). |
| [`relay/thrashdash/lifecycle.py`](../relay/thrashdash/lifecycle.py), [`modinstall.py`](../relay/thrashdash/modinstall.py) | Quitting when the last window closes, and `--install-mod`. |
| [`relay/thrashdash/output.py`](../relay/thrashdash/output.py) | Where messages go when there is no console (the Windows exe). |
| [`packaging/`](../packaging/) | Everything that builds the AppImage (`build.sh`, `build-in-docker.sh`) and the Windows zip (`build.ps1`, `build-windows.bat`). |
| [`web/js/app.js`](../web/js/app.js) | The page: loading, scaling, switching, windows, the data loop. |
| [`web/js/widgets/`](../web/js/widgets/) | One file per widget, plus `index.js` (the registry). |
| [`web/js/skins/`](../web/js/skins/) | The looks: themes, dials, parts, icons. |
| [`web/js/editor/`](../web/js/editor/) | The editor, loaded only when you press **E**. |
| [`web/js/alerts.js`](../web/js/alerts.js), [`economy.js`](../web/js/economy.js), [`units.js`](../web/js/units.js) | Alert rules, fuel formatting, unit conversion. |
| [`tools/`](../tools/) | Developer tools: template generator, headless screenshot probe, browser test driver, packet listener. |
| [`docs/`]() | First captures from the real game, and screenshots. |

### Building the AppImage

```sh
packaging/build-in-docker.sh        # recommended: builds inside an Ubuntu 22.04 container (needs a running Docker)
packaging/build.sh                  # or build directly on this machine
```

Either produces `dist/ThrashDash-<version>-x86_64.AppImage` (about 12 MB). The steps are: make a Python environment under `build/`, freeze the relay, the dashboard (`web/`) and the mod (`mod/`) into one folder with [PyInstaller](https://pyinstaller.org/) (`packaging/thrashdash.spec`), put that in an `AppDir` with `packaging/AppRun`, the `.desktop` file and the icon, and pack it with [`appimagetool`](https://github.com/AppImage/appimagetool).

- **Build on an old system.** An AppImage uses the C library of the machine it runs on, so it runs on anything *not older* than the machine it was built on. `build-in-docker.sh` builds on Ubuntu 22.04 (glibc 2.35), which covers most Linux systems from about 2022 on. A build made directly on a very new distribution may not run on older ones (the script warns about this).
- **SELinux** (Fedora, openSUSE and others): the Docker script runs the container with SELinux labelling switched off for that one container, otherwise it cannot read the mounted folder ("Permission denied"). The folder is not relabelled.
- **What it downloads**: PyInstaller from PyPI, and `appimagetool` and its runtime from the AppImage project's GitHub releases. Their checksums are printed during the build. Nothing else is fetched, and everything it makes stays in `build/` and `dist/`.
- **`AppRun`** starts the program with `--quit-when-windows-close`, and keeps its output in `thrashdash.log` when there is no terminal. Commands that just print and exit (`--version`, `--help`, `--install-mod`) always print to where you ran them.
- **What is not inside**: a browser, and the game. The Windows program is built from the same recipe (below).

### Building the Windows program

On a Windows 10/11 64-bit machine with **Python 3.10 to 3.13** from [python.org](https://www.python.org/downloads/) (keep the *py launcher* option ticked) and nothing else: every package has a ready-made Windows build, so **no compiler or Visual Studio is needed**.

```bat
packaging\build-windows.bat
```

(or `powershell -ExecutionPolicy Bypass -File packaging\build.ps1`). It makes a Python environment under `build\`, freezes the program with PyInstaller using the same `packaging/thrashdash.spec` as the Linux build, and writes `dist\ThrashDash-<version>-windows-x64.zip` with its checksum.

- **Two programs share one folder:** `thrashdash.exe` has no console window (what you double-click; its messages go to `thrashdash.log`), and `thrashdash-cli.exe` is the same program with a console, for `--help`, `--version` and `--install-mod`.
- **Licences travel with it:** the build runs [`packaging/collect_licenses.py`](../packaging/collect_licenses.py), which puts every bundled package's licence in a `licenses` folder next to the program, and **stops** if the bundle contains a DLL it does not know. If that happens, the message names the file: add it, with its licence, to `NATIVE_WINDOWS` in that script. (The Windows table was written without a Windows build to check against, so expect to add an entry or two the first time.)
- **Lightly used on Windows.** The script was written on Linux; it has since built a working program that ran on Windows 11, but it has had far less use than the Linux build, and the Windows table in `collect_licenses.py` may still need an entry if a new DLL is bundled.

### Running the tests

```sh
.venv/bin/python -m unittest discover -s relay/tests -v      # relay (Windows: .venv\Scripts\python)
node --test tests/js                                         # front-end logic (Node 22+)
luajit tests/lua/test_telemetry.lua                          # the mod, against fake game objects
luajit tests/lua/test_trips.lua                              # the trip log's game side, likewise
.venv/bin/python tools/ui_test.py [--keep-shots DIR]         # drives the real UI in a headless browser
```

- The Python tests also run the two Lua tests when `luajit` is installed. As a guide, a full run is about 660 Python tests, 200 Node tests, 80 Lua checks and 560 browser checks.
- `ui_test.py` needs Chrome, Chromium or Edge. It starts its own relays with a throwaway data folder, so it never touches your layouts or settings, and it exercises the editor, menus, skins, alerts, trips, windows and the phone view with real mouse and keyboard input.
- The QR code encoder is cross-checked against Python's `qrcode` package (install `requirements-dev.txt`).
- `ui_test.py` takes several minutes and uses fixed ports (8771 and a few above, and UDP 24568): run one copy at a time, and stop any test relay left by a run that was interrupted.
- There is no continuous integration yet; run these commands before sending changes.

### Tools

| Tool | Use |
|:---|:---|
| `python tools/spike_listen.py [--slow] [--dump FILE]` | Listen to the mod's packets directly and print them (stop the relay first: both use UDP 4568). Handy to see what the game sends. |
| `python tools/shot.py URL out.png [--wait N] [--eval JS]` | Screenshot a page in a headless browser, in real time. |
| `python tools/make_templates.py` | Regenerate the built-in dashboards in `web/layouts/`. |
| `python tools/make_fonts.py` | Rebuild the bundled fonts. |

### Adding things

- **A widget**: add `web/js/widgets/<name>.js` exporting `spec` (label, default size, defaults, properties) and `create(def, ctx)` returning `{el, update(values)}`; register it in `web/js/widgets/index.js`. The editor's properties, the layout validator and the palette all come from `spec`, so no Python changes are needed. Add flat styles to `web/css/dash.css` and look styles to `web/css/skins.css`.
- **An alert**: add an entry to `ALERTS` in `web/js/alerts.js` (a rule that returns text or `null`). The id is stored in settings, so never rename one.
- **A dashboard template**: add a function to `tools/make_templates.py` and run it.
- **A unit type**: add it to `web/js/units.js` and, for a saved setting, to `UNIT_CHOICES` in `relay/thrashdash/validate.py`.
- **Channel names in the picker**: `web/js/editor/channels.js`.

### Conventions

- No build step and no runtime dependency beyond `aiohttp`. The page loads nothing from the internet.
- No `innerHTML` in the page: the interface is built with DOM calls.
- Layouts and settings are data, never code: nothing in them is evaluated.
- Anything that changes state on the relay checks the write rules; anything a phone may not do is hidden *and* refused by the relay.
- The mod only reads, and must never raise into the game.
- Comments explain *why*, not *what*.


## Security

ThrashDash is designed to be safe to leave running on a home computer. In short: it listens on this computer only until you switch on network access, other devices can only look, and other websites cannot reach it.

- **Local by default.** The relay listens on `127.0.0.1` only. When network access is on it also listens on this computer's *private* network addresses, one listener per address, never `0.0.0.0`, so VPN and public interfaces are not exposed. Switching it off closes them and drops connected devices.
- **Devices on the network need the PIN, and can then only view.** Until a device has signed in it is shown only the PIN page. Every change (layouts, settings, deleting, vehicle rules, trips, windows, even switching network access or reading the PIN) needs this computer.
- **The PIN is compulsory.** Network access cannot be switched on without one, and if it were ever on without one, every device would be refused rather than let in.
- **Guessing is slowed.** Five wrong PINs in a row from one device, then a wait that doubles with each further miss (up to an hour); and across all devices together, 30 wrong guesses in ten minutes make everyone wait a minute. Comparison takes the same time whether or not the first digits are right.
- **Sign-in is a signed cookie** (`HttpOnly`, `SameSite=Strict`) good for 30 days; changing the PIN changes the signing secret, which signs every device out at once. The QR code's token is random, works once, and expires in 5 minutes; it is removed from the address as soon as it is used. The PIN page only ever sends a device to a path on this site.
- **`Host` checking.** The `Host` header must name the relay: loopback names, and, only while network access is on, IP addresses or plain computer names (`my-pc`, `my-pc.local`). A public domain name is always refused, which stops a malicious web page using DNS rebinding to reach the relay through your own browser.
- **`Origin` checking and write header.** Requests with a foreign `Origin` are refused, and writes must carry `X-ThrashDash`, a header no other website can send (browsers require a preflight that the relay never answers). Bodies are capped at 256 KB, except the two uploads that are meant to be bigger (a GPX import, 20 MB; a backup restore, 512 MB), which are counted as they arrive and refused as soon as they pass the cap.
- **Safe ids.** Dashboard, window and trip ids match strict patterns, so they can never point outside their folder.
- **Strict page.** The page ships a strict Content-Security-Policy, loads nothing from the internet (the Trips page, and the live map widget it provides, alone may load map pictures from two named map servers) and builds its interface without `innerHTML`. Imported dashboards are checked twice (structure by the relay, meaning by the page).
- **The mod** only reads vehicle data and sends UDP to localhost. It never writes files.
- **Not provided:** https. The PIN and the sign-in cookie cross your network unencrypted, so the PIN keeps casual visitors out but does not protect against someone who can watch the traffic. The PIN is stored in plain text in `auth.json` (so this computer's menu can show it), readable only by your user. ThrashDash does not inspect or change the computer's firewall.


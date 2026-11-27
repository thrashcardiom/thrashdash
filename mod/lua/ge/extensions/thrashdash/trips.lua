-- ThrashDash trip log, game side (BeamNG.Earth).
--
-- Where the player is, once a second, and the world round them (the posted speed limit every second, the place name every
-- two, the weather and route every ten), sent to the ThrashDash relay. Nothing is decided
-- here: the relay turns the stream into trips (relay/thrashdash/triplog.py), so the rules can change without restarting the
-- game. It does nothing at all unless BeamNG.Earth is installed AND its level is loaded.
--
-- Why this lives in lua/ge/extensions/ and not beside the telemetry extension (lua/vehicle/...): the position, the place
-- name, the speed limit and the weather are BeamNG.Earth's (its modding API, beamngEarth_api), and that is only reachable
-- from the game's own Lua (GE), not from inside a vehicle. A phase-0 recording of a real drive showed the API works from
-- here, that LuaSocket (UDP) is available here as in the vehicle, and that the API's coordinates are true latitude and
-- longitude even though the game's own x/y change when BeamNG.Earth moves its origin.
--
-- The extension's name `thrashdash_trips` is this file's path with "/" written as "_": lua/ge/extensions/thrashdash/trips.lua.
--
-- Packets (JSON over UDP to the same port as the telemetry; "v" is the protocol version):
--   geo       every SAMPLE_S: lat, lon, alt (m), spd (m/s), hdg (degrees from north), ready (false while the world loads:
--             the game's speed is nonsense then, so the relay ignores such samples), veh (vehicle id), model, rt (seconds of
--             real time since this extension loaded), ts (unix time), n (counter)
--   geolive   every LIVE_S (a tenth of a second): just lat, lon, hdg, spd and rt, for the live map, which shows the car as close to
--             where it is as it can. It is not recorded (the relay hands it straight to the Trips page); the trip is made from `geo`.
--   geoinfo   every INFO_S while ready: limit {ms, mapped, unit}; and, less often, place {street, place, region, country}
--             (every PLACE_S), wx (weather) and gps (route progress) (every SLOW_S). The relay writes a reading down only when
--             it has changed, or every few seconds regardless (triplog.py), so asking often costs the trip file little.
--   geoevent  teleported, worldReady, routeArrived, reset (the R key), vehicleSwitched, levelStart, levelEnd
--
-- The relay also talks back: a "toast" datagram ({type, title, msg}) when a trip starts or ends, shown on screen the way BeamNG.Earth
-- shows its own messages. The socket is bound to 127.0.0.1 so only this computer can reach it, and only datagrams from the relay's
-- own address are believed.
--
-- Everything is pcall-wrapped: a bug or a changed API here must never disturb the player's game. Sending is fire and
-- forget; with the relay not running the packets simply go nowhere.

local M = {}

local PROTOCOL_VERSION = 1
local ADDRESS = "127.0.0.1"
local PORT = 4568
local SAMPLE_S = 1.0
local LIVE_S = 0.1 -- the live map's position: ten a second, the in-game map's pace to the eye (the page smooths between them)
local INFO_S = 1.0 -- the speed limit: it changes at a sign, so it is read as often as the position
local PLACE_S = 2.0 -- the place name (BeamNG.Earth's own answer lags a jump by several seconds, so faster would not help)
local SLOW_S = 10.0 -- the weather and the route's progress: they change slowly
local POI_STILL_S = 20 -- standing still this long is a stop worth naming (a red light is not)
local POI_RADIUS_M = 100 -- ...and the places looked for are those within this of the car
local POI_MAX = 3 -- at most this many are kept
local logTag = "thrashdash_trips"

local WEATHER_KEYS = { "code", "tempC", "windMs", "gustMs", "rainMmH", "snowCmH", "visibilityM" }
local GPS_KEYS = { "remaining", "eta", "off", "routeVersion" }
local ROUTE_KEYS = { "remaining", "eta", "off", "name", "planning", "routeVersion" } -- what the dashboard's route widget shows, every second
local STEPS_S = 20 -- the route's steps are sent when the route is set or re-planned, and again this often (a relay started later would miss them)
local MAX_STEPS = 120
local MAX_PATH_POINTS = 1500 -- the most points of the route's line sent to the maps (one datagram holds them; the relay takes as many)
local THIN_FROM_M = 8 -- a longer line is simplified by shape, starting at this tolerance (m) and doubling until it fits
local MAX_STOPS = 11 -- what BeamNG.Earth's gpsSetRoute takes: up to 10 stops on the way and the destination
local STEP_KEYS = { "type", "modifier", "name" } -- text kept (cut short); "exit", "offset" and "distance" are numbers

local udp
local clock = 0 -- seconds of real time since this extension loaded
local simClock = 0 -- seconds of GAME time since it loaded: stands still while the game is paused, and runs slower in slow motion (lap times use it)
local sampleT, infoT, placeT, slowT, liveT = 0, 0, 0, 0, 0
local counter = 0
local sent, failed = 0, 0
local shown = 0 -- messages from the relay shown on screen
local MAX_TITLE, MAX_MSG = 80, 240
local MAX_PER_FRAME = 4
local MIN_SECONDS, MAX_SECONDS = 3, 30 -- how long a message from the relay may ask to stay
local warnedSend = false

local function api()
  return rawget(_G, "beamngEarth_api")
end

-- The value of a protected call, or nil when it raised.
local function call(f, ...)
  local ok, value = pcall(f, ...)
  if ok then return value end
  return nil
end

local function pick(source, keys)
  if type(source) ~= "table" then return nil end
  local out
  for _, key in ipairs(keys) do
    if source[key] ~= nil then
      out = out or {}
      out[key] = source[key]
    end
  end
  return out
end

local function send(packet)
  if not udp then return false end
  packet.v = PROTOCOL_VERSION
  local ok, payload = pcall(jsonEncode, packet)
  if not ok or type(payload) ~= "string" then return false end
  local called, result, err = pcall(udp.sendto, udp, payload, ADDRESS, PORT)
  if called and result then
    sent = sent + 1
    return true
  end
  failed = failed + 1
  if not warnedSend then
    warnedSend = true
    log("W", logTag, "could not send to " .. ADDRESS .. ":" .. PORT .. ": " .. tostring(called and err or result))
  end
  return false
end

-- BeamNG.Earth's API when its level is loaded, else nil.
local function earth()
  local a = api()
  if a and call(a.inWorld) == true then return a end
  return nil
end

local function playerVehicle()
  local get = rawget(_G, "getPlayerVehicle")
  local veh = get and call(get, 0)
  if not veh then return nil, nil end
  return call(function() return veh:getID() end), call(function() return veh:getJBeamFilename() end)
end

local stillS, poiAsked, poiGot = 0, false, false

-- Once per stop (the car standing still for POI_STILL_S): ask BeamNG.Earth what is near, and tell the relay, which puts the names
-- against the stop ("Stopped 3 min at Z Petone"). Earth answers from map data it already has, so this costs no network; the answer
-- may come a little later, and only the first one with names in it is sent.
local function askPlaces(a, pose)
  if type(a.places) ~= "function" or call(a.has, "places") ~= true then return end
  local lat, lon = pose.lat, pose.lon
  poiGot = false
  call(a.places, lat, lon, { radius = POI_RADIUS_M, limit = 5, minConfidence = 0.5 }, function(result)
    if poiGot or type(result) ~= "table" or type(result.places) ~= "table" then return end
    local list = {}
    for _, place in ipairs(result.places) do
      if type(place) == "table" and type(place.name) == "string" and place.name ~= "" then
        list[#list + 1] = { n = place.name:sub(1, 80), c = type(place.category) == "string" and place.category:sub(1, 40) or nil }
        if #list >= POI_MAX then break end
      end
    end
    if #list == 0 then return end
    poiGot = true
    send({ type = "geopoi", rt = clock, lat = lat, lon = lon, poi = list })
  end)
end

local function watchStop(a, pose)
  if (tonumber(pose.speed) or 0) >= 1.0 then
    stillS, poiAsked = 0, false
    return
  end
  stillS = stillS + SAMPLE_S
  if stillS >= POI_STILL_S and not poiAsked then
    poiAsked = true
    askPlaces(a, pose)
  end
end

local function takeSample()
  local a = earth()
  if not a then return end
  local pose = call(a.pose)
  -- (no pose: the world is not there yet. camera: the free camera, which is not a drive)
  if type(pose) ~= "table" or not pose.lat or not pose.lon or pose.camera then return end
  watchStop(a, pose)
  local id, model = playerVehicle()
  counter = counter + 1
  send({ type = "geo", n = counter, rt = clock, ts = os.time(), lat = pose.lat, lon = pose.lon, alt = pose.alt,
         spd = pose.speed, hdg = pose.heading, ready = call(a.ready) == true, veh = id, model = model })
end

-- `withPlace` and `withSlow`: whether this reading also carries the place name, and the weather and route.
-- A position for the live map: no vehicle, no altitude, nothing the trip needs. Skipped for the free camera and while loading.
local function takeLive()
  local a = earth()
  if not a or call(a.ready) ~= true then return end
  local pose = call(a.pose)
  if type(pose) ~= "table" or not pose.lat or not pose.lon or pose.camera then return end
  send({ type = "geolive", rt = clock, st = simClock, lat = pose.lat, lon = pose.lon, hdg = pose.heading, spd = pose.speed })
end

-- The steps of the route (turn here, roundabout exit 2...) for the dashboard's "next turn". Each step is {type, modifier, name, exit, offset, distance}
-- (BeamNG.Earth API 1.8: type is "depart", "turn", "fork", "roundabout", "exit roundabout", "arrive"... as a routing service names them; offset is
-- the metres along the route where the step begins). Only what the widget reads is sent, with text cut short: a route can have hundreds.
local stepsVersion, stepsAt = nil, -1000

local function placeOf(item)
  if type(item) == "table" and type(item.lat) == "number" and type(item.lon) == "number" then
    return { lat = item.lat, lon = item.lon, name = type(item.name) == "string" and item.name:sub(1, 60) or nil }
  end
end

-- The points of a line that shape it: those within `tol` metres of the straight line between their kept neighbours are dropped
-- (Ramer-Douglas-Peucker, as BeamNG.Earth simplifies its own). x and y are the points in metres; the ends are always kept.
local function simplify(points, x, y, tol)
  local n = #points
  local keep = { [1] = true, [n] = true }
  local stack = { { 1, n } }
  while #stack > 0 do
    local span = table.remove(stack)
    local a, b = span[1], span[2]
    local ax, ay = x[a], y[a]
    local dx, dy = x[b] - ax, y[b] - ay
    local len2 = dx * dx + dy * dy
    local far, at = tol * tol, nil
    for i = a + 1, b - 1 do
      local px, py = x[i] - ax, y[i] - ay
      local t = len2 > 0 and math.max(0, math.min(1, (px * dx + py * dy) / len2)) or 0
      local d = (px - t * dx) ^ 2 + (py - t * dy) ^ 2
      if d > far then far, at = d, i end
    end
    if at then
      keep[at] = true
      stack[#stack + 1] = { a, at }
      stack[#stack + 1] = { at, b }
    end
  end
  local out = {}
  for i = 1, n do
    if keep[i] then out[#out + 1] = points[i] end
  end
  return out
end

-- At most MAX_PATH_POINTS of the line. Never every Nth point: BeamNG.Earth's line has points only where the road turns, so skipping
-- some by count cuts the bends. A line that is too long loses the points that matter least to its shape instead.
local function thin(points)
  if #points <= MAX_PATH_POINTS then return points end
  local metres = 111320
  local across = metres * math.cos(math.rad(points[1][1]))
  local x, y = {}, {}
  local lon, previous = points[1][2], points[1][2]
  for i, p in ipairs(points) do
    lon = lon + ((p[2] - previous + 180) % 360 - 180) -- (the short way across the date line)
    previous = p[2]
    x[i], y[i] = lon * across, p[1] * metres
  end
  local tol, out = THIN_FROM_M, nil
  repeat
    out = simplify(points, x, y, tol)
    tol = tol * 2
  until #out <= MAX_PATH_POINTS
  return out
end

local thinned = {} -- the last route's line, so a long one is simplified once and not at every resend

local function lineOf(version, source)
  local first, last = source[1], source[#source]
  local key = table.concat({ tostring(version), #source, tostring(first and first[1]), tostring(first and first[2]),
    tostring(last and last[1]), tostring(last and last[2]) }, " ")
  if thinned.key ~= key then
    local points = {}
    for i = 1, #source do
      local p = source[i]
      if type(p) == "table" and type(p[1]) == "number" and type(p[2]) == "number" then points[#points + 1] = { p[1], p[2] } end
    end
    thinned = { key = key, points = thin(points) }
  end
  return thinned.points
end

-- The planned route, for the dashboards and the maps (once per route, and now and then again): its line as [lat, lon] pairs, the
-- destination, the stops still ahead, and the steps (above). One read of gpsRoute() serves both packets.
local function sendRoute(a, gps)
  local version = gps.routeVersion
  stepsVersion, stepsAt = version, clock
  local route = call(a.gpsRoute)
  if type(route) ~= "table" then return end
  if type(route.points) == "table" and #route.points >= 2 then
    local points = lineOf(version, route.points)
    local waypoints = {}
    if type(gps.waypoints) == "table" then
      for i = 1, math.min(#gps.waypoints, MAX_STOPS) do
        local w = placeOf(gps.waypoints[i])
        if w then waypoints[#waypoints + 1] = w end
      end
    end
    if #points >= 2 then send({ type = "georoutepath", rt = clock, version = version, points = points, dest = placeOf(gps), waypoints = waypoints }) end
  end
  if type(route.steps) ~= "table" then return end
  local steps = {}
  for i = 1, math.min(#route.steps, MAX_STEPS) do
    local step = route.steps[i]
    if type(step) == "table" and type(step.offset) == "number" then
      local out = { offset = step.offset, distance = type(step.distance) == "number" and step.distance or nil, exit = type(step.exit) == "number" and step.exit or nil }
      for _, key in ipairs(STEP_KEYS) do
        if type(step[key]) == "string" then out[key] = step[key]:sub(1, 60) end
      end
      steps[#steps + 1] = out
    end
  end
  send({ type = "geosteps", rt = clock, version = version, dist = type(route.dist) == "number" and route.dist or nil, steps = steps })
end

local function takeInfo(withPlace, withSlow)
  local a = earth()
  if not a or call(a.ready) ~= true then return end
  local packet = { type = "geoinfo", rt = clock, ts = os.time(), limit = pick(call(a.speedLimit), { "ms", "mapped", "unit" }) }
  if withPlace then packet.place = call(a.place) end
  if withSlow then
    packet.wx = pick(call(a.weather), WEATHER_KEYS)
    packet.gps = pick(call(a.gps), GPS_KEYS)
  end
  send(packet)
  -- The route's progress for the dashboard, every second (the trip log keeps its own, slower reading above). A separate packet, so
  -- the trip log is not written to more often; and one that says "no route" when there is none, because for the dashboard silence
  -- would be ambiguous.
  local gps = call(a.gps)
  send({ type = "georoute", rt = clock, gps = pick(gps, ROUTE_KEYS) })
  if type(gps) == "table" and gps.routeVersion ~= nil then
    if gps.routeVersion ~= stepsVersion or clock - stepsAt >= STEPS_S then sendRoute(a, gps) end
  else
    stepsVersion = nil -- no route: the next one's steps are sent at once
  end
end

local function sendEvent(name)
  local a = api()
  if not a then return end -- BeamNG.Earth is not installed: nothing to log
  local pose = call(a.pose)
  local packet = { type = "geoevent", name = name, rt = clock, ts = os.time() }
  if type(pose) == "table" then packet.lat, packet.lon = pose.lat, pose.lon end
  send(packet)
end

-- A message on screen, the way BeamNG.Earth shows its own warnings.
-- `seconds`: how long it stays (the game's own default is 5, which is easy to miss while driving). The new UI reads `timeout` in
-- seconds, the older one `timeOut` in milliseconds; both are given.
local function toast(title, msg, kind, seconds)
  local gh = rawget(_G, "guihooks")
  if not (gh and gh.trigger) then return false end
  local data = { type = kind or "info", title = title, msg = msg }
  if seconds then data.config = { timeout = seconds, timeOut = seconds * 1000 } end
  return (pcall(gh.trigger, "toastrMsg", data))
end

-- Whatever the relay has sent back since last time. Only the relay's own address is believed, and only a "toast" of plain text.
-- ThrashDash (the Trips page's Route view) can ask the game to set or clear the GPS route. Only the relay's own address is believed, and
-- only plain places in range. The answer goes back as a `georouteack`, so the page can say whether it worked.
local function ack(ok, why)
  send({ type = "georouteack", rt = clock, ok = ok == true, why = type(why) == "string" and why:sub(1, 120) or nil })
end

local function applyRoute(msg)
  local a = earth()
  if not a then return ack(false, "BeamNG.Earth's level is not loaded") end
  if msg.action == "clear" then
    call(a.gpsClear)
    return ack(true)
  end
  if msg.action ~= "set" or type(msg.stops) ~= "table" or #msg.stops < 1 or #msg.stops > MAX_STOPS then return ack(false, "not a route") end
  local stops = {}
  for i = 1, #msg.stops do
    local stop = placeOf(msg.stops[i])
    if not stop or stop.lat < -90 or stop.lat > 90 or stop.lon < -180 or stop.lon > 180 then return ack(false, "a stop is not a place") end
    stops[i] = stop
  end
  local ok, result, why
  if #stops == 1 then
    ok, result, why = pcall(a.gpsSet, stops[1].lat, stops[1].lon, stops[1].name)
  else
    ok, result, why = pcall(a.gpsSetRoute, stops)
  end
  if not ok then return ack(false, "BeamNG.Earth refused: " .. tostring(result)) end
  if result == true then
    log("I", logTag, "route set by ThrashDash: " .. #stops .. " stop(s)")
    return ack(true)
  end
  return ack(false, type(why) == "string" and why or "BeamNG.Earth did not take the route")
end

local function receiveMessages()
  if not udp then return end
  for _ = 1, MAX_PER_FRAME do
    local ok, data, ip, port = pcall(udp.receivefrom, udp)
    if not ok or type(data) ~= "string" then return end
    if ip == ADDRESS and port == PORT then
      local decoded = call(jsonDecode, data)
      if type(decoded) == "table" and decoded.type == "route" then
        pcall(applyRoute, decoded)
      elseif type(decoded) == "table" and decoded.type == "toast" and type(decoded.msg) == "string" then
        local title = type(decoded.title) == "string" and decoded.title or "ThrashDash"
        local seconds = tonumber(decoded.seconds)
        if seconds then seconds = math.min(MAX_SECONDS, math.max(MIN_SECONDS, seconds)) end
        if toast(title:sub(1, MAX_TITLE), decoded.msg:sub(1, MAX_MSG), nil, seconds) then
          shown = shown + 1
          log("I", logTag, "message from ThrashDash shown: " .. title:sub(1, MAX_TITLE)) -- (so beamng.log says whether one arrived)
        end
      end
    end
  end
end

function M.onUpdate(dtReal, dtSim)
  local dt = tonumber(dtReal) or 0
  if dt <= 0 then return end
  clock = clock + dt
  simClock = simClock + (tonumber(dtSim) or dt) -- (a caller that gives no game time is taken to run in real time)
  receiveMessages()
  sampleT, infoT, placeT, slowT, liveT = sampleT + dt, infoT + dt, placeT + dt, slowT + dt, liveT + dt
  if liveT >= LIVE_S then
    liveT = liveT >= 2 * LIVE_S and 0 or liveT - LIVE_S -- (after a stall, one, not a burst)
    takeLive()
  end
  if sampleT >= SAMPLE_S then
    -- (after a stall of several seconds, one sample: not a burst of them)
    sampleT = sampleT >= 2 * SAMPLE_S and 0 or sampleT - SAMPLE_S
    takeSample()
  end
  if infoT >= INFO_S then
    infoT = 0
    local withPlace, withSlow = placeT >= PLACE_S, slowT >= SLOW_S
    if withPlace then placeT = 0 end
    if withSlow then slowT = 0 end
    takeInfo(withPlace, withSlow)
  end
end

-- The events: the game calls these on every loaded extension that defines them (BeamNG.Earth's through its own hook).
-- The "drop a waypoint" key (core/input/actions/thrashdash.json): marks where the car is in the trip being recorded. The relay answers with
-- a message on screen saying whether it went in.
function M.dropWaypoint() sendEvent("waypoint") end

function M.onBeamNGEarthTeleported() sendEvent("teleported") end
function M.onBeamNGEarthWorldReady() sendEvent("worldReady") end
function M.onBeamNGEarthRouteArrived() sendEvent("routeArrived") end
function M.onVehicleResetted() sendEvent("reset") end
function M.onVehicleSwitched() sendEvent("vehicleSwitched") end
function M.onClientStartMission() sendEvent("levelStart") end
function M.onClientEndMission() sendEvent("levelEnd") end

-- From the console (extensions.load already happened at start-up): what this extension has done so far.
function M.status()
  return { earthApi = api() ~= nil, inWorld = call(function() return api().inWorld() end) == true,
           socket = udp ~= nil, sent = sent, failed = failed, shown = shown }
end

-- A message the way BeamNG.Earth shows its own warnings. (The Messages app that api.notify() and ui_message() rely on is
-- not in BeamNG.Earth's single-player UI layout, so those two show nothing there: found when testing in the real game.)
function M.testToast(text)
  return toast("ThrashDash trips", text or "The trip log is running.")
end

function M.onExtensionLoaded()
  local ok, err = pcall(function()
    udp = socket.udp()
    udp:settimeout(0)
  end)
  if not ok then
    log("E", logTag, "could not create a UDP socket: " .. tostring(err))
    udp = nil
    return
  end
  -- Bound to this computer only, so the relay's messages cannot come from (or the socket be reached by) the network. If this
  -- fails the positions still go out; only the messages back would not arrive.
  local bound, bindErr = pcall(udp.setsockname, udp, ADDRESS, 0)
  if not bound then log("W", logTag, "could not bind the receiving side: " .. tostring(bindErr)) end
  log("I", logTag, string.format("trip log ready -> %s:%d (BeamNG.Earth API %s)", ADDRESS, PORT, api() and "found" or "not found"))
end

function M.onExtensionUnloaded()
  if udp then
    pcall(udp.close, udp)
    udp = nil
  end
end

return M

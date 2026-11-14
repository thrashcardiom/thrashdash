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
local logTag = "thrashdash_trips"

local WEATHER_KEYS = { "code", "tempC", "windMs", "gustMs", "rainMmH", "snowCmH", "visibilityM" }
local GPS_KEYS = { "remaining", "eta", "off", "routeVersion" }

local udp
local clock = 0 -- seconds of real time since this extension loaded
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

local function takeSample()
  local a = earth()
  if not a then return end
  local pose = call(a.pose)
  -- (no pose: the world is not there yet. camera: the free camera, which is not a drive)
  if type(pose) ~= "table" or not pose.lat or not pose.lon or pose.camera then return end
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
  send({ type = "geolive", rt = clock, lat = pose.lat, lon = pose.lon, hdg = pose.heading, spd = pose.speed })
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
local function receiveMessages()
  if not udp then return end
  for _ = 1, MAX_PER_FRAME do
    local ok, data, ip, port = pcall(udp.receivefrom, udp)
    if not ok or type(data) ~= "string" then return end
    if ip == ADDRESS and port == PORT then
      local decoded = call(jsonDecode, data)
      if type(decoded) == "table" and decoded.type == "toast" and type(decoded.msg) == "string" then
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

function M.onUpdate(dtReal)
  local dt = tonumber(dtReal) or 0
  if dt <= 0 then return end
  clock = clock + dt
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

-- Tests for the game-side trip log, run with LuaJIT against FAKE game objects (the real game is not needed):
--     luajit tests/lua/test_trips.lua
-- They load the real extension file and check what it sends, when, and that it stays quiet and harmless when BeamNG.Earth is
-- missing, the world is not ready, or a call fails.

local MOD = "mod/lua/ge/extensions/thrashdash/trips.lua"
local SCRIPT = "mod/scripts/thrashdash/modScript.lua"

local passed, failed = 0, 0
local function check(name, cond, detail)
  if cond then passed = passed + 1
  else failed = failed + 1; print("FAIL  " .. name .. (detail and ("   [" .. tostring(detail) .. "]") or "")) end
end

-- ---- fake game --------------------------------------------------------------------------------------------------------
local captured, logs, closed
local sendFails
local toasts
local inbox, bound, wire

local function resetGame()
  captured, logs, closed, toasts = {}, {}, false, {}
  inbox, bound, wire = {}, nil, {}
  sendFails = false
  rawset(_G, "jsonDecode", function(text) return wire[text] end)
  rawset(_G, "log", function(level, tag, text) logs[#logs + 1] = level .. " " .. tag .. " " .. text end)
  rawset(_G, "jsonEncode", function(t) captured[#captured + 1] = t; return "json" end)
  rawset(_G, "socket", { udp = function()
    return { settimeout = function() end, close = function() closed = true end,
             setsockname = function(_, ip, port) bound = { ip, port } end,
             receivefrom = function()
               local m = table.remove(inbox, 1)
               if m then return m.data, m.ip or "127.0.0.1", m.port or 4568 end
               return nil, "timeout"
             end,
             sendto = function(_, payload, addr, port)
               if sendFails then return nil, "refused" end
               return 1
             end }
  end })
  rawset(_G, "getPlayerVehicle", function() return {
    getID = function() return 56965 end, getJBeamFilename = function() return "etkc" end } end)
  rawset(_G, "guihooks", { trigger = function(name, data) toasts[#toasts + 1] = { name = name, data = data } end })
  rawset(_G, "beamngEarth_api", nil)
end

local world
local function installEarth(overrides)
  world = { inWorld = true, ready = true, camera = false, lat = -41.2258, lon = 174.8803 }
  local api = {
    inWorld = function() return world.inWorld end,
    ready = function() return world.ready end,
    pose = function() return { lat = world.lat, lon = world.lon, alt = 2.5, speed = 12.5, heading = 101.8, camera = world.camera } end,
    place = function() return { street = "Oriental Parade", country = "New Zealand" } end,
    speedLimit = function() return { ms = 13.89, mapped = true, unit = "kmh", extra = "dropped" } end,
    weather = function() return { tempC = 15, windMs = 4.4, secret = 1 } end,
    gps = function() return { remaining = 1000, eta = 90, name = "x", waypoints = {} } end,
  }
  for k, v in pairs(overrides or {}) do api[k] = v end
  rawset(_G, "beamngEarth_api", api)
end

local function load()
  resetGame()
  local mod = dofile(MOD)
  return mod
end

local function ofType(kind)
  local out = {}
  for _, p in ipairs(captured) do if p.type == kind then out[#out + 1] = p end end
  return out
end

-- ---- the extension starts and stops --------------------------------------------------------------------------------------
do
  local M = load()
  installEarth()
  M.onExtensionLoaded()
  check("logs that it is ready and that the Earth API was found", logs[1] and logs[1]:find("found", 1, true) and logs[1]:find("4568", 1, true), logs[1])
  M.onExtensionUnloaded()
  check("closes its socket when unloaded", closed)
end

do
  local M = load()
  M.onExtensionLoaded()
  check("says so when the Earth API is not there", logs[1] and logs[1]:find("not found", 1, true), logs[1])
  M.onUpdate(1.5); M.onUpdate(1.5); M.onUpdate(15)
  check("sends nothing at all without BeamNG.Earth", #captured == 0)
  M.onVehicleResetted(); M.onClientEndMission()
  check("sends no events without BeamNG.Earth", #captured == 0)
end

do
  resetGame()
  rawset(_G, "socket", { udp = function() error("no sockets here") end })
  local M = dofile(MOD)
  installEarth()
  M.onExtensionLoaded()
  check("a missing socket is logged, not raised", logs[1] and logs[1]:find("could not create", 1, true), logs[1])
  M.onUpdate(1.5)
  check("and it then sends nothing", #captured == 0)
end

-- ---- samples -----------------------------------------------------------------------------------------------------------
do
  local M = load()
  installEarth()
  M.onExtensionLoaded()
  for _ = 1, 3 do M.onUpdate(1 / 3) end -- one second in three frames
  local geo = ofType("geo")
  check("one position per second, not per frame", #geo == 1, #geo)
  local g = geo[1] or {}
  check("the sample carries the position and the car", g.lat == -41.2258 and g.lon == 174.8803 and g.alt == 2.5 and g.spd == 12.5 and g.hdg == 101.8, g.lat)
  check("the sample says it is ready, and which vehicle", g.ready == true and g.veh == 56965 and g.model == "etkc")
  check("the sample is stamped for the relay", g.v == 1 and type(g.rt) == "number" and type(g.ts) == "number" and g.n == 1)
  for _ = 1, 9 do M.onUpdate(1) end
  check("ten more seconds, ten more samples", #ofType("geo") == 10, #ofType("geo"))
  check("the counter climbs", ofType("geo")[10].n == 10)
  check("the real-time clock climbs with it", ofType("geo")[10].rt > ofType("geo")[1].rt)
end

do
  local M = load()
  installEarth()
  M.onExtensionLoaded()
  M.onUpdate(40) -- a long stall (a loading screen)
  check("a stall gives one sample, not forty", #ofType("geo") == 1, #ofType("geo"))
  M.onUpdate(0); M.onUpdate(-1); M.onUpdate(nil)
  check("a zero, negative or missing step is ignored", #ofType("geo") == 1)
end

do
  local M = load()
  installEarth()
  M.onExtensionLoaded()
  world.ready = false
  M.onUpdate(1)
  local g = ofType("geo")[1] or {}
  check("a sample taken while loading is sent, marked not ready (the relay drops it)", g.ready == false)
  M.onUpdate(10)
  check("and no place/weather is sent while loading", #ofType("geoinfo") == 0)
end

do
  local M = load()
  installEarth()
  M.onExtensionLoaded()
  world.inWorld = false
  for _ = 1, 12 do M.onUpdate(1) end
  check("nothing is sent outside the BeamNG.Earth level", #captured == 0)
  world.inWorld, world.camera = true, true
  M.onUpdate(1)
  check("nothing is sent from the free camera (not a drive)", #ofType("geo") == 0)
end

do
  local M = load()
  installEarth({ pose = function() return nil end })
  M.onExtensionLoaded()
  M.onUpdate(1)
  check("no position yet sends no position", #ofType("geo") == 0)
  installEarth({ pose = function() error("boom") end, ready = function() error("boom") end })
  M.onUpdate(1)
  check("an error inside the API is swallowed", true)
  check("and no position either", #ofType("geo") == 0)
end

do
  rawset(_G, "getPlayerVehicle", nil)
  local M = load()
  rawset(_G, "getPlayerVehicle", nil)
  installEarth()
  M.onExtensionLoaded()
  M.onUpdate(1)
  local g = ofType("geo")[1]
  check("a position is still sent when the vehicle cannot be read", g and g.lat == -41.2258 and g.veh == nil)
end

-- ---- the live position (ten a second, for the live map) -------------------------------------------------------------------
do
  local M = load()
  installEarth()
  M.onExtensionLoaded()
  for _ = 1, 10 do M.onUpdate(0.1) end
  local live = ofType("geolive")
  check("the live position goes out ten times a second", #live == 10, #live)
  local g = live[1] or {}
  check("it carries where the car is and which way it faces, and nothing the trip needs", g.lat == -41.2258 and g.lon == 174.8803 and g.hdg == 101.8 and g.spd == 12.5 and g.alt == nil and g.veh == nil and g.model == nil and type(g.rt) == "number")
  M.onUpdate(0.1) -- (a whole second has now passed, by a hair)
  check("and the slow recording position stays once a second", #ofType("geo") == 1, #ofType("geo"))
  check("the clock climbs", live[10].rt > live[1].rt)
end

do
  local M = load()
  installEarth()
  M.onExtensionLoaded()
  M.onUpdate(5)
  check("a stall gives one live position, not fifty", #ofType("geolive") == 1, #ofType("geolive"))
end

do
  local M = load()
  installEarth()
  M.onExtensionLoaded()
  world.ready = false
  for _ = 1, 5 do M.onUpdate(0.1) end
  check("nothing live is sent while the world loads", #ofType("geolive") == 0)
  world.ready, world.camera = true, true
  for _ = 1, 5 do M.onUpdate(0.1) end
  check("nor from the free camera", #ofType("geolive") == 0)
  world.camera, world.inWorld = false, false
  for _ = 1, 5 do M.onUpdate(0.1) end
  check("nor outside the BeamNG.Earth level", #ofType("geolive") == 0)
end

do
  local M = load()
  M.onExtensionLoaded()
  for _ = 1, 5 do M.onUpdate(0.1) end
  check("nor without BeamNG.Earth", #captured == 0)
end

-- ---- the slower packets -------------------------------------------------------------------------------------------------
do
  local M = load()
  installEarth()
  M.onExtensionLoaded()
  for _ = 1, 25 do M.onUpdate(1) end
  local info = ofType("geoinfo")
  check("the speed limit is read every second", #info == 25, #info)
  local withPlace, withWeather = 0, 0
  for _, i in ipairs(info) do
    if i.place then withPlace = withPlace + 1 end
    if i.wx then withWeather = withWeather + 1 end
  end
  check("the place name every two seconds", withPlace == 12, withPlace)
  check("the weather and route every ten", withWeather == 2, withWeather)
  local first, tenth = info[1], info[10]
  check("a reading with no place is just the limit", first.place == nil and first.wx == nil and first.gps == nil and first.limit ~= nil)
  check("the speed limit keeps only what the relay reads", first.limit.ms == 13.89 and first.limit.mapped == true and first.limit.unit == "kmh" and first.limit.extra == nil)
  check("the place name comes through", info[2].place and info[2].place.street == "Oriental Parade" and info[2].place.country == "New Zealand")
  check("the weather keeps only what the relay reads", tenth.wx and tenth.wx.tempC == 15 and tenth.wx.secret == nil)
  check("the route keeps only what the relay reads", tenth.gps and tenth.gps.remaining == 1000 and tenth.gps.name == nil and tenth.gps.waypoints == nil)
end

do
  local M = load()
  installEarth({ speedLimit = function() error("boom") end, weather = function() return nil end, gps = function() return nil end,
                 place = function() return nil end })
  M.onExtensionLoaded()
  for _ = 1, 11 do M.onUpdate(1) end
  local info = ofType("geoinfo")
  local bare = true
  for _, i in ipairs(info) do
    if i.limit ~= nil or i.wx ~= nil or i.gps ~= nil or i.place ~= nil then bare = false end
  end
  check("a failing or empty source leaves out only its own part", #info == 11 and bare, #info)
  check("and the positions carry on", #ofType("geo") == 11)
end

do
  local M = load()
  installEarth()
  M.onExtensionLoaded()
  world.ready = false
  for _ = 1, 12 do M.onUpdate(1) end
  check("nothing about the world is read while it loads", #ofType("geoinfo") == 0)
end

-- ---- events --------------------------------------------------------------------------------------------------------------
do
  local M = load()
  installEarth()
  M.onExtensionLoaded()
  M.onBeamNGEarthTeleported(); M.onBeamNGEarthWorldReady(); M.onBeamNGEarthRouteArrived()
  M.onVehicleResetted(); M.onVehicleSwitched(); M.onClientStartMission(); M.onClientEndMission()
  local names = {}
  for _, e in ipairs(ofType("geoevent")) do names[#names + 1] = e.name end
  check("every event is sent, by name", table.concat(names, ",") == "teleported,worldReady,routeArrived,reset,vehicleSwitched,levelStart,levelEnd", table.concat(names, ","))
  local e = ofType("geoevent")[1]
  check("an event carries where it happened", e.lat == -41.2258 and e.lon == 174.8803 and type(e.rt) == "number")
end

-- ---- waypoints and the places near a stop ---------------------------------------------------------------------------------------
do
  local M = load()
  installEarth()
  M.onExtensionLoaded()
  M.dropWaypoint()
  local e = ofType("geoevent")[1] or {}
  check("the waypoint key sends a waypoint event with where it was pressed", e.name == "waypoint" and e.lat == -41.2258 and e.lon == 174.8803)
  rawset(_G, "beamngEarth_api", nil)
  local before = #captured
  M.dropWaypoint()
  check("with no BeamNG.Earth the waypoint key does nothing, harmlessly", #captured == before)
end

local function standingEarth(asks, answer)
  installEarth({
    pose = function() return { lat = -41.2, lon = 174.8, alt = 2.5, speed = world.speed or 0, heading = 0 } end,
    has = function(what) return what == "places" end,
    places = function(lat, lon, opts, cb) asks[#asks + 1] = { lat = lat, lon = lon, opts = opts }; if answer then cb(answer) end return 1 end,
  })
end

do
  local M = load()
  local asks = {}
  standingEarth(asks, { places = { { name = "Z Petone", category = "gas_station" }, { name = "", category = "x" }, { category = "no name" }, { name = "Cafe Roma", category = "cafe" } } })
  M.onExtensionLoaded()
  for _ = 1, 19 do M.onUpdate(1) end
  check("standing still for less than twenty seconds asks for nothing", #asks == 0, #asks)
  M.onUpdate(1)
  check("after twenty seconds it asks what is near, once", #asks == 1 and asks[1].lat == -41.2 and asks[1].opts.radius == 100, #asks)
  local poi = ofType("geopoi")[1] or {}
  check("and sends the names, with their kind, leaving out ones with none", poi.poi and #poi.poi == 2 and poi.poi[1].n == "Z Petone" and poi.poi[1].c == "gas_station" and poi.poi[2].n == "Cafe Roma", poi.poi and #poi.poi)
  check("the reading says where it was", poi.lat == -41.2 and poi.lon == 174.8 and type(poi.rt) == "number")
  for _ = 1, 30 do M.onUpdate(1) end
  check("a long stop is asked about only once", #asks == 1 and #ofType("geopoi") == 1, #asks)
  world.speed = 10
  for _ = 1, 5 do M.onUpdate(1) end
  world.speed = 0
  for _ = 1, 20 do M.onUpdate(1) end
  check("moving off and stopping again is a new stop, asked about again", #asks == 2, #asks)
end

do
  local M = load()
  local asks = {}
  standingEarth(asks, { places = {} })
  M.onExtensionLoaded()
  for _ = 1, 25 do M.onUpdate(1) end
  check("a stop with nothing near it sends nothing", #asks == 1 and #ofType("geopoi") == 0)
end

do
  local M = load()
  local later
  installEarth({
    pose = function() return { lat = -41.2, lon = 174.8, alt = 2.5, speed = 0, heading = 0 } end,
    has = function(what) return what == "places" end,
    places = function(lat, lon, opts, cb) later = cb; return 1 end,
  })
  M.onExtensionLoaded()
  for _ = 1, 25 do M.onUpdate(1) end
  check("nothing is sent until the answer comes", #ofType("geopoi") == 0)
  later({ places = { { name = "Late Cafe" } }, pending = true })
  later({ places = { { name = "Second answer" } } })
  local poi = ofType("geopoi")
  check("an answer that comes later is sent, and only the first with names", #poi == 1 and poi[1].poi[1].n == "Late Cafe", #poi)
  later("junk"); later(nil)
end

do
  local M = load()
  local asks = {}
  installEarth({
    pose = function() return { lat = -41.2, lon = 174.8, alt = 2.5, speed = 0, heading = 0 } end,
    has = function() return false end,
    places = function(lat, lon, opts, cb) asks[#asks + 1] = 1 end,
  })
  M.onExtensionLoaded()
  for _ = 1, 25 do M.onUpdate(1) end
  check("an Earth without the places feature is not asked", #asks == 0)
  installEarth({ pose = function() return { lat = -41.2, lon = 174.8, alt = 2.5, speed = 0, heading = 0 } end, has = function() error("boom") end, places = function() error("boom") end })
  local ok = pcall(function() for _ = 1, 25 do M.onUpdate(1) end end)
  check("an Earth whose places call fails does not stop the trip log", ok and #ofType("geo") > 0)
end

-- ---- trouble ---------------------------------------------------------------------------------------------------------------
do
  local M = load()
  installEarth()
  M.onExtensionLoaded()
  sendFails = true
  for _ = 1, 5 do M.onUpdate(1) end
  local warned = 0
  for _, l in ipairs(logs) do if l:find("could not send", 1, true) then warned = warned + 1 end end
  check("a failing send is logged once, not every second", warned == 1, warned)
  check("failures are counted (a position, a reading and a live position each time)", M.status().failed == 15, M.status().failed)
  sendFails = false
  M.onUpdate(1)
  check("sending resumes by itself when the relay comes back", M.status().sent == 3, M.status().sent)
end

do
  local M = load()
  installEarth()
  M.onExtensionLoaded()
  rawset(_G, "jsonEncode", function() error("encoder gone") end)
  M.onUpdate(1)
  check("an encoder failure is swallowed", true)
end

do
  local M = load()
  installEarth()
  local before = M.status()
  check("status works before loading", before.socket == false and before.sent == 0)
  M.onExtensionLoaded()
  local s = M.status()
  check("status reports the Earth API and the socket", s.earthApi and s.inWorld and s.socket)
end

-- ---- messages from the relay ---------------------------------------------------------------------------------------------
local function say(text, table_, from)
  wire[text] = table_
  inbox[#inbox + 1] = { data = text, ip = from and from.ip, port = from and from.port }
end

do
  local M = load()
  installEarth()
  M.onExtensionLoaded()
  check("the receiving side is bound to this computer only", bound and bound[1] == "127.0.0.1" and bound[2] == 0)
  say("a", { type = "toast", title = "Trip finished", msg = "Courtenay Place: 2.12 km" })
  M.onUpdate(0.1)
  check("a toast from the relay is shown the way BeamNG.Earth shows its own", toasts[1] and toasts[1].name == "toastrMsg" and toasts[1].data.title == "Trip finished" and toasts[1].data.msg == "Courtenay Place: 2.12 km" and toasts[1].data.type == "info")
  check("and counted", M.status().shown == 1)
end

do
  local M = load()
  installEarth()
  M.onExtensionLoaded()
  say("elsewhere", { type = "toast", title = "x", msg = "from another port" }, { port = 9999 })
  say("other host", { type = "toast", title = "x", msg = "from another address" }, { ip = "192.168.1.9" })
  say("not toast", { type = "reboot", msg = "x" })
  say("no text", { type = "toast", title = "x", msg = 5 })
  say("garbage", nil)
  M.onUpdate(0.1)
  check("only a toast of plain text from the relay's own address is believed", #toasts == 0, #toasts)
end

do
  local M = load()
  installEarth()
  M.onExtensionLoaded()
  say("long", { type = "toast", title = string.rep("T", 500), msg = string.rep("m", 5000) })
  say("untitled", { type = "toast", msg = "hello" })
  M.onUpdate(0.1)
  check("a long message is cut short", #toasts[1].data.title <= 80 and #toasts[1].data.msg <= 240)
  check("a message with no title gets one", toasts[2] and toasts[2].data.title == "ThrashDash" and toasts[2].data.msg == "hello")
end

do
  local M = load()
  installEarth()
  M.onExtensionLoaded()
  say("slow", { type = "toast", title = "t", msg = "m", seconds = 12 })
  say("short", { type = "toast", title = "t", msg = "m", seconds = 0.1 })
  say("endless", { type = "toast", title = "t", msg = "m", seconds = 99999 })
  say("none", { type = "toast", title = "t", msg = "m" })
  M.onUpdate(0.1)
  check("a message can ask to stay longer than the game's few seconds (in both of the UI's units)", toasts[1].data.config and toasts[1].data.config.timeout == 12 and toasts[1].data.config.timeOut == 12000)
  check("but not for less than 3 seconds or more than 30", toasts[2].data.config.timeout == 3 and toasts[3].data.config.timeout == 30)
  check("and with no request it gets the game's own time", toasts[4].data.config == nil)
end

do
  local M = load()
  installEarth()
  M.onExtensionLoaded()
  for i = 1, 10 do say("m" .. i, { type = "toast", title = "t", msg = "m" .. i }) end
  M.onUpdate(0.1)
  check("a flood is spread over frames, a few at a time", #toasts == 4, #toasts)
  M.onUpdate(0.1)
  check("and the rest follow", #toasts == 8, #toasts)
end

do
  local M = load()
  installEarth()
  M.onExtensionLoaded()
  rawset(_G, "guihooks", nil)
  say("a", { type = "toast", title = "t", msg = "m" })
  check("a message with no UI to show it on is dropped without trouble", pcall(M.onUpdate, 0.1) and M.status().shown == 0)
end

do
  local M = load()
  installEarth()
  M.onExtensionLoaded()
  rawset(_G, "jsonDecode", function() error("boom") end)
  say("a", { type = "toast", title = "t", msg = "m" })
  check("a decoder that fails is swallowed", pcall(M.onUpdate, 0.1))
end

do
  resetGame()
  rawset(_G, "socket", { udp = function()
    return { settimeout = function() end, close = function() end, sendto = function() return 1 end,
             setsockname = function() error("not allowed") end, receivefrom = function() return nil, "timeout" end }
  end })
  local M = dofile(MOD)
  installEarth()
  M.onExtensionLoaded()
  M.onUpdate(1.5)
  local warned = false
  for _, l in ipairs(logs) do if l:find("could not bind", 1, true) then warned = true end end
  check("if the receiving side cannot be bound, that is logged and the positions still go out", warned and #ofType("geo") == 1)
end

-- ---- the message test, and the mod script -----------------------------------------------------------------------------------
do
  local M = load()
  check("the toast uses the channel BeamNG.Earth itself uses", M.testToast("hello") == true and toasts[1].name == "toastrMsg" and toasts[1].data.msg == "hello")
  rawset(_G, "guihooks", nil)
  check("a toast without the UI hooks is just false", M.testToast() == false)
end

do
  local unload
  rawset(_G, "setExtensionUnloadMode", function(name, mode) unload = { name, mode } end)
  dofile(SCRIPT)
  check("the mod script keeps the extension loaded across levels", unload and unload[1] == "thrashdash_trips" and unload[2] == "manual")
  -- the name must be the file's path with '/' written as '_' (the game maps it back; getting this wrong cost a drive once)
  local path = MOD:gsub("^mod/lua/ge/extensions/", ""):gsub("%.lua$", ""):gsub("/", "_")
  check("the extension name matches its file path", path == "thrashdash_trips", path)
end

print(string.format("%d passed, %d failed", passed, failed))
os.exit(failed == 0 and 0 or 1)

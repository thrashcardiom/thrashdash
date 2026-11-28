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
    gps = function() return { remaining = 1000, eta = 90, name = "x", waypoints = {}, routeVersion = 3 } end,
    gpsRoute = function()
      return { version = 3, dist = 1500, points = { { 1, 2 } }, steps = {
        { type = "depart", name = "Hutt Road", offset = 0, distance = 485, leg = 1, location = { lat = 1, lon = 2 }, duration = 70 },
        { type = "turn", modifier = "slight left", name = string.rep("x", 200), offset = 485, distance = 810 },
        { type = "roundabout", exit = 2, offset = 1295, distance = 31, name = "" },
        { type = "arrive", modifier = "left", offset = 1500, distance = 0 },
        { type = "broken" }, "not a step",
      } }
    end,
  }
  for k, v in pairs(overrides or {}) do api[k] = v end
  rawset(_G, "beamngEarth_api", api)
end

local function load()
  resetGame()
  local mod = dofile(MOD)
  return mod
end

local function say(text, table_, from)
  wire[text] = table_
  inbox[#inbox + 1] = { data = text, ip = from and from.ip, port = from and from.port }
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

-- game time, for lap timing: it stands still while the game is paused and runs slow in slow motion
do
  local M = load()
  installEarth()
  M.onExtensionLoaded()
  for _ = 1, 10 do M.onUpdate(0.1, 0.1) end -- ten live positions at normal speed
  for _ = 1, 10 do M.onUpdate(0.1, 0) end -- paused
  for _ = 1, 10 do M.onUpdate(0.1, 0.05) end -- half speed
  local live = ofType("geolive")
  check("every live position carries the game's clock as well as the real one", #live == 30 and type(live[1].st) == "number", #live)
  check("normal speed: game time keeps pace with real time", math.abs((live[10].st - live[1].st) - (live[10].rt - live[1].rt)) < 1e-9)
  check("paused: real time passes and game time does not", live[20].rt > live[10].rt and live[20].st == live[10].st)
  check("slow motion: game time runs at the game's own rate", math.abs((live[30].st - live[20].st) - 0.5) < 1e-9, live[30].st - live[20].st)
end

do
  local M = load()
  installEarth()
  M.onExtensionLoaded()
  for _ = 1, 10 do M.onUpdate(0.1) end -- (a caller that gives no game time: taken as real time)
  local live = ofType("geolive")
  check("without a game time it follows the real one", live[10].st == live[10].rt)
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

-- ---- the route for the dashboard: its own packet, every second ----------------------------------------------------------------
do
  local M = load()
  installEarth()
  M.onExtensionLoaded()
  for _ = 1, 12 do M.onUpdate(1) end
  local route = ofType("georoute")
  check("the route's progress goes to the dashboard every second", #route == 12, #route)
  check("it keeps what the route widget shows", route[1].gps and route[1].gps.remaining == 1000 and route[1].gps.eta == 90 and route[1].gps.name == "x" and route[1].gps.waypoints == nil)
  check("and the trip log's own packets are not made more frequent", #ofType("geoinfo") == 12)
end

-- ---- the planned route's line, for the maps ---------------------------------------------------------------------------------------
do
  local M = load()
  local many = {}
  for i = 1, 1203 do many[i] = { -41.3 + i * 1e-5, 174.8 + i * 1e-5 } end
  installEarth({
    gps = function() return { lat = -41.29, lon = 174.79, name = "Home", remaining = 1000, routeVersion = 4, waypoints = { { lat = -41.2, lon = 174.7, name = "Cafe", extra = 1 }, "bad" } } end,
    gpsRoute = function() return { version = 4, dist = 5000, points = many, steps = {} } end,
  })
  M.onExtensionLoaded()
  for _ = 1, 3 do M.onUpdate(1) end
  local paths = ofType("georoutepath")
  check("the route's line is sent once when a route appears", #paths == 1, #paths)
  local g = paths[1] or { points = {} }
  local same = #g.points == #many
  for i = 1, #many do same = same and g.points[i] ~= nil and g.points[i][1] == many[i][1] and g.points[i][2] == many[i][2] end
  check("a line that fits is sent just as Earth gave it, no point left out", same, #g.points)
  check("as [lat, lon] pairs", type(g.points[2][1]) == "number" and type(g.points[2][2]) == "number")
  check("with the destination and the stops still ahead", g.dest and g.dest.name == "Home" and g.dest.lat == -41.29 and #g.waypoints == 1 and g.waypoints[1].name == "Cafe" and g.waypoints[1].extra == nil)
  check("and its version", g.version == 4)
  for _ = 1, 20 do M.onUpdate(1) end
  check("and sent again every twenty seconds", #ofType("georoutepath") == 2, #ofType("georoutepath"))
end

do
  local M = load()
  installEarth({ gpsRoute = function() return { points = { { 1, 2 } }, steps = nil } end })
  M.onExtensionLoaded()
  for _ = 1, 3 do M.onUpdate(1) end
  check("a line of fewer than two points is not sent", #ofType("georoutepath") == 0)
  installEarth({ gpsRoute = function() return { points = { { 1, 2 }, { "x", 3 }, { 3, 4 } } } end })
end

-- A line too long to send whole is simplified by its shape: every point left out must still lie close to the line sent.
do
  local METRES = 111320
  local LAT = -41.3
  local ACROSS = METRES * math.cos(math.rad(LAT))
  local function at(x, y) return { LAT + y / METRES, 174.8 + x / ACROSS } end
  -- the farthest (m) any point of `line` is from `sent`, whose points are some of the line's own, in order
  local function worst(line, sent)
    local far, k = 0, 1
    for _, p in ipairs(line) do
      if k < #sent and p[1] == sent[k + 1][1] and p[2] == sent[k + 1][2] then
        k = k + 1
      else
        local a, b = sent[k], sent[math.min(k + 1, #sent)]
        local dx, dy = (b[2] - a[2]) * ACROSS, (b[1] - a[1]) * METRES
        local px, py = (p[2] - a[2]) * ACROSS, (p[1] - a[1]) * METRES
        local len2 = dx * dx + dy * dy
        local t = len2 > 0 and math.max(0, math.min(1, (px * dx + py * dy) / len2)) or 0
        far = math.max(far, math.sqrt((px - t * dx) ^ 2 + (py - t * dy) ^ 2))
      end
    end
    return far, k
  end
  local function sentFor(line)
    local M = load()
    installEarth({ gps = function() return { routeVersion = 7 } end, gpsRoute = function() return { points = line, dist = 1 } end })
    M.onExtensionLoaded()
    for _ = 1, 3 do M.onUpdate(1) end
    local p = ofType("georoutepath")[1]
    return p and p.points or {}, M
  end

  -- long straights with a tight bend now and then, as Earth gives a road: few points on the straights, many in the bends
  local road, x, y, h = { at(0, 0) }, 0, 0, 0
  for bend = 1, 220 do
    x, y = x + 600 * math.cos(h), y + 600 * math.sin(h)
    road[#road + 1] = at(x, y)
    local turn = (bend % 2 == 0 and 1 or -1) * math.rad(80)
    for _ = 1, 16 do
      h = h + turn / 16
      x, y = x + 4 * math.cos(h), y + 4 * math.sin(h)
      road[#road + 1] = at(x, y)
    end
  end
  local sent = sentFor(road)
  local far, reached = worst(road, sent)
  check("a long winding route is cut down to what one datagram holds", #road > 3000 and #sent <= 1500 and #sent >= 500, #road .. " -> " .. #sent)
  check("the points sent are Earth's own, in order, from first to last", reached == #sent and sent[1][1] == road[1][1] and sent[#sent][2] == road[#road][2], reached .. " of " .. #sent)
  check("and no bend is cut: every point left out is within 16 m of the line sent", far <= 16.1, far)

  -- the worst there can be: six thousand points that all matter
  local saw = {}
  for i = 1, 6000 do saw[i] = at(i * 30, (i % 2) * (20 + i % 7 * 15)) end
  local few = sentFor(saw)
  check("a line where every point matters still fits", #few >= 2 and #few <= 1500, #few)

  -- across the date line
  local over = {}
  for i = 1, 2000 do
    local lon = 179.9 + i * 1e-4
    over[i] = { -17 + math.sin(i / 20) * 1e-3, lon > 180 and lon - 360 or lon }
  end
  local across = sentFor(over)
  check("a route across the date line is thinned the same way", #across <= 1500 and #across > 100, #across)
end

-- ---- setting and clearing the route from ThrashDash --------------------------------------------------------------------------------
local RELAY = { ip = "127.0.0.1", port = 4568 }
local function acks() return ofType("georouteack") end

do
  local M = load()
  local calls = {}
  installEarth({
    gpsSet = function(...) calls[#calls + 1] = { "set", ... }; return true end,
    gpsSetRoute = function(points) calls[#calls + 1] = { "route", points }; return true end,
    gpsClear = function() calls[#calls + 1] = { "clear" } end,
  })
  M.onExtensionLoaded()
  say("r1", { type = "route", action = "set", stops = { { lat = -41.3, lon = 174.8, name = "A" }, { lat = -41.4, lon = 174.9, name = "B", evil = 1 } } }, RELAY)
  M.onUpdate(0.1)
  check("two stops set a route through them", calls[1] and calls[1][1] == "route" and #calls[1][2] == 2 and calls[1][2][2].name == "B" and calls[1][2][2].evil == nil)
  check("and the page is told it worked", acks()[1] and acks()[1].ok == true)
  say("r2", { type = "route", action = "set", stops = { { lat = -41.3, lon = 174.8, name = "Home" } } }, RELAY)
  M.onUpdate(0.1)
  check("one stop is a destination", calls[2] and calls[2][1] == "set" and calls[2][2] == -41.3 and calls[2][3] == 174.8 and calls[2][4] == "Home")
  say("r3", { type = "route", action = "clear" }, RELAY)
  M.onUpdate(0.1)
  check("clear takes the route away", calls[3] and calls[3][1] == "clear" and acks()[3].ok == true)
  check("each command is answered", #acks() == 3)
end

do
  local M = load()
  local calls = 0
  installEarth({ gpsSet = function() calls = calls + 1; return true end, gpsSetRoute = function() calls = calls + 1; return true end, gpsClear = function() calls = calls + 1 end })
  M.onExtensionLoaded()
  local twelve = {}
  for i = 1, 12 do twelve[i] = { lat = -41, lon = 174 } end
  for n, bad in ipairs({
    { type = "route", action = "set", stops = { { lat = 95, lon = 0 } } }, { type = "route", action = "set", stops = { { lat = 0, lon = 181 } } },
    { type = "route", action = "set", stops = { { lat = "x", lon = 0 } } }, { type = "route", action = "set", stops = twelve },
    { type = "route", action = "set", stops = {} }, { type = "route", action = "set", stops = "home" }, { type = "route", action = "set" },
    { type = "route", action = "dance", stops = { { lat = 1, lon = 1 } } }, { type = "route" },
  }) do
    say("bad" .. n, bad, RELAY)
  end
  for _ = 1, 3 do M.onUpdate(0.1) end -- (at most four messages are taken a frame)
  check("bad requests change nothing in the game", calls == 0, calls)
  check("and each is refused, saying so", #acks() == 9 and not acks()[1].ok and not acks()[9].ok, #acks())
  say("elsewhere", { type = "route", action = "clear" }, { ip = "127.0.0.1", port = 9999 })
  say("stranger", { type = "route", action = "clear" }, { ip = "192.168.1.5", port = 4568 })
  M.onUpdate(0.1)
  check("only the relay's own address is believed", calls == 0 and #acks() == 9)
end

do
  local M = load()
  installEarth({ gpsSetRoute = function() return false, "too far apart" end, gpsSet = function() error("boom") end })
  M.onExtensionLoaded()
  say("x1", { type = "route", action = "set", stops = { { lat = -41, lon = 174 }, { lat = -40, lon = 175 } } }, RELAY)
  say("x2", { type = "route", action = "set", stops = { { lat = -41, lon = 174 } } }, RELAY)
  M.onUpdate(0.1)
  check("a route BeamNG.Earth will not take is reported with its reason", acks()[1] and acks()[1].ok == false and acks()[1].why == "too far apart")
  check("and an error inside it never reaches the game", acks()[2] and acks()[2].ok == false and acks()[2].why:find("refused", 1, true) ~= nil)
end

do
  local M = load()
  installEarth()
  M.onExtensionLoaded()
  world.inWorld = false
  say("w", { type = "route", action = "set", stops = { { lat = -41, lon = 174 } } }, RELAY)
  M.onUpdate(0.1)
  check("with no BeamNG.Earth level loaded it says so", acks()[1] and acks()[1].ok == false and acks()[1].why:find("not loaded", 1, true) ~= nil)
end

-- ---- the route's steps, for "next turn" --------------------------------------------------------------------------------------------
do
  local M = load()
  installEarth()
  M.onExtensionLoaded()
  for _ = 1, 5 do M.onUpdate(1) end
  local steps = ofType("geosteps")
  check("the steps are sent once when a route appears", #steps == 1, #steps)
  local g = steps[1] or { steps = {} }
  check("with the route's version and length", g.version == 3 and g.dist == 1500)
  check("keeping only usable steps, and only what the widget reads", #g.steps == 4 and g.steps[1].type == "depart" and g.steps[1].name == "Hutt Road" and g.steps[1].offset == 0 and g.steps[1].leg == nil and g.steps[1].location == nil and g.steps[1].duration == nil)
  check("text is cut short", #g.steps[2].name == 60 and g.steps[2].modifier == "slight left")
  check("a roundabout carries its exit number", g.steps[3].exit == 2 and g.steps[3].name == "")
  check("the progress packets say which version of the route they are about", ofType("georoute")[1].gps.routeVersion == 3)
  for _ = 1, 20 do M.onUpdate(1) end
  check("the steps are sent again every twenty seconds (a relay started later would miss them)", #ofType("geosteps") == 2, #ofType("geosteps"))
end

do
  local M = load()
  local version = 1
  installEarth({ gps = function() return { remaining = 10, routeVersion = version } end })
  M.onExtensionLoaded()
  for _ = 1, 3 do M.onUpdate(1) end
  version = 2 -- the route was re-planned (a wrong turn)
  M.onUpdate(1)
  local steps = ofType("geosteps")
  check("a re-planned route's steps are sent at once", #steps == 2 and steps[2].version == 2, #steps)
end

do
  local M = load()
  installEarth({ gps = function() return nil end })
  M.onExtensionLoaded()
  for _ = 1, 25 do M.onUpdate(1) end
  check("with no route no steps are sent", #ofType("geosteps") == 0)
end

do
  local M = load()
  local on = false
  installEarth({ gps = function() return on and { remaining = 10, routeVersion = 7 } or nil end })
  M.onExtensionLoaded()
  for _ = 1, 3 do M.onUpdate(1) end
  on = true
  for _ = 1, 2 do M.onUpdate(1) end
  check("a route set later has its steps sent when it appears", #ofType("geosteps") == 1)
  on = false
  M.onUpdate(1)
  on = true
  M.onUpdate(1) -- cleared, then the same version set again
  check("and sent again if the route was cleared and set again", #ofType("geosteps") == 2, #ofType("geosteps"))
end

do
  local M = load()
  installEarth({ gpsRoute = function() return nil end })
  M.onExtensionLoaded()
  for _ = 1, 5 do M.onUpdate(1) end
  check("a route with no steps (an older BeamNG.Earth app) sends none, and does not ask again every second", #ofType("geosteps") == 0)
  local N = load()
  installEarth({ gpsRoute = function() error("boom") end })
  N.onExtensionLoaded()
  for _ = 1, 5 do N.onUpdate(1) end
  check("a failing route read never disturbs the game", #ofType("georoute") == 5, #ofType("georoute"))
end

do
  local M = load()
  installEarth({ gps = function() return nil end })
  M.onExtensionLoaded()
  for _ = 1, 3 do M.onUpdate(1) end
  local route = ofType("georoute")
  check("with no route the packet is still sent, saying so by carrying nothing", #route == 3 and route[1].gps == nil, #route)
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
  check("failures are counted (a position, a reading, the route and a live position each time, and the route's steps once)", M.status().failed == 21, M.status().failed)
  sendFails = false
  M.onUpdate(1)
  check("sending resumes by itself when the relay comes back", M.status().sent == 4, M.status().sent)
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
  local unload = {}
  rawset(_G, "setExtensionUnloadMode", function(name, mode) unload[name] = mode end) -- (every extension the script names, not only the last)
  dofile(SCRIPT)
  check("the mod script keeps the extension loaded across levels", unload["thrashdash_trips"] == "manual")
  -- the name must be the file's path with '/' written as '_' (the game maps it back; getting this wrong cost a drive once)
  local path = MOD:gsub("^mod/lua/ge/extensions/", ""):gsub("%.lua$", ""):gsub("/", "_")
  check("the extension name matches its file path", path == "thrashdash_trips", path)
end

-- ---- the stops still ahead, for the dashboard's "next stop" ------------------------------------------------------------------------
local function progressFor(gps)
  local M = load()
  installEarth({ gps = function() return gps end })
  M.onExtensionLoaded()
  M.onUpdate(1)
  local p = ofType("georoute")[1]
  return p and p.gps or {}
end

do
  local legs = { { 1000, 60 }, { 2000, 120 }, { 3000, 180 } }
  local sent = progressFor({ remaining = 5500, routeVersion = 2, legs = legs, waypoints = { { lat = 1, lon = 2, name = string.rep("n", 90) }, { lat = 3, lon = 4 } } })
  check("the stops ahead go with the progress: how many, the first one's name, and the legs", sent.stops and sent.stops.ahead == 2 and #sent.stops.name == 60 and #sent.stops.legs == 3 and sent.stops.legs[2][1] == 2000 and sent.stops.legs[2][2] == 120)
  check("and nothing else about them", sent.waypoints == nil and sent.legs == nil and sent.stops.lat == nil)
  check("with no stop ahead nothing is said", progressFor({ remaining = 10, routeVersion = 2, legs = legs, waypoints = {} }).stops == nil)
  check("with no legs planned yet nothing is said", progressFor({ remaining = 10, routeVersion = 2, waypoints = { { lat = 1, lon = 2 } } }).stops == nil)
  check("a leg that is not two numbers spoils the lot", progressFor({ remaining = 10, routeVersion = 2, legs = { { 1, 2 }, { "x", 3 } }, waypoints = { { lat = 1, lon = 2 } } }).stops == nil)
end

-- ---- filling stations and chargers nearby ------------------------------------------------------------------------------------------
local function fuel() return ofType("geofuel") end
local function station(name, category, extra)
  local p = { name = name, category = category or "gas_station", lat = -41.2, lon = 174.8, open = "open", id = "x", confidence = 1 }
  for k, v in pairs(extra or {}) do p[k] = v end
  return p
end
local function search(overrides, id)
  local M = load()
  installEarth(overrides)
  M.onExtensionLoaded()
  say("f", { type = "places", action = "fuel", id = id or 41 }, RELAY)
  M.onUpdate(0.1)
  return M
end
local function has(...) local set = {}; for _, f in ipairs({ ... }) do set[f] = true end; return function(name) return set[name] == true end end

do
  local asks = {}
  search({ has = has("places", "cachedPlaces"), places = function(lat, lon, opts, answer)
    asks[#asks + 1] = { lat = lat, lon = lon, opts = opts }
    answer({ places = { station("Z Petone"), station("ChargeNet Hutt", "ev_charging_station"), station("Tesla Supercharger"), station("Gone", nil, { open = "permanently_closed" }),
                        station("Shut", nil, { open = "temporarily_closed" }), station(""), "junk", station("Nowhere", nil, { lat = "x" }) } })
    return 7
  end })
  local r = fuel()[1] or { places = {} }
  check("fuel is searched for round the car, three kilometres first", #asks == 1 and asks[1].lat == -41.2258 and asks[1].opts.radius == 3000 and asks[1].opts.categories[1] == "group:fuel" and asks[1].opts.limit == 15 and not asks[1].opts.cachedOnly)
  check("the open, named places come back, and nothing about the closed ones", #r.places == 3 and r.places[1].n == "Z Petone", #r.places)
  check("chargers are told apart, by category or by name", r.places[1].kind == "fuel" and r.places[2].kind == "charger" and r.places[3].kind == "charger")
  check("with the number of the search, where it looked from, and that it is finished", r.id == 41 and r.lat == -41.2258 and r.done == true and r.why == nil)
end

do
  local asks = {}
  search({ has = has("places", "cachedPlaces"), places = function(_, _, opts, answer)
    asks[#asks + 1] = opts
    if opts.cachedOnly then answer({ places = { station("Far one"), station("Farther one") } }) else answer({ places = { station("Only near") } }) end
    return 1
  end })
  local r = fuel()
  check("too few near: it looks as far as 25 km through the map it has", #asks == 2 and asks[2].radius == 25000 and asks[2].cachedOnly == true and asks[2].categories[1] == "group:fuel")
  check("and gives the fuller answer", #r == 1 and #r[1].places == 2 and r[1].done == true)
end

do
  local late
  local M = search({ has = has("places"), places = function(_, _, _, answer) late = answer; answer({ places = { station("From disk") }, pending = true }); return 1 end })
  check("while more of the map is being fetched the answer says it is not finished", #fuel() == 1 and fuel()[1].done == false and #fuel()[1].places == 1)
  late({ places = { station("From disk"), station("Fetched") } })
  check("and the second answer finishes it (an older BeamNG.Earth searches no wider)", #fuel() == 2 and fuel()[2].done == true and #fuel()[2].places == 2)
  say("g", { type = "places", action = "fuel", id = 42 }, RELAY)
  local first = late
  M.onUpdate(0.1)
  local before = #fuel()
  first({ places = { station("Stale") } })
  check("an answer to an older search is dropped", #fuel() == before, #fuel() - before)
end

do
  search({ has = has("places", "cachedPlaces"), places = function(_, _, opts, answer) if opts.cachedOnly then answer({ places = {} }) else answer({ places = {}, error = "no map data" }) end return 1 end })
  check("nothing found and a reason: the reason is passed on", fuel()[1] and #fuel()[1].places == 0 and fuel()[1].why == "no map data" and fuel()[1].done == true)
  search({ has = has("places"), places = function() return nil, "the app is not connected" end })
  check("a search BeamNG.Earth will not take says why", fuel()[1] and fuel()[1].why == "the app is not connected")
  search({ has = has() })
  check("a BeamNG.Earth with no place search says so", fuel()[1] and fuel()[1].why:find("cannot search") ~= nil)
  search({ has = has("places"), places = function() error("boom") end })
  check("a search that raises is answered, not left hanging", fuel()[1] and fuel()[1].done == true and fuel()[1].why ~= nil)
  local M = load()
  installEarth({ has = has("places"), places = function() error("must not be asked") end })
  M.onExtensionLoaded()
  say("h", { type = "places", action = "fuel", id = "x" }, RELAY)
  say("i", { type = "places", action = "other", id = 1 }, RELAY)
  say("j", { type = "places", action = "fuel", id = 1 }, { ip = "192.168.1.5", port = 4568 })
  M.onUpdate(0.1)
  check("only a proper request from the relay is acted on", #fuel() == 0, #fuel())
end

print(string.format("%d passed, %d failed", passed, failed))
os.exit(failed == 0 and 0 or 1)

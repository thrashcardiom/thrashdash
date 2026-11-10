-- ThrashDash telemetry sender (PHASE 0 SPIKE).
--
-- Why this lives in lua/vehicle/extensions/auto/: the game calls
-- extensions.loadModulesInDirectory("lua/vehicle/extensions/auto") for every vehicle once its
-- jbeam is parsed (see game lua/vehicle/main.lua), so a mod only has to drop a file here -
-- no game-engine (GE) extension, no spawn/switch hooks needed.
--
-- Why it sends from inside the vehicle VM: that is where `electrics.values` lives, and the game's own
-- OutGauge/MotionSim protocols (lua/vehicle/protocols.lua) already send UDP from here using the
-- global `socket` (LuaSocket) - so we know it is available and allowed.
--
-- Every vehicle loads this file, but only the one a player is seated in may transmit
-- (playerInfo.firstPlayerSeated, same guard the game's protocols use) - otherwise AI/traffic cars
-- would flood the port.
--
-- Two kinds of data are sent:
--   "frame"  30x/s: the whole `electrics` table (speed, rpm, temperatures, lights...).
--   "slow"    4x/s: things that are NOT in electrics and change slowly - tyres (temperature, pressure, flat), body damage
--             (six zones plus beam statistics) and the game's component states (engine overheating, oil critical,
--             hydrolocked, ...). Kept in a separate packet so the 30 Hz frames stay small.
--
-- Fixed address/port/rate; nothing configurable yet.
-- Everything risky is pcall-wrapped: a telemetry bug must never break the player's game, and a car that lacks one of
-- these systems (or a game update that renames one) simply sends less, never errors.

local M = {}

local PROTOCOL_VERSION = 1
local ADDRESS = "127.0.0.1"
local PORT = 4568
local RATE_HZ = 30
local CATALOG_EVERY_S = 2
local SLOW_EVERY_S = 0.25
local ATMOSPHERE_PA = 101325 -- the game reports tyre pressure as ABSOLUTE pascals (see wheels.lua: startingPressure adds 101325)
local MAX_WHEELS = 12
local MAX_STATES = 400

local logTag = "thrashdash"

local udp
local timer
local nextSend = 0
local nextCatalog = 0
local nextSlow = 0
local latestStates -- the damage tracker's live table (group -> name -> value), handed to us by its update callback
local frameCounter = 0
local failures = 0
local MAX_FAILURES = 20 -- stop trying after this many consecutive errors rather than spamming the log

local function send(payload)
  udp:sendto(payload, ADDRESS, PORT)
end

-- A catalog lists every channel currently present in electrics with its Lua type. The relay/editor
-- uses it to offer "what can I put on a gauge" without hard-coding names.
local function buildCatalog()
  local channels = {}
  for key, value in pairs(electrics.values) do
    channels[tostring(key)] = type(value)
  end
  return {
    v = PROTOCOL_VERSION,
    type = "catalog",
    vehicle = vehiclePath,
    channels = channels,
  }
end

local function buildFrame()
  frameCounter = frameCounter + 1
  return {
    v = PROTOCOL_VERSION,
    type = "frame",
    n = frameCounter,
    vehicle = vehiclePath,
    electrics = electrics.values,
  }
end


-- ---- slow data: tyres, damage, component states -----------------------------------------------------------------

local function finite(x)
  return type(x) == "number" and x == x and x ~= math.huge and x ~= -math.huge
end

local function num(x)
  if finite(x) then return x end
  return nil
end

-- Run fn, returning its result, or nil if it raised: one missing API must not cost us the rest.
local function try(fn, ...)
  local ok, result = pcall(fn, ...)
  if ok then return result end
  return nil
end

-- The game reports tyre temperatures in kelvin (its own tyre debug overlay subtracts 273.15; confirmed from a capture
-- showing 290.45 on a cold tyre next to brake temperatures of 17 deg C).
local KELVIN = 273.15

local function celsius(kelvin)
  if kelvin then return kelvin - KELVIN end
  return nil
end

-- Per wheel: surface and core temperature (deg C), gauge pressure (kPa), flat/punctured flags, load and slip.
local function collectTires()
  local rotators = wheels and wheels.wheelRotators
  if type(rotators) ~= "table" then return nil end
  local groups = v and v.data and v.data.pressureGroups
  local out, count = {}, 0
  for _, wd in pairs(rotators) do
    local name = type(wd) == "table" and wd.name
    if type(name) == "string" and name ~= "" and count < MAX_WHEELS then
      local tire = {}
      if wd.wheelID ~= nil then
        tire.temp = celsius(num(try(function() return obj:getWheelAvgTemperature(wd.wheelID) end)))
        tire.coreTemp = celsius(num(try(function() return obj:getWheelCoreTemperature(wd.wheelID) end)))
      end
      local group = wd.pressureGroup and type(groups) == "table" and groups[wd.pressureGroup]
      if group ~= nil and group ~= false then
        local absolute = num(try(function() return obj:getGroupPressure(group) end))
        if absolute then tire.pressure = (absolute - ATMOSPHERE_PA) / 1000 end
      end
      tire.deflated = wd.isTireDeflated == true
      tire.punctured = wd.isPunctured == true
      tire.load = num(wd.downForce)
      tire.slip = num(wd.lastSlip)
      out[name] = tire
      count = count + 1
    end
  end
  if count == 0 then return nil end
  return out
end

-- Body damage: six zones (FL FR ML MR RL RR, each 0..1) from the game's body-zone tracker, plus beam statistics.
local function collectDamage()
  local d = {}
  if beamstate then d.total = num(beamstate.damage) end
  local zones = beamstate and type(beamstate.getBodyZoneDamage) == "function" and try(beamstate.getBodyZoneDamage)
  if type(zones) == "table" then
    local z = {}
    for key, value in pairs(zones) do
      local n = num(value)
      if n then z[tostring(key)] = n end
    end
    if next(z) then d.zone = z end
  end
  local stats = try(function() return obj:calcBeamStats() end)
  if type(stats) == "table" and finite(stats.beam_count) and stats.beam_count > 0 then
    d.beamsTotal = stats.beam_count
    d.beamsBroken = num(stats.beams_broken)
    d.beamsDeformed = num(stats.beams_deformed)
    if d.beamsBroken then d.brokenPct = d.beamsBroken / stats.beam_count * 100 end
    if d.beamsDeformed then d.deformedPct = d.beamsDeformed / stats.beam_count * 100 end
  end
  if next(d) == nil then return nil end
  return d
end

-- Component states the game tracks (engine, wheels, powertrain, gearbox, fuel tank...). Only booleans and numbers.
local function collectStates()
  if type(latestStates) ~= "table" then return nil end
  local out, count = {}, 0
  for group, entries in pairs(latestStates) do
    if type(entries) == "table" then
      local g = {}
      for name, value in pairs(entries) do
        local kind = type(value)
        if count < MAX_STATES and (kind == "boolean" or (kind == "number" and finite(value))) then
          g[tostring(name)] = value
          count = count + 1
        end
      end
      if next(g) then out[tostring(group)] = g end
    end
  end
  if next(out) == nil then return nil end
  return out
end

-- What kind of energy the car carries. electrics.fuelVolume is litres for a petrol or diesel tank but kWh for a battery
-- (and a meaningless sum for a hybrid), and nothing in electrics says which, so the trip computer needs this.
local NOT_FUEL = { electricEnergy = true, n2o = true, air = true }

local function collectEnergy()
  local storages = energyStorage and type(energyStorage.getStorages) == "function" and energyStorage.getStorages()
  if type(storages) ~= "table" then return nil end
  local electric, liquid = false, false
  for _, storage in pairs(storages) do
    local kind = type(storage) == "table" and storage.energyType
    if kind == "electricEnergy" then
      electric = true
    elseif type(kind) == "string" and not NOT_FUEL[kind] then
      liquid = true
    end
  end
  if not (electric or liquid) then return nil end
  return { electric = electric, liquid = liquid }
end

local function buildSlow()
  local tire = try(collectTires)
  local damage = try(collectDamage)
  local state = try(collectStates)
  local energy = try(collectEnergy)
  if not (tire or damage or state or energy) then return nil end
  return { v = PROTOCOL_VERSION, type = "slow", vehicle = vehiclePath, tire = tire, damage = damage, state = state, energy = energy }
end

-- The damage tracker calls this whenever a state changes (about 3x/s at most), passing its live table.
local function registerStateSource()
  local tracker = damageTracker
  if type(tracker) == "table" and type(tracker.registerDamageUpdateCallback) == "function" then
    tracker.registerDamageUpdateCallback(function(data) latestStates = data end)
  end
end

local function onReset()
  latestStates = nil -- a reset (repair) replaces the tracker's table, so our reference is stale
  nextSlow = 0 -- and say so promptly
end

local function tick()
  local now = timer:stop() * 0.001 -- HighPerfTimer returns ms; wall clock, so slow-motion does not change the rate
  if now < nextSend then return end
  nextSend = now + 1 / RATE_HZ

  if not electrics.values.watertemp then return end -- vehicle not fully initialised yet (same check as game OutGauge)

  if now >= nextCatalog then
    nextCatalog = now + CATALOG_EVERY_S
    send(jsonEncode(buildCatalog()))
  end
  send(jsonEncode(buildFrame()))

  if now >= nextSlow then
    nextSlow = now + SLOW_EVERY_S
    local slow = buildSlow()
    if slow then send(jsonEncode(slow)) end
  end
end

local function updateGFX(dt)
  if not udp then return end
  if not playerInfo.firstPlayerSeated then return end
  local ok, err = pcall(tick)
  if ok then
    failures = 0
  else
    failures = failures + 1
    if failures == 1 then log("W", logTag, "send failed: " .. tostring(err)) end
    if failures >= MAX_FAILURES then
      log("E", logTag, "too many consecutive failures, disabling telemetry for this vehicle")
      M.updateGFX = nop
    end
  end
end

local function onExtensionLoaded()
  local ok, err = pcall(function()
    udp = socket.udp()
    udp:settimeout(0)
    timer = HighPerfTimer()
    timer:reset()
    registerStateSource()
  end)
  if not ok then
    log("E", logTag, "could not create UDP socket: " .. tostring(err))
    udp = nil
    return
  end
  log("I", logTag, string.format("telemetry ready for %s -> %s:%d @ %d Hz", tostring(vehiclePath), ADDRESS, PORT, RATE_HZ))
end

local function onExtensionUnloaded()
  if udp then
    udp:close()
    udp = nil
  end
end

M.onReset = onReset
M.onExtensionLoaded = onExtensionLoaded
M.onExtensionUnloaded = onExtensionUnloaded
M.updateGFX = updateGFX

-- exposed so tests/lua can exercise the collectors against fake game objects
M._collectTires = collectTires
M._collectDamage = collectDamage
M._collectStates = collectStates
M._collectEnergy = collectEnergy
M._buildSlow = buildSlow

return M

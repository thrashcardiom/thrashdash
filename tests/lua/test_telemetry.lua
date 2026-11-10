-- Tests for the BeamNG telemetry mod, run with LuaJIT against FAKE game objects (the real game is not needed):
--     luajit tests/lua/test_telemetry.lua
-- They load the real mod file, so they check its logic: conversions, defensive behaviour when a car lacks a system or a
-- game call fails, how often packets go out, and what happens on a reset.

local MOD = "mod/lua/vehicle/extensions/auto/thrashdash_telemetry.lua"

local passed, failed = 0, 0
local function check(name, cond, detail)
  if cond then passed = passed + 1
  else failed = failed + 1; print("FAIL  " .. name .. (detail and ("   [" .. tostring(detail) .. "]") or "")) end
end
local function near(a, b) return type(a) == "number" and math.abs(a - b) < 1e-6 end

-- a tiny deterministic JSON encoder standing in for the game's jsonEncode (sorted keys, so tests can compare text)
local function encode(v)
  local t = type(v)
  if t == "table" then
    local keys = {}
    for k in pairs(v) do keys[#keys + 1] = tostring(k) end
    table.sort(keys)
    local parts = {}
    for _, k in ipairs(keys) do parts[#parts + 1] = string.format("%q:%s", k, encode(v[k] ~= nil and v[k] or v[tonumber(k)])) end
    return "{" .. table.concat(parts, ",") .. "}"
  elseif t == "string" then return string.format("%q", v)
  else return tostring(v) end
end

local sent, nowMs

local function resetWorld()
  sent = {}
  nowMs = 0
  _G.nop = function() end
  _G.log = function() end
  _G.vehiclePath = "/vehicles/testcar/"
  _G.electrics = { values = { watertemp = 85, rpm = 3000 } }
  _G.playerInfo = { firstPlayerSeated = true }
  _G.jsonEncode = encode
  _G.socket = { udp = function() return { settimeout = function() end, close = function() end, sendto = function(_, data) sent[#sent + 1] = data end } end }
  _G.HighPerfTimer = function() return { reset = function() end, stop = function() return nowMs end } end
  _G.wheels, _G.v, _G.obj, _G.beamstate, _G.damageTracker, _G.energyStorage = nil, nil, nil, nil, nil, nil
end

local function load()
  local chunk = assert(loadfile(MOD))
  return chunk()
end

local function countType(kind)
  local n = 0
  for _, s in ipairs(sent) do if s:find('"type":"' .. kind .. '"', 1, true) then n = n + 1 end end
  return n
end

-- ============ tyres ============
resetWorld()
_G.wheels = { wheelRotators = {
  [0] = { name = "FL", wheelID = 0, pressureGroup = "front", isTireDeflated = false, isPunctured = false, downForce = 3500.5, lastSlip = 0.25 },
  [1] = { name = "FR", wheelID = 1, pressureGroup = "front", isTireDeflated = true, isPunctured = true, downForce = 3400 },
  [2] = { name = "RL", wheelID = 2 }, -- no pressure group (e.g. a trailer wheel)
} }
_G.v = { data = { pressureGroups = { front = "G1" } } }
_G.obj = {
  getWheelAvgTemperature = function(_, id) return 273.15 + 40 + id * 10 end, -- the game reports kelvin
  getWheelCoreTemperature = function(_, id) return 273.15 + 50 + id * 10 end,
  getGroupPressure = function(_, group) return 101325 + 220000 end, -- 220 kPa gauge
}
local M = load()
local tires = M._collectTires()
check("tyres: every wheel by name", tires and tires.FL and tires.FR and tires.RL)
local near = function(a, b) return a and math.abs(a - b) < 1e-9 end
check("tyres: kelvin from the game is sent as degrees C, per wheel", near(tires.FL.temp, 40) and near(tires.FR.temp, 50) and near(tires.FL.coreTemp, 50) and near(tires.RL.coreTemp, 70))
check("tyres: pressure is converted from ABSOLUTE pascals to gauge kPa", near(tires.FL.pressure, 220), tires.FL.pressure)
check("tyres: a wheel without a pressure group has no pressure (not 0, not an error)", tires.RL.pressure == nil)
check("tyres: flat and punctured flags", tires.FR.deflated == true and tires.FR.punctured == true and tires.FL.deflated == false)
check("tyres: load and slip carried through, missing ones are nil", tires.FL.load == 3500.5 and tires.FL.slip == 0.25 and tires.FR.slip == nil)

_G.obj.getGroupPressure = function() error("engine exploded") end
local broken = M._collectTires()
check("tyres: a failing pressure call loses only the pressure", broken and near(broken.FL.temp, 40) and broken.FL.pressure == nil)

_G.obj.getWheelAvgTemperature = function() return 0 / 0 end -- NaN
_G.obj.getWheelCoreTemperature = function() return math.huge end
local junk = M._collectTires()
check("tyres: NaN and infinity are dropped, not sent", junk.FL.temp == nil and junk.FL.coreTemp == nil)

_G.wheels = nil
check("tyres: a vehicle with no wheel system yields nothing (no error)", M._collectTires() == nil)
_G.wheels = { wheelRotators = {} }
check("tyres: no wheels yields nothing", M._collectTires() == nil)
_G.wheels = { wheelRotators = { { name = "", wheelID = 0 }, { wheelID = 1 }, "junk" } }
check("tyres: nameless entries are ignored", M._collectTires() == nil)
local many = {}
for i = 0, 30 do many[i] = { name = "W" .. i, wheelID = i } end
_G.wheels = { wheelRotators = many }
local n = 0
for _ in pairs(M._collectTires()) do n = n + 1 end
check("tyres: the number of wheels is capped", n == 12, n)

-- ============ damage ============
resetWorld()
M = load()
_G.beamstate = { damage = 1234.5, getBodyZoneDamage = function() return { FL = 0.5, FR = 0, ML = 0.25, MR = 0, RL = 0, RR = 1 } end }
_G.obj = { calcBeamStats = function() return { beam_count = 2000, beams_broken = 20, beams_deformed = 100 } end }
local d = M._collectDamage()
check("damage: total", d.total == 1234.5)
check("damage: six zones", d.zone and d.zone.FL == 0.5 and d.zone.RR == 1 and d.zone.MR == 0)
check("damage: beam statistics and percentages", d.beamsTotal == 2000 and d.beamsBroken == 20 and near(d.brokenPct, 1) and near(d.deformedPct, 5))

_G.beamstate = { damage = 0 }
d = M._collectDamage()
check("damage: no zone tracker still gives the rest", d and d.zone == nil and d.total == 0 and d.beamsTotal == 2000)

_G.obj = { calcBeamStats = function() error("nope") end }
d = M._collectDamage()
check("damage: failing beam statistics lose only the statistics", d and d.total == 0 and d.beamsTotal == nil)

_G.obj = { calcBeamStats = function() return { beam_count = 0 } end }
check("damage: zero beams does not divide by zero", M._collectDamage().brokenPct == nil)

_G.beamstate = { damage = 0 / 0, getBodyZoneDamage = function() return { FL = math.huge, FR = 0.1 } end }
_G.obj = {}
d = M._collectDamage()
check("damage: non-finite values are dropped", d and d.total == nil and d.zone and d.zone.FL == nil and d.zone.FR == 0.1)

_G.beamstate, _G.obj = nil, nil
check("damage: nothing available yields nothing", M._collectDamage() == nil)

-- ============ component states ============
resetWorld()
local registered
_G.damageTracker = { registerDamageUpdateCallback = function(cb) registered = cb end }
M = load()
M.onExtensionLoaded()
check("states: the mod subscribes to the damage tracker", type(registered) == "function")
check("states: nothing before the first update", M._collectStates() == nil)
registered({ engine = { oilOverheating = true, coolantOverheating = false, rpmDanger = 0.5 }, wheels = { brakeFL = true }, junk = "text", body = { part = { nested = 1 } } })
local s = M._collectStates()
check("states: booleans and numbers by group", s.engine.oilOverheating == true and s.engine.coolantOverheating == false and s.engine.rpmDanger == 0.5 and s.wheels.brakeFL == true)
check("states: strings, tables and non-table groups are skipped", s.junk == nil and (s.body == nil))
local big = { engine = {} }
for i = 1, 1000 do big.engine["flag" .. i] = true end
registered(big)
local count = 0
for _ in pairs(M._collectStates().engine) do count = count + 1 end
check("states: the number of states is capped", count == 400, count)
M.onReset()
check("states: a reset forgets the old (stale) table", M._collectStates() == nil)

resetWorld()
M = load()
_G.damageTracker = nil
M.onExtensionLoaded()
check("states: a car with no damage tracker loads fine", M._collectStates() == nil)

-- ============ packets over time ============
resetWorld()
_G.damageTracker = { registerDamageUpdateCallback = function() end }
_G.beamstate = { damage = 5, getBodyZoneDamage = function() return { FL = 0.1 } end }
_G.obj = { calcBeamStats = function() return { beam_count = 100, beams_broken = 1, beams_deformed = 2 } end }
M = load()
M.onExtensionLoaded()
nowMs = 0; M.updateGFX(0.016)
check("packets: the first update sends a catalog, a frame and a slow packet", countType("catalog") == 1 and countType("frame") == 1 and countType("slow") == 1)
local slowText
for _, p in ipairs(sent) do if p:find('"type":"slow"', 1, true) then slowText = p end end
check("packets: the slow packet carries the vehicle and the damage section", slowText:find("/vehicles/testcar/", 1, true) and slowText:find('"damage"', 1, true))
nowMs = 20; M.updateGFX(0.016)
check("packets: too soon for another frame sends nothing", countType("frame") == 1)
nowMs = 40; M.updateGFX(0.016)
check("packets: the next frame comes at 30 Hz but the slow packet does not repeat yet", countType("frame") == 2 and countType("slow") == 1)
for t = 80, 300, 40 do nowMs = t; M.updateGFX(0.016) end
check("packets: slow data goes out about 4 times a second", countType("slow") == 2, countType("slow"))
M.onReset()
nowMs = 340; M.updateGFX(0.016)
check("packets: a reset makes the next slow packet go out promptly", countType("slow") == 3)

playerInfo.firstPlayerSeated = false
local before = #sent
nowMs = 1000; M.updateGFX(0.016)
check("packets: nothing is sent for a vehicle nobody is sitting in", #sent == before)

-- a car with none of the optional systems still works, and sends no slow packets at all
resetWorld()
M = load()
M.onExtensionLoaded()
nowMs = 0; M.updateGFX(0.016)
check("a bare car: frames still flow", countType("frame") == 1)
check("a bare car: no empty slow packets", countType("slow") == 0)
nowMs = 500; M.updateGFX(0.016)
check("a bare car: still no slow packets, and no errors", countType("slow") == 0 and countType("frame") == 2)

-- energy kind: the trip computer needs to know litres from kWh
resetWorld()
M = load()
local function storages(...)
  local list = {}
  for i, kind in ipairs({ ... }) do list["tank" .. i] = { energyType = kind } end
  _G.energyStorage = { getStorages = function() return list end }
end
storages("gasoline")
local e = M._collectEnergy()
check("energy: a petrol car is liquid, not electric", e and e.liquid == true and e.electric == false)
storages("diesel")
check("energy: diesel is liquid too", M._collectEnergy().liquid == true)
storages("electricEnergy")
e = M._collectEnergy()
check("energy: an electric car is electric, not liquid", e and e.electric == true and e.liquid == false)
storages("gasoline", "electricEnergy")
e = M._collectEnergy()
check("energy: a hybrid is both", e and e.electric == true and e.liquid == true)
storages("n2o", "air")
check("energy: nitrous and air tanks do not count as fuel", M._collectEnergy() == nil)
_G.energyStorage = nil
check("energy: no energy system gives nothing, without an error", M._collectEnergy() == nil)
_G.energyStorage = { getStorages = function() error("boom") end }
check("energy: a failing call is contained by buildSlow", pcall(M._buildSlow))

-- an exploding collector must not stop the 30 Hz frames
resetWorld()
_G.wheels = setmetatable({}, { __index = function() error("wheels exploded") end })
_G.beamstate = setmetatable({}, { __index = function() error("beamstate exploded") end })
M = load()
M.onExtensionLoaded()
local ok = pcall(function() nowMs = 0; M.updateGFX(0.016); nowMs = 40; M.updateGFX(0.016) end)
check("exploding game objects never raise into the game", ok)
check("...and the frames keep flowing", countType("frame") == 2)

print(string.format("%d passed, %d failed", passed, failed))
os.exit(failed == 0 and 0 or 1)

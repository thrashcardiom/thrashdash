// Friendly names, grouping and unit hints for telemetry channels. Pure functions (unit-tested).
//
// The game sends 120-150 raw `electrics` names per car, a third of which are useless on a dashboard
// (door couplers, sun visors, filament states...). The picker shows the curated set first, then
// whatever else THIS car reports, and hides the noise in a collapsed group - never removed, because
// somebody will want `hoodLatchCoupler_attached` for a "bonnet open" light.

/** channel -> [friendly label, suggested unit kind]. Kind is what a widget's `kind` property should become. */
const KNOWN = {
  rpm: ["Engine RPM", "none"],
  maxrpm: ["Engine max RPM", "none"],
  idlerpm: ["Idle RPM", "none"],
  wheelspeed: ["Speed (from wheels)", "speed"],
  airspeed: ["Speed (air)", "speed"],
  gear: ["Gear", "none"],
  gearIndex: ["Gear number", "none"],
  throttle: ["Throttle pedal", "fraction"],
  brake: ["Brake pedal", "fraction"],
  clutch: ["Clutch pedal", "fraction"],
  steering: ["Steering", "none"],
  fuel: ["Fuel level", "fraction"],
  fuelVolume: ["Fuel volume (litres) or battery energy (kWh)", "none"],
  fuelCapacity: ["Fuel capacity (litres)", "none"],
  watertemp: ["Coolant temperature", "temp"],
  oiltemp: ["Oil temperature", "temp"],
  turboBoost: ["Turbo boost", "none"],
  boost: ["Boost", "none"],
  engineLoad: ["Engine load", "fraction"],
  odometer: ["Odometer", "distance"],
  trip: ["Trip meter", "distance"],
  altitude: ["Altitude (m)", "none"],
  parkingbrake: ["Parking brake", "none"],
  signal_L: ["Left indicator", "none"],
  signal_R: ["Right indicator", "none"],
  hazard: ["Hazard lights", "none"],
  highbeam: ["High beam", "none"],
  lowbeam: ["Low beam", "none"],
  fog: ["Fog lights", "none"],
  checkengine: ["Check engine", "none"],
  lowfuel: ["Low fuel warning", "none"],
  oil: ["Oil pressure warning", "none"],
  absActive: ["ABS active", "none"],
  escActive: ["ESC active", "none"],
  tcsActive: ["Traction control active", "none"],
  esc: ["ESC (switched on)", "none"],
  tcs: ["Traction control (switched on)", "none"],
  engineRunning: ["Engine running", "none"],
  ignition: ["Ignition", "none"],
  isShifting: ["Shifting", "none"],
  horn: ["Horn", "none"],
  radiatorFanSpin: ["Radiator fan", "none"],
};

const WHEEL_THERMAL = /^wheelThermals\.(FL|FR|RL|RR)\.(brakeCoreTemperature|brakeSurfaceTemperature|brakeThermalEfficiency)$/;
const WHEEL_THERMAL_NAME = { brakeCoreTemperature: "Brake core temp", brakeSurfaceTemperature: "Brake surface temp", brakeThermalEfficiency: "Brake efficiency" };

/** Names that are almost never wanted on a dashboard. */
const MINOR = /(_filament$|[cC]oupler|^sunvisor|_wigwag_|^dualAxis|^hPatternAxis|^nop$|_movement$|^brakeGlow_|^acc[XYZ]Smooth|^electricalLoadCoef$|^lowhigh|^dse|^freezeState$)/;

// ---- the slow data the mod sends separately: tyres, damage, component states ----------------------------------------
const TIRE = /^tire\.([A-Za-z0-9_]+)\.(temp|coreTemp|pressure|deflated|punctured|load|slip)$/;
const TIRE_FIELD = { temp: "Tyre temperature", coreTemp: "Tyre core temperature", pressure: "Tyre pressure", deflated: "Tyre flat", punctured: "Tyre punctured", load: "Tyre load", slip: "Tyre slip" };
const ZONE = /^damage\.zone\.(FL|FR|ML|MR|RL|RR)$/;
const ZONE_NAME = { FL: "front left", FR: "front right", ML: "middle left", MR: "middle right", RL: "rear left", RR: "rear right" };
const DAMAGE_NAME = {
  "damage.total": "Damage (total)", "damage.brokenPct": "Broken beams (%)", "damage.deformedPct": "Deformed beams (%)",
  "damage.beamsTotal": "Beams (total)", "damage.beamsBroken": "Beams broken", "damage.beamsDeformed": "Beams deformed",
};
const STATE = /^state\.([A-Za-z0-9_]+)\.([A-Za-z0-9_]+)$/;
const STATE_GROUP = { engine: "Engine", wheels: "Wheels", powertrain: "Powertrain", gearbox: "Gearbox", body: "Body", energyStorage: "Fuel/energy", pressureTank: "Pressure tank", drivingDynamics: "Driving dynamics" };

// ---- the trip computer's channels (kept by the relay): trip.A.distance ... ----------------------------------------------
const TRIP = /^trip\.([AB])\.(distance|seconds|avgSpeed|maxSpeed|energyUsed|percentUsed|consumption|range)$/;
const TRIP_FIELD = {
  distance: ["distance", "distance"], seconds: ["time (seconds)", null], avgSpeed: ["average speed", "speed"], maxSpeed: ["top speed", "speed"],
  energyUsed: ["fuel or energy used (L or kWh)", null], percentUsed: ["tank or battery used (%)", null],
  consumption: ["consumption (L or kWh per 100 km)", null], range: ["range left", "distance"],
};

/** "oilOverheating" -> "oil overheating", "brakeFL" -> "brake FL": split camelCase, keep short capital runs (wheel names). */
export function humanize(name) {
  return name
    .replace(/_/g, " ")
    .replace(/([a-z0-9])([A-Z])/g, "$1 $2")
    .split(" ")
    .map((w) => (w.length > 1 && w === w.toUpperCase() ? w : w.toLowerCase()))
    .join(" ");
}

/** Friendly label for a channel, or null when we have nothing better than the raw name. */
export function describeChannel(name) {
  if (Object.hasOwn(KNOWN, name)) return KNOWN[name][0];
  let m = WHEEL_THERMAL.exec(name);
  if (m) return `${WHEEL_THERMAL_NAME[m[2]]} ${m[1]}`;
  if ((m = TIRE.exec(name))) return `${TIRE_FIELD[m[2]]} ${m[1]}`;
  if ((m = ZONE.exec(name))) return `Body damage: ${ZONE_NAME[m[1]]}`;
  if (Object.hasOwn(DAMAGE_NAME, name)) return DAMAGE_NAME[name];
  if ((m = TRIP.exec(name))) return `Trip ${m[1]}: ${TRIP_FIELD[m[2]][0]}`;
  if (name === "trip.energyKind") return "Trip: kind of energy (liquid, electric, mixed)";
  if (name === "energy.electric") return "Car has a battery";
  if (name === "energy.liquid") return "Car has a fuel tank";
  if ((m = STATE.exec(name))) return `${Object.hasOwn(STATE_GROUP, m[1]) ? STATE_GROUP[m[1]] : humanize(m[1])}: ${humanize(m[2])}`;
  return null;
}

/** Unit kind a widget should use for this channel ("speed", "temp", ...), or null when unknown. */
export function suggestKind(name) {
  if (Object.hasOwn(KNOWN, name)) return KNOWN[name][1];
  let m = WHEEL_THERMAL.exec(name);
  if (m) return m[2] === "brakeThermalEfficiency" ? "fraction" : "temp";
  if ((m = TIRE.exec(name))) return m[2] === "temp" || m[2] === "coreTemp" ? "temp" : m[2] === "pressure" ? "pressure" : "none";
  if (ZONE.test(name)) return "fraction";
  if ((m = TRIP.exec(name))) return TRIP_FIELD[m[2]][1];
  return null;
}

/** "common" (curated), "tires", "damage", "states" (component states), "trip", "car" (everything else) or "minor" (probably noise). */
export function classifyChannel(name) {
  if (Object.hasOwn(KNOWN, name)) return "common";
  if (name.startsWith("tire.")) return "tires";
  if (name.startsWith("damage.")) return "damage";
  if (name.startsWith("state.")) return "states";
  if (name.startsWith("trip.")) return "trip";
  if (name.startsWith("energy.")) return "minor";
  if (WHEEL_THERMAL.test(name)) return "car";
  return MINOR.test(name) ? "minor" : "car";
}

/**
 * Split a catalog ({name: type}) into picker groups, each sorted by what the user would search for:
 * friendly label if there is one, otherwise the raw name. Case-insensitive search matches either.
 * @returns {{common: object[], tires: object[], damage: object[], states: object[], trip: object[], car: object[], minor: object[]}} entries are {name, label, type}
 */
export function groupChannels(channels, query = "") {
  const q = query.trim().toLowerCase();
  const groups = { common: [], tires: [], damage: [], states: [], trip: [], car: [], minor: [] };
  for (const [name, type] of Object.entries(channels)) {
    const label = describeChannel(name);
    if (q && !name.toLowerCase().includes(q) && !(label && label.toLowerCase().includes(q))) continue;
    groups[classifyChannel(name)].push({ name, label, type });
  }
  // With a search, the best match comes first (exact channel name, then names starting with the query),
  // so typing "rpm" and pressing Enter picks `rpm`, not the alphabetically earlier "Engine max RPM".
  const key = (e) => (e.label ?? e.name).toLowerCase();
  const rank = (e) => (!q ? 2 : e.name.toLowerCase() === q ? 0 : e.name.toLowerCase().startsWith(q) ? 1 : 2);
  for (const list of Object.values(groups)) list.sort((a, b) => rank(a) - rank(b) || key(a).localeCompare(key(b)));
  return groups;
}

/** Compact text for a live value in the picker. */
export function formatLive(value) {
  if (value === undefined) return "";
  if (value === null) return "n/a";
  if (typeof value === "number") return Number.isInteger(value) ? String(value) : String(Math.round(value * 1000) / 1000);
  if (typeof value === "boolean") return value ? "true" : "false";
  return JSON.stringify(String(value).slice(0, 24));
}

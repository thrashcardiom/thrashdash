// Fuel and energy figures for the trip computer, in the units the user chose. The relay measures in litres (fuel) or
// kWh (battery) and per 100 km; this turns those into L/100 km, km/L, mpg or kWh/100 km, km/kWh, mi/kWh.
// "kind" is the car's energy: "liquid" (a fuel tank), "electric" (a battery), "mixed" (hybrid) or "unknown".

const LITRES_PER_US_GALLON = 3.785411784;
const LITRES_PER_UK_GALLON = 4.54609;
const KM_PER_MILE = 1.609344;

/** The economy unit in effect: the saved choice, or for "auto" the one that goes with the distance unit. */
export function resolveEconomy(prefs) {
  const choice = prefs?.economy ?? "auto";
  if (choice !== "auto") return choice;
  return prefs?.distance === "mi" ? "mpgus" : "l100";
}

/**
 * Consumption for display from the relay's litres (or kWh) per 100 km.
 * Petrol/diesel: L/100 km, km/L, mpg. Electric: kWh/100 km, km/kWh, mi/kWh (the mpg choices become miles per kWh).
 * @returns {{value: number, unit: string} | null} null when there is no meaningful figure (nothing used yet, or a hybrid)
 */
export function formatConsumption(per100km, kind, economy) {
  if (typeof per100km !== "number" || !Number.isFinite(per100km) || per100km <= 0) return null;
  if (kind === "electric") {
    if (economy === "kmpl") return { value: 100 / per100km, unit: "km/kWh" };
    if (economy === "mpgus" || economy === "mpguk") return { value: 100 / (per100km * KM_PER_MILE), unit: "mi/kWh" };
    return { value: per100km, unit: "kWh/100 km" };
  }
  if (kind !== "liquid") return null;
  if (economy === "kmpl") return { value: 100 / per100km, unit: "km/L" };
  if (economy === "mpgus") return { value: (100 * LITRES_PER_US_GALLON) / (per100km * KM_PER_MILE), unit: "mpg" };
  if (economy === "mpguk") return { value: (100 * LITRES_PER_UK_GALLON) / (per100km * KM_PER_MILE), unit: "mpg UK" };
  return { value: per100km, unit: "L/100 km" };
}

/** Fuel or energy used, from litres (or kWh): litres or gallons for a tank (gallons when mpg is chosen), always kWh for a battery. */
export function formatEnergy(volume, kind, economy) {
  if (typeof volume !== "number" || !Number.isFinite(volume)) return null;
  if (kind === "electric") return { value: volume, unit: "kWh" };
  if (kind !== "liquid") return null;
  if (economy === "mpgus") return { value: volume / LITRES_PER_US_GALLON, unit: "gal" };
  if (economy === "mpguk") return { value: volume / LITRES_PER_UK_GALLON, unit: "gal UK" };
  return { value: volume, unit: "L" };
}

/** 3725 -> "1:02:05" (hours:minutes:seconds), and "--" for no value. */
export function formatDuration(seconds) {
  if (typeof seconds !== "number" || !Number.isFinite(seconds) || seconds < 0) return "--";
  const total = Math.floor(seconds);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  return `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

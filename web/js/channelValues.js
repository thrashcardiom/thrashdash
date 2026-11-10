// Small helpers for interpreting channel values, shared by the plain and the skinned widgets.

/** True for anything the game uses to mean "on": true, a number above 0.5, a non-empty non-"0" string. */
export function isOn(value) {
  if (typeof value === "boolean") return value;
  if (typeof value === "number") return value > 0.5;
  if (typeof value === "string") return value !== "" && value !== "0";
  return false;
}

/**
 * Current gear text. `gear` is a string on automatics ("N", "D", "R", "M2"...) but a number on manuals
 * (confirmed in docs/phase0-findings.md), so both are normalised. If `gear` is absent we fall back to
 * `gearIndex` (-1 = reverse, 0 = neutral).
 */
export function formatGear(gear, gearIndex) {
  const v = gear !== undefined && gear !== null ? gear : gearIndex;
  if (v === undefined || v === null) return null;
  if (typeof v === "string") return v === "" ? null : v;
  if (typeof v === "number") return v < 0 ? "R" : v === 0 ? "N" : String(Math.round(v));
  return null;
}

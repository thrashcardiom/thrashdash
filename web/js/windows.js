import { randomWindowId } from "./windowState.js";

// Opening another dashboard window the same size as this one, to show different metrics side by side (or on another monitor).

const MIN_W = 320;
const MIN_H = 200;
const OFFSET = 40; // so the new window does not sit exactly on top of this one

/**
 * window.open features for a window the same size as this one. `insetRight` is space this window gives to something that
 * a new window will not have (the editor panel). The size is the window's inner (page) size, which is what `width` and
 * `height` mean here; the position is a little down and to the right (some systems place windows themselves and ignore it).
 */
export function sameSizeFeatures({ insetRight = 0 } = {}, win = window) {
  const clamp = (n, lo, hi) => Math.min(hi, Math.max(lo, Math.round(n)));
  const maxW = win.screen?.availWidth || 10000;
  const maxH = win.screen?.availHeight || 10000;
  const width = clamp(win.innerWidth - insetRight, MIN_W, maxW);
  const height = clamp(win.innerHeight, MIN_H, maxH);
  const left = Math.max(0, Math.round((win.screenX ?? 0) + OFFSET));
  const top = Math.max(0, Math.round((win.screenY ?? 0) + OFFSET));
  return `popup=yes,width=${width},height=${height},left=${left},top=${top}`;
}

// The Trips (map) window remembers how big it was, and where, so the next one opens like it. (Windows ThrashDash reopens by itself
// at start-up come from the saved window config instead; this is for the one you open with the Trips button or the T key.)
export const TRIPS_WINDOW_KEY = "thrashdash.tripswindow";
const TRIPS_DEFAULT = { width: 1180, height: 760 };

/** What to remember about a Trips window: its inner (page) size, which is what window.open's width and height mean, and its place. */
export function tripsGeometry(win = window) {
  const n = (v) => Math.round(Number(v) || 0);
  return { width: n(win.innerWidth), height: n(win.innerHeight), x: n(win.screenX), y: n(win.screenY) };
}

/** The remembered Trips window geometry from `storage` (localStorage), or null when there is none or it is not sensible. */
export function loadTripsGeometry(storage) {
  try {
    const g = JSON.parse(storage.getItem(TRIPS_WINDOW_KEY));
    return g && [g.width, g.height].every((v) => Number.isFinite(v) && v >= MIN_W && v <= 20000) ? g : null;
  } catch {
    return null; // (no storage, or not what we wrote)
  }
}

/** Remember a Trips window's geometry. Nothing when it has not changed (this is called every time the window is looked at). */
export function saveTripsGeometry(storage, g) {
  try {
    const text = JSON.stringify({ width: g.width, height: g.height, x: g.x, y: g.y });
    if (storage.getItem(TRIPS_WINDOW_KEY) !== text) storage.setItem(TRIPS_WINDOW_KEY, text);
  } catch {
    /* a browser that will not remember is fine */
  }
}

/**
 * window.open features for a new Trips window: the size the last one had (kept to the screen), and its place when that is known.
 * Under Wayland a window cannot say where it is (it reads 0,0) and cannot be placed, so a place of 0,0 is left out.
 */
export function tripsFeatures(saved, win = window) {
  const clamp = (n, lo, hi) => Math.min(hi, Math.max(lo, Math.round(n)));
  const maxW = win.screen?.availWidth || 10000;
  const maxH = win.screen?.availHeight || 10000;
  const g = saved ?? TRIPS_DEFAULT;
  let features = `popup=yes,width=${clamp(g.width, MIN_W, maxW)},height=${clamp(g.height, MIN_H, maxH)}`;
  if (saved && Number.isFinite(g.x) && Number.isFinite(g.y) && (g.x !== 0 || g.y !== 0)) features += `,left=${Math.round(g.x)},top=${Math.round(g.y)}`;
  return features;
}

/**
 * The address of a window showing dashboard `id`. `windowId` names the window (so ThrashDash can reopen it next time).
 * With `menu`, the Dashboards menu opens at once so another can be picked.
 */
export function dashboardUrl(id, { menu = false, windowId = null } = {}, origin = location.origin) {
  return `${origin}/?d=${encodeURIComponent(id)}${windowId ? `&w=${windowId}` : ""}${menu ? "&menu=1" : ""}`;
}

/** Open dashboard `id` in a new window the same size as this one. Returns the window (null if the browser blocked it). */
export function openDashboardWindow(id, { menu = true, insetRight = 0 } = {}) {
  return window.open(dashboardUrl(id, { menu, windowId: randomWindowId() }), "_blank", sameSizeFeatures({ insetRight }));
}

/** The address of a Trips (map) window `windowId`: the id is what lets ThrashDash remember its size and place and reopen it. */
export function tripsUrl(windowId, origin = location.origin) {
  return `${origin}/trips?w=${windowId}`;
}

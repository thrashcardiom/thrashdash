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

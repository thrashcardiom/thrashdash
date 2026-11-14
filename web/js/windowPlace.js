// Putting a window where it was. When "Place windows exactly" is on, the browser runs in X11 compatibility mode and a window can be moved
// and sized by its own page (window.moveTo / resizeTo). ThrashDash starts every window with the first window's size and place (a browser
// reads its size and position flags once, for the whole process), so each window puts itself right as it opens, from what the relay
// remembers of it, before it starts reporting its geometry (reporting first would overwrite what is remembered with the wrong numbers).
//
// Everything it touches is passed in, so it can be tested without a browser.

export const TOLERANCE_PX = 3; // a window within this many pixels of where it should be is there
export const SETTLE_MS = 1500; // how long to wait for the window manager to carry out a move before carrying on anyway
const POLL_MS = 100;

const finite = (v) => typeof v === "number" && Number.isFinite(v);
const near = (a, b) => Math.abs(a - b) <= TOLERANCE_PX;

/** The window's remembered geometry, as the relay keeps it (windows.js `readGeometry` made it): {width, height, x, y}. */
function wanted(entry) {
  if (!entry || !finite(entry.width) || !finite(entry.height)) return null;
  // (a position of 0,0 means "not known": a system that does not tell a page where its window is reports that)
  const placed = finite(entry.x) && finite(entry.y) && (entry.x !== 0 || entry.y !== 0);
  return { width: entry.width, height: entry.height, x: placed ? entry.x : null, y: placed ? entry.y : null };
}

const there = (win, w) => near(win.outerWidth, w.width) && near(win.outerHeight, w.height) && (w.x === null || (near(win.screenX ?? 0, w.x) && near(win.screenY ?? 0, w.y)));

/**
 * Move and size this window to its remembered geometry, if "Place windows exactly" is on. Returns true if it had to move it.
 * `getSettings()` and `listWindows()` are the relay's calls (listWindows gives {windows: {id: entry}}); `sleep(ms)` waits.
 */
export async function applySavedPlacement(id, { getSettings, listWindows, win = globalThis.window, sleep = (ms) => new Promise((r) => setTimeout(r, ms)) }) {
  try {
    const settings = await getSettings();
    if (settings?.exactWindowPlacement !== true) return false;
    const target = wanted((await listWindows())?.windows?.[id]);
    if (!target || there(win, target)) return false;
    win.resizeTo(target.width, target.height);
    if (target.x !== null) win.moveTo(target.x, target.y);
    for (let waited = 0; waited < SETTLE_MS && !there(win, target); waited += POLL_MS) await sleep(POLL_MS);
    return true;
  } catch {
    return false; // not allowed here (a system that does not let a page move its window): the window stays where the system put it
  }
}

// An optional sound when an alert appears: a short tone made in the browser (nothing is loaded, so the page's policy needs no change).
// It is per WINDOW: it depends on which screen you are listening at, so a second monitor does not chime in step with the first, and a
// phone can have its own (add ?sound=1 to its address). Off until switched on; remembered in this browser (localStorage), not by the relay.
//
// Browsers hold sound back until the page has been clicked once. Windows opened by ThrashDash are started allowing it; in any other window
// the first click or key press lets it through, and until then the editor says so.

const KEY = "thrashdash.alertsound";
export const MIN_GAP_MS = 2000; // never two chimes closer than this, whatever the alerts do
const DEFAULT = { on: false, volume: 0.5 };

/** The settings kept in this browser, cleaned: {on, volume (0 to 1)}. `search` is the page's address query, where ?sound=1 turns it on for this window. */
export function soundSettings(stored, search = "") {
  let raw = null;
  try {
    raw = typeof stored === "string" ? JSON.parse(stored) : stored;
  } catch {
    raw = null;
  }
  const volume = typeof raw?.volume === "number" && Number.isFinite(raw.volume) ? Math.min(1, Math.max(0, raw.volume)) : DEFAULT.volume;
  const on = raw?.on === true || new URLSearchParams(search).get("sound") === "1";
  return { on, volume };
}

/** The tones of a chime: [{freq (Hz), at (s after the start), len (s)}]. A warning is one note; a critical alert two, falling, so they differ by ear. */
export function chimeFor(level) {
  return level === "crit"
    ? [{ freq: 988, at: 0, len: 0.14 }, { freq: 740, at: 0.18, len: 0.2 }]
    : [{ freq: 880, at: 0, len: 0.18 }];
}

/**
 * Which alerts are new since last time (ones that were not showing, and are now), and the level to chime at: "crit" if any new one is,
 * else "warn", else null. An alert that stays up chimes once, not again; one that goes away and comes back chimes again.
 * @param {Set<string>} before ids showing at the last look
 * @param {{id: string, level: string}[]} now alerts showing now
 */
export function newAlerts(before, now) {
  const fresh = now.filter((a) => !before.has(a.id));
  return { ids: new Set(now.map((a) => a.id)), level: fresh.some((a) => a.level === "crit") ? "crit" : fresh.length ? "warn" : null };
}

/** The sound for a window. `audio` is the AudioContext class (injected, so tests need no browser), `storage` a localStorage-like object. */
export function createAlertSound({ audio = globalThis.AudioContext ?? globalThis.webkitAudioContext, storage = null, search = "", now = () => performance.now() } = {}) {
  let ctx = null;
  let blocked = false;
  let before = null; // the alerts showing at the last look; null until the first, so alerts already up when the window opens do not chime
  let lastAt = -Infinity;
  const read = () => {
    try {
      return soundSettings(storage?.getItem?.(KEY) ?? null, search);
    } catch {
      return soundSettings(null, search); // (a page that may not read what it kept)
    }
  };
  let settings = read();

  const context = () => {
    if (!audio) return null;
    ctx ??= new audio();
    return ctx;
  };

  /** Play a chime now, if allowed. Returns whether it could start (false while the browser still holds sound back). */
  function play(level, volume = settings.volume) {
    const c = context();
    if (!c) return false;
    if (c.state === "suspended") c.resume?.().catch?.(() => {});
    blocked = c.state !== "running";
    if (blocked) return false;
    const t0 = c.currentTime;
    for (const tone of chimeFor(level)) {
      const osc = c.createOscillator();
      const gain = c.createGain();
      osc.type = "sine";
      osc.frequency.value = tone.freq;
      const peak = 0.35 * volume;
      gain.gain.setValueAtTime(0.0001, t0 + tone.at);
      gain.gain.exponentialRampToValueAtTime(Math.max(0.0001, peak), t0 + tone.at + 0.015);
      gain.gain.exponentialRampToValueAtTime(0.0001, t0 + tone.at + tone.len);
      osc.connect(gain).connect(c.destination);
      osc.start(t0 + tone.at);
      osc.stop(t0 + tone.at + tone.len + 0.05);
    }
    return true;
  }

  return {
    get settings() {
      return { ...settings };
    },
    /** True after a sound could not start because the browser has not allowed it yet (the page needs a click). */
    get blocked() {
      return blocked;
    },
    set(patch) {
      settings = { ...settings, ...(typeof patch.on === "boolean" ? { on: patch.on } : {}), ...(Number.isFinite(patch.volume) ? { volume: Math.min(1, Math.max(0, patch.volume)) } : {}) };
      try {
        storage?.setItem?.(KEY, JSON.stringify({ on: settings.on, volume: settings.volume }));
      } catch {
        /* a browser that will not remember is fine */
      }
    },
    /** Play a chime, whatever the setting says (the Test button, which is also the click a browser wants before it allows sound). */
    test(level = "warn") {
      return play(level);
    },
    /** Let the page's first click or key press unlock sound. */
    unlockOnGesture(target = globalThis.document) {
      const unlock = () => {
        const c = context();
        if (c?.state === "suspended") c.resume?.().then?.(() => (blocked = c.state !== "running"), () => {});
      };
      for (const type of ["pointerdown", "keydown"]) target?.addEventListener?.(type, unlock, { once: false, passive: true });
    },
    /** Look at the alerts now showing; chime for a new one, if the sound is on. @returns {boolean} whether it chimed */
    update(alerts) {
      const seen = newAlerts(before ?? new Set(), alerts);
      const first = before === null;
      before = seen.ids;
      if (first || !settings.on || !seen.level) return false;
      const t = now();
      if (t - lastAt < MIN_GAP_MS) return false;
      lastAt = t;
      return play(seen.level);
    },
  };
}

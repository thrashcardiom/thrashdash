// The Trips page: the list of recorded drives, and for the one picked, its route on a map, its figures and a speed and
// altitude graph. Read-only except for renaming and deleting a trip, which only the computer running ThrashDash may do.
//
// All page text is put in with textContent (see dom.js): a trip's name is something the user typed.
// The pure parts (colours, graph geometry, the figures) are in model.js, where Node tests them.

import { api } from "../api.js";
import { h, s } from "../dom.js";
import { buildPrefs, resolveUnit } from "../units.js";
import { createWindowReporter, ensureWindowId, readGeometry } from "../windowState.js";
import { applySavedPlacement } from "../windowPlace.js";
import { saveTripsGeometry, tripsGeometry } from "../windows.js";
import { compareRows, gapSeries, signedDuration } from "./compare.js";
import { createCircuits } from "./circuits.js";
import { createRouteView } from "./route.js";
import { routeSummary } from "./stops.js";
import { statsCsv, statsCsvName } from "./csv.js";
import { formatConsumption, formatDuration, formatEnergy, resolveEconomy } from "../economy.js";
import { RAMP, REPLAY_RATES, advanceLive, interpolateFeed, liveReadout, nextAutoOpen, WHEN_OPTIONS, advanceReplay, clockText, colourScaleTop, filterOptions, filterRoutes, overviewTiles, routesBounds, turnToward, clampMapHeight, mapHeightFor, MAP_STEP_PX, distanceText, graphModel, replayReadout, limitAt, routeRuns, sameTrip, sampleAtDistance, speedText, statTiles, tripTitle, filterTrips, tabTitle, recentTotals, stopText, refuelText, impactText, impactRow, impactDetails, impactsOf, DEFAULT_IMPACT_RULE, waypointRows, lifetimeStats, DEFAULT_PRESET_TAGS, moneyText, costPer100, addTypedTag, hasTag, knownTags, tagsInUse, toggleTag } from "./model.js";

const LIVE_MS = 3000; // while a trip is being recorded (or open and not finished), the page re-reads it this often
const IDLE_TICKS = 1; // with nothing being recorded it only looks every IDLE_TICKS * LIVE_MS, to notice a new trip (the list is cheap to read)
const BASE_KEY = "thrashdash.trips.base";
const GRAPH_HEIGHT = 160;

// Map tiles. OpenStreetMap refuses tile requests that carry no Referer, so the tiles are asked for with the page's origin as the
// Referer (and nothing more of the address; see server.TRIPS_REFERRER_POLICY). A "no-referrer" here once showed "Access blocked".
// The street map is OpenStreetMap's (used lightly, by someone looking at their own trips); the satellite picture is
// EOX's Sentinel-2 cloudless 2016, the one year of it that is free to use with credit (the later years are non-commercial).
// Its pixels are about 10 m, so it stops being sharper past zoom 13: beyond that the map just enlarges it.
const BASES = {
  street: { url: "https://tile.openstreetmap.org/{z}/{x}/{y}.png", maxNativeZoom: 19, maxZoom: 19,
            attribution: '&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener noreferrer">OpenStreetMap</a> contributors' },
  sat: { url: "https://tiles.maps.eox.at/wmts/1.0.0/s2cloudless_3857/default/g/{z}/{y}/{x}.jpg", maxNativeZoom: 13, maxZoom: 18,
         attribution: '<a href="https://s2maps.eu" target="_blank" rel="noopener noreferrer">Sentinel-2 cloudless</a> by EOX IT Services GmbH (contains modified Copernicus Sentinel data 2016), <a href="https://creativecommons.org/licenses/by/4.0/" target="_blank" rel="noopener noreferrer">CC BY 4.0</a>' },
};

const ENDED = {
  imported: "imported from a file", split: "cut in two here", idle: "parked", teleport: "teleported", jump: "moved elsewhere", vehicle: "changed vehicle", level: "left the level",
  lost: "the game stopped", shutdown: "ThrashDash was closed", interrupted: "ThrashDash stopped unexpectedly",
};

const $ = (id) => document.getElementById(id);

// A trip shared as one html file (see relay/thrashdash/tripshare.py) runs this same program, with the page's data embedded and no
// relay behind it: it shows the one trip, with the map, figures, graph and replay, and none of the things that need a relay.
const SHARED = Boolean(globalThis.THRASHDASH_SHARE);
// The same page also runs inside a dashboard as its live map widget (/trips?embed=1&headup=1&zoom=17&base=street): just the map,
// following the trip being driven. See web/js/widgets/tripmap.js.
const QUERY = new URLSearchParams(globalThis.location?.search ?? "");
const EMBED = QUERY.get("embed") === "1";
if (EMBED) document.body.classList.add("embed");
const stored = (key) => {
  try {
    return localStorage.getItem(key);
  } catch {
    return null; // (a page that may not keep anything, such as one shown in a sandbox)
  }
};
const state = { trips: [], enabled: true, selected: null, detail: null, client: { local: false }, prefs: buildPrefs({}, location.search), recording: false, query: "", starredOnly: false, tag: "", presetTags: DEFAULT_PRESET_TAGS, picked: new Set(), rule: { ...DEFAULT_IMPACT_RULE } };
const BASE_TITLE = document.title; // (a shared page has its own title; a recording is marked in front of whichever it is)
let selectSeq = 0; // the newest request to open a trip: an older one that arrives later is dropped, so stepping quickly through the list ends on the last
let map = null;
let baseLayer = null;
let trackLayer = null;
let cursor = null;
let refreshTimer = null;
let drawnFor = null;
let fittedFor = null; // the trip the map has been moved to show (a trip that opens before it has any points is fitted when the first arrive)
// (OpenStreetMap refuses tile requests with no Referer, and a page opened from a disk sends none: it starts on the satellite picture)
let currentBase = EMBED && ["sat", "street"].includes(QUERY.get("base")) ? QUERY.get("base") : ["sat", "street"].includes(stored(BASE_KEY)) ? stored(BASE_KEY) : SHARED && location.protocol === "file:" ? "sat" : "street";
let circuitsView = null; // the Circuits view (circuits.js), made once the page has started
let routeView = null; // the Route view (route.js), likewise
const plan = { state: null, layer: null, key: "" }; // the GPS route now (GET /api/route), and what of it is drawn on the trip's map
/** Another view is opening (or the trip list is back): the Circuits and Route views step aside. */
function hideTools() {
  circuitsView?.hide();
  routeView?.hide();
}
const overview = { map: null, layer: null, base: null, routes: [], filters: { when: "all", vehicle: "", country: "", tag: "" }, ticks: 0 };
let graphRef = null; // the drawn graph: {g (its geometry), rule (the replay's line on it)}, so the replay can move the line
const AUTO_KEY = "thrashdash.trips.autoopen";
let autoOpened = null; // the trip being recorded that the page last moved to (or chose not to), so it moves once per trip
const HEADUP_KEY = "thrashdash.trips.headup";
const view = { zoom: 16 }; // the zoom used when the map follows the car heading-up (the car's zoom while you zoom; a dashboard's map widget sets it)
const rotation = { on: false, bearing: 0, lastWheel: 0 }; // heading-up: the map turned so the car points up
const LIVE_POLL_MS = 1000; // while following a trip being driven, new points are fetched this often
const LIVE_DELAY_S = 2; // and the car is shown this far behind the newest point, so it moves smoothly between them
// The car on the live map comes from the game's own live positions (ten a second, pushed over a websocket), shown a moment behind
// the newest and smoothed between them, so it keeps up with the in-game map. The recorded points (once a second, through the
// file) draw the route and the figures, and stand in for the car when no live positions are arriving.
const FEED_DELAY_S = 0.15; // how far behind the newest position the car is drawn: enough to always have two to draw between
const FEED_STALE_S = 2; // positions older than this are not the car's position any more
const FEED_KEEP = 80;
const feed = { ws: null, samples: [], offsets: [], last: 0 };
let liveTail = null; // the stretch of route between the newest recorded point and the car

const replay = { live: false, wantLive: false, active: false, playing: false, t: 0, rate: 4, raf: 0, last: 0, marker: null };

const when = (ts) => new Date(ts * 1000).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });

// ---- the list --------------------------------------------------------------------------------------------------------

/** What a trip can be found by when searching the list: its name, its vehicle, and its date as shown. */
const searchText = (trip) => [tripTitle(trip.id === state.selected && state.detail && !trip.stats ? { ...trip, stats: state.detail.stats } : trip), trip.vehicle, when(trip.ts), ...(trip.tags ?? [])].join(" ");

function renderList() {
  const list = $("list");
  const hadFocus = list.contains(document.activeElement) ? document.activeElement.dataset.id : null; // (the list is redrawn every few seconds: keyboard focus must survive it)
  const hadTick = list.contains(document.activeElement) && document.activeElement.classList.contains("pick");
  for (const id of [...state.picked]) if (!state.trips.some((t) => t.id === id && !t.recording)) state.picked.delete(id); // (a ticked trip that has gone, or is being recorded, is not ticked)
  list.replaceChildren();
  const shown = shownTrips();
  const searching = Boolean(state.query.trim()) || state.starredOnly || Boolean(state.tag);
  $("search").hidden = !state.trips.length;
  drawTagFilter();
  $("starred-row").hidden = !state.trips.some((t) => t.starred) && !state.starredOnly; // (nothing to filter by until a trip has been starred)
  $("count").textContent = !state.trips.length ? "" : searching ? `${shown.length} of ${state.trips.length}` : `${state.trips.length} ${state.trips.length === 1 ? "trip" : "trips"}`;
  const week = recentTotals(state.trips);
  const totals = $("totals");
  totals.hidden = week.count === 0;
  if (week.count) {
    const d = distanceText(week.distance, state.prefs);
    totals.textContent = `Last 7 days: ${week.count} ${week.count === 1 ? "trip" : "trips"} \u00B7 ${d.value} ${d.label} \u00B7 ${formatDuration(week.duration)}`;
  }
  const empty = $("empty");
  empty.hidden = shown.length > 0;
  if (!state.trips.length) {
    empty.textContent = state.enabled
      ? "No trips yet. Drive in BeamNG.Earth with ThrashDash running and each drive appears here."
      : "The trip log is switched off in this run of ThrashDash (it is not kept in --simulate mode).";
  } else if (!shown.length) {
    empty.textContent = state.query.trim() ? "No trip matches that search." : state.tag ? `No trip has the tag ${state.tag}.` : "No trip is starred yet.";
  }
  for (const trip of shown) {
    const li = h("li", "", list);
    if (state.client.local && !trip.recording) { // (only the computer running ThrashDash can delete; a trip being recorded cannot be)
      const tick = h("input", "pick", li);
      tick.type = "checkbox";
      tick.dataset.id = trip.id;
      tick.checked = state.picked.has(trip.id);
      tick.setAttribute("aria-label", `Select ${tripTitle(trip.id === state.selected && state.detail && !trip.stats ? { ...trip, stats: state.detail.stats } : trip)}`);
      tick.addEventListener("change", () => {
        if (tick.checked) state.picked.add(trip.id);
        else state.picked.delete(trip.id);
        resetDelete(); // (a "Really delete?" that was waiting was about a different selection)
        updatePicked();
      });
      if (trip.id === hadFocus && hadTick) tick.focus({ preventScroll: true });
    }
    const b = h("button", "item", li);
    b.type = "button";
    b.dataset.id = trip.id;
    b.setAttribute("aria-current", trip.id === state.selected ? "true" : "false");
    // (a trip still being recorded has no stored summary, so the one worked out for the open trip stands in for its name)
    const known = trip.id === state.selected && state.detail && !trip.stats ? { ...trip, stats: state.detail.stats } : trip;
    const title = h("span", "item-title", b);
    if (trip.starred) h("span", "star", title, "\u2605").setAttribute("aria-label", "Starred");
    h("span", "", title, tripTitle(known));
    const bits = [when(trip.ts)];
    if (trip.distance != null) bits.push(distanceString(trip.distance));
    if (trip.duration != null) bits.push(formatDuration(trip.duration));
    h("span", "item-sub muted", b, bits.join(" · "));
    if (trip.tags?.length) {
      const tags = h("span", "item-tags", b);
      for (const t of trip.tags) h("span", "tag-mini", tags, t);
    }
    if (trip.recording) h("span", "badge", b, "Recording");
    else if (trip.imported) h("span", "badge imported", b, "Imported");
    b.addEventListener("click", () => select(trip.id));
    if (trip.id === hadFocus && !hadTick) b.focus({ preventScroll: true });
  }
  updatePicked();
}

/** Which trips are ticked: the bar above the list (how many, select all, clear, delete) and the Delete button on an open trip, which
 * deletes the ticked trips while any are ticked. */
function updatePicked() {
  const n = state.picked.size;
  $("selbar").hidden = n === 0;
  $("sel-count").textContent = `${n} ${n === 1 ? "trip" : "trips"} selected`;
  for (const box of $("list").querySelectorAll(".pick")) box.checked = state.picked.has(box.dataset.id);
  labelDelete();
}

function shownTrips() {
  const base = state.trips.filter((t) => (!state.starredOnly || t.starred) && hasTag(t, state.tag));
  return filterTrips(base, state.query, searchText);
}

/** The search box and the arrow keys. Typing narrows the list; Down from the box goes to the first trip; in the list Up and Down
 * move to the previous or next trip and open it (Home and End go to the first and last), and Escape in the box clears it. */
function wireList() {
  $("sel-all").addEventListener("click", () => {
    for (const t of shownTrips()) if (!t.recording) state.picked.add(t.id);
    updatePicked();
  });
  $("sel-clear").addEventListener("click", () => {
    state.picked.clear();
    updatePicked();
  });
  $("sel-delete").addEventListener("click", () => askDelete($("sel-delete")));
  $("starred-only").addEventListener("change", () => {
    state.starredOnly = $("starred-only").checked;
    renderList();
  });
  $("tag-filter").addEventListener("change", () => {
    state.tag = $("tag-filter").value;
    renderList();
  });
  const search = $("search");
  search.addEventListener("input", () => {
    state.query = search.value;
    renderList();
  });
  search.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && search.value) {
      e.preventDefault();
      search.value = "";
      state.query = "";
      renderList();
    } else if (e.key === "ArrowDown") {
      const first = $("list").querySelector(".item");
      if (first) {
        e.preventDefault();
        first.focus();
      }
    }
  });
  $("list").addEventListener("keydown", (e) => {
    const items = [...$("list").querySelectorAll(".item")];
    const at = items.indexOf(document.activeElement);
    if (at < 0 || e.ctrlKey || e.altKey || e.metaKey) return;
    const to = { ArrowDown: Math.min(at + 1, items.length - 1), ArrowUp: Math.max(at - 1, 0), Home: 0, End: items.length - 1 }[e.key];
    if (to === undefined) return;
    e.preventDefault();
    if (e.key === "ArrowUp" && at === 0 && !$("search").hidden) {
      $("search").focus();
      return;
    }
    if (to !== at) {
      items[to].focus();
      select(items[to].dataset.id);
    }
  });
}

const distanceString = (m) => {
  const d = distanceText(m, state.prefs);
  return `${d.value} ${d.label}`;
};

async function loadList() {
  try {
    const data = await api.listTrips();
    state.enabled = data.enabled;
    state.trips = data.trips;
    state.presetTags = Array.isArray(data.presetTags) && data.presetTags.length ? data.presetTags : DEFAULT_PRESET_TAGS;
    state.recording = Boolean(data.status?.recording);
    const live = $("live");
    live.hidden = !state.recording;
    document.title = tabTitle(BASE_TITLE, state.recording);
    if (state.recording) live.textContent = `Recording now: ${distanceString(data.status.distance)}`;
  } catch (e) {
    state.trips = [];
    $("empty").hidden = false;
    $("empty").textContent = `Could not load the trips: ${e.message}`;
    return;
  }
  renderList();
  if (isStats()) drawStats(); // (a trip has finished, or been renamed: the totals follow)
}

// ---- one trip --------------------------------------------------------------------------------------------------------

async function select(id, { refit = true } = {}) {
  const mine = ++selectSeq;
  let trip;
  try {
    trip = await api.getTrip(id);
  } catch (e) {
    if (state.selected === id) showNothing();
    return;
  }
  if (mine !== selectSeq) return; // (something newer was asked for while this was being read)
  show(trip, refit);
}

/** Draw a trip that has been read. `refit`: move the map to show all of it (not wanted when only new points arrived). */
function show(trip, refit) {
  const id = trip.header.id;
  const wasFollowing = Boolean(map && state.detail && state.detail.header.id === id && state.detail.points.length && map.getBounds().contains(lastLatLng(state.detail)));
  state.selected = id;
  state.detail = trip;
  if (!EMBED && location.hash !== `#${id}`) history.replaceState(null, "", `#${id}`); // (in a dashboard the address is left alone: a reload must stay the widget)
  document.body.dataset.view = "detail";
  $("placeholder").hidden = true;
  $("overview").hidden = true;
  $("stats").hidden = true;
  $("compare-view").hidden = true;
  hideTools();
  $("all").setAttribute("aria-pressed", "false");
  $("stats-btn").setAttribute("aria-pressed", "false");
  $("detail").hidden = false;
  for (const b of $("list").querySelectorAll(".item")) b.setAttribute("aria-current", b.dataset.id === id ? "true" : "false");
  drawHeader();
  drawTiles();
  drawImpacts();
  drawWaypoints();
  if (drawnFor !== id) {
    stopReplay(); // (before the map is drawn: it may be turned, and must be upright and the right size to fit the new trip)
    replay.wantLive = !trip.end; // a trip being driven is followed live: the car moves on the map as it drives
  }
  if (replay.wantLive && !trip.end) startLive(trip); // (tried again at each refresh until the trip has two points to follow)
  if (trip.end && replay.live) {
    replay.live = false; // it has ended: let the car finish the route it has not yet shown, then stop
    replay.playing = true;
    replay.last = performance.now();
    ensureLoop();
  }
  drawMap(refit || drawnFor !== id);
  // A trip being recorded keeps going: if the map was showing the car and it has now driven off the edge, follow it
  // (but never pull the map back from somewhere the user has moved it to look at).
  if (!trip.end && wasFollowing && !refit && trip.points.length && !map.getBounds().contains(lastLatLng(trip))) map.panTo(lastLatLng(trip));
  drawnFor = id;
  drawPlanOnMap();
  drawGraph();
  renderReplay(replay.active ? 0 : undefined); // (0: the turn of a heading-up map is not snapped by a refresh)
}

const lastLatLng = (trip) => {
  const p = trip.points[trip.points.length - 1];
  return [p.la, p.lo];
};

/** The Circuits view is opening: whatever else was open steps aside (the view itself does the rest). */
function leaveForCircuits() {
  routeView?.hide();
  leaveViews();
}

/** The Route view is opening. */
function leaveForRoute() {
  circuitsView?.hide();
  leaveViews();
}

function leaveViews() {
  stopReplay();
  state.selected = null;
  state.detail = null;
  drawnFor = null;
  for (const id of ["detail", "overview", "stats", "compare-view", "placeholder"]) $(id).hidden = true;
  $("all").setAttribute("aria-pressed", "false");
  $("stats-btn").setAttribute("aria-pressed", "false");
  for (const b of $("list").querySelectorAll(".item")) b.setAttribute("aria-current", "false");
}

function showNothing() {
  stopReplay();
  fittedFor = null;
  state.selected = null;
  state.detail = null;
  document.body.dataset.view = "list";
  $("detail").hidden = true;
  $("overview").hidden = true;
  $("stats").hidden = true;
  $("compare-view").hidden = true;
  hideTools();
  $("all").setAttribute("aria-pressed", "false");
  $("stats-btn").setAttribute("aria-pressed", "false");
  $("placeholder").hidden = false;
  if (!EMBED) history.replaceState(null, "", location.pathname);
}

function drawHeader() {
  const trip = state.detail;
  $("title").textContent = tripTitle(trip);
  const bits = [when(trip.header.ts)];
  if (trip.header.vehicle) bits.push(trip.header.vehicle);
  if (trip.end) bits.push(`ended: ${ENDED[trip.end.reason] ?? trip.end.reason}`);
  else bits.push("still being recorded");
  $("subtitle").textContent = bits.join(" · ");
  const recording = !trip.end;
  $("actions").hidden = !state.client.local || recording;
  drawEditButtons(trip);
  $("star").textContent = trip.starred ? "\u2605 Starred" : "\u2606 Star";
  $("star").setAttribute("aria-pressed", trip.starred ? "true" : "false");
  const id = encodeURIComponent(trip.header.id);
  $("export-gpx").href = `/api/triplog/${id}/gpx`;
  $("export-kml").href = `/api/triplog/${id}/kml`;
  $("export-csv").href = `/api/triplog/${id}/csv`;
  $("export-share").href = `/api/triplog/${id}/share`;
  $("export-share").hidden = !trip.end; // (a trip being recorded is shared once it has ended)
  drawTags();
  drawPrice();
}

const impactPins = new Map(); // impact time -> its marker on the map, so a row in the list can point at it

/** The list of impacts under the figures: when, how hard, where. Clicking one shows it on the map. */
function drawImpacts() {
  const trip = state.detail;
  const hits = trip ? impactsOf(trip.stats, state.rule) : [];
  $("impacts").hidden = hits.length === 0;
  const list = $("im-list");
  list.replaceChildren();
  for (const hit of hits) {
    const row = impactRow(hit);
    const li = h("li", "", list);
    const b = h("button", "im-row", li);
    b.type = "button";
    h("span", "wp-time", b, formatDuration(row.t));
    h("span", "im-figures", b, row.figures);
    h("span", "wp-at", b, row.at);
    b.addEventListener("click", () => {
      const pin = impactPins.get(hit.t);
      if (!pin || !map) return;
      map.setView(pin.getLatLng(), Math.max(map.getZoom(), 16), { animate: false });
      pin.openTooltip();
    });
  }
}

/** The list of waypoints under the figures: when, where, and a note you can write (on a finished trip, on this computer). */
function drawWaypoints() {
  const trip = state.detail;
  const rows = trip ? waypointRows(trip) : [];
  const section = $("waypoints");
  section.hidden = rows.length === 0;
  const list = $("wp-list");
  if (document.activeElement?.closest?.("#wp-list")) return; // (a note is being typed: a redraw must not take it away)
  list.replaceChildren();
  const editable = state.client.local && Boolean(trip?.end);
  for (const wp of rows) {
    const li = h("li", "", list);
    li.dataset.t = String(wp.t);
    h("span", "wp-time", li, formatDuration(wp.t));
    h("span", "wp-at", li, wp.at || "");
    if (editable) {
      const input = h("input", "", li);
      input.type = "text";
      input.maxLength = 200;
      input.value = wp.text;
      input.placeholder = "Add a note";
      input.setAttribute("aria-label", `Note for the waypoint at ${formatDuration(wp.t)}`);
      let saved = wp.text;
      const save = async () => {
        if (input.value === saved) return;
        const text = input.value;
        try {
          await api.noteWaypoint(trip.header.id, wp.t, text);
          saved = text;
          await select(trip.header.id, { refit: false });
        } catch (e) {
          note(`Could not save the note: ${e.message}`, "error");
        }
      };
      input.addEventListener("keydown", (e) => {
        if (e.key === "Enter") input.blur();
      });
      input.addEventListener("blur", save);
    } else {
      h("span", "wp-text", li, wp.text);
    }
  }
}

function drawTiles() {
  const box = $("tiles");
  box.replaceChildren();
  for (const tile of statTiles(state.detail, state.prefs, state.rule)) {
    const d = h("div", `tile ${tile.tone}`.trim(), box);
    d.dataset.key = tile.key;
    h("span", "tile-label muted", d, tile.label);
    h("span", "tile-value", d, tile.value);
    if (tile.sub) h("span", "tile-sub muted", d, tile.sub);
  }
}

// ---- the map ---------------------------------------------------------------------------------------------------------

function ensureMap() {
  if (map || typeof L === "undefined") return;
  map = L.map("map", { preferCanvas: true, zoomControl: true, worldCopyJump: false });
  map.attributionControl.setPrefix('<a href="https://leafletjs.com" target="_blank" rel="noopener noreferrer">Leaflet</a>');
  trackLayer = L.layerGroup().addTo(map);
  setBase(currentBase);
  map.on("moveend", updateRecentre);
  $("recentre").addEventListener("click", () => {
    if (!replay.marker || !map) return;
    $("follow").checked = true; // (centring on a car that is driving away would not last: it follows as well)
    map.setView(replay.marker.getLatLng(), map.getZoom(), { animate: true });
  });
}

/** The "Re-centre" button shows only while the car is on its way but off the part of the map you are looking at. */
function updateRecentre() {
  $("recentre").hidden = !(map && replay.marker && !rotation.on && !map.getBounds().contains(replay.marker.getLatLng()));
}

/** The posted speed limit where the car is, as a road sign on the map; red and flashing while the car is over it. `ms`: the limit in
 * metres per second, or null when none is posted there (a limit the game only guessed is never shown, as it is never counted). */
function drawLimitSign(ms, over) {
  const sign = $("limit-sign");
  sign.hidden = ms === null;
  if (ms === null) return;
  const l = speedText(ms, state.prefs);
  sign.firstChild.textContent = l.value;
  sign.setAttribute("aria-label", `Speed limit ${l.value} ${l.label}`);
  sign.classList.toggle("over", Boolean(over));
}

function tileLayerFor(name) {
  const base = BASES[name];
  return L.tileLayer(base.url, { maxNativeZoom: base.maxNativeZoom, maxZoom: base.maxZoom, attribution: base.attribution, crossOrigin: false, referrerPolicy: "origin" });
}

/** Street or satellite, on whichever maps exist (the trip's and the all-trips one share the choice). */
function setBase(name) {
  currentBase = name;
  if (map) {
    if (baseLayer) map.removeLayer(baseLayer);
    baseLayer = tileLayerFor(name).addTo(map);
    baseLayer.bringToBack();
  }
  if (overview.map) {
    if (overview.base) overview.map.removeLayer(overview.base);
    overview.base = tileLayerFor(name).addTo(overview.map);
    overview.base.bringToBack();
  }
  if (cmp.map) {
    if (cmp.base) cmp.map.removeLayer(cmp.base);
    cmp.base = tileLayerFor(name).addTo(cmp.map);
    cmp.base.bringToBack();
  }
  circuitsView?.setBase(name);
  routeView?.setBase(name);
  for (const b of document.querySelectorAll(".base button")) b.setAttribute("aria-pressed", String(b.dataset.base === name));
  try {
    localStorage.setItem(BASE_KEY, name);
  } catch {
    /* a browser that will not remember is fine */
  }
}

function drawMap(fit) {
  ensureMap();
  if (!map) {
    $("map").textContent = "The map could not load.";
    return;
  }
  map.invalidateSize();
  trackLayer.clearLayers();
  cursor = null;
  const trip = state.detail;
  const points = trip.points;
  if (!points.length) {
    // A trip that has just begun has no points yet. If the car's live position is known, go there now, so the map is not left
    // looking at wherever the last trip was (the new trip is fitted properly once its first points arrive).
    const here = !rotation.on && fit ? feedHere() : null;
    if (here) map.setView([here.lat, here.lon], Math.max(view.zoom, 15), { animate: false });
    return;
  }
  const top = colourScaleTop(points);

  // under the coloured line: a dark edge so it shows on satellite and street alike, and a red one where the car was over a posted limit
  const all = points.map((p) => [p.la, p.lo]);
  L.polyline(all, { color: "#000", weight: 8, opacity: 0.55, lineCap: "round", lineJoin: "round", interactive: false }).addTo(trackLayer);
  for (const [t0, t1] of trip.stats?.limits?.spans ?? []) {
    const stretch = points.filter((p) => p.t >= t0 - 1 && p.t <= t1 + 1).map((p) => [p.la, p.lo]);
    if (stretch.length > 1) L.polyline(stretch, { color: "#ff3b30", weight: 11, opacity: 0.95, lineCap: "round", lineJoin: "round", interactive: false }).addTo(trackLayer);
  }
  for (const run of routeRuns(points, top)) L.polyline(run.coords, { color: RAMP[run.bucket], weight: 5, opacity: 1, lineCap: "round", lineJoin: "round", interactive: false }).addTo(trackLayer);

  for (const stop of trip.stats?.stops ?? []) L.circleMarker([stop.la, stop.lo], { radius: 6, color: "#fff", weight: 2, fillColor: "#8e99a8", fillOpacity: 1 }).bindTooltip(stopText(stop)).addTo(trackLayer);
  impactPins.clear();
  for (const impact of impactsOf(trip.stats, state.rule)) {
    const icon = L.divIcon({ className: "", html: '<span class="impact-pin" aria-hidden="true">!</span>', iconSize: [24, 24], iconAnchor: [12, 12] });
    const lines = impactDetails(impact, points, state.prefs);
    const words = () => { // (a fresh element each time: Leaflet keeps the one it is given)
      const box = document.createElement("div");
      box.className = "impact-tip";
      lines.forEach((line, i) => box.appendChild(Object.assign(document.createElement(i === 0 ? "strong" : "div"), { textContent: line })));
      return box;
    };
    impactPins.set(impact.t, L.marker([impact.la, impact.lo], { icon, zIndexOffset: 1000, alt: impactText(impact) }).bindTooltip(words, { direction: "top", offset: [0, -10] }).bindPopup(words, { closeButton: true }).addTo(trackLayer));
  }
  for (const wp of waypointRows(trip)) {
    if (!Number.isFinite(wp.la) || !Number.isFinite(wp.lo)) continue;
    const pin = L.circleMarker([wp.la, wp.lo], { radius: 8, color: "#fff", weight: 2, fillColor: "#b36bff", fillOpacity: 1 }).bindTooltip(wp.text || `Waypoint${wp.at ? ` at ${wp.at}` : ""}`).addTo(trackLayer);
    pin.on("click", () => document.querySelector(`#wp-list li[data-t="${wp.t}"] input`)?.focus());
  }
  for (const refuel of trip.stats?.refuels ?? []) L.circleMarker([refuel.la, refuel.lo], { radius: 7, color: "#fff", weight: 2, fillColor: "#ffc83d", fillOpacity: 1 }).bindTooltip(refuelText(refuel, trip.header?.energy, state.prefs)).addTo(trackLayer);
  const first = points[0];
  const last = points[points.length - 1];
  L.circleMarker([first.la, first.lo], { radius: 8, color: "#fff", weight: 3, fillColor: "#3ddc84", fillOpacity: 1 }).bindTooltip("Start").addTo(trackLayer);
  if (trip.end) L.circleMarker([last.la, last.lo], { radius: 8, color: "#fff", weight: 3, fillColor: "#ff4d4d", fillOpacity: 1 }).bindTooltip("End").addTo(trackLayer);
  else if (!replay.live) L.circleMarker([last.la, last.lo], { radius: 8, color: "#fff", weight: 3, fillColor: "#35c2ff", fillOpacity: 1 }).bindTooltip("Now").addTo(trackLayer);

  // Fit the map to the trip the first time there is something to fit (not only when it is opened: it may have been opened empty).
  if ((fit || fittedFor !== trip.header.id) && !rotation.on) {
    map.fitBounds(L.latLngBounds(all), { padding: [30, 30], maxZoom: 17 });
    fittedFor = trip.header.id;
  }
  drawLegend(top);
}

function drawLegend(top) {
  const box = $("legend");
  box.replaceChildren();
  const speed = resolveUnit("speed", state.prefs);
  h("span", "", box, "0");
  const bar = h("span", "ramp", box);
  for (const colour of RAMP) h("span", "", bar).style.background = colour;
  h("span", "", box, `${Math.round(speed.convert(top))} ${speed.label}`);
}

// ---- the graph -------------------------------------------------------------------------------------------------------

function drawGraph() {
  const host = $("graph");
  host.replaceChildren();
  graphRef = null;
  const trip = state.detail;
  if (!trip || trip.points.length < 2) return;
  const width = Math.max(240, host.clientWidth);
  const speedUnit = resolveUnit("speed", state.prefs);
  const distUnit = resolveUnit("distance", state.prefs);
  const g = graphModel(trip, { width, height: GRAPH_HEIGHT, speed: speedUnit.convert, distance: distUnit.convert });

  const svg = s("svg", { viewBox: `0 0 ${width} ${GRAPH_HEIGHT}`, width, height: GRAPH_HEIGHT, role: "img", "aria-label": "Speed and altitude along the trip" }, host);
  for (const t of g.yTicks) {
    s("line", { x1: g.pad.l, x2: width - g.pad.r, y1: t.y, y2: t.y, class: "grid" }, svg);
    s("text", { x: g.pad.l - 5, y: t.y + 4, class: "axis end" }, svg, t.label);
  }
  for (const t of g.xTicks) s("text", { x: t.x, y: GRAPH_HEIGHT - 5, class: "axis mid" }, svg, t.label);
  s("text", { x: width - g.pad.r, y: GRAPH_HEIGHT - 5, class: "axis end unit" }, svg, distUnit.label);
  if (g.altitude) s("path", { d: g.altitude.area, class: "alt" }, svg);
  for (const o of g.over) s("rect", { x: o.x0, y: g.pad.t, width: Math.max(2, o.x1 - o.x0), height: g.inner.h, class: "over" }, svg);
  for (const l of g.limits) s("line", { x1: l.x0, x2: l.x1, y1: l.y, y2: l.y, class: "limit" }, svg);
  s("path", { d: g.speedLine, class: "speed" }, svg);
  const rule = s("line", { y1: g.pad.t, y2: g.pad.t + g.inner.h, class: "rule", visibility: "hidden" }, svg);
  graphRef = { g, rule: s("line", { y1: g.pad.t, y2: g.pad.t + g.inner.h, class: "replay-rule", visibility: "hidden" }, svg) };
  const speedWord = speedUnit.label;
  $("graphkey").textContent = `Speed (${speedWord}) along the trip${g.altitude ? ", with the height of the road behind it" : ""}${g.limits.length ? ". The orange line is the posted limit" : ""}${g.over.length ? ", red where the car was over it" : ""}.`;

  const tip = $("tip");
  const hide = () => {
    rule.setAttribute("visibility", "hidden");
    tip.hidden = true;
    if (cursor && map) {
      map.removeLayer(cursor);
      cursor = null;
    }
  };
  svg.addEventListener("pointerleave", hide);
  svg.addEventListener("pointermove", (e) => {
    const box = svg.getBoundingClientRect();
    const px = e.clientX - box.left;
    const here = sampleAtDistance(trip.points, g.dFromX(px));
    if (!here) return;
    rule.setAttribute("x1", px);
    rule.setAttribute("x2", px);
    rule.setAttribute("visibility", "visible");
    const sp = speedText(here.s ?? 0, state.prefs);
    const parts = [`${sp.value} ${sp.label}`, distanceString(here.d), formatDuration(here.t)];
    if (Number.isFinite(here.al)) parts.push(`${Math.round(here.al)} m up`);
    tip.textContent = parts.join(" · ");
    tip.hidden = false;
    tip.style.left = `${Math.min(Math.max(px, 90), width - 90)}px`;
    if (map) {
      if (!cursor) cursor = L.circleMarker([here.la, here.lo], { radius: 7, color: "#000", weight: 2, fillColor: "#fff", fillOpacity: 1, interactive: false }).addTo(map);
      else cursor.setLatLng([here.la, here.lo]);
    }
  });
}


// ---- replay ----------------------------------------------------------------------------------------------------------------
// A car marker that drives the route again: a slider to go anywhere in the trip, play and pause, 1x to 64x. The clock is the
// trip's own time (seconds into it); the position, speed and distance at that time are interpolated from the points.

const tripDuration = () => state.detail?.points?.at(-1)?.t ?? 0;
const replayPossible = () => Boolean(state.detail && state.detail.points.length > 1);


function connectFeed() {
  if (SHARED || feed.ws || typeof WebSocket === "undefined") return;
  const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws?feed=geo`);
  feed.ws = ws;
  ws.onmessage = (event) => {
    let msg;
    try {
      msg = JSON.parse(event.data);
    } catch {
      return;
    }
    if (msg.type === "quit") {
      if (!EMBED) window.close(); // ThrashDash is closing and was asked to take its windows with it (a map inside a dashboard goes with its dashboard)
      return;
    }
    if (msg.type === "trip") {
      tick(); // a trip started or ended: look at the list now (a page behind the game has its timers slowed, but a message still arrives at once)
      return;
    }
    if (msg.type !== "geo" || !Number.isFinite(msg.lat) || !Number.isFinite(msg.lon) || !Number.isFinite(msg.rt)) return;
    const now = performance.now() / 1000;
    const newest = feed.samples[feed.samples.length - 1];
    if (newest && msg.rt < newest.rt - 1) {
      feed.samples = []; // the game side started its clock again
      feed.offsets = [];
    }
    feed.samples.push(msg);
    routeView?.drawCar(); // (the car on the Route view's map follows the live position)
    feed.offsets.push(now - msg.rt);
    if (feed.samples.length > FEED_KEEP) feed.samples.shift();
    if (feed.offsets.length > 30) feed.offsets.shift();
    feed.last = now;
  };
  ws.onclose = () => {
    feed.ws = null;
    feed.samples = [];
    feed.offsets = [];
    setTimeout(connectFeed, 1000); // (kept open for as long as the page is: it carries trip starts as well as the car's position)
  };
}

/** The car now, from the live positions, or null when none are arriving (the recorded points are used then). */
function feedHere(now = performance.now() / 1000) {
  if (!feed.samples.length || now - feed.last > FEED_STALE_S) return null;
  // (the least delay a position has taken to arrive is the best estimate of the gap between the game's clock and this page's)
  const offset = Math.min(...feed.offsets);
  return interpolateFeed(feed.samples, now - offset - FEED_DELAY_S);
}

/** Draw the route between the newest recorded point and the car, so the line reaches the car and not where it was a second ago. */
function updateTail(at) {
  const trip = state.detail;
  if (!trip || trip.end || !trackLayer || !trip.points.length) return;
  const last = trip.points[trip.points.length - 1];
  const coords = [[last.la, last.lo], at];
  if (!liveTail || !trackLayer.hasLayer(liveTail)) {
    liveTail = L.polyline(coords, { color: "#35c2ff", weight: 5, opacity: 1, lineCap: "round", lineJoin: "round", interactive: false }).addTo(trackLayer);
  } else {
    liveTail.setLatLngs(coords);
  }
}

/** `dt`: real seconds since the last frame while playing; none for a jump (a drag of the slider), where the turn snaps. */
function renderReplay(dt) {
  const possible = replayPossible();
  const duration = tripDuration();
  for (const id of ["play", "scrub", "rate", "follow", "headup"]) $(id).disabled = !possible;
  $("scrub").value = duration ? Math.round((replay.t / duration) * 1000) : 0;
  $("clock").textContent = clockText(replay.t, duration);
  $("play").textContent = replay.playing || replay.live ? "Pause" : replay.active && replay.t >= duration ? "Replay" : "Play";
  $("golive").hidden = !(state.detail && !state.detail.end && !replay.live); // (only for a trip being driven, once you have left the live view)
  let shown = possible && replay.active ? replayReadout(state.detail, replay.t, state.prefs) : null;
  const live = replay.live && shown?.here ? feedHere() : null;
  if (live) shown = liveReadout(state.detail, live, state.prefs); // (the car's own position, this moment)
  $("readout").textContent = shown ? shown.text : "";
  $("readout").classList.toggle("over", Boolean(shown?.over));
  if (!shown?.here) {
    if (rotation.on) exitRotation(true);
    if (replay.marker && map) map.removeLayer(replay.marker);
    replay.marker = null;
    drawLimitSign(null);
    updateRecentre();
    graphRef?.rule.setAttribute("visibility", "hidden");
    return;
  }
  const at = [shown.here.la, shown.here.lo];
  if (live) updateTail(at);
  if (!replay.marker && map) {
    replay.marker = L.marker(at, { icon: L.divIcon({ className: "car", html: "<span>\u25B2</span>", iconSize: [22, 22] }), interactive: false, keyboard: false, zIndexOffset: 1000 }).addTo(map);
  }
  replay.marker?.setLatLng(at);
  if ($("headup").checked && map) {
    if (!rotation.on) enterRotation(at, shown.here.h);
    rotation.bearing = turnToward(rotation.bearing, shown.here.h, dt);
    turnMap(at);
  } else if (rotation.on) {
    exitRotation(true);
  }
  const arrow = replay.marker?.getElement()?.firstChild;
  // The marker sits inside the map, so a turned map turns it too. North up, it is drawn at the car's heading. Heading up, the map is
  // turned by -bearing and the marker by +bearing, which cancel: the tip points straight up whatever the map is doing.
  const turn = rotation.on ? rotation.bearing : shown.here.h;
  if (arrow && Number.isFinite(turn)) arrow.style.transform = `rotate(${turn}deg)`;
  if (map && !rotation.on && $("follow").checked && !map.getBounds().pad(-0.25).contains(at)) map.setView(at, map.getZoom(), { animate: false });
  const limitT = live ? state.detail.points[state.detail.points.length - 1]?.t : replay.t;
  drawLimitSign(limitT === undefined ? null : limitAt(state.detail, limitT), shown.over);
  updateRecentre();
  if (graphRef) {
    const x = graphRef.g.x(shown.here.d);
    graphRef.rule.setAttribute("x1", x);
    graphRef.rule.setAttribute("x2", x);
    graphRef.rule.setAttribute("visibility", "visible");
  }
}

function frame(now) {
  if (!replay.playing && !replay.live) return;
  const dt = (now - replay.last) / 1000;
  replay.last = now;
  if (replay.live) {
    replay.t = advanceLive(replay.t, dt, tripDuration(), LIVE_DELAY_S);
  } else {
    const next = advanceReplay(replay.t, dt, replay.rate, tripDuration());
    replay.t = next.t;
    if (next.ended) replay.playing = false;
  }
  renderReplay(Math.min(dt, 0.25));
  if (replay.playing || replay.live) replay.raf = requestAnimationFrame(frame);
}

function ensureLoop() {
  cancelAnimationFrame(replay.raf);
  replay.last = performance.now();
  replay.raf = requestAnimationFrame(frame);
}

function startReplay() {
  if (!replayPossible()) return;
  replay.active = true;
  replay.live = false;
  replay.wantLive = false;
  if (replay.t >= tripDuration()) replay.t = 0;
  replay.playing = true;
  ensureLoop();
  renderReplay();
}

function pauseReplay() {
  replay.playing = false;
  replay.live = false; // (pausing the live view holds the car where it is; "Go live" returns)
  replay.wantLive = false;
  cancelAnimationFrame(replay.raf);
  renderReplay();
}

/** Follow a trip being driven: the car on the map, a couple of seconds behind the newest point, moving as it drives. */
function startLive(trip) {
  if (trip.points.length < 2) return; // (nothing to follow yet: the next refresh tries again)
  connectFeed(); // the car's live position (ten a second): the page reads it for as long as it follows a trip
  replay.wantLive = false;
  replay.active = true;
  replay.live = true;
  replay.playing = false;
  replay.t = advanceLive(NaN, 0, trip.points[trip.points.length - 1].t, LIVE_DELAY_S);
  ensureLoop();
}

/** Back to the start with nothing shown (another trip was opened, or none). */
function stopReplay() {
  replay.playing = false;
  replay.live = false;
  replay.wantLive = false;
  replay.active = false;
  replay.t = 0;
  cancelAnimationFrame(replay.raf);
  if (rotation.on) exitRotation(false);
  if (replay.marker && map) map.removeLayer(replay.marker);
  replay.marker = null;
}

let liveBusy = false;

/** About once a second while following: fetch what the trip has gained, so the car has points to move along without waiting for the page's slower full refresh. */
async function pullLive() {
  const trip = state.detail;
  if (!replay.live || liveBusy || !trip || document.visibilityState !== "visible") return;
  liveBusy = true;
  try {
    const after = trip.points.length ? trip.points[trip.points.length - 1].t : -1;
    const update = await api.liveTrip(trip.header.id, after);
    if (state.detail !== trip || !replay.live) return; // (another trip was opened, or the live view left, meanwhile)
    if (update.end) {
      show(await api.getTrip(trip.header.id), false); // it ended: the whole page changes (the figures, the name), so draw it afresh
      return;
    }
    if (update.points.length) {
      const before = trip.points[trip.points.length - 1];
      trip.points.push(...update.points);
      // (the route's new stretch is drawn at once; the page's full refresh, every few seconds, redraws it coloured)
      const stretch = [...(before ? [before] : []), ...update.points].map((p) => [p.la, p.lo]);
      if (trackLayer && stretch.length > 1) L.polyline(stretch, { color: "#35c2ff", weight: 5, opacity: 1, lineCap: "round", lineJoin: "round", interactive: false }).addTo(trackLayer);
    }
    trip.info.push(...update.info);
    trip.events.push(...update.events);
  } catch {
    /* the next one tries again */
  } finally {
    liveBusy = false;
  }
}

function setupReplay() {
  const rate = $("rate");
  for (const r of REPLAY_RATES) {
    const o = h("option", "", rate, `${r}\u00D7`);
    o.value = String(r);
  }
  rate.value = String(replay.rate);
  rate.addEventListener("change", () => (replay.rate = Number(rate.value)));
  $("play").addEventListener("click", () => (replay.playing || replay.live ? pauseReplay() : startReplay()));
  $("golive").addEventListener("click", () => {
    if (state.detail && !state.detail.end) {
      startLive(state.detail);
      renderReplay();
    }
  });
  setInterval(pullLive, LIVE_POLL_MS);
  $("scrub").addEventListener("input", () => {
    if (!replayPossible()) return;
    replay.active = true;
    replay.live = false; // (dragging the slider leaves the live view)
    replay.wantLive = false;
    replay.t = (Number($("scrub").value) / 1000) * tripDuration();
    renderReplay();
  });
  document.addEventListener("keydown", (e) => {
    const t = e.target;
    const typing = t && ((t.tagName === "INPUT" && t.type === "text") || t.tagName === "BUTTON" || t.tagName === "SELECT" || t.tagName === "TEXTAREA");
    if ((e.code !== "Space" && e.key !== " ") || typing || e.ctrlKey || e.altKey || e.metaKey || !replayPossible() || $("detail").hidden) return;
    e.preventDefault();
    if (replay.playing || replay.live) pauseReplay();
    else startReplay();
  });
}


// ---- all trips on one map ---------------------------------------------------------------------------------------------------
// Every trip's route (thinned by the relay) in one colour, so the roads driven most often are the brightest, with filters by
// when, vehicle and country, and totals. Clicking a route opens that trip.

const isOverview = () => document.body.dataset.view === "all";

async function showOverview() {
  stopReplay();
  state.selected = null;
  state.detail = null;
  drawnFor = null;
  document.body.dataset.view = "all";
  history.replaceState(null, "", "#all");
  $("detail").hidden = true;
  $("placeholder").hidden = true;
  $("stats").hidden = true;
  $("compare-view").hidden = true;
  hideTools();
  $("stats-btn").setAttribute("aria-pressed", "false");
  $("overview").hidden = false;
  $("all").setAttribute("aria-pressed", "true");
  for (const b of $("list").querySelectorAll(".item")) b.setAttribute("aria-current", "false");
  if (!overview.map && typeof L !== "undefined") {
    overview.map = L.map("ovmap", { preferCanvas: true, worldCopyJump: false });
    overview.map.attributionControl.setPrefix('<a href="https://leafletjs.com" target="_blank" rel="noopener noreferrer">Leaflet</a>');
    overview.layer = L.layerGroup().addTo(overview.map);
    setBase(currentBase);
    overview.map.setView([0, 0], 2);
  }
  overview.map?.invalidateSize();
  await loadRoutes(true);
}

async function loadRoutes(fit) {
  try {
    overview.routes = (await api.listRoutes()).trips;
  } catch (e) {
    $("ov-sub").textContent = `Could not load the routes: ${e.message}`;
    return;
  }
  fillFilters();
  drawOverview(fit);
}

function fillFilters() {
  const options = filterOptions(overview.routes);
  const fill = (id, entries, current) => {
    const sel = $(id);
    sel.replaceChildren();
    for (const [value, label] of entries) {
      const o = h("option", "", sel, label);
      o.value = value;
    }
    sel.value = entries.some(([v]) => v === current) ? current : entries[0][0]; // a choice that no longer exists falls back to "all"
    return sel.value;
  };
  overview.filters.when = fill("f-when", WHEN_OPTIONS.map((w) => [w.id, w.label]), overview.filters.when);
  overview.filters.vehicle = fill("f-vehicle", [["", "All vehicles"], ...options.vehicles.map((v) => [v, v])], overview.filters.vehicle);
  overview.filters.country = fill("f-country", [["", "All countries"], ...options.countries.map((c) => [c, c])], overview.filters.country);
  overview.filters.tag = fill("f-tag", [["", "All tags"], ...options.tags.map((t) => [t, t])], overview.filters.tag);
  $("f-tag").parentElement.hidden = options.tags.length === 0; // (nothing to filter by until a trip has a tag)
}

function drawOverview(fit) {
  if (!overview.map) return;
  const shown = filterRoutes(overview.routes, overview.filters);
  overview.layer.clearLayers();
  for (const route of shown) {
    if (route.line.length < 2) continue;
    const label = route.title || route.name || "Trip";
    const line = L.polyline(route.line, { color: route.recording ? "#3ddc84" : "#35c2ff", weight: 3, opacity: 0.55, lineCap: "round", lineJoin: "round" }).addTo(overview.layer);
    line.bindTooltip(`${label} \u00B7 ${when(route.ts)} \u00B7 ${distanceString(route.dist)}`, { sticky: true });
    line.on("mouseover", () => line.setStyle({ weight: 6, opacity: 1, color: "#fff" }).bringToFront());
    line.on("mouseout", () => line.setStyle({ weight: 3, opacity: 0.55, color: route.recording ? "#3ddc84" : "#35c2ff" }));
    line.on("click", () => select(route.id));
  }
  $("ov-sub").textContent = shown.length === overview.routes.length ? `${shown.length} ${shown.length === 1 ? "trip" : "trips"}` : `${shown.length} of ${overview.routes.length} trips`;
  const box = $("ov-tiles");
  box.replaceChildren();
  for (const tile of overviewTiles(shown, state.prefs)) {
    const d = h("div", "tile", box);
    d.dataset.key = tile.key;
    h("span", "tile-label muted", d, tile.label);
    h("span", "tile-value", d, tile.value);
  }
  const bounds = routesBounds(shown);
  if (fit && bounds) overview.map.fitBounds(bounds, { padding: [30, 30], maxZoom: 16 });
}

function setupOverview() {
  $("all").addEventListener("click", () => (isOverview() ? showNothing() : showOverview()));
  $("ov-back").addEventListener("click", () => showNothing());
  for (const [id, key] of [["f-when", "when"], ["f-vehicle", "vehicle"], ["f-country", "country"], ["f-tag", "tag"]]) {
    $(id).addEventListener("change", () => {
      overview.filters[key] = $(id).value;
      drawOverview(true);
    });
  }
}


// ---- the stats view -----------------------------------------------------------------------------------------------------------
// Everything the finished trips add up to: totals, records (each opens its trip), each vehicle's economy, and distance by month.
// It is worked out here from the list the page already has (each trip carries its summary), filtered like the all-trips map.

const isStats = () => document.body.dataset.view === "stats";
const statsView = { filters: { when: "all", vehicle: "", country: "", tag: "" }, drawn: null, shown: [] };

async function showStats() {
  stopReplay();
  state.selected = null;
  state.detail = null;
  drawnFor = null;
  document.body.dataset.view = "stats";
  statsView.drawn = null;
  history.replaceState(null, "", "#stats");
  $("detail").hidden = true;
  $("overview").hidden = true;
  $("compare-view").hidden = true;
  hideTools();
  $("placeholder").hidden = true;
  $("stats").hidden = false;
  $("all").setAttribute("aria-pressed", "false");
  $("stats-btn").setAttribute("aria-pressed", "true");
  for (const b of $("list").querySelectorAll(".item")) b.setAttribute("aria-current", "false");
  drawStats();
}

function fillSelect(id, entries, current) {
  const sel = $(id);
  if ([...sel.options].map((o) => o.value).join("\u0001") !== entries.map((e) => e[0]).join("\u0001")) { // (left alone when unchanged, so an open choice list is not closed by a refresh)
    sel.replaceChildren();
    for (const [value, label] of entries) h("option", "", sel, label).value = value;
  }
  sel.value = entries.some(([v]) => v === current) ? current : entries[0][0]; // a choice that no longer exists falls back to "all"
  return sel.value;
}

const heightText = (m) => (state.prefs?.distance === "mi" ? `${Math.round(m * 3.28084)} ft` : `${Math.round(m)} m`);

function drawStats() {
  // (a drive imported from a GPX file is a real drive, not a game one: it is left out of the totals)
  const withCountries = state.trips.filter((t) => !t.imported).map((t) => ({ ...t, countries: t.stats?.countries ?? [] }));
  const options = filterOptions(withCountries);
  statsView.filters.when = fillSelect("sf-when", WHEN_OPTIONS.map((w) => [w.id, w.label]), statsView.filters.when);
  statsView.filters.vehicle = fillSelect("sf-vehicle", [["", "All vehicles"], ...options.vehicles.map((v) => [v, v])], statsView.filters.vehicle);
  statsView.filters.country = fillSelect("sf-country", [["", "All countries"], ...options.countries.map((c) => [c, c])], statsView.filters.country);
  statsView.filters.tag = fillSelect("sf-tag", [["", "All tags"], ...options.tags.map((t) => [t, t])], statsView.filters.tag);
  $("sf-tag").parentElement.hidden = options.tags.length === 0;
  const shown = filterRoutes(withCountries, statsView.filters);
  statsView.shown = shown; // (what Download as CSV takes: the trips the filters leave)
  const signature = JSON.stringify([statsView.filters, state.prefs, state.rule, shown.map((t) => [t.id, t.title, t.stats?.dist, t.stats?.name, t.tags, t.price])]);
  if (signature === statsView.drawn) return; // (nothing changed since it was last drawn: leave it alone, so a keyboard focus or a hover is not lost every refresh)
  statsView.drawn = signature;
  const s = lifetimeStats(shown, state.rule);
  $("st-sub").textContent = s.trips === 0 ? "No finished trips yet" : shown.length === state.trips.length ? `${s.trips} ${s.trips === 1 ? "trip" : "trips"}` : `${s.trips} of ${state.trips.length} trips`;

  const tiles = $("st-tiles");
  tiles.replaceChildren();
  const tile = (key, label, value, sub = "") => {
    const d = h("div", "tile", tiles);
    d.dataset.key = key;
    h("span", "tile-label muted", d, label);
    h("span", "tile-value", d, value);
    if (sub) h("span", "tile-sub muted", d, sub);
  };
  const dist = distanceText(s.dist, state.prefs);
  tile("trips", "Trips", String(s.trips), `${s.days} ${s.days === 1 ? "day" : "days"} of driving`);
  tile("distance", "Distance", `${dist.value} ${dist.label}`);
  tile("time", "Driving time", formatDuration(s.dur), `${formatDuration(s.moving)} moving`);
  if (s.countries.length) tile("countries", "Countries", String(s.countries.length), s.countries.length > 3 ? `${s.countries.slice(0, 3).join(", ")} and ${s.countries.length - 3} more` : s.countries.join(", "));
  if (s.towns) tile("places", "Places", String(s.towns), "towns and cities passed through");
  if (s.impacts) tile("impacts", "Impacts", String(s.impacts), "knocks that damaged the body");
  const economy = resolveEconomy(state.prefs);
  for (const [unit, used] of Object.entries(s.fuel)) {
    const e = formatEnergy(used, unit === "kWh" ? "electric" : "liquid", economy);
    if (e) tile(unit === "kWh" ? "energy" : "fuel", unit === "kWh" ? "Energy used" : "Fuel used", `${e.value.toFixed(e.value >= 10 ? 0 : 1)} ${e.unit}`);
  }
  // What it cost, by currency (a price is saved with each trip, so one that changed currency adds up in each). Trips with no price are left out
  // and counted, so the total is never mistaken for the whole.
  for (const c of s.cost.byCurrency) {
    const per = costPer100({ amount: c.amount }, c.dist, state.prefs);
    const label = s.cost.byCurrency.length > 1 ? `Fuel cost (${c.currency || "no currency"})` : "Fuel cost";
    const bits = [`${s.cost.priced} of ${s.cost.fuelTrips} ${s.cost.fuelTrips === 1 ? "trip" : "trips"} priced`];
    if (per) bits.push(`${moneyText(per.amount, c.currency)} per ${per.per}`);
    tile("cost", label, moneyText(c.amount, c.currency), bits.join(" \u00B7 "));
  }

  const records = $("st-records");
  records.replaceChildren();
  const rec = (name, label, text) => {
    const r = s.records[name];
    if (!r) return;
    const b = h("button", "", h("li", "", records));
    b.type = "button";
    b.dataset.record = name;
    h("span", "rec-what", b, label);
    h("span", "rec-value", b, text(r.value));
    h("span", "rec-trip", b, `${tripTitle(r.trip)} \u00B7 ${when(r.trip.ts)}`);
    b.addEventListener("click", () => select(r.id));
  };
  rec("longest", "Longest trip", (v) => `${distanceText(v, state.prefs).value} ${distanceText(v, state.prefs).label}`);
  rec("longestTime", "Longest drive", (v) => formatDuration(v));
  rec("top", "Top speed", (v) => `${speedText(v, state.prefs).value} ${speedText(v, state.prefs).label}`);
  rec("highest", "Highest point", heightText);
  rec("climb", "Biggest climb", (v) => `+${heightText(v)}`);
  rec("peakG", "Strongest force", (v) => `${v.toFixed(2)} g`);
  rec("countries", "Most countries in a trip", (v) => String(v));
  if (!records.children.length) h("li", "muted", records, "Records appear once a trip has finished.");

  const box = $("st-vehicles");
  box.replaceChildren();
  if (s.vehicles.length) {
    const table = h("table", "", box);
    const head = h("tr", "", h("thead", "", table));
    for (const [text, num] of [["Vehicle", false], ["Trips", true], ["Distance", true], ["Time", true], ["Used", true], ["Economy", true]]) h("th", num ? "num" : "", head, text);
    const body = h("tbody", "", table);
    for (const v of s.vehicles) {
      const row = h("tr", "", body);
      const kind = v.unit === "kWh" ? "electric" : "liquid";
      const d = distanceText(v.dist, state.prefs);
      const used = v.used > 0 ? formatEnergy(v.used, kind, economy) : null;
      const per = v.per100 != null ? formatConsumption(v.per100, kind, economy) : null;
      h("td", "", row, v.vehicle || "Unknown");
      h("td", "num", row, String(v.trips));
      h("td", "num", row, `${d.value} ${d.label}`);
      h("td", "num", row, formatDuration(v.dur));
      h("td", "num", row, used ? `${used.value.toFixed(used.value >= 10 ? 0 : 1)} ${used.unit}` : "\u2013");
      h("td", "num", row, per ? `${per.value.toFixed(1)} ${per.unit}` : "\u2013");
    }
  }

  const months = $("st-months");
  months.replaceChildren();
  const recent = s.months.slice(-12);
  const most = Math.max(1, ...recent.map((m) => m.dist));
  const years = new Set(recent.map((m) => m.key.slice(0, 4)));
  for (const m of recent) {
    const bar = h("div", "bar", months);
    const [y, mo] = m.key.split("-").map(Number);
    const d = distanceText(m.dist, state.prefs);
    bar.title = `${new Date(y, mo - 1).toLocaleString(undefined, { month: "long", year: "numeric" })}: ${d.value} ${d.label}, ${m.trips} ${m.trips === 1 ? "trip" : "trips"}`;
    h("i", "", bar).style.height = `${Math.max(2, Math.round((m.dist / most) * 100))}%`;
    h("span", "", bar, new Date(y, mo - 1).toLocaleString(undefined, years.size > 1 ? { month: "short", year: "2-digit" } : { month: "short" }));
  }
}

function setupStats() {
  $("stats-btn").addEventListener("click", () => (isStats() ? showNothing() : showStats()));
  $("st-back").addEventListener("click", () => showNothing());
  $("st-csv").addEventListener("click", () => {
    const blob = new Blob([statsCsv(statsView.shown, state.rule)], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = h("a", "", document.body);
    a.href = url;
    a.download = statsCsvName();
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 4000);
  });
  for (const [id, key] of [["sf-when", "when"], ["sf-vehicle", "vehicle"], ["sf-country", "country"], ["sf-tag", "tag"]]) {
    $(id).addEventListener("change", () => {
      statsView.filters[key] = $(id).value;
      drawStats();
    });
  }
}

// ---- heading up --------------------------------------------------------------------------------------------------------------
// The car stays in the middle of the map pointing up and the map turns round it. Leaflet cannot rotate, so the map is given a box
// larger than the one you see (its diagonal, so its corners still cover the box when turned), turned with CSS about its middle,
// and clipped by the box. A turned map cannot be dragged sensibly, so while it is on the map follows the car, and the zoom
// buttons, the wheel and the credit (which would turn away and fall outside the box) are handled by the page.

function sizeRotatingBox() {
  const box = $("mapbox");
  box.style.height = "";
  box.classList.remove("rotating");
  const r = box.getBoundingClientRect(); // (its upright size, from the stylesheet)
  box.style.height = `${r.height}px`;
  box.style.setProperty("--d", `${Math.ceil(Math.hypot(r.width, r.height))}px`);
  box.classList.add("rotating");
}

function enterRotation(at, heading) {
  sizeRotatingBox();
  rotation.on = true;
  rotation.bearing = Number.isFinite(heading) ? heading : 0;
  const credit = $("rot-attrib");
  credit.replaceChildren();
  credit.insertAdjacentHTML("beforeend", BASES[currentBase].attribution); // (a constant above: the credits the map's licences require)
  map.invalidateSize({ animate: false });
  map.setView(at, view.zoom, { animate: false }); // (the zoom of the loaded view, or the one last used here)
}

/** Put the car in the middle (by moving the map pane, which is cheap) and turn the map to its heading. */
function turnMap(at) {
  $("mapbox").style.setProperty("--rot", `${-rotation.bearing}deg`);
  const offset = map.latLngToContainerPoint(at).subtract(map.getSize().divideBy(2));
  if (offset.x || offset.y) map.panBy(offset, { animate: false });
}

function exitRotation(refit) {
  rotation.on = false;
  const box = $("mapbox");
  box.classList.remove("rotating");
  box.style.height = "";
  box.style.removeProperty("--d");
  box.style.removeProperty("--rot");
  map?.invalidateSize({ animate: false });
  const trip = state.detail;
  if (refit && map && trip?.points.length) map.fitBounds(L.latLngBounds(trip.points.map((p) => [p.la, p.lo])), { padding: [30, 30], maxZoom: 17 });
}

function setupHeadUp() {
  const box = $("headup");
  box.checked = stored(HEADUP_KEY) === "1";
  box.addEventListener("change", () => {
    try {
      localStorage.setItem(HEADUP_KEY, box.checked ? "1" : "0");
    } catch {
      /* a browser that will not remember is fine */
    }
    renderReplay();
  });
  const zoom = (by) => {
    if (!(map && rotation.on)) return;
    map.setZoom(map.getZoom() + by, { animate: false });
    view.zoom = map.getZoom(); // (what you zoom to while following is what a view saves)
  };
  $("rot-in").addEventListener("click", () => zoom(1));
  $("rot-out").addEventListener("click", () => zoom(-1));
  $("mapbox").addEventListener("wheel", (e) => {
    if (!rotation.on) return;
    e.preventDefault();
    if (e.timeStamp - rotation.lastWheel < 150) return; // (one notch of the wheel is a burst of events)
    rotation.lastWheel = e.timeStamp;
    zoom(e.deltaY < 0 ? 1 : -1);
  }, { passive: false });
  window.addEventListener("resize", () => {
    if (rotation.on) {
      sizeRotatingBox();
      map.invalidateSize({ animate: false });
    }
  });
}




// ---- the size of the maps ---------------------------------------------------------------------------------------------------------
// Drag the grip under a map, click smaller or bigger, or fill the window with the map (Esc goes back). The height is remembered by this
// browser, like the street / satellite choice. The Trips window's own size and place are saved with the window config instead.

const MAPH_KEYS = { detail: "thrashdash.trips.maph", overview: "thrashdash.trips.ovmaph" };
const MAPH_VARS = { detail: "--map-h", overview: "--ovmap-h" };
const MAPH_BOXES = { detail: "mapbox", overview: "ovmap" };

/** After a map's room changed: the maps take the room they now have, and the graph redraws for its width. */
function relayout() {
  if (rotation.on) sizeRotatingBox();
  map?.invalidateSize({ animate: false });
  overview.map?.invalidateSize({ animate: false });
  if (state.detail) drawGraph();
}

const mapHeight = (which) => Math.round($(MAPH_BOXES[which]).getBoundingClientRect().height);

// The height a person chose for each map is kept apart from the height it is shown at: the window may be shorter than the choice for a
// moment (while it opens, or when it is made smaller), and the map must grow back to the choice when the window does, not stay small.
const wantedMapHeight = { detail: null, overview: null };

/** Show a map at the height that was chosen for it, kept to the window as it is now. */
function applyMapHeight(which) {
  const height = mapHeightFor(wantedMapHeight[which], window.innerHeight);
  if (height === null) document.documentElement.style.removeProperty(MAPH_VARS[which]);
  else document.documentElement.style.setProperty(MAPH_VARS[which], `${height}px`);
}

function setMapHeight(which, px, remember = true) {
  const height = clampMapHeight(px, window.innerHeight);
  wantedMapHeight[which] = height;
  document.documentElement.style.setProperty(MAPH_VARS[which], `${height}px`);
  if (remember) {
    try {
      localStorage.setItem(MAPH_KEYS[which], String(height));
    } catch {
      /* a browser that will not remember is fine */
    }
  }
  relayout();
}

function resetMapHeight(which) {
  wantedMapHeight[which] = null;
  document.documentElement.style.removeProperty(MAPH_VARS[which]);
  try {
    localStorage.removeItem(MAPH_KEYS[which]);
  } catch {
    /* fine */
  }
  relayout();
}

function setFill(on) {
  document.body.classList.toggle("map-fill", on);
  for (const id of ["map-fill", "ov-fill"]) $(id).setAttribute("aria-pressed", String(on));
  requestAnimationFrame(relayout); // (after the page has been laid out again)
}

function setupMapSize() {
  if (EMBED) return;
  for (const which of Object.keys(MAPH_KEYS)) {
    const saved = Number(stored(MAPH_KEYS[which]));
    wantedMapHeight[which] = saved > 0 ? saved : null;
    applyMapHeight(which);
  }
  requestAnimationFrame(() => {
    for (const which of Object.keys(MAPH_KEYS)) applyMapHeight(which); // (once the window has been laid out: it may have had no height yet)
    relayout();
  });
  const grips = { detail: $("map-grip"), overview: $("ov-grip") };
  const buttons = { detail: ["map-smaller", "map-bigger", "map-fill"], overview: ["ov-smaller", "ov-bigger", "ov-fill"] };
  for (const which of Object.keys(grips)) {
    const [smaller, bigger, fill] = buttons[which];
    $(smaller).addEventListener("click", () => setMapHeight(which, mapHeight(which) - MAP_STEP_PX));
    $(bigger).addEventListener("click", () => setMapHeight(which, mapHeight(which) + MAP_STEP_PX));
    $(fill).addEventListener("click", () => setFill(!document.body.classList.contains("map-fill")));
    const grip = grips[which];
    let frame = 0;
    grip.addEventListener("pointerdown", (e) => {
      grip.setPointerCapture(e.pointerId);
      grip.classList.add("dragging");
      const from = e.clientY;
      const base = mapHeight(which);
      const move = (ev) => {
        cancelAnimationFrame(frame);
        frame = requestAnimationFrame(() => setMapHeight(which, base + ev.clientY - from, false));
      };
      const up = (ev) => {
        grip.classList.remove("dragging");
        grip.removeEventListener("pointermove", move);
        grip.removeEventListener("pointerup", up);
        grip.removeEventListener("pointercancel", up);
        setMapHeight(which, base + ev.clientY - from); // (and remembered, now it has stopped)
      };
      grip.addEventListener("pointermove", move);
      grip.addEventListener("pointerup", up);
      grip.addEventListener("pointercancel", up);
      e.preventDefault();
    });
    grip.addEventListener("dblclick", () => resetMapHeight(which));
    grip.addEventListener("keydown", (e) => {
      const step = { ArrowUp: -24, ArrowDown: 24 }[e.key];
      if (step) {
        e.preventDefault();
        setMapHeight(which, mapHeight(which) + step);
      } else if (e.key === "Home") {
        e.preventDefault();
        resetMapHeight(which);
      }
    });
  }
  document.addEventListener("keydown", (e) => {
    const typing = e.target && (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA" || e.target.tagName === "SELECT");
    if (e.key === "Escape" && document.body.classList.contains("map-fill") && !typing) setFill(false);
  });
  window.addEventListener("resize", () => {
    // (a window made smaller must not be left with a map taller than it, and one made taller gives the map back the height chosen for it)
    for (const which of Object.keys(MAPH_KEYS)) applyMapHeight(which);
    relayout();
  });
}

// ---- renaming and deleting ---------------------------------------------------------------------------------------------

/** Quit ThrashDash from this window (this computer only; a dashboard window has the same button). It asks again first. */
function setupQuit() {
  const button = $("quit");
  button.hidden = !state.client.local || EMBED || SHARED;
  let sure = false;
  button.addEventListener("click", async () => {
    if (!sure) {
      sure = true;
      button.textContent = "Really quit?";
      setTimeout(() => {
        sure = false;
        if (!button.disabled) button.textContent = "Quit";
      }, 4000);
      return;
    }
    button.disabled = true;
    button.textContent = "Quitting\u2026";
    try {
      await api.quit();
    } catch (e) {
      button.disabled = false;
      button.textContent = "Quit";
      sure = false;
      console.warn("ThrashDash: could not quit", e);
    }
  });
}

// ---- joining and cutting ------------------------------------------------------------------------------------------------------
// Two trips that a long stop split in two can be joined back into one, and one trip can be cut in two where the replay is. Neither
// rewrites anything: the new trips are new files, and the old ones go to the trash folder like a deleted trip.

/** What the open trip may be joined with, asked of the relay (which knows where the trips were): {prev, next}, each {id, ts, title} or null. */
const joinOptions = { id: null, prev: null, next: null };

/** The line under the trip's title that says what an edit did, or why it could not be done (shown until another trip is opened). */
function note(text, kind = "") {
  const el = $("edit-note");
  el.textContent = text;
  el.className = `edit-note ${kind}`.trim();
  el.hidden = !text;
}

function drawEditButtons(trip) {
  const finished = Boolean(trip.end);
  if (joinOptions.id !== trip.header.id) note("");
  joinOptions.id = trip.header.id;
  joinOptions.prev = joinOptions.next = null;
  for (const [id, label] of [["join-prev", "Join with previous"], ["join-next", "Join with next"]]) {
    $(id).hidden = true; // (shown once the relay says there is a trip that could be joined)
    resetSure($(id), label);
  }
  $("cut").hidden = !finished;
  $("make-circuit").hidden = !finished || trip.header.imported === "gpx";
  resetSure($("cut"), "Cut here");
  $("compare").hidden = !finished || state.trips.filter((t) => t.ended && t.id !== trip.header.id).length === 0;
  if (finished && state.client.local) loadJoinOptions(trip.header.id);
}

async function loadJoinOptions(id) {
  let options;
  try {
    options = await api.joinOptions(id);
  } catch {
    return; // (no joins offered: nothing is lost)
  }
  if (!state.detail || state.detail.header.id !== id) return; // (another trip was opened meanwhile)
  joinOptions.prev = options.prev;
  joinOptions.next = options.next;
  // A neighbour that cannot be joined is still shown, greyed out, and says why when asked: a button that comes and goes with no reason
  // looks like a fault.
  for (const [button, other, label] of [["join-prev", options.prev, "previous"], ["join-next", options.next, "next"]]) {
    const b = $(button);
    b.hidden = !other;
    if (!other) continue;
    const name = `${other.title || "Trip"}, ${when(other.ts)}`;
    b.classList.toggle("unavailable", !other.ok);
    b.setAttribute("aria-disabled", String(!other.ok));
    b.title = other.ok ? `Make one trip of this and the ${label} one: ${name}`
      : other.forceable ? `Does not look like one drive (${other.reason}), but you can join the ${label} one (${name}) anyway: press it twice`
      : `Cannot join with the ${label} one (${name}): ${other.reason}`;
  }
}

function resetSure(button, label) {
  clearTimeout(button._sureTimer);
  button.dataset.sure = "0";
  if (label !== undefined) button.dataset.label = label;
  button.textContent = button.dataset.label || button.textContent;
}

async function changeTrips(work, done, success) {
  try {
    const result = await work();
    await loadList();
    await select(done(result), { refit: true });
    note(success, "ok");
  } catch (e) {
    note(`Could not do that: ${e.message}`, "error");
  }
}

/**
 * A Join button. A join the relay says will work asks once more ("Really join?") and is done. One it says does not look like one drive
 * (hours apart, far apart, another car) is greyed out and says why when pressed, and the second press ("Join anyway?") does it on
 * purpose, for someone who knows the trips belong together. One that makes no sense at all (it cannot be forced) only says why.
 */
function setupJoinButton(id, side, label) {
  const button = $(id);
  button.dataset.label = button.textContent;
  button.addEventListener("click", async () => {
    const other = joinOptions[side];
    if (!other) return;
    const force = !other.ok;
    if (force && !other.forceable) {
      note(`Cannot join with the ${label} trip: ${other.reason}.`, "hint");
      return;
    }
    if (button.dataset.sure !== "1") {
      button.dataset.sure = "1";
      button.textContent = force ? "Join anyway?" : "Really join?";
      if (force) note(`${other.reason.charAt(0).toUpperCase()}${other.reason.slice(1)}. Press the button again to join them anyway.`, "hint");
      clearTimeout(button._sureTimer);
      button._sureTimer = setTimeout(() => resetSure(button), 4000);
      return;
    }
    resetSure(button);
    const [first, second] = side === "prev" ? [other.id, state.detail.header.id] : [state.detail.header.id, other.id];
    await changeTrips(() => api.joinTrips(first, second, force), (r) => r.id, force ? "Joined anyway: these two trips are now one, with the gap between them kept as a stop." : "Joined: these two trips are now one.");
  });
}

function setupEditing() {
  setupJoinButton("join-prev", "prev", "previous");
  setupJoinButton("join-next", "next", "next");
  const cutTime = () => (replay.active ? Math.round(replay.t) : null);
  $("cut").dataset.label = "Cut here";
  $("cut").addEventListener("click", async () => {
    const button = $("cut");
    const at = cutTime();
    if (at === null) {
      note("Press Play or move the slider to where you want to cut, then press Cut here.", "hint");
      return;
    }
    if (button.dataset.sure !== "1") {
      button.dataset.sure = "1";
      button.textContent = `Cut at ${formatDuration(at)}?`;
      clearTimeout(button._sureTimer);
      button._sureTimer = setTimeout(() => resetSure(button), 4000);
      return;
    }
    resetSure(button);
    await changeTrips(() => api.cutTrip(state.detail.header.id, at), (r) => r.ids[0], `Cut in two at ${formatDuration(at)}: this is the first part; the second is the next trip in the list.`);
  });
}

// ---- backing up and restoring all trips ------------------------------------------------------------------------------------------------

function setupBackup() {
  $("data").hidden = !state.client.local || EMBED || SHARED;
  $("import-gpx").addEventListener("click", () => $("import-gpx-file").click());
  $("import-gpx-file").addEventListener("change", async () => {
    const file = $("import-gpx-file").files[0];
    $("import-gpx-file").value = "";
    if (!file) return;
    const note = $("data-note");
    note.textContent = "Importing\u2026";
    try {
      const r = await api.importGpx(file);
      note.textContent = `${r.ids.length} ${r.ids.length === 1 ? "trip" : "trips"} imported${r.duplicates ? `, ${r.duplicates} already here` : ""}${r.skipped ? `, ${r.skipped} too short to be a trip` : ""}.`;
      await loadList();
      if (r.ids.length) await select(r.ids[0]);
    } catch (e) {
      note.textContent = `Could not import: ${e.message}`;
    }
  });
  $("restore").addEventListener("click", () => $("restore-file").click());
  $("restore-file").addEventListener("change", async () => {
    const file = $("restore-file").files[0];
    $("restore-file").value = "";
    if (!file) return;
    const note = $("data-note");
    note.textContent = "Restoring\u2026";
    try {
      const r = await api.restoreTrips(file);
      note.textContent = `${r.added} added, ${r.skipped} already here${r.rejected ? `, ${r.rejected} not used (not trip files)` : ""}.`;
      await loadList();
    } catch (e) {
      note.textContent = `Could not restore: ${e.message}`;
    }
  });
}

// ---- comparing two trips ------------------------------------------------------------------------------------------------------------
// The open trip (A) beside another (B), chosen here: both routes on a map, their figures in a table, and how far ahead or behind B
// was of A along the way (by distance driven, so it is exact only where both followed the same road).

const isCompare = () => document.body.dataset.view === "compare";
const cmp = { map: null, base: null, layer: null, a: null, b: null };

async function showCompare() {
  const a = state.detail;
  if (!a || !a.end) return;
  stopReplay();
  cmp.a = a;
  cmp.b = null;
  document.body.dataset.view = "compare";
  history.replaceState(null, "", "#compare");
  for (const id of ["detail", "overview", "stats", "placeholder"]) $(id).hidden = true;
  hideTools();
  $("compare-view").hidden = false;
  $("cmp-a").textContent = `${tripTitle(a)} (${when(a.header.ts)})`;
  $("cmp-body").hidden = true;
  $("cmp-empty").hidden = false;
  $("cmp-sub").textContent = "";
  // the others, the ones with the same name (the same route, driven again) first, then the newest
  const name = tripTitle(a);
  const others = state.trips.filter((t) => t.ended && t.id !== a.header.id);
  const same = (t) => tripTitle(t) === name;
  others.sort((x, y) => Number(same(y)) - Number(same(x)) || y.ts - x.ts);
  const pick = $("cmp-pick");
  pick.replaceChildren();
  h("option", "", pick, "Choose a trip\u2026").value = "";
  for (const t of others) h("option", "", pick, `${same(t) ? "\u2605 " : ""}${tripTitle(t)} \u00B7 ${when(t.ts)} \u00B7 ${distanceString(t.distance ?? 0)}`).value = t.id;
  pick.value = "";
}

async function loadCompare(id) {
  if (!id) {
    cmp.b = null;
    $("cmp-body").hidden = true;
    $("cmp-empty").hidden = false;
    return;
  }
  try {
    cmp.b = await api.getTrip(id);
  } catch (e) {
    $("cmp-sub").textContent = `Could not read that trip: ${e.message}`;
    return;
  }
  drawCompare();
}

function drawCompare() {
  const { a, b } = cmp;
  if (!a || !b) return;
  $("cmp-empty").hidden = true;
  $("cmp-body").hidden = false;
  $("cmp-sub").textContent = `${tripTitle(a)}, ${when(a.header.ts)}  \u2013  ${tripTitle(b)}, ${when(b.header.ts)}`;
  if (!cmp.map && typeof L !== "undefined") {
    cmp.map = L.map("cmap", { preferCanvas: true, worldCopyJump: false });
    cmp.map.attributionControl.setPrefix('<a href="https://leafletjs.com" target="_blank" rel="noopener noreferrer">Leaflet</a>');
    cmp.layer = L.layerGroup().addTo(cmp.map);
    setBase(currentBase);
  }
  if (cmp.map) {
    cmp.map.invalidateSize();
    cmp.layer.clearLayers();
    const all = [];
    for (const [trip, colour] of [[a, "#35c2ff"], [b, "#ffa733"]]) {
      const line = trip.points.map((p) => [p.la, p.lo]);
      all.push(...line);
      if (line.length > 1) L.polyline(line, { color: colour, weight: 4, opacity: 0.85, lineCap: "round", lineJoin: "round" }).addTo(cmp.layer);
    }
    if (all.length) cmp.map.fitBounds(L.latLngBounds(all), { padding: [30, 30], maxZoom: 17 });
  }

  const table = $("cmp-table");
  table.replaceChildren();
  const rows = compareRows(a, b, state.prefs);
  const t = h("table", "", table);
  const head = h("tr", "", h("thead", "", t));
  for (const [text, num] of [["", false], ["This trip", true], ["The other", true], ["Other vs this", true]]) h("th", num ? "num" : "", head, text);
  const body = h("tbody", "", t);
  for (const r of rows) {
    const row = h("tr", "", body);
    row.dataset.key = r.key;
    h("td", "", row, r.label);
    h("td", `num${r.better === "a" ? " better" : ""}`, row, r.a);
    h("td", `num${r.better === "b" ? " better" : ""}`, row, r.b);
    h("td", "num", row, r.diff);
  }
  drawGap();
}

function drawGap() {
  const host = $("cmp-graph");
  host.replaceChildren();
  const series = gapSeries(cmp.a, cmp.b);
  if (series.length < 2) {
    $("cmp-key").textContent = "These trips have no shared distance to compare along.";
    return;
  }
  const width = Math.max(240, host.clientWidth);
  const height = GRAPH_HEIGHT;
  const pad = { l: 46, r: 8, t: 10, b: 20 };
  const innerW = width - pad.l - pad.r;
  const innerH = height - pad.t - pad.b;
  const most = Math.max(10, ...series.map((p) => Math.abs(p.gap)));
  const total = series[series.length - 1].d;
  const x = (d) => pad.l + (d / total) * innerW;
  const y = (g) => pad.t + innerH / 2 - (g / most) * (innerH / 2);
  const distUnit = resolveUnit("distance", state.prefs);
  const svg = s("svg", { viewBox: `0 0 ${width} ${height}`, width, height, role: "img", "aria-label": "How far ahead or behind the other trip was, along the way" }, host);
  s("line", { x1: pad.l, x2: width - pad.r, y1: y(0), y2: y(0), class: "zero" }, svg);
  s("text", { x: pad.l - 5, y: pad.t + 9, class: "axis end" }, svg, signedDuration(most));
  s("text", { x: pad.l - 5, y: y(0) + 4, class: "axis end" }, svg, "0");
  s("text", { x: pad.l - 5, y: height - pad.b, class: "axis end" }, svg, signedDuration(-most));
  s("text", { x: width - pad.r, y: height - 5, class: "axis end unit" }, svg, `${distanceText(total, state.prefs).value} ${distanceText(total, state.prefs).label}`);
  s("text", { x: pad.l, y: height - 5, class: "axis" }, svg, `0 ${distUnit.label}`);
  const line = series.map((p, i) => `${i ? "L" : "M"}${x(p.d).toFixed(1)} ${y(p.gap).toFixed(1)}`).join("");
  s("path", { d: `${line}L${x(total).toFixed(1)} ${y(0)}L${pad.l} ${y(0)}Z`, class: "gap-area" }, svg);
  s("path", { d: line, class: "gap" }, svg);
  const end = series[series.length - 1].gap;
  $("cmp-key").textContent = end === 0 ? "Level at the end." : `Above the dotted line the other trip was behind this one, below it ahead. At the end it was ${signedDuration(Math.abs(end)).slice(1)} ${end > 0 ? "behind" : "ahead"}. (Measured by distance driven.)`;
  const tip = $("cmp-tip");
  svg.addEventListener("pointerleave", () => (tip.hidden = true));
  svg.addEventListener("pointermove", (e) => {
    const box = svg.getBoundingClientRect();
    const d = Math.min(total, Math.max(0, ((e.clientX - box.left - pad.l) / innerW) * total));
    const i = Math.round((d / total) * (series.length - 1));
    const p = series[i];
    const dd = distanceText(p.d, state.prefs);
    tip.hidden = false;
    tip.textContent = `${dd.value} ${dd.label}: this trip ${formatDuration(p.a)}, the other ${formatDuration(p.b)} (${signedDuration(p.gap)})`;
    tip.style.left = `${Math.min(e.clientX - box.left + 12, Math.max(0, width - 220))}px`;
    tip.style.top = "6px";
  });
}

function setupCompare() {
  $("compare").addEventListener("click", showCompare);
  $("cmp-back").addEventListener("click", () => cmp.a && select(cmp.a.header.id));
  $("cmp-pick").addEventListener("change", () => loadCompare($("cmp-pick").value));
}

/** Mark the open trip as a favourite, or take the mark off. */
async function toggleStar() {
  const trip = state.detail;
  if (!trip || !trip.end) return;
  const next = !trip.starred;
  try {
    await api.starTrip(trip.header.id, next);
  } catch (e) {
    $("subtitle").textContent = `Could not star: ${e.message}`;
    return;
  }
  trip.starred = next;
  const item = state.trips.find((t) => t.id === trip.header.id);
  if (item) item.starred = next;
  drawHeader();
  renderList();
}

// ---- tags ----------------------------------------------------------------------------------------------------------------------
// Short labels on a trip. The fixed set is offered as buttons that switch on and off; a tag of your own is typed in, and from then on is
// offered like the others (every tag in use on any trip is a button, so there is no list of tags to keep). Only the computer running
// ThrashDash changes them, and only on a finished trip; anyone else just sees the ones the trip has.

/** The tag filter above the list: the tags in use. Hidden while no trip has one. */
function drawTagFilter() {
  const inUse = tagsInUse(state.trips, state.presetTags);
  if (state.tag && !inUse.some((t) => t.toLowerCase() === state.tag.toLowerCase())) state.tag = ""; // (the last trip with it lost it)
  $("tag-filter-row").hidden = inUse.length === 0;
  const sel = $("tag-filter");
  const shown = JSON.stringify([inUse, state.tag]);
  if (sel.dataset.drawn === shown) return; // (the list is redrawn every few seconds: leave an open dropdown alone)
  sel.dataset.drawn = shown;
  sel.replaceChildren();
  h("option", "", sel, "All tags").value = "";
  for (const t of inUse) h("option", "", sel, t).value = t;
  sel.value = state.tag;
}

function drawTags() {
  const trip = state.detail;
  const row = $("tag-row");
  if (!trip) {
    row.hidden = true;
    return;
  }
  const tags = trip.tags ?? [];
  const canEdit = state.client.local && Boolean(trip.end) && !SHARED && !EMBED;
  row.hidden = !canEdit && tags.length === 0;
  $("tag-form").hidden = !canEdit;
  const chips = $("tag-chips");
  chips.replaceChildren();
  const offered = canEdit ? knownTags(state.trips, state.presetTags, tags) : tags;
  for (const tag of offered) {
    const on = tags.some((t) => t.toLowerCase() === tag.toLowerCase());
    const li = h("li", "", chips);
    if (!canEdit) {
      h("span", "tag-chip on", li, tag);
      continue;
    }
    const b = h("button", `tag-chip${on ? " on" : ""}`, li, tag);
    b.type = "button";
    b.setAttribute("aria-pressed", String(on));
    b.addEventListener("click", () => setTags(toggleTag(tags, tag)));
  }
  $("tag-note").hidden = true;
}

function tagNote(text) {
  const el = $("tag-note");
  el.textContent = text;
  el.hidden = !text;
}

/** Save a trip's tags, then show them (the list, the header row and any filter that offers them). */
async function setTags(tags) {
  const trip = state.detail;
  if (!trip || !trip.end) return;
  try {
    const reply = await api.tagTrip(trip.header.id, tags);
    trip.tags = reply.tags;
  } catch (e) {
    tagNote(`Could not change the tags: ${e.message}`);
    return;
  }
  const item = state.trips.find((t) => t.id === trip.header.id);
  if (item) item.tags = trip.tags;
  drawTags();
  renderList();
  if (isStats()) drawStats();
}

function setupTags() {
  $("tag-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const trip = state.detail;
    if (!trip) return;
    const input = $("tag-input");
    const known = knownTags(state.trips, state.presetTags, trip.tags ?? []);
    const added = addTypedTag(trip.tags ?? [], input.value, known);
    if (added.error) {
      tagNote(added.error);
      return;
    }
    input.value = "";
    await setTags(added.tags);
  });
}

// ---- the fuel price: the setting, and a trip's own ------------------------------------------------------------------------------
// The price of fuel is a setting (per litre, per kWh and the currency) that each trip is given as it starts, so what an old drive cost
// never changes when the price does. A finished trip's own price can be changed by hand (a trip recorded before there was a price, or one
// that began with the wrong one). Only the computer running ThrashDash changes them.

const priceFields = (prefix) => ({ liquid: $(`${prefix}-liquid`), electric: $(`${prefix}-electric`), currency: $(`${prefix}-currency`) });

function showPrice(fields, price) {
  fields.liquid.value = price?.liquid > 0 ? String(price.liquid) : "";
  fields.electric.value = price?.electric > 0 ? String(price.electric) : "";
  fields.currency.value = price?.currency ?? "";
}

/** What was typed, as a price: blank is 0 (no price), and a number that is not one is refused. */
function readPrice(fields) {
  const read = (el) => (el.value.trim() === "" ? 0 : Number(el.value));
  const price = { liquid: read(fields.liquid), electric: read(fields.electric), currency: fields.currency.value.trim() };
  return Number.isFinite(price.liquid) && Number.isFinite(price.electric) && price.liquid >= 0 && price.electric >= 0 ? price : null;
}

const priceLine = (price) => {
  const bits = [];
  if (price?.liquid > 0) bits.push(`${moneyText(price.liquid, price.currency)} per litre`);
  if (price?.electric > 0) bits.push(`${moneyText(price.electric, price.currency)} per kWh`);
  return bits.join(", ");
};

function setupFuelPrice(settings) {
  const box = $("price-settings");
  box.hidden = !state.client.local || EMBED || SHARED;
  if (box.hidden) return;
  const fields = priceFields("price");
  showPrice(fields, settings?.fuelPrice);
  $("price-save").addEventListener("click", async () => {
    const price = readPrice(fields);
    const note = $("price-note");
    if (!price) {
      note.textContent = "A price is a number, 0 or more.";
      return;
    }
    try {
      await api.saveSettings({ fuelPrice: price });
      note.textContent = priceLine(price) ? `Saved: ${priceLine(price)}. Trips that start from now on are given it; each keeps the price it started with.` : "Saved: no price, so new trips have no cost.";
    } catch (e) {
      note.textContent = `Could not save: ${e.message}`;
    }
  });
}

function drawPrice() {
  const trip = state.detail;
  const box = $("price-box");
  box.hidden = !trip || !trip.end || !state.client.local || SHARED || EMBED;
  if (box.hidden) return;
  $("price-summary").textContent = trip.price ? `Fuel price for this trip: ${priceLine(trip.price)}` : "Fuel price for this trip: none";
  showPrice(priceFields("tp"), trip.price ?? settingsLoaded?.fuelPrice);
  $("tp-note").hidden = true;
}

async function setTripPrice(price) {
  const trip = state.detail;
  if (!trip || !trip.end) return;
  const note = $("tp-note");
  try {
    const reply = await api.priceTrip(trip.header.id, price);
    trip.price = reply.price;
  } catch (e) {
    note.textContent = `Could not change the price: ${e.message}`;
    note.hidden = false;
    return;
  }
  const item = state.trips.find((t) => t.id === trip.header.id);
  if (item) item.price = trip.price;
  drawPrice();
  drawTiles();
  if (isStats()) drawStats();
}

function setupTripPrice() {
  $("tp-save").addEventListener("click", () => {
    const price = readPrice(priceFields("tp"));
    if (!price) {
      $("tp-note").textContent = "A price is a number, 0 or more.";
      $("tp-note").hidden = false;
      return;
    }
    setTripPrice(price);
  });
  $("tp-remove").addEventListener("click", () => setTripPrice(null));
}

function startRename() {
  const trip = state.detail;
  const title = $("title");
  const input = h("input", "rename-input", null);
  input.type = "text";
  input.maxLength = 80;
  input.value = trip.title || "";
  input.placeholder = trip.stats?.name || "Trip";
  input.setAttribute("aria-label", "Trip name");
  title.replaceChildren(input);
  input.focus();
  input.select();
  let finished = false;
  const done = async (save) => {
    if (finished) return;
    finished = true;
    if (save) {
      try {
        await api.renameTrip(trip.header.id, input.value);
      } catch (e) {
        $("subtitle").textContent = `Could not rename: ${e.message}`;
      }
    }
    await Promise.all([loadList(), select(trip.header.id, { refit: false })]);
  };
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") done(true);
    else if (e.key === "Escape") done(false);
  });
  input.addEventListener("blur", () => done(true));
}

let deleteTimer = null;
const deleteButtons = () => [$("delete"), $("sel-delete")];

/** Delete: the ticked trips if any are ticked, otherwise the open one. Asks twice (the button changes to "Really delete ...?" for four
 * seconds). A deleted trip goes to the trash folder in the trip log folder, not erased. */
async function askDelete(button) {
  if (button.dataset.sure !== "1") {
    resetDelete();
    button.dataset.sure = "1";
    const n = state.picked.size;
    button.textContent = n ? `Really delete ${n}?` : "Really delete?";
    deleteTimer = setTimeout(resetDelete, 4000);
    return;
  }
  const ids = state.picked.size ? [...state.picked] : [state.detail.header.id];
  resetDelete();
  const failed = [];
  for (const id of ids) {
    try {
      await api.deleteTrip(id);
      state.picked.delete(id);
    } catch (e) {
      failed.push(e.message);
    }
  }
  await loadList();
  if (state.detail && !state.trips.some((t) => t.id === state.detail.header.id)) showNothing(); // (the open trip was one of them)
  if (failed.length) {
    $("sel-count").textContent = `Could not delete ${failed.length} of ${ids.length}: ${failed[0]}`; // (the ones that failed stay ticked, in the bar)
    $("subtitle").textContent = `Could not delete: ${failed[0]}`;
  }
}

function resetDelete() {
  clearTimeout(deleteTimer);
  for (const button of deleteButtons()) button.dataset.sure = "0";
  labelDelete();
}

/** The Delete buttons' words, except one that is waiting for a second click (the list refreshes every few seconds and must not undo it). */
function labelDelete() {
  const n = state.picked.size;
  for (const button of deleteButtons()) {
    if (button.dataset.sure === "1") continue;
    button.textContent = button === $("sel-delete") ? "Delete" : n ? `Delete ${n} selected` : "Delete";
  }
}

// ---- start ----------------------------------------------------------------------------------------------------------

// ---- the planned GPS route on the live map -------------------------------------------------------------------------------------
// BeamNG.Earth plans the route; the relay passes it on (GET /api/route: the line, the destination and the progress). It is drawn on the map of
// the trip being driven, a dashed line from the car to the destination with a chip saying where to and how far, and goes when the route does.
// A trip that has ended shows no route: the route is about now.

async function fetchPlan() {
  try {
    plan.state = await api.getRoute();
  } catch {
    plan.state = null;
  }
  drawPlanOnMap();
  routeView?.drawPlan();
}

let planBusy = false;
async function pollPlan() {
  if (SHARED || planBusy) return;
  const wanted = routeView?.isOpen() || (state.detail && !state.detail.end);
  if (!wanted) {
    if (plan.state) {
      plan.state = null;
      drawPlanOnMap();
    }
    return;
  }
  planBusy = true;
  try {
    await fetchPlan();
  } finally {
    planBusy = false;
  }
}

function drawPlanOnMap() {
  if (!map) return;
  plan.layer ??= L.layerGroup().addTo(map);
  const s = plan.state;
  const show = Boolean(s?.active && s.points && state.detail && !state.detail.end);
  const chip = $("route-chip");
  chip.hidden = !(s?.active && state.detail && !state.detail.end);
  if (!chip.hidden) chip.textContent = routeSummary(s, state.prefs);
  const key = show ? `${s.version}:${s.points.length}:${s.dest?.lat}:${s.dest?.lon}` : "";
  if (key === plan.key) return;
  plan.key = key;
  plan.layer.clearLayers();
  if (!show) return;
  L.polyline(s.points, { color: "#35c2ff", weight: 4, opacity: 0.85, dashArray: "2 9", lineCap: "round", interactive: false }).addTo(plan.layer);
  for (const w of s.waypoints ?? []) L.circleMarker([w.lat, w.lon], { radius: 5, color: "#fff", weight: 2, fillColor: "#ffb020", fillOpacity: 1 }).bindTooltip(w.name || "Stop").addTo(plan.layer);
  if (s.dest) L.circleMarker([s.dest.lat, s.dest.lon], { radius: 7, color: "#fff", weight: 2, fillColor: "#ff4d4d", fillOpacity: 1 }).bindTooltip(s.dest.name || "Destination").addTo(plan.layer);
}

let idleTicks = 0;
let refreshing = false;

/** A new trip has started: show it (the live map), unless that is switched off, or the all-trips map is open, or a name is being typed. */
async function maybeAutoOpen(allowSwitch) {
  const id = nextAutoOpen(state.trips, autoOpened);
  if (!id) return;
  if (allowSwitch && $("auto-open").checked && document.activeElement?.classList.contains("rename-input")) return; // (try again once the name is done)
  autoOpened = id;
  if (allowSwitch && $("auto-open").checked && state.selected !== id) await select(id, { refit: true });
}

/** Called every LIVE_MS. Does the work only when something is being recorded (or the open trip is unfinished). */
async function tick() {
  if (SHARED) return; // (a shared page has one finished trip and nothing to refresh)
  pollPlan(); // (the GPS route, while a live trip or the Route view is open: its own request, so it never holds the trip refresh up)
  if (refreshing) return;
  // Looking for a new trip is cheap, so it goes on even when the window is hidden behind the game; redrawing a trip nobody can see is not.
  const visible = document.visibilityState === "visible";
  if (isOverview() || isStats() || isCompare() || circuitsView?.isOpen() || routeView?.isOpen()) {
    // The all-trips map: a newly recorded trip appears and one being driven grows. It reads every trip, so not as often as a single trip.
    if (!state.recording && ++idleTicks < IDLE_TICKS) return;
    idleTicks = 0;
    refreshing = true;
    try {
      await loadList();
      await maybeAutoOpen(false); // (the all-trips map already shows the new trip; this only notes it, so it does not jump later)
      if (!isOverview()) return;
      if (visible && state.recording && ++overview.ticks >= 3) {
        overview.ticks = 0;
        await loadRoutes(false);
      } else if (visible && !state.recording && overview.routes.some((r) => r.recording)) {
        await loadRoutes(false); // (it has just finished)
      }
    } finally {
      refreshing = false;
    }
    return;
  }
  const open = state.detail && !state.detail.end;
  if (!state.recording && !open && ++idleTicks < IDLE_TICKS) return;
  idleTicks = 0;
  refreshing = true;
  try {
    await loadList();
    // The open trip is gone (a nudge of the car is thrown away when it ends, and one that was open on screen goes with it): leave it,
    // rather than keep asking for a trip that is not there.
    if (state.selected && state.detail && !state.trips.some((t) => t.id === state.selected)) showNothing();
    await maybeAutoOpen(true);
    if ((visible || replay.wantLive) && state.detail && !state.detail.end && !document.activeElement?.classList.contains("rename-input")) { // (hidden: only to get a new trip's live view going)
      const trip = await api.getTrip(state.selected).catch(() => null);
      // (nothing new: leave the map and graph alone, so a hover or a drag is not interrupted)
      if (trip && state.detail && !sameTrip(state.detail, trip)) show(trip, false);
    }
  } finally {
    refreshing = false;
  }
}

// ---- this window, remembered ------------------------------------------------------------------------------------------------------
// The Trips (map) window tells the relay its size and position like a dashboard window does, so it is part of the config ThrashDash
// saves (Dashboards menu, Window config) and is reopened at start-up. Only the computer running ThrashDash takes part, and not a
// map embedded in a dashboard or a shared file.

async function reportAsWindow() {
  if (EMBED || SHARED || !state.client.local) return;
  const id = ensureWindowId();
  await applySavedPlacement(id, { getSettings: () => api.getSettings(), listWindows: () => api.listWindows() }); // (before reporting, or the report would overwrite what is remembered)
  createWindowReporter({
    id,
    state: () => ({ id: null, pinned: false, kind: "trips" }),
    geometry: () => {
      saveTripsGeometry(localStorage, tripsGeometry()); // (so the next Trips window opens this big, and here; a no-op while it has not changed)
      return readGeometry();
    },
    send: (report) => api.putWindow(id, report),
    forget: (stamp) => api.forgetWindow(id, stamp).catch(() => {}),
  }).start();
}

// ---- popups -----------------------------------------------------------------------------------------------------------------
// Which popups about trips there are: in the game when a trip ends (or starts), and on the dashboard windows when one ends.
// Kept by the relay (settings.tripNotices) and changeable only from the computer running ThrashDash, like every setting.

/** The "start BeamNG.Earth" setting: a folder (or file), what it would start shown as you type, saved for next time. This computer only. */
function setupEarth(settings) {
  const box = $("earth-box");
  box.hidden = !state.client.local || EMBED || SHARED;
  if (box.hidden) return;
  const input = $("earth-path");
  const note = $("earth-note");
  input.value = settings?.earthPath ?? "";
  let timer = null;
  let asked = 0;
  const look = async () => {
    const mine = ++asked;
    try {
      const info = await api.earthInfo(input.value);
      if (mine !== asked) return; // (typing went on: a newer answer is coming)
      note.textContent = `${info.configured ? info.message : "Not set: ThrashDash does not start it."}${info.running ? " Earth is running now." : ""}`;
    } catch (e) {
      if (mine === asked) note.textContent = `Could not check: ${e.message}`;
    }
  };
  input.addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(look, 400);
  });
  $("earth-save").addEventListener("click", async () => {
    try {
      await api.saveSettings({ earthPath: input.value });
      await look();
      note.textContent += " Saved: ThrashDash will start it from now on, the next time it starts.";
    } catch (e) {
      note.textContent = `Could not save: ${e.message}`;
    }
  });
  look();
}

/** The two thresholds that decide what counts as an impact. Saved on this computer; every trip's impacts are re-worked from them at once. */
function setupImpactRule() {
  const boxes = { damage: $("rule-damage"), g: $("rule-g") };
  for (const [key, box] of Object.entries(boxes)) {
    box.value = String(state.rule[key]);
    box.disabled = !state.client.local;
    box.addEventListener("change", async () => {
      const value = Number(box.value);
      try {
        const saved = await api.saveSettings({ impactRule: { [key]: value } });
        state.rule = { ...DEFAULT_IMPACT_RULE, ...(saved?.impactRule ?? { [key]: value }) };
        $("pop-note").textContent = "";
        drawTiles();
        drawImpacts();
        if (state.detail) drawMap(false);
        statsView.drawn = null;
        if (!$("stats").hidden) drawStats();
      } catch (e) {
        box.value = String(state.rule[key]);
        $("pop-note").textContent = `Could not save: ${e.message}`;
      }
    });
  }
}

function setupPopups(settings) {
  const notices = settings?.tripNotices ?? { game: true, gameStart: false, window: true };
  const boxes = { game: $("pop-game"), gameStart: $("pop-start"), window: $("pop-window") };
  for (const [key, box] of Object.entries(boxes)) {
    box.checked = Boolean(notices[key]);
    box.disabled = !state.client.local;
    box.addEventListener("change", async () => {
      try {
        await api.saveSettings({ tripNotices: { [key]: box.checked } });
        $("pop-note").textContent = "";
      } catch (e) {
        box.checked = !box.checked;
        $("pop-note").textContent = `Could not save: ${e.message}`;
      }
    });
  }
  const idle = $("idle-end");
  idle.value = String(settings?.tripIdleEndMin ?? 2);
  idle.disabled = !state.client.local;
  idle.addEventListener("change", async () => {
    try {
      await api.saveSettings({ tripIdleEndMin: Number(idle.value) });
      $("pop-note").textContent = "";
    } catch (e) {
      $("pop-note").textContent = `Could not save: ${e.message}`;
    }
  });
  setupImpactRule();
  $("pop-test").disabled = !state.client.local;
  $("pop-test").addEventListener("click", async () => {
    try {
      const r = await api.testPopup();
      $("pop-note").textContent = r.game
        ? "Sent to the game and the dashboard windows. If the game shows nothing, tell the developer."
        : "Sent to the dashboard windows. The game has not sent a position lately, so it was not sent there: a BeamNG.Earth level must be loaded and driven in.";
    } catch (e) {
      $("pop-note").textContent = `Could not send: ${e.message}`;
    }
  });
  if (!state.client.local) $("pop-note").textContent = "Only the computer running ThrashDash can change these.";
}

let settingsLoaded = null;

async function start() {
  try {
    const [client, settings] = await Promise.all([api.getClient(), api.getSettings()]);
    state.client = client;
    settingsLoaded = settings;
    state.prefs = buildPrefs(settings.units, location.search);
    state.rule = { ...DEFAULT_IMPACT_RULE, ...(settings.impactRule ?? {}) };
  } catch {
    /* the defaults will do */
  }
  for (const b of document.querySelectorAll(".base button")) b.addEventListener("click", () => setBase(b.dataset.base));
  $("back").addEventListener("click", () => {
    document.body.dataset.view = "list";
    history.replaceState(null, "", location.pathname);
  });
  setupReplay();
  setupHeadUp();
  setupMapSize();
  setupOverview();
  setupStats();
  circuitsView = createCircuits({ $, state: () => state, tileLayerFor, currentBase: () => currentBase, when, tripTitle, leave: leaveForCircuits, closed: () => showNothing() });
  circuitsView.setup();
  routeView = createRouteView({
    $, state: () => state, tileLayerFor, currentBase: () => currentBase, when, leave: leaveForRoute, closed: () => showNothing(),
    plan: () => plan.state, refreshPlan: fetchPlan, carPosition: () => { const here = feedHere(); return here && Number.isFinite(here.lat) ? [here.lat, here.lon] : null; },
  });
  routeView.setup();
  $("make-circuit").addEventListener("click", () => circuitsView.startFromTrip(null, state.selected));
  wireList();
  setupPopups(settingsLoaded);
  setupEarth(settingsLoaded);
  reportAsWindow();
  setupQuit();
  setupEditing();
  setupTags();
  setupTripPrice();
  setupFuelPrice(settingsLoaded);
  setupBackup();
  setupCompare();
  $("star").addEventListener("click", toggleStar);
  $("rename").addEventListener("click", startRename);
  $("delete").addEventListener("click", () => askDelete($("delete")));
  window.addEventListener("resize", () => {
    if (state.detail) {
      drawGraph();
      map?.invalidateSize();
    }
  });
  window.addEventListener("hashchange", () => {
    const id = location.hash.slice(1);
    if (id === "all") {
      if (!isOverview()) showOverview();
    } else if (id === "stats") {
      if (!isStats()) showStats();
    } else if (id === "circuits") {
      if (!circuitsView.isOpen()) circuitsView.show();
    } else if (id === "route") {
      if (!routeView.isOpen()) routeView.show();
    } else if (id && id !== state.selected) select(id);
  });
  const auto = $("auto-open");
  auto.checked = stored(AUTO_KEY) !== "0";
  auto.addEventListener("change", () => {
    try {
      localStorage.setItem(AUTO_KEY, auto.checked ? "1" : "0");
    } catch {
      /* a browser that will not remember is fine */
    }
  });
  if (EMBED) {
    $("headup").checked = QUERY.get("headup") !== "0";
    $("auto-open").checked = true; // (a live map follows each new trip)
    const zoom = Number(QUERY.get("zoom"));
    if (zoom >= 5 && zoom <= 19) view.zoom = Math.round(zoom);
    $("placeholder").textContent = "Waiting for a trip. Drive in BeamNG.Earth and the map follows the car.";
  }
  await loadList();
  autoOpened = nextAutoOpen(state.trips, null); // (a trip already being recorded when the page opens is simply the newest: no jump later)
  const wanted = EMBED ? "" : location.hash.slice(1);
  if (wanted === "all") await showOverview();
  else if (wanted === "stats") await showStats();
  else if (wanted === "circuits") await circuitsView.show();
  else if (wanted === "route") await routeView.show();
  else if (wanted && state.trips.some((t) => t.id === wanted)) await select(wanted);
  else if (EMBED) {
    const live = state.trips.find((t) => t.recording);
    if (live) await select(live.id); // (otherwise it waits: the next trip to start opens by itself)
  } else if (state.trips.length && (SHARED || matchMedia("(min-width: 900px)").matches)) await select(state.trips[0].id);
  refreshTimer = setInterval(tick, LIVE_MS);
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") {
      map?.invalidateSize({ animate: false }); // (it may have been resized, or opened, while out of sight)
      tick(); // back in view: catch up at once
    }
  });
  connectFeed(); // trip starts and ends arrive here at once, and so does the car's position when a trip is followed
}

if (QUERY.get("debug") === "1") {
  // (for the tests: where the car marker is drawn, and whether live positions are arriving)
  globalThis.__tripsDebug = () => ({ center: map ? map.getCenter() : null, selected: state.selected, marker: replay.marker ? replay.marker.getLatLng() : null, feedSamples: feed.samples.length, feedLive: feedHere() !== null, live: replay.live, plan: plan.key });
}

start();

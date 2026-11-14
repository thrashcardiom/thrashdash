// The Trips page: the list of recorded drives, and for the one picked, its route on a map, its figures and a speed and
// altitude graph. Read-only except for renaming and deleting a trip, which only the computer running ThrashDash may do.
//
// All page text is put in with textContent (see dom.js): a trip's name is something the user typed.
// The pure parts (colours, graph geometry, the figures) are in model.js, where Node tests them.

import { api } from "../api.js";
import { h, s } from "../dom.js";
import { buildPrefs, resolveUnit } from "../units.js";
import { createWindowReporter, ensureWindowId } from "../windowState.js";
import { applySavedPlacement } from "../windowPlace.js";
import { formatDuration } from "../economy.js";
import { RAMP, REPLAY_RATES, advanceLive, interpolateFeed, liveReadout, nextAutoOpen, WHEN_OPTIONS, advanceReplay, clockText, colourScaleTop, filterOptions, filterRoutes, overviewTiles, routesBounds, turnToward, clampMapHeight, MAP_STEP_PX, distanceText, graphModel, replayReadout, routeRuns, sameTrip, sampleAtDistance, speedText, statTiles, tripTitle } from "./model.js";

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
  idle: "parked", teleport: "teleported", jump: "moved elsewhere", vehicle: "changed vehicle", level: "left the level",
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
const state = { trips: [], enabled: true, selected: null, detail: null, client: { local: false }, prefs: buildPrefs({}, location.search), recording: false };
let map = null;
let baseLayer = null;
let trackLayer = null;
let cursor = null;
let refreshTimer = null;
let drawnFor = null;
let fittedFor = null; // the trip the map has been moved to show (a trip that opens before it has any points is fitted when the first arrive)
// (OpenStreetMap refuses tile requests with no Referer, and a page opened from a disk sends none: it starts on the satellite picture)
let currentBase = EMBED && ["sat", "street"].includes(QUERY.get("base")) ? QUERY.get("base") : ["sat", "street"].includes(stored(BASE_KEY)) ? stored(BASE_KEY) : SHARED && location.protocol === "file:" ? "sat" : "street";
const overview = { map: null, layer: null, base: null, routes: [], filters: { when: "all", vehicle: "", country: "" }, ticks: 0 };
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

function renderList() {
  const list = $("list");
  list.replaceChildren();
  $("count").textContent = state.trips.length ? `${state.trips.length} ${state.trips.length === 1 ? "trip" : "trips"}` : "";
  const empty = $("empty");
  empty.hidden = state.trips.length > 0;
  if (!state.trips.length) {
    empty.textContent = state.enabled
      ? "No trips yet. Drive in BeamNG.Earth with ThrashDash running and each drive appears here."
      : "The trip log is switched off in this run of ThrashDash (it is not kept in --simulate mode).";
  }
  for (const trip of state.trips) {
    const li = h("li", "", list);
    const b = h("button", "item", li);
    b.type = "button";
    b.dataset.id = trip.id;
    b.setAttribute("aria-current", trip.id === state.selected ? "true" : "false");
    // (a trip still being recorded has no stored summary, so the one worked out for the open trip stands in for its name)
    const known = trip.id === state.selected && state.detail && !trip.stats ? { ...trip, stats: state.detail.stats } : trip;
    h("span", "item-title", b, tripTitle(known));
    const bits = [when(trip.ts)];
    if (trip.distance != null) bits.push(distanceString(trip.distance));
    if (trip.duration != null) bits.push(formatDuration(trip.duration));
    h("span", "item-sub muted", b, bits.join(" · "));
    if (trip.recording) h("span", "badge", b, "Recording");
    b.addEventListener("click", () => select(trip.id));
  }
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
    state.recording = Boolean(data.status?.recording);
    const live = $("live");
    live.hidden = !state.recording;
    if (state.recording) live.textContent = `Recording now: ${distanceString(data.status.distance)}`;
  } catch (e) {
    state.trips = [];
    $("empty").hidden = false;
    $("empty").textContent = `Could not load the trips: ${e.message}`;
    return;
  }
  renderList();
}

// ---- one trip --------------------------------------------------------------------------------------------------------

async function select(id, { refit = true } = {}) {
  let trip;
  try {
    trip = await api.getTrip(id);
  } catch (e) {
    if (state.selected === id) showNothing();
    return;
  }
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
  $("all").setAttribute("aria-pressed", "false");
  $("detail").hidden = false;
  for (const b of $("list").querySelectorAll(".item")) b.setAttribute("aria-current", b.dataset.id === id ? "true" : "false");
  drawHeader();
  drawTiles();
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
  drawGraph();
  renderReplay(replay.active ? 0 : undefined); // (0: the turn of a heading-up map is not snapped by a refresh)
}

const lastLatLng = (trip) => {
  const p = trip.points[trip.points.length - 1];
  return [p.la, p.lo];
};

function showNothing() {
  stopReplay();
  fittedFor = null;
  state.selected = null;
  state.detail = null;
  document.body.dataset.view = "list";
  $("detail").hidden = true;
  $("overview").hidden = true;
  $("all").setAttribute("aria-pressed", "false");
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
  const id = encodeURIComponent(trip.header.id);
  $("export-gpx").href = `/api/triplog/${id}/gpx`;
  $("export-kml").href = `/api/triplog/${id}/kml`;
  $("export-share").href = `/api/triplog/${id}/share`;
  $("export-share").hidden = !trip.end; // (a trip being recorded is shared once it has ended)
}

function drawTiles() {
  const box = $("tiles");
  box.replaceChildren();
  for (const tile of statTiles(state.detail, state.prefs)) {
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
  for (const [id, key] of [["f-when", "when"], ["f-vehicle", "vehicle"], ["f-country", "country"]]) {
    $(id).addEventListener("change", () => {
      overview.filters[key] = $(id).value;
      drawOverview(true);
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

function setMapHeight(which, px, remember = true) {
  const height = clampMapHeight(px, window.innerHeight);
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
    if (saved) document.documentElement.style.setProperty(MAPH_VARS[which], `${clampMapHeight(saved, window.innerHeight)}px`);
  }
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
    // (a window made smaller must not be left with a map taller than it)
    for (const which of Object.keys(MAPH_KEYS)) {
      const current = document.documentElement.style.getPropertyValue(MAPH_VARS[which]);
      if (current) setMapHeight(which, parseFloat(current), false);
    }
  });
}

// ---- renaming and deleting ---------------------------------------------------------------------------------------------

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
async function askDelete() {
  const button = $("delete");
  if (button.dataset.sure !== "1") {
    button.dataset.sure = "1";
    button.textContent = "Really delete?";
    deleteTimer = setTimeout(resetDelete, 4000);
    return;
  }
  resetDelete();
  try {
    await api.deleteTrip(state.detail.header.id);
  } catch (e) {
    $("subtitle").textContent = `Could not delete: ${e.message}`;
    return;
  }
  showNothing();
  await loadList();
}

function resetDelete() {
  clearTimeout(deleteTimer);
  const button = $("delete");
  button.dataset.sure = "0";
  button.textContent = "Delete";
}

// ---- start ----------------------------------------------------------------------------------------------------------

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
  if (refreshing) return;
  // Looking for a new trip is cheap, so it goes on even when the window is hidden behind the game; redrawing a trip nobody can see is not.
  const visible = document.visibilityState === "visible";
  if (isOverview()) {
    // The all-trips map: a newly recorded trip appears and one being driven grows. It reads every trip, so not as often as a single trip.
    if (!state.recording && ++idleTicks < IDLE_TICKS) return;
    idleTicks = 0;
    refreshing = true;
    try {
      await loadList();
      await maybeAutoOpen(false); // (the all-trips map already shows the new trip; this only notes it, so it does not jump later)
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
    send: (report) => api.putWindow(id, report),
    forget: (stamp) => api.forgetWindow(id, stamp).catch(() => {}),
  }).start();
}

// ---- popups -----------------------------------------------------------------------------------------------------------------
// Which popups about trips there are: in the game when a trip ends (or starts), and on the dashboard windows when one ends.
// Kept by the relay (settings.tripNotices) and changeable only from the computer running ThrashDash, like every setting.

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
  setupPopups(settingsLoaded);
  reportAsWindow();
  $("rename").addEventListener("click", startRename);
  $("delete").addEventListener("click", askDelete);
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
  globalThis.__tripsDebug = () => ({ center: map ? map.getCenter() : null, selected: state.selected, marker: replay.marker ? replay.marker.getLatLng() : null, feedSamples: feed.samples.length, feedLive: feedHere() !== null, live: replay.live });
}

start();

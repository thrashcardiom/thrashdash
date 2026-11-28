// The Route view of the Trips page: plan a GPS route on a big map and send it to the game.
//
// Click the map, or pick a saved destination, or a place from an earlier trip, to add stops; the last stop is the destination. "Send to the
// game" asks BeamNG.Earth (through the game's extension) to plan the route, and the planned route then comes back and is drawn here and on the
// live map. Only the computer running ThrashDash can send or clear a route (the page just looks otherwise).
//
// Everything typed by the user (a saved destination's name) is put in with textContent, never as markup (see dom.js).

import { api } from "../api.js";
import { h } from "../dom.js";
import { MAX_STOPS, addStop, distanceText, insertFirst, moveStop, placesFromRoutes, removeStop, routeSummary, routeToExtend, stopLabel, stopsSummary } from "./stops.js";

const SURE_MS = 4000;
const PLAN_COLOUR = "#35c2ff";
const STOP_COLOUR = "#ffb020";
const FUEL_COLOUR = "#b78bfa";

/**
 * @param {object} deps
 * @param {(id: string) => HTMLElement} deps.$ getElementById
 * @param {() => object} deps.state the page's state (client, prefs)
 * @param {(name: string) => any} deps.tileLayerFor a Leaflet tile layer for "street" or "sat"
 * @param {() => string} deps.currentBase
 * @param {() => object | null} deps.plan the latest GPS route state (GET /api/route)
 * @param {() => Promise<void>} deps.refreshPlan asks for it again now
 * @param {() => number[] | null} deps.carPosition [lat, lon] of the car now, or null
 * @param {(date: number) => string} deps.when
 * @param {() => void} deps.leave called when the view opens, so the page can hide the others
 * @param {() => void} deps.closed called when the user goes back
 */
/** Text for a map tooltip as an element: Leaflet would read a string as markup, and a place's name is somebody else's text. */
function plain(text) {
  const el = document.createElement("span");
  el.textContent = text;
  return el;
}

export function createRouteView(deps) {
  const { $, state, tileLayerFor, currentBase, plan, refreshPlan, carPosition, when } = deps;
  const v = {
    open: false, stops: [], destinations: [], destinationsOn: true, places: [], sending: false,
    map: null, tiles: null, stopLayer: null, planLayer: null, fuelLayer: null, car: null, planKey: "", fitted: false, saving: null,
    sent: false, fuel: null, finding: false, // (sent: the stops are just what was last sent; fuel: the places found nearby)
  };

  const local = () => state().client.local;
  const note = (text, kind = "") => {
    const el = $("rv-note");
    el.textContent = text;
    el.className = `cir-note ${kind}`.trim();
    el.hidden = !text;
  };

  // ---- the page ---------------------------------------------------------------------------------------------------------------------

  async function show() {
    deps.leave();
    v.open = true;
    document.body.dataset.view = "route";
    history.replaceState(null, "", "#route");
    $("route-view").hidden = false;
    $("route-btn").setAttribute("aria-pressed", "true");
    $("rv-edit").hidden = !local();
    $("rv-fuel-section").hidden = !local();
    ensureMap();
    v.fitted = false;
    drawAll();
    await Promise.all([loadDestinations(), loadPlaces(), refreshPlan()]);
    drawAll();
  }

  function hide() {
    v.open = false;
    $("route-view").hidden = true;
    $("route-btn").setAttribute("aria-pressed", "false");
  }

  async function loadDestinations() {
    try {
      const got = await api.listDestinations();
      v.destinations = got.destinations;
      v.destinationsOn = got.enabled;
    } catch (e) {
      note(`Could not read the saved destinations: ${e.message}`, "error");
    }
  }

  async function loadPlaces() {
    try {
      v.places = placesFromRoutes((await api.listRoutes()).trips ?? []);
    } catch {
      v.places = []; // (the trip log may be off in this run: there are just no places to offer)
    }
  }

  // ---- stops -------------------------------------------------------------------------------------------------------------------------

  function add(place) {
    if (!local()) return;
    const got = addStop(v.stops, place);
    if (!got.error) v.sent = false;
    v.stops = got.stops;
    note(got.error, got.error ? "error" : "");
    drawAll();
  }

  function drawStops() {
    const list = $("rv-stops");
    list.replaceChildren();
    $("rv-summary").textContent = stopsSummary(v.stops, state().prefs);
    if (!v.stops.length) h("li", "empty", list, local() ? "Click the map, or add a saved destination or a place from your trips." : "No stops.");
    v.stops.forEach((stop, i) => {
      const li = h("li", "rv-stop", list);
      const row = h("div", "rv-stop-row", li);
      h("span", "rv-badge", row, String(i + 1));
      h("span", "rv-stop-name", row, stopLabel(v.stops, i));
      if (local()) {
        const up = button(row, "↑", () => change(moveStop(v.stops, i, -1)), "btn small", "Earlier in the route");
        const down = button(row, "↓", () => change(moveStop(v.stops, i, 1)), "btn small", "Later in the route");
        up.disabled = i === 0;
        down.disabled = i === v.stops.length - 1;
        up.setAttribute("aria-label", `${stopLabel(v.stops, i)}: earlier`);
        down.setAttribute("aria-label", `${stopLabel(v.stops, i)}: later`);
        const star = button(row, "☆", () => startSave(i), "btn small", "Save this place under a name");
        star.setAttribute("aria-label", `${stopLabel(v.stops, i)}: save as a destination`);
        const gone = button(row, "✕", () => change(removeStop(v.stops, i)), "btn small danger", "Take this stop away");
        gone.setAttribute("aria-label", `Remove ${stopLabel(v.stops, i)}`);
      }
      if (v.saving === i) drawSaveForm(li, i);
    });
    $("rv-send").disabled = !local() || v.sending || v.stops.length === 0;
    $("rv-clear-stops").disabled = !v.stops.length;
  }

  function change(stops) {
    v.stops = stops;
    v.sent = false;
    v.saving = null;
    note("");
    drawAll();
  }

  function startSave(i) {
    v.saving = v.saving === i ? null : i;
    drawStops();
    $("rv-save-name")?.focus();
  }

  function drawSaveForm(parent, i) {
    const form = h("form", "rv-save", parent);
    const input = h("input", "", form);
    input.type = "text";
    input.id = "rv-save-name";
    input.maxLength = 40;
    input.placeholder = "A name, such as Home";
    input.value = v.stops[i].name ?? "";
    input.setAttribute("aria-label", "A name for this destination");
    const ok = h("button", "btn small", form, "Save");
    ok.type = "submit";
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const stop = v.stops[i];
      try {
        await api.saveDestination(input.value, stop.lat, stop.lon);
        v.saving = null;
        note(`Saved as ${input.value.trim()}.`);
        await loadDestinations();
        drawAll();
      } catch (err) {
        note(`Could not save: ${err.message}`, "error");
      }
    });
  }

  // ---- saved destinations and places from trips -------------------------------------------------------------------------------------

  function drawSources() {
    const box = $("rv-saved");
    box.replaceChildren();
    if (!v.destinationsOn) h("li", "empty", box, "Saved destinations are not available in this run.");
    else if (!v.destinations.length) h("li", "empty", box, "None yet: press the star beside a stop to save it.");
    for (const d of v.destinations) {
      const li = h("li", "rv-dest", box);
      const addBtn = h("button", "rv-dest-add", li, d.name);
      addBtn.type = "button";
      addBtn.disabled = !local();
      addBtn.title = "Add this as a stop";
      addBtn.addEventListener("click", () => add({ lat: d.lat, lon: d.lon, name: d.name }));
      if (local()) {
        const del = button(li, "✕", () => {}, "btn small danger", "Delete this saved destination");
        del.setAttribute("aria-label", `Delete ${d.name}`);
        sure(del, "✕", "Delete?", async () => {
          await api.deleteDestination(d.id);
          await loadDestinations();
          drawAll();
        });
      }
    }
    const pick = $("rv-places");
    pick.replaceChildren();
    h("option", "", pick, v.places.length ? "Choose a place…" : "No places from trips yet").value = "";
    v.places.forEach((p, i) => (h("option", "", pick, `${p.name} · ${when(p.when)}`).value = String(i)));
    $("rv-places-add").disabled = !local() || !v.places.length;
  }

  // ---- fuel and chargers nearby ------------------------------------------------------------------------------------------------------

  async function findFuel() {
    if (!local() || v.finding) return;
    v.finding = true;
    v.fuel = null;
    drawFuel();
    $("rv-fuel-note").textContent = "Asking BeamNG.Earth\u2026";
    try {
      const got = await api.findFuel();
      v.fuel = got.places;
      const more = got.done ? "" : " BeamNG.Earth is still fetching map data: search again in a moment for more.";
      $("rv-fuel-note").textContent = (got.places.length ? "Distances are in a straight line." : "None found nearby.") + more;
    } catch (e) {
      $("rv-fuel-note").textContent = e.message;
    } finally {
      v.finding = false;
      drawFuel();
    }
  }

  function addFuel(place) {
    // (a route is set but where it ends has not arrived yet: adding now would leave the rest of it behind)
    if ((!v.stops.length || v.sent) && plan()?.active && !plan().dest) return note("The route in the game is still arriving: try again in a moment.", "error");
    const base = routeToExtend(v.stops, v.sent, plan());
    const got = insertFirst(base, { lat: place.lat, lon: place.lon, name: place.name });
    if (got.error) return note(got.error, "error");
    change(got.stops);
    note(`${place.name} is now the next stop. Press Send to the game.`);
  }

  function drawFuel() {
    const list = $("rv-fuel");
    list.replaceChildren();
    $("rv-fuel-find").disabled = !local() || v.finding;
    v.fuelLayer?.clearLayers();
    for (const place of v.fuel ?? []) {
      const li = h("li", "rv-found", list);
      const words = h("div", "rv-found-words", li);
      h("span", "rv-found-name", words, place.name);
      h("span", "rv-found-what muted", words, [place.kind === "charger" ? "Charger" : "Fuel", distanceText(place.metres, state().prefs)].filter(Boolean).join(" \u00b7 "));
      const go = button(li, "Add as next stop", () => addFuel(place), "btn small", "Put this before your other stops");
      go.setAttribute("aria-label", `Add ${place.name} as the next stop`);
      if (v.fuelLayer) {
        const tip = document.createElement("span"); // (a place's name is somebody else's text: never as markup)
        tip.textContent = place.name;
        L.circleMarker([place.lat, place.lon], { radius: 6, color: "#fff", weight: 2, fillColor: FUEL_COLOUR, fillOpacity: 1 }).bindTooltip(tip).addTo(v.fuelLayer);
      }
    }
  }

  // ---- the map -----------------------------------------------------------------------------------------------------------------------

  function ensureMap() {
    if (v.map || typeof L === "undefined") return;
    v.map = L.map("rvmap", { worldCopyJump: false });
    v.map.attributionControl.setPrefix('<a href="https://leafletjs.com" target="_blank" rel="noopener noreferrer">Leaflet</a>');
    v.planLayer = L.layerGroup().addTo(v.map);
    v.fuelLayer = L.layerGroup().addTo(v.map);
    v.stopLayer = L.layerGroup().addTo(v.map);
    v.map.setView([0, 0], 2);
    setBase(currentBase());
    v.map.on("click", (e) => add({ lat: e.latlng.lat, lon: e.latlng.lng }));
  }

  function setBase(name) {
    if (!v.map) return;
    if (v.tiles) v.map.removeLayer(v.tiles);
    v.tiles = tileLayerFor(name).addTo(v.map);
    v.tiles.bringToBack();
  }

  function numbered(i, last) {
    const el = h("div", `rv-pin${last ? " last" : ""}`);
    el.textContent = String(i + 1);
    return L.divIcon({ html: el, className: "rv-pin-box", iconSize: [26, 26], iconAnchor: [13, 13] });
  }

  function drawMap() {
    if (!v.map) return;
    v.map.invalidateSize();
    v.stopLayer.clearLayers();
    if (v.stops.length > 1) L.polyline(v.stops.map((s) => [s.lat, s.lon]), { color: STOP_COLOUR, weight: 2, opacity: 0.8, dashArray: "4 8", interactive: false }).addTo(v.stopLayer);
    v.stops.forEach((s, i) => L.marker([s.lat, s.lon], { icon: numbered(i, i === v.stops.length - 1), interactive: false, keyboard: false }).addTo(v.stopLayer));
    drawCar();
    if (!v.fitted) fit();
  }

  function drawCar() {
    if (!v.map) return;
    const here = carPosition();
    if (!here) {
      v.car?.remove();
      v.car = null;
      return;
    }
    if (!v.car) v.car = L.circleMarker(here, { radius: 7, color: "#fff", weight: 2, fillColor: "#3ddc84", fillOpacity: 1, interactive: false }).addTo(v.map);
    else v.car.setLatLng(here);
    if (!v.fitted) fit(); // (the view opened before the car's position had arrived: go to it now, once)
  }

  /** Move the map to show what there is: the planned route, else the stops, else the car. Only once each time the view opens. */
  function fit() {
    const points = [];
    const s = plan();
    if (s?.active && s.points) points.push(...s.points);
    else points.push(...v.stops.map((p) => [p.lat, p.lon]));
    const here = carPosition();
    if (here) points.push(here);
    if (!points.length) return;
    v.fitted = true;
    v.map.fitBounds(L.latLngBounds(points), { padding: [40, 40], maxZoom: 16 });
  }

  /** The planned route (called by the page after each look at it, and when the view opens): the line, its destination and the status. */
  function drawPlan() {
    const s = plan();
    const text = routeSummary(s, state().prefs);
    const game = s ? s.game !== false : true;
    $("rv-status").textContent = !game ? "The game is not connected: start BeamNG, load the BeamNG.Earth level and drive a moment." : text ? `Route set: ${text}` : "No route is set in the game.";
    $("rv-clear").disabled = !local() || !s?.active;
    if (!v.map) return;
    const key = s?.active && s.points ? `${s.version}:${s.points.length}:${s.dest?.lat}` : "";
    if (key !== v.planKey) {
      v.planKey = key;
      v.planLayer.clearLayers();
      if (key) {
        L.polyline(s.points, { color: PLAN_COLOUR, weight: 5, opacity: 0.9, lineCap: "round", lineJoin: "round", interactive: false }).addTo(v.planLayer);
        for (const w of s.waypoints ?? []) L.circleMarker([w.lat, w.lon], { radius: 6, color: "#fff", weight: 2, fillColor: STOP_COLOUR, fillOpacity: 1 }).bindTooltip(plain(w.name || "Stop")).addTo(v.planLayer);
        if (s.dest) L.circleMarker([s.dest.lat, s.dest.lon], { radius: 8, color: "#fff", weight: 2, fillColor: "#ff4d4d", fillOpacity: 1 }).bindTooltip(plain(s.dest.name || "Destination")).addTo(v.planLayer);
        if (!v.fitted) fit();
      }
    }
    drawCar();
  }

  function drawAll() {
    drawStops();
    drawSources();
    drawMap();
    drawPlan();
    drawFuel();
  }

  // ---- sending -----------------------------------------------------------------------------------------------------------------------

  async function send() {
    if (!local() || v.sending || !v.stops.length) return;
    v.sending = true;
    drawStops();
    note("Sending the route to the game…");
    try {
      await api.setRoute(v.stops);
      v.sent = true;
      note("The game took it: BeamNG.Earth is planning the route, which will appear on the map in a moment.");
      v.fitted = false;
      for (let i = 0; i < 6; i++) {
        await new Promise((r) => setTimeout(r, 1000));
        await refreshPlan();
        if (plan()?.active && plan().points) break;
      }
      note(plan()?.active ? "" : "BeamNG.Earth has not planned a route yet. It may still be working, or the stops may not be joined by road.", plan()?.active ? "" : "error");
    } catch (e) {
      note(e.message, "error");
    } finally {
      v.sending = false;
      drawAll();
    }
  }

  async function clearRoute() {
    try {
      await api.clearRoute();
      note("The route was cleared in the game.");
      await refreshPlan();
    } catch (e) {
      note(e.message, "error");
    }
    drawAll();
  }

  // ---- small helpers -----------------------------------------------------------------------------------------------------------------

  function button(parent, label, onClick, cls = "btn", title = "") {
    const b = h("button", cls, parent, label);
    b.type = "button";
    if (title) b.title = title;
    b.addEventListener("click", onClick);
    return b;
  }

  /** A button that must be pressed twice: it asks the first time (its label changes), and acts the second. */
  function sure(btn, label, question, action) {
    let timer = null;
    btn.addEventListener("click", async () => {
      if (timer === null) {
        btn.textContent = question;
        btn.classList.add("armed");
        timer = setTimeout(() => {
          timer = null;
          btn.textContent = label;
          btn.classList.remove("armed");
        }, SURE_MS);
        return;
      }
      clearTimeout(timer);
      timer = null;
      try {
        await action();
      } catch (e) {
        note(`Could not do that: ${e.message}`, "error");
        btn.textContent = label;
        btn.classList.remove("armed");
      }
    });
  }

  function setup() {
    $("route-btn").addEventListener("click", () => (v.open ? deps.closed() : show()));
    $("rv-back").addEventListener("click", () => deps.closed());
    $("rv-send").addEventListener("click", send);
    $("rv-clear").addEventListener("click", clearRoute);
    $("rv-fuel-find").addEventListener("click", findFuel);
    $("rv-clear-stops").addEventListener("click", () => change([]));
    $("rv-places-add").addEventListener("click", () => {
      const p = v.places[Number($("rv-places").value)];
      if (p) add({ lat: p.lat, lon: p.lon, name: p.name });
    });
    for (const b of $("route-view").querySelectorAll(".base button")) b.addEventListener("click", () => setBase(b.dataset.base));
    $("rv-limit").textContent = `Up to ${MAX_STOPS} stops, the last the destination.`;
  }

  if (new URLSearchParams(globalThis.location?.search ?? "").get("debug") === "1") {
    // (for the tests: where a place is on the screen, and what the stops are)
    globalThis.__routeDebug = {
      screenPoint: (lat, lon) => {
        const q = v.map.latLngToContainerPoint([lat, lon]);
        const r = $("rvmap").getBoundingClientRect();
        return { x: r.left + q.x, y: r.top + q.y };
      },
      center: () => v.map.getCenter(),
      stops: () => JSON.parse(JSON.stringify(v.stops)),
      mapReady: () => Boolean(v.map),
      planDrawn: () => v.planKey,
      planPoints: () => v.planLayer?.getLayers().find((l) => l.getLatLngs)?.getLatLngs().length ?? 0,
    };
  }

  return { setup, show, hide, setBase, isOpen: () => v.open, drawPlan: () => (v.open ? drawPlan() : undefined), drawCar: () => (v.open ? drawCar() : undefined) };
}

// The Circuits view of the Trips page: draw the lines that make a lap-timing circuit on a map of a drive you did there, and see the laps
// timed on it. The timing itself is the relay's (relay/thrashdash/laps.py); the geometry of the lines is in gates.js (tested in Node).
//
// A circuit is made on a recorded trip: pick one you drove the circuit on, then click its route to put the start line, any sector lines
// and (for a point to point stage) the finish line across it. A line is drawn across the road where you click, facing the way the car was
// going there, so it counts the right way round without a thought.
//
// Everything typed by the user (a circuit's name) is put in with textContent, never as markup (see dom.js).

import { api } from "../api.js";
import { h } from "../dom.js";
import { formatLapTime } from "../widgets/laptimer.js";
import {
  DEFAULT_GATE_WIDTH_M, MAX_GATE_WIDTH_M, MAX_SECTOR_LINES, MIN_GATE_WIDTH_M, circuitProblems, circuitSummary, drawnGates, flipGate, gateCentre, gateOnRoute,
  moveSector, nearestOnRoute, sameLines, travelBearing,
} from "./gates.js";

const COLOURS = { start: "#3ddc84", sector: "#35c2ff", finish: "#ff4d4d" };
const SURE_MS = 4000;

/**
 * @param {object} deps
 * @param {(id: string) => HTMLElement} deps.$ getElementById
 * @param {() => object} deps.state the page's state (trips, client, prefs)
 * @param {(name: string) => any} deps.tileLayerFor a Leaflet tile layer for "street" or "sat"
 * @param {() => string} deps.currentBase
 * @param {(ts: number) => string} deps.when
 * @param {(trip: object) => string} deps.tripTitle
 * @param {() => void} deps.leave called when the view is opened, so the page can hide the others
 * @param {() => void} deps.closed called when the user goes back
 */
export function createCircuits(deps) {
  const { $, state, tileLayerFor, currentBase, when, tripTitle } = deps;
  const v = {
    open: false, circuits: [], enabled: true, selected: null, laps: null, // the saved circuits, the one shown, and its laps
    draft: null, original: null, // the circuit being made or changed, and the saved one it began as
    base: null, route: null, tool: null, width: DEFAULT_GATE_WIDTH_M, // the trip drawn under it, its vertices as [lat, lon], the line being placed, and the width
    map: null, tiles: null, layer: null, routeLayer: null, gateLayer: null, sure: null,
  };

  const note = (text, kind = "") => {
    const el = $("cir-note");
    el.textContent = text;
    el.className = `cir-note ${kind}`.trim();
    el.hidden = !text;
  };

  // ---- the page -------------------------------------------------------------------------------------------------------------------

  async function show(id = null) {
    deps.leave();
    v.open = true;
    document.body.dataset.view = "circuits";
    history.replaceState(null, "", "#circuits");
    $("circuits").hidden = false;
    $("circuits-btn").setAttribute("aria-pressed", "true");
    $("cir-new").hidden = !state().client.local;
    await reload();
    if (id && v.circuits.some((c) => c.id === id)) await select(id);
    else if (!v.selected && !v.draft) showHint();
  }

  function hide() {
    v.open = false;
    $("circuits").hidden = true;
    $("circuits-btn").setAttribute("aria-pressed", "false");
    cancelEdit(false);
  }

  async function reload() {
    try {
      const got = await api.listCircuits();
      v.circuits = got.circuits;
      v.enabled = got.enabled;
    } catch (e) {
      $("cir-sub").textContent = `Could not read the circuits: ${e.message}`;
      return;
    }
    drawList();
  }

  function bestText(circuit) {
    const times = Object.entries(circuit.bests ?? {}).map(([vehicle, b]) => `${vehicle} ${formatLapTime(b.t)}`);
    return times.length ? `Best: ${times.join(", ")}` : "No laps yet";
  }

  function drawList() {
    const list = $("cir-list");
    list.replaceChildren();
    $("cir-sub").textContent = !v.enabled ? "Lap timing is not available in this run." : v.circuits.length === 0 ? "None yet" : `${v.circuits.length} ${v.circuits.length === 1 ? "circuit" : "circuits"}`;
    for (const c of v.circuits) {
      const item = h("li", "", list);
      const button = h("button", "cir-item", item);
      button.type = "button";
      button.dataset.id = c.id;
      button.setAttribute("aria-current", String(c.id === v.selected));
      const head = h("span", "cir-item-head", button);
      h("span", "cir-item-name", head, c.name);
      if (!c.enabled) h("span", "cir-off", head, "off");
      h("span", "cir-item-sub muted", button, `${circuitSummary(c)} · ${c.lapCount} ${c.lapCount === 1 ? "lap" : "laps"}`);
      h("span", "cir-item-sub muted", button, bestText(c));
      button.addEventListener("click", () => select(c.id));
    }
  }

  function showHint() {
    $("cir-hint").hidden = false;
    $("cir-body").hidden = true;
    $("cir-hint").textContent = !v.enabled ? "Lap timing is not available in this run."
      : v.circuits.length ? "Pick a circuit to see its laps."
      : state().client.local ? "No circuits yet. Press New circuit, pick a trip you drove it on, and click its route to place the start line." : "No circuits yet.";
  }

  async function select(id) {
    if (v.draft) cancelEdit(false);
    v.selected = id;
    await reload(); // (laps may have been driven since the list was read: its counts and bests, and what a change would clear, must be current)
    const circuit = v.circuits.find((c) => c.id === id);
    if (!circuit) return showHint();
    try {
      v.laps = await api.circuitLaps(id);
    } catch (e) {
      note(`Could not read the laps: ${e.message}`, "error");
      return;
    }
    drawList();
    $("cir-hint").hidden = true;
    $("cir-body").hidden = false;
    $("cir-editor").hidden = true;
    $("cir-view").hidden = false;
    $("cir-title").textContent = circuit.name;
    note("");
    ensureMap();
    drawGates(circuit, true);
    drawView(circuit);
  }

  // ---- the saved circuit: its laps -------------------------------------------------------------------------------------------------

  function drawView(circuit) {
    const local = state().client.local;
    const actions = $("cir-actions");
    actions.replaceChildren();
    if (local) {
      const on = h("label", "cir-enable", actions);
      const box = h("input", "", on);
      box.type = "checkbox";
      box.checked = circuit.enabled;
      h("span", "", on, "Time laps here");
      box.addEventListener("change", async () => {
        try {
          await api.saveCircuit(circuit.id, { ...circuit, enabled: box.checked });
          await reload();
        } catch (e) {
          note(`Could not save: ${e.message}`, "error");
          box.checked = !box.checked;
        }
      });
      button(actions, "Change the lines", () => startEdit(circuit));
      const clear = button(actions, "Clear laps", () => {}, "btn");
      sure(clear, "Clear laps", `Clear all ${circuit.lapCount} laps?`, async () => {
        await api.clearCircuitLaps(circuit.id);
        await reload();
        await select(circuit.id);
      }, circuit.lapCount === 0);
      const del = button(actions, "Delete", () => {}, "btn danger");
      sure(del, "Delete", "Delete it and its laps?", async () => {
        await api.deleteCircuit(circuit.id);
        v.selected = null;
        await reload();
        showHint();
      });
    }
    drawLaps(circuit);
  }

  function drawLaps(circuit) {
    const box = $("cir-laps");
    box.replaceChildren();
    const laps = [...(v.laps?.laps ?? [])].reverse().slice(0, 200);
    const bests = v.laps?.bests ?? {};
    if (!laps.length) {
      h("p", "empty", box, "No laps yet. Drive over the start line, then round to it again: the first crossing only starts the clock.");
      return;
    }
    const table = h("table", "cir-table", box);
    const head = h("tr", "", h("thead", "", table));
    const sectors = circuit.sectors.length + 1;
    for (const label of ["Lap", "Time", ...(circuit.sectors.length ? Array.from({ length: sectors }, (_, i) => `S${i + 1}`) : []), "Vehicle", "When"]) h("th", "", head, label);
    const body = h("tbody", "", table);
    for (const lap of laps) {
      const isBest = lap.ok && bests[lap.v] && Math.abs(bests[lap.v].t - lap.t) < 0.0005;
      const row = h("tr", `${isBest ? "best" : ""}${lap.ok ? "" : " invalid"}`.trim(), body);
      h("td", "", row, String(lap.n));
      h("td", "cir-time", row, lap.ok ? formatLapTime(lap.t) + (isBest ? " ★" : "") : `${formatLapTime(lap.t)} (not valid)`);
      if (circuit.sectors.length) for (let i = 0; i < sectors; i++) h("td", "", row, lap.s?.[i] === undefined ? "--" : formatLapTime(lap.s[i]));
      h("td", "", row, lap.v ?? "");
      h("td", "muted", row, lap.ts ? when(lap.ts) : "");
    }
  }

  // ---- making or changing a circuit ----------------------------------------------------------------------------------------------

  async function startEdit(circuit = null, tripId = null) {
    if (!state().client.local) return;
    if (!v.open) await show();
    v.original = circuit ? JSON.parse(JSON.stringify(circuit)) : null;
    v.draft = circuit ? JSON.parse(JSON.stringify(circuit)) : { name: "", kind: "circuit", enabled: true, start: null, sectors: [] };
    v.selected = circuit ? circuit.id : null;
    v.tool = null;
    v.base = null;
    v.route = null;
    $("cir-hint").hidden = true;
    $("cir-body").hidden = false;
    $("cir-editor").hidden = false;
    $("cir-view").hidden = true;
    $("cir-title").textContent = circuit ? `Change ${circuit.name}` : "New circuit";
    $("cir-name").value = v.draft.name;
    $("cir-kind").value = v.draft.kind;
    $("cir-width").value = String(v.width);
    fillTripPicker(tripId);
    note("");
    ensureMap();
    drawGates(v.draft, !v.draft.start);
    drawEditor();
    drawList();
    const pick = $("cir-trip");
    if (pick.value) await loadBase(pick.value);
  }

  function fillTripPicker(preferred) {
    const pick = $("cir-trip");
    pick.replaceChildren();
    h("option", "", pick, "Choose a trip…").value = "";
    const trips = state().trips.filter((t) => t.ended && (t.distance ?? 0) > 0);
    for (const t of trips) h("option", "", pick, `${tripTitle(t)} · ${when(t.ts)}`).value = t.id;
    pick.value = preferred && trips.some((t) => t.id === preferred) ? preferred : "";
  }

  async function loadBase(id) {
    v.base = null;
    v.route = null;
    v.routeLayer?.clearLayers();
    if (!id) return;
    try {
      v.base = await api.getTrip(id);
    } catch (e) {
      note(`Could not read that trip: ${e.message}`, "error");
      return;
    }
    v.route = v.base.points.map((p) => [p.la, p.lo]);
    if (v.route.length < 2) {
      note("That trip has no route to draw on.", "error");
      v.route = null;
      return;
    }
    L.polyline(v.route, { color: "#8aa0b8", weight: 4, opacity: 0.9, lineCap: "round", lineJoin: "round" }).addTo(v.routeLayer);
    if (!v.draft.start) v.map.fitBounds(L.latLngBounds(v.route), { padding: [30, 30], maxZoom: 17 });
    note(v.draft.start ? "" : "Now press Start/finish line, then click the route where the lap begins.");
  }

  function cancelEdit(redraw = true) {
    v.draft = null;
    v.original = null;
    v.tool = null;
    v.base = null;
    v.route = null;
    v.routeLayer?.clearLayers();
    $("cir-editor").hidden = true;
    if (redraw && v.open) {
      const circuit = v.circuits.find((c) => c.id === v.selected);
      if (circuit) select(circuit.id);
      else showHint();
    }
  }

  function drawEditor() {
    const d = v.draft;
    if (!d) return;
    $("cir-finish-tool").hidden = d.kind !== "stage";
    $("cir-start-tool").textContent = d.kind === "stage" ? "Start line" : "Start/finish line";
    for (const [id, tool] of [["cir-start-tool", "start"], ["cir-sector-tool", "sector"], ["cir-finish-tool", "finish"]]) $(id).setAttribute("aria-pressed", String(v.tool === tool));
    $("cir-sector-tool").disabled = d.sectors.length >= MAX_SECTOR_LINES;
    const list = $("cir-gates");
    list.replaceChildren();
    for (const line of drawnGates(d)) {
      const row = h("li", "cir-gate", list);
      const swatch = h("span", "cir-swatch", row);
      swatch.style.background = COLOURS[line.role];
      h("span", "cir-gate-label", row, line.label);
      const flip = button(row, "⇄", () => flip1(line), "btn small", "Count the other way across this line");
      flip.setAttribute("aria-label", `${line.label}: count the other way`);
      if (line.role === "sector") {
        const up = button(row, "↑", () => moveLine(line.index, -1), "btn small", "Earlier in the lap");
        const down = button(row, "↓", () => moveLine(line.index, 1), "btn small", "Later in the lap");
        up.disabled = line.index === 0;
        down.disabled = line.index === d.sectors.length - 1;
        up.setAttribute("aria-label", `${line.label}: earlier`);
        down.setAttribute("aria-label", `${line.label}: later`);
      }
      const gone = button(row, "✕", () => removeLine(line), "btn small danger", "Remove this line");
      gone.setAttribute("aria-label", `Remove ${line.label}`);
    }
    $("cir-problems").textContent = circuitProblems({ ...d, name: $("cir-name").value }).join(" ");
    drawGates(d, false);
  }

  function flip1(line) {
    const d = v.draft;
    if (line.role === "start") d.start = flipGate(d.start);
    else if (line.role === "finish") d.finish = flipGate(d.finish);
    else d.sectors[line.index] = flipGate(d.sectors[line.index]);
    drawEditor();
  }

  function moveLine(index, step) {
    v.draft.sectors = moveSector(v.draft.sectors, index, step);
    drawEditor();
  }

  function removeLine(line) {
    const d = v.draft;
    if (line.role === "start") d.start = null;
    else if (line.role === "finish") d.finish = null;
    else d.sectors.splice(line.index, 1);
    drawEditor();
  }

  function armTool(tool) {
    v.tool = v.tool === tool ? null : tool;
    if (v.tool && !v.route) note("Choose a trip to draw on first, then click its route.", "error");
    else if (v.tool) note(`Click the route where the ${tool === "start" ? (v.draft.kind === "stage" ? "start" : "start/finish") : tool === "finish" ? "finish" : "sector"} line goes.`);
    else note("");
    drawEditor();
  }

  function placeAt(latlng) {
    if (!v.draft || !v.tool || !v.route) return;
    const pixels = v.route.map((p) => {
      const q = v.map.latLngToContainerPoint(p);
      return [q.x, q.y];
    });
    const click = v.map.latLngToContainerPoint(latlng);
    const hit = nearestOnRoute(pixels, [click.x, click.y]);
    if (!hit) {
      note("That is not on the route: click closer to the grey line.", "error");
      return;
    }
    const gate = gateOnRoute(v.route, hit, v.width);
    const d = v.draft;
    if (v.tool === "start") d.start = gate;
    else if (v.tool === "finish") d.finish = gate;
    else if (d.sectors.length < MAX_SECTOR_LINES) d.sectors.push(gate);
    // After a sector line, stay armed: a circuit has several. After a start or finish line, put the tool down.
    if (v.tool !== "sector") v.tool = null;
    note("");
    drawEditor();
  }

  async function save() {
    const d = v.draft;
    if (!d) return;
    d.name = $("cir-name").value;
    const problems = circuitProblems(d);
    if (problems.length) return note(problems.join(" "), "error");
    const body = { name: d.name, kind: d.kind, enabled: d.enabled !== false, start: d.start, sectors: d.sectors, ...(d.kind === "stage" ? { finish: d.finish } : {}) };
    const saveButton = $("cir-save");
    const laps = v.original?.lapCount ?? 0;
    if (v.original && laps > 0 && !sameLines(d, v.original) && v.sure !== saveButton) {
      // Different lines mean the old laps were timed over something else: they would be cleared. Ask once.
      v.sure = saveButton;
      saveButton.textContent = `Clear ${laps} ${laps === 1 ? "lap" : "laps"} and save`;
      saveButton.classList.add("armed");
      setTimeout(() => resetSave(), SURE_MS);
      return note("Changing the lines clears this circuit's laps, because they were timed over the old lines. Press again to go ahead.", "error");
    }
    resetSave();
    try {
      let id = v.original?.id;
      if (id) await api.saveCircuit(id, body);
      else id = (await api.createCircuit(body)).id;
      v.draft = null;
      v.tool = null;
      await reload();
      $("cir-editor").hidden = true;
      await select(id);
    } catch (e) {
      note(`Could not save: ${e.message}`, "error");
    }
  }

  function resetSave() {
    if (v.sure === null) return;
    v.sure = null;
    const b = $("cir-save");
    b.textContent = "Save";
    b.classList.remove("armed");
  }

  // ---- the map -------------------------------------------------------------------------------------------------------------------

  function ensureMap() {
    if (v.map || typeof L === "undefined") return;
    v.map = L.map("cirmap", { preferCanvas: false, worldCopyJump: false });
    v.map.attributionControl.setPrefix('<a href="https://leafletjs.com" target="_blank" rel="noopener noreferrer">Leaflet</a>');
    v.routeLayer = L.layerGroup().addTo(v.map);
    v.gateLayer = L.layerGroup().addTo(v.map);
    v.map.setView([0, 0], 2);
    setBase(currentBase());
    v.map.on("click", (e) => placeAt(e.latlng));
  }

  function setBase(name) {
    if (!v.map) return;
    if (v.tiles) v.map.removeLayer(v.tiles);
    v.tiles = tileLayerFor(name).addTo(v.map);
    v.tiles.bringToBack();
  }

  /** Draw a circuit's lines, each with an arrow for the way it counts and its label. `fit` moves the map to them. */
  function drawGates(circuit, fit) {
    if (!v.map) return;
    v.map.invalidateSize();
    v.gateLayer.clearLayers();
    const points = [];
    for (const line of drawnGates(circuit)) {
      const { gate } = line;
      L.polyline([gate.a, gate.b], { color: COLOURS[line.role], weight: 6, opacity: 0.95, lineCap: "butt", interactive: false }).addTo(v.gateLayer);
      const arrow = h("div", "cir-arrow");
      arrow.style.setProperty("--turn", `${travelBearing(gate)}deg`);
      arrow.style.setProperty("--colour", COLOURS[line.role]);
      h("span", "cir-arrow-tip", arrow, "▲");
      h("span", "cir-arrow-label", arrow, line.label);
      L.marker(gateCentre(gate), { icon: L.divIcon({ html: arrow, className: "cir-marker", iconSize: [60, 60], iconAnchor: [30, 30] }), interactive: false, keyboard: false }).addTo(v.gateLayer);
      points.push(gate.a, gate.b);
    }
    if (fit && points.length) v.map.fitBounds(L.latLngBounds(points), { padding: [60, 60], maxZoom: 18 });
  }

  // ---- small helpers -------------------------------------------------------------------------------------------------------------

  function button(parent, label, onClick, cls = "btn", title = "") {
    const b = h("button", cls, parent, label);
    b.type = "button";
    if (title) b.title = title;
    b.addEventListener("click", onClick);
    return b;
  }

  /** A button that must be pressed twice: it asks the first time (its label changes), and acts the second. */
  function sure(btn, label, question, action, disabled = false) {
    btn.disabled = disabled;
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
    $("circuits-btn").addEventListener("click", () => (v.open ? deps.closed() : show()));
    $("cir-back").addEventListener("click", () => deps.closed());
    $("cir-new").addEventListener("click", () => startEdit(null, state().selected));
    $("cir-name").addEventListener("input", () => drawEditor());
    $("cir-kind").addEventListener("change", () => {
      v.draft.kind = $("cir-kind").value;
      if (v.draft.kind !== "stage") {
        v.draft.finish = null;
        if (v.tool === "finish") v.tool = null;
      }
      drawEditor();
    });
    $("cir-trip").addEventListener("change", () => loadBase($("cir-trip").value));
    $("cir-width").addEventListener("change", () => {
      const n = Number($("cir-width").value);
      v.width = Number.isFinite(n) ? Math.min(MAX_GATE_WIDTH_M, Math.max(MIN_GATE_WIDTH_M, n)) : DEFAULT_GATE_WIDTH_M;
      $("cir-width").value = String(v.width);
    });
    $("cir-start-tool").addEventListener("click", () => armTool("start"));
    $("cir-sector-tool").addEventListener("click", () => armTool("sector"));
    $("cir-finish-tool").addEventListener("click", () => armTool("finish"));
    $("cir-save").addEventListener("click", save);
    $("cir-cancel").addEventListener("click", () => cancelEdit(true));
    for (const b of $("circuits").querySelectorAll(".base button")) b.addEventListener("click", () => setBase(b.dataset.base));
    $("cir-width").min = String(MIN_GATE_WIDTH_M);
    $("cir-width").max = String(MAX_GATE_WIDTH_M);
  }

  if (new URLSearchParams(globalThis.location?.search ?? "").get("debug") === "1") {
    // (for the tests: where a point of the drawn route is on the screen, and what is being edited)
    globalThis.__circuitsDebug = {
      routePoint: (i) => {
        const q = v.map.latLngToContainerPoint(v.route[i]);
        const r = $("cirmap").getBoundingClientRect();
        return { x: r.left + q.x, y: r.top + q.y };
      },
      routeLength: () => v.route?.length ?? 0,
      draft: () => JSON.parse(JSON.stringify(v.draft)),
      mapReady: () => Boolean(v.map),
    };
  }

  return { setup, show, hide, setBase, isOpen: () => v.open, startFromTrip: (circuit, tripId) => startEdit(circuit, tripId), drawEditor };
}

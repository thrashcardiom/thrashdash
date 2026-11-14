import { h } from "../dom.js";

// A live map of the trip being driven, for a dashboard (BeamNG.Earth only). It is the Trips page's own map in a compact mode
// (/trips?embed=1): the same code follows the car, turns the map heading-up and draws the route, so there is nothing to keep in
// step. It shows the trip being recorded, and "Waiting for a trip" until one starts. Like every widget it is placed and sized in
// the dashboard editor, and saved with the dashboard (Save, Save as).

export const spec = {
  label: "Live map (BeamNG.Earth)",
  size: { w: 420, h: 300 },
  defaults: { headUp: true, zoom: 17, satellite: false },
  props: {
    headUp: { type: "bool", label: "Heading up (the car points up and the map turns)" },
    zoom: { type: "number", label: "Zoom when following the car", min: 12, max: 19, int: true },
    satellite: { type: "bool", label: "Satellite picture (blurry close up) instead of the street map" },
  },
};

export function create(def) {
  const root = h("div", "w w-tripmap");
  const params = new URLSearchParams({ embed: "1", headup: def.headUp === false ? "0" : "1", zoom: String(def.zoom ?? 17), base: def.satellite ? "sat" : "street" });
  const frame = h("iframe", "tm-frame", root);
  frame.title = "Live map of the trip being driven";
  frame.src = `/trips?${params}`;
  return { el: root, update() {} }; // (the map reads the trip itself; the dashboard's telemetry is not needed)
}

// Widget registry: layout entries name a `type`, this maps it to its implementation and to its
// property spec (what the editor shows, and what the layout sanitiser accepts).
// A new widget type is one file plus one line here.
import * as roundGauge from "./roundGauge.js";
import * as bar from "./bar.js";
import * as gear from "./gear.js";
import * as light from "./light.js";
import * as readout from "./readout.js";
import * as text from "./text.js";
import * as graph from "./graph.js";
import * as tyres from "./tyres.js";
import * as damage from "./damage.js";
import * as inputs from "./inputs.js";
import * as gmeter from "./gmeter.js";
import * as trip from "./trip.js";

export const registry = { roundGauge, bar, gear, light, readout, graph, text, tyres, damage, inputs, gmeter, trip };

/** type -> spec, for code that only needs the property descriptions (no DOM). */
export const specs = Object.fromEntries(Object.entries(registry).map(([type, impl]) => [type, impl.spec]));

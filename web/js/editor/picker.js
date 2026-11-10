// Channel picker: search the channels the game is sending right now, with their live values.
import { h } from "../dom.js";
import { CHANNEL_RE } from "../layoutSchema.js";
import { formatLive, groupChannels } from "./channels.js";

let current = null;

function closePicker(result) {
  if (!current) return;
  const { backdrop, resolve, timer, onKey } = current;
  current = null;
  clearInterval(timer);
  document.removeEventListener("keydown", onKey, true);
  backdrop.remove();
  resolve(result);
}

/**
 * Open the picker. Resolves with the chosen channel name, or null if cancelled.
 * @param {{channels: Record<string,string>, getValues: () => Record<string, any>, selected?: string, title?: string, connected?: boolean}} opts
 */
export function pickChannel({ channels, getValues, selected, title = "Choose a channel", connected = true }) {
  if (current) closePicker(null);
  return new Promise((resolve) => {
    const backdrop = h("div", "dlg-backdrop", document.body);
    const box = h("div", "dlg picker", backdrop);
    h("h2", "dlg-title", box, title);
    const search = h("input", "dlg-input", box);
    search.type = "search";
    search.placeholder = "Search channels (e.g. rpm, temp, fuel)";
    search.setAttribute("aria-label", "Search channels");
    const status = h("p", "dlg-message pk-status", box);
    const list = h("div", "pk-list", box);
    const row = h("div", "dlg-buttons", box);
    const cancel = h("button", "btn", row, "Cancel");
    cancel.type = "button";
    cancel.addEventListener("click", () => closePicker(null));

    let showMinor = false;
    let liveCells = new Map(); // channel name -> value element, refreshed while open
    let firstPick = null;

    const refreshLive = () => {
      const values = getValues();
      for (const [name, el] of liveCells) el.textContent = formatLive(values[name]) || "--";
    };

    function render() {
      list.replaceChildren();
      liveCells = new Map();
      const q = search.value.trim();
      const groups = groupChannels(channels, q);
      const total = Object.values(groups).reduce((n, list) => n + list.length, 0);
      firstPick = null;

      const addGroup = (heading, entries) => {
        if (!entries.length) return;
        h("div", "pk-heading", list, `${heading} (${entries.length})`);
        for (const e of entries) {
          const item = h("button", `pk-row${e.name === selected ? " pk-selected" : ""}`, list);
          item.type = "button";
          const names = h("span", "pk-names", item);
          h("span", "pk-label", names, e.label ?? e.name);
          if (e.label) h("span", "pk-raw", names, e.name);
          const live = h("span", "pk-live", item, "--");
          liveCells.set(e.name, live);
          item.addEventListener("click", () => closePicker(e.name));
          firstPick ??= e.name;
        }
      };
      addGroup("Common", groups.common);
      addGroup("Tyres", groups.tires);
      addGroup("Damage", groups.damage);
      addGroup("Component states", groups.states);
      addGroup("Trip computer", groups.trip);
      addGroup("This vehicle", groups.car);

      // Minor channels stay out of the way unless asked for (or searched for).
      if (groups.minor.length) {
        if (showMinor || q) addGroup("Other (rarely useful)", groups.minor);
        else {
          const more = h("button", "pk-more", list, `Show ${groups.minor.length} other channels (couplers, filaments...)`);
          more.type = "button";
          more.addEventListener("click", () => {
            showMinor = true;
            render();
          });
        }
      }

      // Allow typing a channel that is not in the current data (car not spawned, or another car's channel).
      if (q && CHANNEL_RE.test(q) && !Object.hasOwn(channels, q)) {
        const item = h("button", "pk-row pk-custom", list);
        item.type = "button";
        h("span", "pk-label", item, `Use "${q}" anyway`);
        h("span", "pk-raw", item, "not in the current data");
        item.addEventListener("click", () => closePicker(q));
        firstPick ??= q;
      }
      if (!total && !(q && CHANNEL_RE.test(q))) h("p", "dlg-message", list, q ? "No channel matches that search." : "No data yet.");
      status.textContent = Object.keys(channels).length
        ? connected ? "Values are live." : "The game is not sending data right now; values show --."
        : "No channels yet: start the game and sit in a vehicle, or run ThrashDash with --simulate. You can still type a channel name.";
      refreshLive();
    }

    const onKey = (e) => {
      if (e.key === "Escape") {
        e.preventDefault();
        e.stopPropagation();
        closePicker(null);
      } else if (e.key === "Enter" && firstPick && e.target === search) {
        e.preventDefault();
        e.stopPropagation();
        closePicker(firstPick);
      }
    };
    backdrop.addEventListener("pointerdown", (e) => {
      if (e.target === backdrop) closePicker(null);
    });
    search.addEventListener("input", render);
    document.addEventListener("keydown", onKey, true);
    current = { backdrop, resolve, onKey, timer: setInterval(refreshLive, 250) };
    render();
    search.focus();
  });
}

export const pickerOpen = () => current !== null;

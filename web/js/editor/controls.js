// Property controls, generated from a widget's spec. One control per spec type:
//   channel / channels -> picker button(s)      number -> number field      unitNumber -> one field per unit
//   text -> text field   color -> theme/hex     select -> dropdown          bool -> checkbox
// Controls write through EditorState.setProp, which validates against the same spec, so a control can
// never put a value into the layout that the sanitiser would later reject.
import { h } from "../dom.js";
import { UNIT_OPTIONS } from "../units.js";
import { describeChannel, suggestKind } from "./channels.js";

const THEME_COLOURS = [
  ["var(--accent)", "Blue (accent)"],
  ["var(--ok)", "Green"],
  ["var(--warn)", "Amber"],
  ["var(--crit)", "Red"],
  ["var(--text)", "White (text)"],
  ["var(--muted)", "Grey (muted)"],
];

/**
 * @param {object} ctx {state, pick(opts) -> Promise<string|null>, catalog() -> {channels}, rebuild() -> void}
 * @returns {HTMLElement} one labelled row
 */
export function buildControl(ctx, widget, key, ps) {
  const { state } = ctx;
  const id = widget.id;
  const spec = state.specs[widget.type];
  const hasValue = () => key in state.widget(id);
  const current = () => state.widget(id)[key];
  const defaultValue = spec.defaults?.[key];

  const row = h("div", "pr");
  h("label", "pr-label", row, ps.label);
  const body = h("div", "pr-body", row);
  const error = h("div", "pr-error", row);
  error.hidden = true;

  const reset = h("button", "pr-reset", row, "×");
  reset.type = "button";
  reset.title = "Reset to default";
  reset.setAttribute("aria-label", `Reset ${ps.label}`);
  reset.hidden = !hasValue();

  /** Write a value (or undefined to remove it) and reflect success/failure on the row. */
  const commit = (value) => {
    const res = state.setProp(id, key, value);
    error.hidden = res.ok;
    error.textContent = res.ok ? "" : res.reason;
    reset.hidden = !hasValue();
    return res;
  };
  reset.addEventListener("click", () => {
    commit(undefined);
    ctx.rebuild(); // redraw so the control shows the default again
  });

  switch (ps.type) {
    case "channel": {
      const btn = h("button", "pr-pick", body);
      btn.type = "button";
      const warn = h("div", "pr-warn", body);
      const show = () => {
        const name = current();
        btn.textContent = name ? describeChannel(name) ?? name : "Choose channel...";
        btn.title = name ?? "";
        const channels = ctx.catalog().channels;
        warn.textContent = name && Object.keys(channels).length && !Object.hasOwn(channels, name) ? "Not in the data right now" : "";
      };
      btn.addEventListener("click", async () => {
        const picked = await ctx.pick({ title: ps.label, selected: current() });
        if (!picked) return;
        if (commit(picked).ok) {
          // Convenience: picking e.g. "wheelspeed" on a widget with a unit type switches it to "speed".
          const hint = suggestKind(picked);
          if (hint && "kind" in spec.props && key === "channel" && (current_(state, id).kind ?? "none") === "none" && hint !== "none") {
            state.setProp(id, "kind", hint);
            ctx.rebuild();
          } else show();
        }
      });
      show();
      break;
    }

    case "channels": {
      const chips = h("div", "pr-chips", body);
      const draw = () => {
        chips.replaceChildren();
        for (const name of current() ?? []) {
          const chip = h("span", "pr-chip", chips, describeChannel(name) ?? name);
          chip.title = name;
          const x = h("button", "pr-chip-x", chip, "×");
          x.type = "button";
          x.setAttribute("aria-label", `Remove ${name}`);
          x.addEventListener("click", () => {
            const next = (current() ?? []).filter((n) => n !== name);
            commit(next.length ? next : undefined);
            draw();
          });
        }
        const add = h("button", "pr-add", chips, "+ add");
        add.type = "button";
        add.addEventListener("click", async () => {
          const picked = await ctx.pick({ title: ps.label });
          if (!picked) return;
          const list = current() ?? [];
          if (!list.includes(picked)) commit([...list, picked]);
          draw();
        });
      };
      draw();
      break;
    }

    case "number": {
      const input = h("input", "pr-input", body);
      input.type = "number";
      if (ps.min !== undefined) input.min = ps.min;
      if (ps.max !== undefined) input.max = ps.max;
      input.step = ps.int ? "1" : "any";
      input.placeholder = defaultValue !== undefined ? String(defaultValue) : "default";
      if (hasValue()) input.value = current();
      input.addEventListener("input", () => {
        if (input.value.trim() === "") return void commit(undefined);
        const n = Number(input.value);
        if (Number.isFinite(n)) commit(n);
      });
      input.addEventListener("change", () => {
        input.value = hasValue() ? current() : ""; // show what was actually stored (it may have been clamped)
      });
      break;
    }

    case "unitNumber": {
      const kind = ps.unitKind ?? widget.kind ?? "none"; // a property may fix its own unit kind (e.g. tyre pressure)
      const units = UNIT_OPTIONS[kind] ?? UNIT_OPTIONS.none;
      const box = h("div", "pr-units", body);
      const valueFor = (unitKey) => {
        const v = hasValue() ? current() : defaultValue;
        if (typeof v === "number") return v;
        return v && typeof v === "object" && unitKey in v ? v[unitKey] : "";
      };
      for (const u of units) {
        const wrap = h("label", "pr-unit", box);
        if (units.length > 1) h("span", "pr-unit-label", wrap, u.label);
        const input = h("input", "pr-input", wrap);
        input.type = "number";
        input.step = "any";
        input.value = valueFor(u.key);
        input.addEventListener("input", () => {
          const prev = hasValue() ? current() : defaultValue;
          const text = input.value.trim();
          const n = Number(text);
          if (text !== "" && !Number.isFinite(n)) return;
          if (units.length === 1) return void commit(text === "" ? undefined : n);
          // Several units: store {unit: value}. A plain number counts as "same value for every unit".
          const next = typeof prev === "number" ? Object.fromEntries(units.map((x) => [x.key, prev])) : { ...(prev && typeof prev === "object" ? prev : {}) };
          if (text === "") delete next[u.key];
          else next[u.key] = n;
          commit(Object.keys(next).length ? next : undefined);
        });
      }
      break;
    }

    case "text": {
      const input = h("input", "pr-input", body);
      input.type = "text";
      input.maxLength = ps.max ?? 100;
      input.placeholder = defaultValue !== undefined ? String(defaultValue) : "default";
      if (hasValue()) input.value = current();
      input.addEventListener("input", () => commit(input.value));
      break;
    }

    case "textList": {
      const input = h("input", "pr-input", body);
      input.type = "text";
      input.placeholder = "e.g. E, \u00bd, F";
      if (hasValue()) input.value = current().join(", ");
      input.addEventListener("input", () => {
        if (input.value.trim() === "") return void commit(undefined);
        commit(input.value.split(",").map((part) => part.trim())); // "E,,F" keeps the gap in the middle
      });
      break;
    }

    case "select": {
      const select = h("select", "pr-input", body);
      const unset = h("option", "", select, "(default)");
      unset.value = "__default__";
      for (const opt of ps.options) {
        const o = h("option", "", select, opt === "" ? "(none)" : opt);
        o.value = opt;
      }
      select.value = hasValue() ? current() : "__default__";
      select.addEventListener("change", () => {
        commit(select.value === "__default__" ? undefined : select.value);
        if (key === "kind") ctx.rebuild(); // unit-aware fields below depend on the unit type
      });
      break;
    }

    case "bool": {
      const box = h("input", "pr-check", body);
      box.type = "checkbox";
      box.checked = hasValue() ? current() === true : defaultValue === true;
      box.addEventListener("change", () => commit(box.checked));
      break;
    }

    case "color": {
      const select = h("select", "pr-input", body);
      const unset = h("option", "", select, "(default)");
      unset.value = "__default__";
      for (const [value, label] of THEME_COLOURS) {
        const o = h("option", "", select, label);
        o.value = value;
      }
      const custom = h("option", "", select, "Custom colour...");
      custom.value = "__custom__";
      const picker = h("input", "pr-colour", body);
      picker.type = "color";
      const v = hasValue() ? current() : null;
      const isHex = typeof v === "string" && v.startsWith("#");
      select.value = v === null ? "__default__" : isHex ? "__custom__" : v;
      picker.hidden = !isHex;
      if (isHex && /^#[0-9a-fA-F]{6}$/.test(v)) picker.value = v;
      select.addEventListener("change", () => {
        picker.hidden = select.value !== "__custom__";
        if (select.value === "__default__") commit(undefined);
        else if (select.value === "__custom__") commit(picker.value);
        else commit(select.value);
      });
      picker.addEventListener("input", () => commit(picker.value));
      break;
    }

    default:
      body.textContent = `(unsupported property type ${ps.type})`;
  }
  return row;
}

const current_ = (state, id) => state.widget(id) ?? {};

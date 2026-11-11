// The Dashboards menu shown in normal view: switch dashboards, delete saved ones, choose the look of the open one,
// and set up per-vehicle dashboards. (Arranging widgets is in the editor; this is the quick menu for while driving.)
import { h, s } from "./dom.js";
import { dialogOpen } from "./editor/dialog.js";
import { qrSvg } from "./qr.js";

const TRASH = ["M5 7h14", "M10 7V5h4v2", "M7 7l1 12h8l1-12", "M10 11v5M14 11v5"];

/**
 * @param {object} deps
 *   button, menu            the toggle button and the menu element
 *   api                     the relay client (listLayouts, getSettings, saveSettings)
 *   currentId()             id of the dashboard open in this window
 *   load(id)                show another dashboard in this window (pins the window to it)
 *   remove(layout)          ask for confirmation and delete a saved dashboard; resolves true if it was deleted
 *   openWindow(id)          open a new window, the same size as this one, showing a dashboard
 *   skins(), currentSkin(), setSkin(id)    the looks, and the one in use for the open dashboard
 *   vehicle()               the vehicle key being driven ("etkc"), or null
 *   pinned()                true if this window was set to one dashboard (so it does not follow vehicle rules)
 *   follow()                make this window follow the vehicle rules again
 *   onToggle(open)          optional
 */
export function createSwitcher({ button, menu, api, currentId, load, remove, openWindow, skins, currentSkin, setSkin, vehicle, pinned, follow, onToggle = () => {} }) {
  let isOpen = false;
  let items = []; // ids in display order, for the 1-9 shortcuts
  let networkError = ""; // why the last attempt to switch network access on failed
  let pinMessage = ""; // what to say under the PIN field after saving it (or failing to)

  const buttons = () => [...menu.querySelectorAll(".dm-item, .dm-skin, .dm-action")].filter((b) => !b.disabled);

  function focusMove(delta) {
    const list = buttons();
    if (!list.length) return;
    const at = list.indexOf(document.activeElement);
    list[(at + delta + list.length) % list.length].focus();
  }

  async function choose(id) {
    hide();
    try {
      await load(id);
    } catch (e) {
      console.warn("ThrashDash: could not load dashboard", id, e);
    }
  }

  async function saveSetting(patch) {
    try {
      await api.saveSettings(patch);
    } catch (e) {
      console.warn("ThrashDash: could not save the setting", e);
    }
    await refresh(); // redraw so the menu shows what is now stored
  }

  async function setNetwork(enabled) {
    networkError = "";
    try {
      await api.saveSettings({ lan: enabled });
    } catch (e) {
      networkError = e.message; // e.g. no network address, or the port is already in use
    }
    await refresh();
    menu.querySelector(".dm-net, .dm-error")?.scrollIntoView({ block: "nearest" }); // show the QR code (or the reason it failed)
  }

  async function requestDelete(layout) {
    if (await remove(layout)) await refresh();
  }

  function render(layouts, settings, client, lan = { pin: { set: false, pin: "" }, token: null }) {
    const local = client.local; // only the computer running ThrashDash may change things; a phone or tablet can only look
    menu.replaceChildren();
    items = layouts.map((l) => l.id);
    h("div", "dm-title", menu, "Dashboards");
    layouts.forEach((l, i) => {
      const row = h("div", "dm-row", menu);
      const item = h("button", `dm-item${l.id === currentId() ? " dm-current" : ""}`, row);
      item.type = "button";
      item.setAttribute("role", "menuitem");
      item.dataset.id = l.id;
      item.dataset.deletable = String(!l.builtin);
      h("span", "dm-num", item, i < 9 ? String(i + 1) : "");
      h("span", "dm-name", item, l.name);
      const used = Object.entries(settings.vehicleDashboards ?? {}).filter(([, id]) => id === l.id).map(([v]) => v);
      const tags = [l.builtin ? "built-in" : null, l.id === settings.defaultLayout ? "★ default" : null, used.length ? `for ${used.join(", ")}` : null].filter(Boolean).join(" · ");
      if (tags) h("span", "dm-tag", item, tags);
      if (l.id === currentId()) h("span", "dm-check", item, "✓");
      item.addEventListener("click", () => choose(l.id));
      if (!l.builtin && local) {
        // Your own dashboards can be deleted from here; the built-in ones are read-only.
        const del = h("button", "dm-del", row);
        del.type = "button";
        del.title = `Delete "${l.name}"`;
        del.setAttribute("aria-label", `Delete ${l.name}`);
        const svg = s("svg", { viewBox: "0 0 24 24", width: 15, height: 15, fill: "none", stroke: "currentColor", "stroke-width": 1.8, "stroke-linecap": "round", "stroke-linejoin": "round" }, del);
        for (const d of TRASH) s("path", { d }, svg);
        del.addEventListener("click", () => requestDelete(l));
      }
    });
    if (!layouts.length) h("div", "dm-empty", menu, "No saved dashboards.");

    // The look of the OPEN dashboard. Choosing one restyles it in place; its layout is untouched.
    h("div", "dm-title dm-title-sep", menu, "Look of this dashboard");
    const grid = h("div", "dm-skins", menu);
    for (const skin of skins()) {
      const b = h("button", `dm-skin${skin.id === currentSkin() ? " dm-current" : ""}`, grid, skin.label);
      b.type = "button";
      b.setAttribute("role", "menuitemradio");
      b.setAttribute("aria-checked", String(skin.id === currentSkin()));
      b.addEventListener("click", async () => {
        try {
          await setSkin(skin.id);
        } catch (e) {
          console.warn("ThrashDash: could not change the skin", e);
        }
        grid.querySelectorAll(".dm-skin").forEach((el) => {
          const on = el === b;
          el.classList.toggle("dm-current", on);
          el.setAttribute("aria-checked", String(on));
        });
      });
    }

    // Per-vehicle dashboards: remember which dashboard goes with the vehicle being driven.
    h("div", "dm-title dm-title-sep", menu, "Vehicle");
    const key = vehicle();
    const rule = key ? settings.vehicleDashboards?.[key] : null;
    const forThis = key && rule === currentId();
    h("div", "dm-vehicle", menu, key ? `Driving: ${key}` : "No vehicle yet (start the game and get in a car)");
    if (local) {
      const assign = h("button", "dm-action dm-wide", menu, forThis ? `\u2713 Used for ${key} (click to stop)` : key ? `Use this dashboard for ${key}` : "Use this dashboard for this vehicle");
      assign.type = "button";
      assign.disabled = !key || !currentId();
      assign.addEventListener("click", () => saveSetting({ vehicleDashboards: { [key]: forThis ? null : currentId() } }));
      const auto = h("button", "dm-action dm-wide", menu, `${settings.autoSwitch ? "\u2611" : "\u2610"} Switch automatically when the vehicle changes`);
      auto.type = "button";
      auto.setAttribute("role", "menuitemcheckbox");
      auto.setAttribute("aria-checked", String(settings.autoSwitch));
      auto.addEventListener("click", () => saveSetting({ autoSwitch: !settings.autoSwitch }));
    }
    if (pinned()) {
      const unpin = h("button", "dm-action dm-wide", menu, "Follow the vehicle rules in this window");
      unpin.type = "button";
      unpin.title = "This window was set to one dashboard. Click to let it switch with the vehicle again.";
      unpin.addEventListener("click", async () => {
        hide();
        await follow();
      });
    }

    if (local) {
      // Serving to phones and tablets on the same network (switched on and off here, no restart). It always needs a PIN.
      h("div", "dm-title dm-title-sep", menu, "Phones and other devices");
      h("div", "dm-note", menu, "Devices on your network must enter this PIN (4 to 8 digits) before they can see anything.");
      const pinRow = h("div", "dm-pin", menu);
      const pinInput = h("input", "dm-pin-input", pinRow);
      pinInput.type = "text";
      pinInput.inputMode = "numeric";
      pinInput.maxLength = 8;
      pinInput.pattern = "[0-9]*";
      pinInput.autocomplete = "off";
      pinInput.placeholder = "PIN";
      pinInput.value = lan.pin.pin;
      pinInput.setAttribute("aria-label", "PIN for phones and tablets");
      const savePin = h("button", "dm-action dm-pin-save", pinRow, "Save PIN");
      savePin.type = "button";
      const saveThePin = async () => {
        const value = pinInput.value.trim();
        if (!/^[0-9]{4,8}$/.test(value)) {
          pinMessage = "The PIN must be 4 to 8 digits.";
          return refresh();
        }
        try {
          const result = await api.setLanPin(value);
          pinMessage = result.error ? `PIN saved, but network access could not start: ${result.error}` : lan.pin.set ? "PIN saved. Every phone must sign in again." : "PIN saved.";
        } catch (e) {
          pinMessage = e.message;
        }
        await refresh();
      };
      savePin.addEventListener("click", saveThePin);
      pinInput.addEventListener("keydown", (e) => {
        if (e.key === "Enter") {
          e.preventDefault();
          saveThePin();
        }
      });
      if (pinMessage) h("div", "dm-note dm-pin-status", menu, pinMessage);
      pinMessage = "";

      const net = h("button", "dm-action dm-wide", menu, `${client.lan.enabled ? "\u2611" : "\u2610"} Serve to devices on my network`);
      net.type = "button";
      net.setAttribute("role", "menuitemcheckbox");
      net.setAttribute("aria-checked", String(client.lan.enabled));
      net.disabled = !lan.pin.set && !client.lan.enabled; // no PIN, no network access
      net.title = net.disabled ? "Choose a PIN first" : "";
      net.addEventListener("click", () => setNetwork(!client.lan.enabled));
      if (!lan.pin.set) h("div", "dm-note", menu, "Save a PIN above to be able to switch this on.");
      if (networkError) h("div", "dm-error", menu, networkError);
      if (client.lan.enabled && client.lan.urls.length) {
        const withDashboard = (base) => base + (currentId() ? `?d=${encodeURIComponent(currentId())}` : "");
        // The QR code also carries a single-use sign-in token (good for a few minutes), so scanning it needs no PIN typing.
        // The address shown as text has no token in it: anyone typing it in is asked for the PIN.
        const qrAddress = new URL(withDashboard(client.lan.urls[0]));
        if (lan.token) qrAddress.searchParams.set("t", lan.token);
        const box = h("div", "dm-net", menu);
        try {
          box.appendChild(qrSvg(qrAddress.toString(), { size: 176 }));
        } catch (e) {
          console.warn("ThrashDash: could not draw the QR code", e);
        }
        const text = h("div", "dm-urls", box);
        h("div", "dm-note", text, "Scan with your phone's camera (signs in at once), or type an address and the PIN:");
        for (const base of client.lan.urls) h("div", "dm-url", text, withDashboard(base));
        h("div", "dm-note", menu, "The QR code works once, for about 5 minutes: close and reopen this menu for a fresh one. A phone stays signed in for 30 days; saving a new PIN signs every phone out. Only this computer can change anything.");
        h("div", "dm-note", menu, "The PIN is sent unencrypted over your network. It keeps casual visitors out; it does not protect against someone who can watch the network traffic.");
        // ThrashDash does not look at or change the firewall: letting other devices through is up to whoever administers this computer.
        h("div", "dm-note", menu, `If a phone cannot connect, this computer's firewall may need to allow port ${new URL(client.lan.urls[0]).port}. That is for your system administrator to decide.`);
      }
    }

    const foot = h("div", "dm-foot", menu);
    if (local) {
      const asDefault = h("button", "dm-action", foot, "Use as default");
      asDefault.type = "button";
      asDefault.title = "Open this dashboard whenever ThrashDash starts, or when no vehicle rule applies";
      asDefault.disabled = currentId() === settings.defaultLayout;
      asDefault.addEventListener("click", async () => {
        try {
          await api.saveSettings({ defaultLayout: currentId() });
        } catch (e) {
          console.warn("ThrashDash: could not set the default", e);
        }
        hide();
      });
      const win = h("button", "dm-action", foot, "New window");
      win.title = "Open another window the same size as this one (N), with this menu open so you can pick what it shows";
      win.type = "button";
      win.addEventListener("click", () => {
        hide();
        openWindow(currentId());
      });
    }
  }

  /** Re-read the list and settings and redraw, keeping keyboard focus roughly where it was. */
  async function refresh() {
    const focusedId = document.activeElement?.dataset?.id;
    try {
      const [layouts, settings, client] = await Promise.all([api.listLayouts(), api.getSettings(), api.getClient()]);
      const lan = { pin: { set: false, pin: "" }, token: null };
      if (client.local) {
        lan.pin = await api.getLanPin().catch(() => lan.pin);
        if (client.lan.enabled) lan.token = await api.lanToken().then((r) => r.token).catch(() => null);
      }
      render(layouts, settings, client, lan);
    } catch (e) {
      menu.replaceChildren();
      h("div", "dm-empty", menu, `Could not load the list: ${e.message}`);
      items = [];
    }
    const again = focusedId && menu.querySelector(`.dm-item[data-id="${CSS.escape(focusedId)}"]`);
    (again || menu.querySelector(".dm-current") || buttons()[0])?.focus();
  }

  async function show() {
    if (isOpen) return;
    await refresh();
    isOpen = true;
    menu.hidden = false;
    button.setAttribute("aria-expanded", "true");
    (menu.querySelector(".dm-current") ?? buttons()[0])?.focus();
    onToggle(true);
  }

  function hide() {
    if (!isOpen) return;
    isOpen = false;
    menu.hidden = true;
    button.setAttribute("aria-expanded", "false");
    onToggle(false);
  }

  button.addEventListener("click", () => (isOpen ? hide() : show()));

  // While open, the menu owns the keyboard (so "0" or "-" cannot also zoom the dashboard behind it).
  document.addEventListener(
    "keydown",
    (e) => {
      if (!isOpen || dialogOpen()) return; // a confirmation dialog above the menu owns the keyboard
      const swallow = () => {
        e.preventDefault();
        e.stopPropagation();
      };
      const focused = document.activeElement;
      if (focused?.tagName === "INPUT" && e.key !== "Escape") return; // typing (the PIN field): 1-9 must not pick a dashboard, nor arrows move
      if (e.key === "Escape") {
        swallow();
        hide();
        button.focus();
      } else if (e.key === "ArrowDown") {
        swallow();
        focusMove(1);
      } else if (e.key === "ArrowUp") {
        swallow();
        focusMove(-1);
      } else if (e.key === "Delete" && focused?.classList.contains("dm-item") && focused.dataset.deletable === "true") {
        swallow();
        requestDelete({ id: focused.dataset.id, name: focused.querySelector(".dm-name")?.textContent ?? "this dashboard" });
      } else if (/^[1-9]$/.test(e.key) && items[Number(e.key) - 1]) {
        swallow();
        choose(items[Number(e.key) - 1]);
      }
    },
    true,
  );

  document.addEventListener("pointerdown", (e) => {
    if (isOpen && !dialogOpen() && !menu.contains(e.target) && e.target !== button) hide();
  });

  return { toggle: () => (isOpen ? hide() : show()), hide, get isOpen() { return isOpen; } };
}

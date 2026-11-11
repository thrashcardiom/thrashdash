// Thin client for the relay's REST API. Writes carry X-ThrashDash, which the relay requires: a header
// that only pages served by the relay itself can attach (see relay/thrashdash/api.py).

async function call(method, path, body, extra = {}) {
  const init = { method, headers: { "X-ThrashDash": "1" }, ...extra };
  if (body !== undefined) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(body);
  }
  const res = await fetch(path, init);
  let data = null;
  try {
    data = await res.json();
  } catch {
    /* error pages may not be JSON */
  }
  if (!res.ok) throw new Error((data && data.error) || `${method} ${path} failed (${res.status})`);
  return data;
}

export const api = {
  listLayouts: () => call("GET", "/api/layouts").then((d) => d.layouts),
  getLayout: (id) => call("GET", `/api/layouts/${encodeURIComponent(id)}`),
  createLayout: (layout) => call("POST", "/api/layouts", layout),
  saveLayout: (id, layout) => call("PUT", `/api/layouts/${encodeURIComponent(id)}`, layout),
  deleteLayout: (id) => call("DELETE", `/api/layouts/${encodeURIComponent(id)}`),
  resetTrip: (name) => call("POST", `/api/trips/${encodeURIComponent(name)}/reset`),
  /** This window reports itself to the relay (see windowState.js). */
  putWindow: (id, report) => call("PUT", `/api/windows/${encodeURIComponent(id)}`, report),
  /** This window was closed. `keepalive` lets the request finish while the page is going away. */
  forgetWindow: (id, stamp) => call("DELETE", `/api/windows/${encodeURIComponent(id)}?t=${encodeURIComponent(stamp)}`, undefined, { keepalive: true }),
  /** The PIN devices on the network must enter ({set, pin}); only this computer may read it. */
  getLanPin: () => call("GET", "/api/lan/pin"),
  setLanPin: (pin) => call("PUT", "/api/lan/pin", { pin }),
  /** A single-use token for the QR code, so scanning it signs a phone in without typing the PIN. */
  lanToken: () => call("POST", "/api/lan/token", {}),
  getClient: () => call("GET", "/api/client"),
  getSettings: () => call("GET", "/api/settings"),
  saveSettings: (patch) => call("PUT", "/api/settings", patch),
};

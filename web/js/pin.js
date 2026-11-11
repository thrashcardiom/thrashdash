// The sign-in page for devices on the network: send the PIN to the relay, which answers with a cookie that keeps this device
// signed in. The PIN is never stored here. (The page loads nothing else: a device that has not signed in may fetch only this.)
/** Where to go after signing in: the address the person was trying to reach, but only ever a path on this site. */
export function safeNext(value) {
  if (typeof value !== "string" || !value.startsWith("/") || value.startsWith("//") || value.startsWith("/\\")) return "/";
  if (value.startsWith("/pin.html")) return "/"; // never back to this page
  return value;
}

function start() {
  const input = document.getElementById("pin");
  const button = document.getElementById("go");
  const message = document.getElementById("message");
  const next = safeNext(new URLSearchParams(location.search).get("next"));

  async function signIn() {
    const pin = input.value.trim();
    if (!/^[0-9]{4,8}$/.test(pin)) {
      message.textContent = "Enter the PIN: 4 to 8 digits.";
      return;
    }
    button.disabled = true;
    message.textContent = "";
    try {
      const res = await fetch("/api/pin/login", {
        method: "POST",
        headers: { "X-ThrashDash": "1", "Content-Type": "application/json" },
        body: JSON.stringify({ pin }),
      });
      if (res.ok) {
        location.replace(next);
        return;
      }
      const data = await res.json().catch(() => ({}));
      message.textContent = data.error || "Could not sign in.";
      input.value = "";
      input.focus();
    } catch {
      message.textContent = "Could not reach ThrashDash. Is the computer on the same network?";
    }
    button.disabled = false;
  }

  button.addEventListener("click", signIn);
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") signIn();
  });
}

if (typeof document !== "undefined") start(); // (importing this file in a test has no page to start)

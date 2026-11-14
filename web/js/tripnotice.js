// The banner a dashboard window shows when a trip ends: what the relay sent as a "notice" message (see relay/thrashdash/tripnotice.py).
// It is a line of text over the dashboard for a few seconds, and a click (or tap) puts it away. Text goes in with textContent only.

const SHOW_MS = 12000;

/** `el` is the (hidden) banner element. Returns {show(notice), hide()}. */
export function createTripNotice(el, { setTimer = setTimeout, clearTimer = clearTimeout } = {}) {
  let timer = null;
  function hide() {
    clearTimer(timer);
    timer = null;
    el.hidden = true;
    el.replaceChildren();
  }
  function show(notice) {
    if (!notice || typeof notice.msg !== "string") return;
    const title = document.createElement("div");
    title.className = "tn-title";
    title.textContent = typeof notice.title === "string" ? notice.title : "ThrashDash";
    const msg = document.createElement("div");
    msg.className = "tn-msg";
    msg.textContent = notice.msg;
    el.replaceChildren(title, msg);
    el.hidden = false;
    clearTimer(timer);
    timer = setTimer(hide, SHOW_MS);
  }
  el.addEventListener("click", hide);
  return { show, hide };
}

// Small modal dialogs (confirm / prompt / choice / info). Built in the page instead of window.confirm()
// because browser dialogs block the whole dashboard (and the live data with it), look out of place in
// an app window, and cannot show lists such as import warnings.
import { h } from "../dom.js";

let open = null; // only one dialog at a time

function close(result) {
  if (!open) return;
  const { backdrop, resolve, onKey, previousFocus } = open;
  open = null;
  document.removeEventListener("keydown", onKey, true);
  backdrop.remove();
  previousFocus?.focus?.();
  resolve(result);
}

/**
 * Show a dialog and resolve with the `value` of the button pressed (or `cancelValue` for Esc/backdrop click).
 * @param {{title: string, message?: string, lines?: string[], input?: {value: string, maxLength?: number}, buttons: {label: string, value: any, primary?: boolean, danger?: boolean}[], cancelValue?: any}} opts
 * @returns {Promise<any>} resolves to the button value; for `input` dialogs, {value, text}
 */
export function dialog(opts) {
  if (open) close(opts.cancelValue ?? null); // never stack dialogs
  return new Promise((resolve) => {
    const backdrop = h("div", "dlg-backdrop", document.body);
    const box = h("div", "dlg", backdrop);
    box.setAttribute("role", "dialog");
    box.setAttribute("aria-modal", "true");
    h("h2", "dlg-title", box, opts.title);
    if (opts.message) h("p", "dlg-message", box, opts.message);
    if (opts.lines?.length) {
      const list = h("ul", "dlg-lines", box);
      for (const line of opts.lines.slice(0, 30)) h("li", "", list, line);
      if (opts.lines.length > 30) h("li", "", list, `...and ${opts.lines.length - 30} more`);
    }
    let input = null;
    if (opts.input) {
      input = h("input", "dlg-input", box);
      input.type = "text";
      input.value = opts.input.value ?? "";
      input.maxLength = opts.input.maxLength ?? 60;
    }
    const row = h("div", "dlg-buttons", box);
    const done = (value) => close(input ? { value, text: input.value } : value);
    let primaryButton = null;
    for (const b of opts.buttons) {
      const el = h("button", `btn${b.primary ? " btn-primary" : ""}${b.danger ? " btn-danger" : ""}`, row, b.label);
      el.type = "button";
      el.addEventListener("click", () => done(b.value));
      if (b.primary) primaryButton = el;
    }
    const cancelValue = opts.cancelValue ?? null;
    const onKey = (e) => {
      if (e.key === "Escape") {
        e.preventDefault();
        e.stopPropagation();
        close(input ? { value: cancelValue, text: input.value } : cancelValue);
      } else if (e.key === "Enter" && primaryButton && (e.target === input || e.target === document.body || e.target === primaryButton)) {
        e.preventDefault();
        e.stopPropagation();
        done(opts.buttons.find((b) => b.primary).value);
      }
    };
    backdrop.addEventListener("pointerdown", (e) => {
      if (e.target === backdrop) close(input ? { value: cancelValue, text: input.value } : cancelValue);
    });
    open = { backdrop, resolve, onKey, previousFocus: document.activeElement };
    document.addEventListener("keydown", onKey, true);
    (input ?? primaryButton ?? row.firstChild)?.focus();
    input?.select();
  });
}

export const confirmDialog = ({ title, message, confirm = "OK", danger = false }) =>
  dialog({ title, message, buttons: [{ label: "Cancel", value: false }, { label: confirm, value: true, primary: true, danger }], cancelValue: false });

/** @returns {Promise<string|null>} the text, or null if cancelled */
export const promptDialog = async ({ title, message, value = "", confirm = "OK", maxLength = 60 }) => {
  const res = await dialog({ title, message, input: { value, maxLength }, buttons: [{ label: "Cancel", value: false }, { label: confirm, value: true, primary: true }], cancelValue: false });
  return res.value ? res.text.trim() : null;
};

export const infoDialog = ({ title, message, lines, close = "OK" }) => dialog({ title, message, lines, buttons: [{ label: close, value: true, primary: true }], cancelValue: true });

export const closeDialog = () => close(null);
export const dialogOpen = () => open !== null;

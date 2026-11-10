// Layout management: everything that talks to the relay's storage (save, save as, switch, new, delete,
// import, export, set default). The UI calls these; they own the "unsaved changes" rules.
import { LayoutError, sanitizeLayout } from "../layoutSchema.js";
import { confirmDialog, dialog, infoDialog, promptDialog } from "./dialog.js";
import { openDashboardWindow } from "../windows.js";

const MAX_IMPORT_BYTES = 256 * 1024; // same cap the relay enforces on request bodies

/** "My Dash / v2?" -> "My-Dash-v2": safe to use as a download file name on Windows and Linux. */
export function safeFileName(name) {
  const cleaned = String(name).normalize("NFKD").replace(/[^\w.\- ]+/g, "").trim().replace(/\s+/g, "-").replace(/^\.+/, "");
  return cleaned.slice(0, 50) || "dashboard";
}

export const blankLayout = () => ({ format: 1, name: "New dashboard", width: 1200, height: 420, widgets: [] });

export class LayoutManager {
  /**
   * @param {{state: import("./state.js").EditorState, host: object, api: object, specs: object, notify: (msg: string, kind?: string) => void, changed: () => void}} deps
   */
  constructor({ state, host, api, specs, notify, changed }) {
    Object.assign(this, { state, host, api, specs, notify, changed });
    this.list = [];
    this.defaultId = "default";
  }

  async refresh() {
    try {
      this.list = await this.api.listLayouts();
      this.defaultId = (await this.api.getSettings()).defaultLayout;
    } catch (e) {
      this.notify(`Could not read the layout list: ${e.message}`, "error");
    }
    this.changed();
  }

  // ---- guards -----------------------------------------------------------------------------

  /** Ask what to do with unsaved edits. Resolves true if it is OK to continue (saved or discarded). */
  async resolveUnsaved(actionName) {
    if (!this.state.dirty) return true;
    const choice = await dialog({
      title: "Unsaved changes",
      message: `You have unsaved changes to "${this.state.layout.name}". What should happen before you ${actionName}?`,
      buttons: [
        { label: "Cancel", value: "cancel" },
        { label: "Discard changes", value: "discard", danger: true },
        { label: "Save", value: "save", primary: true },
      ],
      cancelValue: "cancel",
    });
    if (choice === "save") return this.save();
    return choice === "discard";
  }

  // ---- saving -----------------------------------------------------------------------------

  /** Save in place for your own layouts; the built-in default (or an unsaved new layout) goes through Save As. */
  async save() {
    const { meta } = this.state;
    if (meta.builtin || !meta.id) return this.saveAs();
    try {
      await this.api.saveLayout(meta.id, this.state.toLayout());
      this.state.markSaved();
      this.notify("Saved");
      await this.refresh();
      return true;
    } catch (e) {
      await infoDialog({ title: "Could not save", message: e.message });
      return false;
    }
  }

  /** Save as a NEW layout (always creates a copy), then keep editing that copy. */
  async saveAs() {
    const suggestion = this.state.meta.builtin || this.state.layout.name === "Default" ? "My dashboard" : this.state.layout.name;
    const name = await promptDialog({
      title: this.state.meta.builtin ? "Save your own copy" : "Save as",
      message: this.state.meta.builtin ? "The built-in Default is read-only, so your changes are saved as a new dashboard." : "Name for the new dashboard:",
      value: suggestion,
      confirm: "Save",
    });
    if (!name) return false;
    this.state.setName(name);
    try {
      const created = await this.api.createLayout(this.state.toLayout());
      this.state.markSaved({ id: created.id, builtin: false });
      await this.host.setUrl(created.id, { inheritLook: true }); // the copy keeps the look the original had
      this.notify(`Saved as "${name}"`);
      await this.refresh();
      return true;
    } catch (e) {
      await infoDialog({ title: "Could not save", message: e.message });
      return false;
    }
  }

  // ---- switching --------------------------------------------------------------------------

  /** Load another stored layout into the editor. */
  async switchTo(id, { force = false } = {}) {
    if (!force && !(await this.resolveUnsaved("switch layouts"))) return false;
    try {
      const loaded = await this.api.getLayout(id);
      const { layout, warnings } = sanitizeLayout(loaded.layout, this.specs);
      await this.host.setUrl(loaded.id); // first, so the widgets are redrawn in THIS dashboard's look
      this.state.replace(layout, { id: loaded.id, builtin: loaded.builtin });
      if (warnings.length) this.notify(`Loaded with ${warnings.length} repair(s); see the browser console`, "warn");
      for (const w of warnings) console.warn("ThrashDash layout:", w);
      this.changed();
      return true;
    } catch (e) {
      await infoDialog({ title: "Could not open that layout", message: e.message });
      return false;
    }
  }

  async newLayout() {
    if (!(await this.resolveUnsaved("start a new dashboard"))) return false;
    await this.host.setUrl(null);
    this.state.replace(blankLayout(), { id: null, builtin: false });
    this.changed();
    return true;
  }

  async deleteCurrent() {
    const { id, builtin } = this.state.meta;
    if (!id || builtin) return false;
    const ok = await confirmDialog({ title: "Delete dashboard", message: `Delete "${this.state.layout.name}" permanently? This cannot be undone.`, confirm: "Delete", danger: true });
    if (!ok) return false;
    try {
      await this.api.deleteLayout(id); // the relay also forgets its look, vehicle rules and default status
    } catch (e) {
      await infoDialog({ title: "Could not delete", message: e.message });
      return false;
    }
    await this.switchTo("default", { force: true });
    await this.refresh();
    this.notify("Deleted");
    return true;
  }

  async setAsDefault() {
    const { id } = this.state.meta;
    if (!id) return this.notify("Save the layout first", "warn");
    try {
      await this.api.saveSettings({ defaultLayout: id });
      this.defaultId = id;
      this.notify("New windows will open this dashboard");
      this.changed();
    } catch (e) {
      await infoDialog({ title: "Could not change the default", message: e.message });
    }
  }

  // ---- import / export / new window -------------------------------------------------------

  exportCurrent() {
    const text = JSON.stringify(this.state.toLayout(), null, 2);
    const url = URL.createObjectURL(new Blob([text], { type: "application/json" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = `${safeFileName(this.state.layout.name)}.thrashdash.json`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 5000);
    this.notify("Exported");
  }

  /** Import a layout file as a NEW layout (never overwrites anything) and switch to it. */
  async importFile(file) {
    if (!file) return false;
    if (file.size > MAX_IMPORT_BYTES) {
      await infoDialog({ title: "Cannot import", message: `That file is ${Math.round(file.size / 1024)} KB; layouts are limited to ${MAX_IMPORT_BYTES / 1024} KB.` });
      return false;
    }
    let raw;
    try {
      raw = JSON.parse(await file.text());
    } catch {
      await infoDialog({ title: "Cannot import", message: "That file is not valid JSON." });
      return false;
    }
    let result;
    try {
      result = sanitizeLayout(raw, this.specs);
    } catch (e) {
      await infoDialog({ title: "Cannot import", message: e instanceof LayoutError ? e.message : "That file is not a ThrashDash layout." });
      return false;
    }
    const { layout, warnings } = result;
    if (warnings.length) {
      const go = await dialog({
        title: "Import with repairs?",
        message: "Parts of this layout were not understood and will be left out or corrected:",
        lines: warnings,
        buttons: [{ label: "Cancel", value: false }, { label: "Import anyway", value: true, primary: true }],
        cancelValue: false,
      });
      if (!go) return false;
    }
    if (!(await this.resolveUnsaved("import"))) return false;
    try {
      const created = await this.api.createLayout(layout);
      await this.switchTo(created.id, { force: true });
      this.notify(`Imported "${layout.name}"`);
      await this.refresh();
      return true;
    } catch (e) {
      await infoDialog({ title: "Could not import", message: e.message });
      return false;
    }
  }

  /** Open the current (saved) layout in its own window so it can live on another monitor. */
  async openInNewWindow() {
    if (!this.state.meta.id || this.state.dirty) {
      if (!(await this.resolveUnsaved("open it in a new window"))) return false;
      if (!this.state.meta.id) return false;
    }
    // The same size as this window, less the editor panel the new one will not have.
    openDashboardWindow(this.state.meta.id, { menu: true, insetRight: this.host.getInsetRight?.() ?? 0 });
    return true;
  }
}

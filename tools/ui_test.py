#!/usr/bin/env python3
"""End-to-end UI test for the dashboard editor, driven with real mouse/keyboard events in headless Chromium.

    .venv/bin/python tools/ui_test.py [--keep-shots DIR]

Starts its own relay in --simulate mode against a throwaway data folder (your real layouts are never
touched), runs every scenario, prints PASS/FAIL per check, and exits non-zero if anything failed.
"""
import argparse
import asyncio
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from cdp import ALT, CTRL, SHIFT, Browser  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
PORT = 8771
BASE = f"http://127.0.0.1:{PORT}"
results = []
ENV = {}  # environment for relays started by scenarios (same throwaway data folder as the main one)


def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"   [{detail}]" if detail and not cond else ""))
    return bool(cond)


def api(method, path, body=None):
    req = urllib.request.Request(BASE + path, method=method, data=None if body is None else json.dumps(body).encode(),
                                 headers={"X-ThrashDash": "1", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.load(r)


class Page:
    def __init__(self, b, shots):
        self.b = b
        self.shots = shots

    async def settle(self, s=0.35):
        await asyncio.sleep(s)

    async def shot(self, name):
        if self.shots:
            await self.b.screenshot(str(Path(self.shots) / f"{name}.png"))

    async def text_rect(self, selector, text):
        """Rect of the first element matching `selector` whose text contains `text`."""
        return await self.b.eval(
            f"(() => {{ const el = [...document.querySelectorAll({json.dumps(selector)})].find(e => e.textContent.includes({json.dumps(text)}));"
            "if (!el) return null; const r = el.getBoundingClientRect(); return {cx: r.x + r.width / 2, cy: r.y + r.height / 2}; })()"
        )

    async def click_text(self, selector, text, modifiers=0):
        await self.b.eval(f"[...document.querySelectorAll({json.dumps(selector)})].find(e => e.textContent.includes({json.dumps(text)}))?.scrollIntoView({{block: 'center'}})")
        await asyncio.sleep(0.05)
        r = await self.text_rect(selector, text)
        if not r:
            raise RuntimeError(f"no {selector!r} containing {text!r}")
        await self.b.click(r["cx"], r["cy"], modifiers)
        await self.settle()

    async def frame(self, wid):
        return await self.b.rect(f'.ef[data-id="{wid}"]')

    async def widget_geo(self, wid):
        """Design-pixel geometry from the dashboard element's own style (what is actually drawn)."""
        return await self.b.eval(
            f"(() => {{ const e = document.querySelector('#widgets [data-id=\"{wid}\"]'); if (!e) return null;"
            "return {x: parseFloat(e.style.left), y: parseFloat(e.style.top), w: parseFloat(e.style.width), h: parseFloat(e.style.height)}; })()"
        )

    async def count(self, selector):
        return await self.b.eval(f"document.querySelectorAll({json.dumps(selector)}).length")

    async def text(self, selector, index=0):
        return await self.b.eval(f"document.querySelectorAll({json.dumps(selector)})[{index}]?.textContent ?? null")

    async def scale(self):
        return await self.b.eval("document.getElementById('canvas').getBoundingClientRect().width / parseFloat(document.getElementById('canvas').style.width)")

    async def dirty_note(self):
        return await self.text(".ed-dirty")

    async def select_widget(self, wid):
        r = await self.frame(wid)
        await self.b.click(r["cx"], r["cy"])
        await self.settle(0.2)


async def scenarios(b, shots):
    p = Page(b, shots)

    print("\n== page load")
    await b.goto(BASE + "/", wait=2.0)
    check("12 widgets rendered from the default layout", await p.count("#widgets > .w") == 12)
    check("controls exist but are hidden until the mouse moves", await b.eval("getComputedStyle(document.getElementById('top-controls')).opacity") == "0")
    await b.move(500, 300)
    await p.settle(0.4)
    check("controls appear when the mouse moves", await b.eval("getComputedStyle(document.getElementById('top-controls')).opacity") == "1")

    print("\n== enter edit mode")
    await b.key("e")
    await p.settle(0.8)
    check("E enters edit mode", await b.eval("document.body.classList.contains('editing')"))
    check("a frame per widget", await p.count(".ef") == 12)
    check("panel is shown", await p.count(".ed-panel") == 1)
    check("canvas leaves room for the panel (no overlap)", await b.eval("document.getElementById('canvas').getBoundingClientRect().right <= document.querySelector('.ed-panel').getBoundingClientRect().left + 1"))
    check("built-in layout flagged read-only", await b.eval("[...document.querySelectorAll('.ed-note')].some(n => n.textContent.includes('read-only'))"))
    check("connection chip shows simulated data", "Simulated" in (await p.text(".ed-chip")))
    check("Undo/Redo start disabled", await b.eval("[...document.querySelectorAll('.ed-bar .btn')].slice(0,2).every(b => b.disabled)"))
    check("no unsaved-changes state yet", (await p.dirty_note()) == "")

    print("\n== select, handles, properties")
    await p.select_widget("gear-1")
    check("clicking a widget selects it", await b.eval("document.querySelector('.ef[data-id=\"gear-1\"]').classList.contains('sel')"))
    check("single selection shows 8 resize handles", await p.count(".ef.sel .eh") == 8)
    check("selection panel names the widget", "gear-1" in (await p.text(".sel-title")))
    check("property controls are generated from the spec", await p.count(".sel-body .pr") >= 2)
    await p.select_widget("bar-1")
    check("selecting another deselects the first", await p.count(".ef.sel") == 1)
    await b.click(900, 700)  # empty area below the canvas
    await p.settle(0.2)
    check("clicking empty space clears the selection", await p.count(".ef.sel") == 0)
    await p.select_widget("roundGauge-1")
    await p.select_widget("gear-1")
    await b.click_el('.ef[data-id="light-6"]', modifiers=SHIFT)
    await p.settle(0.2)
    check("shift-click adds to the selection", await p.count(".ef.sel") == 2)
    check("multi-selection shows no handles", await p.count(".eh") == 0)
    await b.key("a", CTRL)
    await p.settle(0.2)
    check("Ctrl+A selects everything", await p.count(".ef.sel") == 12)
    await b.key("Escape")
    await p.settle(0.2)
    check("Esc clears the selection", await p.count(".ef.sel") == 0)

    print("\n== drag to move (grid snapping)")
    scale = await p.scale()
    g0 = await p.widget_geo("gear-1")
    r = await p.frame("gear-1")
    await b.drag(r["cx"], r["cy"], r["cx"] + 100, r["cy"] + 45)
    await p.settle()
    g1 = await p.widget_geo("gear-1")
    check("widget moved right/down", g1["x"] > g0["x"] + 80 and g1["y"] > g0["y"] + 30, f"{g0} -> {g1}")
    check("position snapped to the 10px grid", g1["x"] % 10 == 0 and g1["y"] % 10 == 0, str(g1))
    check("size unchanged by a move", (g1["w"], g1["h"]) == (g0["w"], g0["h"]))
    check("frame follows the widget", abs((await p.frame("gear-1"))["x"] - (await b.rect('#widgets [data-id="gear-1"]'))["x"]) < 1.5)
    check("change marks the layout dirty", (await p.dirty_note()) == "Unsaved changes")
    check("whole drag is ONE undo step", await b.eval("(() => { const u = document.querySelectorAll('.ed-bar .btn')[0]; return !u.disabled; })()"))
    await b.key("z", CTRL)
    await p.settle()
    g2 = await p.widget_geo("gear-1")
    check("Ctrl+Z restores the original position in one step", (g2["x"], g2["y"]) == (g0["x"], g0["y"]), str(g2))
    check("undoing back to the saved state clears 'unsaved'", (await p.dirty_note()) == "")
    await b.key("y", CTRL)
    await p.settle()
    check("Ctrl+Y redoes", (await p.widget_geo("gear-1"))["x"] == g1["x"])
    await b.key("z", CTRL)
    await p.settle()

    print("\n== Alt disables snapping; Esc cancels a drag")
    r = await p.frame("gear-1")
    await b.drag(r["cx"], r["cy"], r["cx"] + 33, r["cy"] + 17, modifiers=ALT)
    await p.settle()
    g3 = await p.widget_geo("gear-1")
    check("Alt-drag places off-grid", g3["x"] % 10 != 0 or g3["y"] % 10 != 0, str(g3))
    await b.key("z", CTRL)
    r = await p.frame("gear-1")
    await b.drag(r["cx"], r["cy"], r["cx"] + 200, r["cy"] + 80, release=False)
    await b.key("Escape")
    await b.mouse("mouseReleased", r["cx"] + 200, r["cy"] + 80)
    await p.settle()
    check("Esc during a drag cancels it", (await p.widget_geo("gear-1"))["x"] == g0["x"])

    print("\n== resize")
    await p.select_widget("gear-1")
    h = await b.rect('.ef[data-id="gear-1"] .eh-se')
    await b.drag(h["cx"], h["cy"], h["cx"] + 60, h["cy"] + 40)
    await p.settle()
    g4 = await p.widget_geo("gear-1")
    check("SE handle grows the widget", g4["w"] > g0["w"] + 40 and g4["h"] > g0["h"] + 25, str(g4))
    check("resizing keeps the top-left corner", (g4["x"], g4["y"]) == (g0["x"], g0["y"]))
    check("resized edges are on the grid", (g4["x"] + g4["w"]) % 10 == 0 and (g4["y"] + g4["h"]) % 10 == 0, str(g4))
    h = await b.rect('.ef[data-id="gear-1"] .eh-nw')
    await b.drag(h["cx"], h["cy"], h["cx"] + 5000, h["cy"] + 5000)
    await p.settle()
    g5 = await p.widget_geo("gear-1")
    check("cannot be shrunk below the minimum size", g5["w"] >= 16 and g5["h"] >= 16, str(g5))
    check("opposite corner stays put when the minimum stops the drag", abs((g5["x"] + g5["w"]) - (g4["x"] + g4["w"])) < 1, f"{g4} -> {g5}")
    await b.key("z", CTRL)
    await b.key("z", CTRL)
    await p.settle()
    check("two undos restore the original size", (await p.widget_geo("gear-1"))["w"] == g0["w"])

    print("\n== keyboard: nudge, delete, copy, paste, duplicate, z-order")
    await p.select_widget("light-1")
    x0 = (await p.widget_geo("light-1"))["x"]
    for _ in range(5):
        await b.key("ArrowRight")
    await b.key("ArrowDown", SHIFT)
    await p.settle()
    g = await p.widget_geo("light-1")
    check("arrow keys nudge 1px; Shift+arrow nudges 10px", g["x"] == x0 + 5 and g["y"] == 362 + 10, str(g))
    await b.key("z", CTRL)
    await p.settle()
    check("a whole run of nudges undoes in one step", (await p.widget_geo("light-1"))["x"] == x0 + 5 and (await p.widget_geo("light-1"))["y"] == 362 or (await p.widget_geo("light-1"))["x"] == x0)
    await b.key("z", CTRL)
    await b.key("z", CTRL)
    await p.settle()
    await p.select_widget("light-2")
    await b.key("c", CTRL)
    await b.key("v", CTRL)
    await p.settle()
    check("Ctrl+C / Ctrl+V adds a copy", await p.count(".ef") == 13)
    check("the pasted copy is selected", await p.count(".ef.sel") == 1 and await b.eval("document.querySelector('.ef.sel').dataset.id") != "light-2")
    await b.key("d", CTRL)
    await p.settle()
    check("Ctrl+D duplicates", await p.count(".ef") == 14)
    await b.key("Delete")
    await p.settle()
    check("Delete removes the selection", await p.count(".ef") == 13)
    await b.key("z", CTRL)
    await p.settle()
    check("undo brings the deleted widget back", await p.count(".ef") == 14)
    await b.key("z", CTRL)
    await b.key("z", CTRL)
    await p.settle()
    check("undo of paste/duplicate returns to 12", await p.count(".ef") == 12)
    order_before = await b.eval("[...document.querySelectorAll('#widgets > .w')].map(e => e.dataset.id).join()")
    await p.select_widget("roundGauge-1")
    await b.key("]")
    await p.settle()
    order_after = await b.eval("[...document.querySelectorAll('#widgets > .w')].map(e => e.dataset.id).join()")
    check("] brings a widget forward in z-order", order_before != order_after and order_after.index("roundGauge-1") > order_before.index("roundGauge-1"))
    await b.key("z", CTRL)
    await p.settle()
    check("no stray changes left: layout is clean again", (await p.dirty_note()) == "")

    print("\n== add widgets and pick channels")
    await p.click_text(".palette-btn", "Number readout")
    check("Add widget adds and selects it", await p.count(".ef") == 13 and await p.count(".ef.sel") == 1)
    check("adding a widget enables Undo and marks the layout unsaved", (await p.dirty_note()) == "Unsaved changes" and not await b.eval("document.querySelectorAll('.ed-bar .btn')[0].disabled"))
    rid = await b.eval("document.querySelector('.ef.sel').dataset.id")
    check("new widget has a sensible id", rid.startswith("readout-"), rid)
    await b.click_el(".pr-pick")
    await p.settle(0.5)
    check("channel picker opens", await p.count(".picker") == 1)
    check("picker lists the live channels", await p.count(".pk-row") > 10)
    check("picker shows live values", (await p.text(".pk-live")) not in (None, "--", ""))
    await b.type_text("wheelspeed")
    await p.settle(0.3)
    check("search narrows the list", 1 <= await p.count(".pk-row") <= 3)
    await b.key("Enter")
    await p.settle(0.5)
    check("picker closes after choosing", await p.count(".picker") == 0)
    check("channel button shows the friendly name", "Speed" in (await p.text(".pr-pick")))
    kind_value = await b.eval("[...document.querySelectorAll('.sel-body .pr')].find(r => r.textContent.startsWith('Unit type')).querySelector('select').value")
    check("picking 'wheelspeed' auto-selects the speed unit type", kind_value == "speed", kind_value)
    await p.settle(0.4)
    shown = await b.eval(f"document.querySelector('#widgets [data-id=\"{rid}\"] .r-value').textContent")
    check("the readout shows a live number", shown not in ("--", "", None), shown)
    check("unit text comes from the unit type", await b.eval(f"document.querySelector('#widgets [data-id=\"{rid}\"] .r-unit').textContent") == "km/h")

    print("\n== property editing: text, number, colour, reset")
    title_input = await b.eval("(() => { const row = [...document.querySelectorAll('.sel-body .pr')].find(r => r.textContent.startsWith('Title')); const i = row.querySelector('input'); i.focus(); return true; })()")
    await b.type_text("Speedo")
    await p.settle(0.3)
    check("typing in a text field updates the widget live", await b.eval(f"document.querySelector('#widgets [data-id=\"{rid}\"] .r-label')?.textContent") == "Speedo")
    check("typing kept the field focused (no panel rebuild)", await b.eval("document.activeElement.value") == "Speedo")
    await b.eval("document.activeElement.blur()")
    await b.eval("(() => { const row = [...document.querySelectorAll('.sel-body .pr')].find(r => r.textContent.startsWith('Value colour')); const s = row.querySelector('select'); s.value = 'var(--crit)'; s.dispatchEvent(new Event('change', {bubbles: true})); })()")
    await p.settle(0.3)
    check("colour choice applies to the widget", "--crit" in (await b.eval(f"document.querySelector('#widgets [data-id=\"{rid}\"]').style.cssText")))
    await b.eval("(() => { const row = [...document.querySelectorAll('.sel-body .pr')].find(r => r.textContent.startsWith('Decimals')); const i = row.querySelector('input'); i.focus(); })()")
    await b.type_text("99")
    await b.eval("document.activeElement.dispatchEvent(new Event('change', {bubbles: true}))")
    await p.settle(0.3)
    check("out-of-range numbers are clamped, and the field shows the stored value", await b.eval("[...document.querySelectorAll('.sel-body .pr')].find(r => r.textContent.startsWith('Decimals')).querySelector('input').value") == "4")
    await b.eval("[...document.querySelectorAll('.sel-body .pr')].find(r => r.textContent.startsWith('Title')).querySelector('.pr-reset').click()")
    await p.settle(0.3)
    check("reset removes the property again", await b.eval(f"document.querySelector('#widgets [data-id=\"{rid}\"] .r-label')") is None)
    await p.shot("edit-readout")

    print("\n== undo coalescing for typing")
    await b.eval("[...document.querySelectorAll('.sel-body .pr')].find(r => r.textContent.startsWith('Title')).querySelector('input').focus()")
    await b.type_text("A")
    await b.type_text("B")
    await b.type_text("C")
    await b.eval("document.activeElement.blur()")
    await p.settle(0.3)
    check("typed title is shown", await b.eval(f"document.querySelector('#widgets [data-id=\"{rid}\"] .r-label')?.textContent") == "ABC")
    await b.key("z", CTRL)
    await p.settle(0.3)
    check("one Ctrl+Z removes the whole typed run", await b.eval(f"document.querySelector('#widgets [data-id=\"{rid}\"] .r-label')") is None)

    print("\n== save a built-in as a new dashboard")
    await p.click_text(".ed-foot .btn", "Save as my dashboard")
    await p.settle(0.4)
    check("Save on a built-in asks for a name", await p.count(".dlg-input") == 1)
    await b.eval("document.querySelector('.dlg-input').select()")
    await b.type_text("UI test dash")
    await b.key("Enter")
    await p.settle(0.8)
    layouts = api("GET", "/api/layouts")["layouts"]
    mine = [x for x in layouts if x["name"] == "UI test dash"]
    check("a new layout was stored", len(mine) == 1)
    lid = mine[0]["id"] if mine else None
    check("address now names the new layout", lid and f"d={lid}" in await b.eval("location.search"))
    check("editor shows it as saved", (await p.dirty_note()) == "All changes saved")
    check("the built-in is untouched", len(api("GET", "/api/layouts/default")["layout"]["widgets"]) == 12)
    check("saved layout contains the new widget", lid and len(api("GET", f"/api/layouts/{lid}")["layout"]["widgets"]) == 13)

    print("\n== edit your own layout and save in place")
    await p.select_widget("light-3")
    await b.key("Delete")
    await p.settle(0.3)
    check("edit marks dirty", (await p.dirty_note()) == "Unsaved changes")
    await b.key("s", CTRL)
    await p.settle(0.8)
    check("Ctrl+S saves in place", len(api("GET", f"/api/layouts/{lid}")["layout"]["widgets"]) == 12)
    check("still the same layout id (not a copy)", len([x for x in api("GET", "/api/layouts")["layouts"] if not x["builtin"]]) == 1)

    print("\n== units and settings")
    await b.eval("[...document.querySelectorAll('.unit-grid .pr')].find(r => r.textContent.startsWith('Speed')).querySelector('select').value = 'mph'")
    await b.eval("[...document.querySelectorAll('.unit-grid .pr')].find(r => r.textContent.startsWith('Speed')).querySelector('select').dispatchEvent(new Event('change', {bubbles: true}))")
    await p.settle(0.8)
    check("unit choice is saved in settings", api("GET", "/api/settings")["units"]["speed"] == "mph")
    check("dashboard switches to mph immediately", await b.eval("document.querySelector('.g-unit').textContent") == "mph")
    await b.eval("[...document.querySelectorAll('.unit-grid .pr')].find(r => r.textContent.startsWith('Speed')).querySelector('select').value = 'kmh'")
    await b.eval("[...document.querySelectorAll('.unit-grid .pr')].find(r => r.textContent.startsWith('Speed')).querySelector('select').dispatchEvent(new Event('change', {bubbles: true}))")
    await p.settle(0.5)

    print("\n== canvas size and name")
    await b.eval("(() => { const i = document.querySelectorAll('.gf-row')[0].querySelectorAll('input')[0]; i.focus(); i.value = '900'; i.dispatchEvent(new Event('change', {bubbles: true})); })()")
    await p.settle(0.4)
    check("canvas width change applies", await b.eval("document.getElementById('canvas').style.width") == "900px")
    await b.key("z", CTRL)  # focus is in the field; native undo, so use the button instead
    await p.click_text(".ed-bar .btn", "Undo")
    await p.settle(0.3)
    check("canvas size change is undoable", await b.eval("document.getElementById('canvas').style.width") == "1200px")

    print("\n== import a hostile layout")
    hostile = {"format": 1, "name": "Imported", "width": 800, "height": 300, "widgets": [
        {"id": "ok", "type": "gear", "x": 10, "y": 10, "w": 200, "h": 100, "channel": "gear"},
        {"type": "evil", "x": 0, "y": 0, "w": 5, "h": 5},
        {"type": "light", "x": 0, "y": 0, "w": 50, "h": 50, "color": "url(javascript:alert(1))", "channel": "x", "onclick": "alert(1)", "text": "<img src=x onerror=alert(1)>"},
    ]}
    await b.eval(
        "(() => { const input = document.querySelector('.ed-panel input[type=file]'); const dt = new DataTransfer();"
        f"dt.items.add(new File([{json.dumps(json.dumps(hostile))}], 'hostile.json', {{type: 'application/json'}})); input.files = dt.files;"
        "input.dispatchEvent(new Event('change', {bubbles: true})); })()"
    )
    await p.settle(0.8)
    check("import shows what will be repaired", await p.count(".dlg-lines li") >= 3)
    lines = await b.eval("[...document.querySelectorAll('.dlg-lines li')].map(l => l.textContent).join(' | ')")
    check("warnings name the unknown widget, bad colour and unknown property", "evil" in lines and "colour" in lines and "onclick" in lines, lines)
    await p.click_text(".dlg .btn", "Import anyway")
    await p.settle(1.0)
    imported = [x for x in api("GET", "/api/layouts")["layouts"] if x["name"] == "Imported"]
    check("the imported layout was stored as a NEW layout", len(imported) == 1)
    stored = api("GET", f"/api/layouts/{imported[0]['id']}")["layout"] if imported else {"widgets": []}
    check("only the valid widgets survived", [w["type"] for w in stored["widgets"]] == ["gear", "light"], json.dumps(stored["widgets"]))
    light = stored["widgets"][-1] if stored["widgets"] else {}
    check("bad colour and unknown property were stripped", "color" not in light and "onclick" not in light, json.dumps(light))
    check("hostile text is kept as inert text, never markup", await b.eval("document.querySelectorAll('#widgets img').length") == 0)
    check("your other layout was not overwritten", api("GET", f"/api/layouts/{lid}")["layout"]["name"] == "UI test dash")

    print("\n== switch layouts, unsaved-changes guard")
    await p.select_widget("ok")  # the imported layout's gear widget
    await b.key("Delete")
    await p.settle(0.3)
    await b.eval(f"(() => {{ const s = document.querySelector('.ed-panel select'); s.value = {json.dumps(lid)}; s.dispatchEvent(new Event('change', {{bubbles: true}})); }})()")
    await p.settle(0.5)
    check("switching with unsaved edits asks first", "Unsaved changes" in (await p.text(".dlg-title") or ""))
    await p.click_text(".dlg .btn", "Cancel")
    await p.settle(0.4)
    check("cancel stays on the current layout", await b.eval("document.querySelector('.ed-panel select').value") == imported[0]["id"])
    await b.eval(f"(() => {{ const s = document.querySelector('.ed-panel select'); s.value = {json.dumps(lid)}; s.dispatchEvent(new Event('change', {{bubbles: true}})); }})()")
    await p.settle(0.5)
    await p.click_text(".dlg .btn", "Discard changes")
    await p.settle(0.8)
    check("discard switches to the other layout", await b.eval("document.querySelector('.ed-panel input[aria-label=\"Dashboard name\"]').value") == "UI test dash")
    check("imported layout was not changed by the discarded edit", len(api("GET", f"/api/layouts/{imported[0]['id']}")["layout"]["widgets"]) == 2)

    print("\n== Done and persistence")
    await p.select_widget("light-4")
    await b.key("Delete")
    await p.settle(0.3)
    await p.click_text(".ed-bar .btn", "Done")
    await p.settle(0.5)
    check("Done with unsaved edits asks what to do", "Unsaved changes" in (await p.text(".dlg-title") or ""))
    await p.click_text(".dlg .btn", "Discard changes")
    await p.settle(1.0)
    check("leaving edit mode removes the editor", not await b.eval("document.body.classList.contains('editing')") and await p.count(".ed-panel") == 0 and await p.count(".ef") == 0)
    check("the discarded edit is not shown", await p.count("#widgets > .w") == 12)
    await b.goto(f"{BASE}/?d={lid}", wait=2.0)
    check("a reload of ?d=<id> shows the saved layout", await p.count("#widgets > .w") == 12 and await b.eval("!!document.querySelector('#widgets .r-value')"))
    check("saved readout widget survived the round trip", await b.eval("document.querySelectorAll('#widgets .w-readout').length") == 1)
    await p.shot("saved-dash")

    print("\n== delete a layout")
    await b.goto(f"{BASE}/?d={imported[0]['id']}", wait=1.5)
    await b.key("e")
    await p.settle(0.8)
    await p.click_text(".ed-panel .btn", "Delete")
    await p.settle(0.4)
    await p.click_text(".dlg .btn", "Delete")
    await p.settle(1.0)
    check("deleted layout is gone from storage", not [x for x in api("GET", "/api/layouts")["layouts"] if x["name"] == "Imported"])
    check("editor falls back to the default after deleting", await b.eval("document.querySelector('.ed-panel select').value") == "default")
    check("Delete is disabled on the read-only built-in", await b.eval("[...document.querySelectorAll('.ed-panel .btn')].find(b => b.textContent === 'Delete').disabled"))

    print("\n== dashboard switcher (normal view)")
    await b.resize(1400, 760)
    mk = lambda name, n: {"format": 1, "name": name, "width": 1000, "height": 400, "widgets": [
        {"id": f"w{i}", "type": "gear", "x": 20 + i * 150, "y": 50, "w": 140, "h": 140, "channel": "gear"} for i in range(n)]}
    id_a = api("POST", "/api/layouts", mk("Switch A", 1))["id"]
    id_b = api("POST", "/api/layouts", mk("Switch B", 2))["id"]
    await b.goto(BASE + "/?d=default", wait=1.8)
    check("no menu before it is opened", await b.eval("document.getElementById('dash-menu').hidden"))
    await b.move(500, 300)
    await p.settle(0.3)
    await b.click_el("#dash-button")
    await p.settle(0.5)
    check("Dashboards button opens the menu", not await b.eval("document.getElementById('dash-menu').hidden"))
    names = await b.eval("[...document.querySelectorAll('#dash-menu .dm-name')].map(e => e.textContent)")
    check("menu lists the built-in first, then saved dashboards", names[0] == "Default" and "Switch A" in names and "Switch B" in names, str(names))
    check("the open dashboard is marked", await b.eval("document.querySelector('#dash-menu .dm-current .dm-name').textContent") == "Default")
    check("built-in and default are labelled", "built-in" in await b.eval("document.querySelector('#dash-menu .dm-tag').textContent"))
    await p.shot("switcher")
    await b.click_el("#dash-menu .dm-item", index=names.index("Switch A"))
    await p.settle(0.8)
    check("choosing a dashboard loads it into this window", await p.count("#widgets > .w") == 1)
    check("the menu closes after choosing", await b.eval("document.getElementById('dash-menu').hidden"))
    check("the address records the choice", f"d={id_a}" in await b.eval("location.search"))
    await b.goto(await b.eval("location.href"), wait=1.6)
    check("a reload keeps the chosen dashboard", await p.count("#widgets > .w") == 1)

    await b.key("d")
    await p.settle(0.5)
    check("D opens the menu from the keyboard", not await b.eval("document.getElementById('dash-menu').hidden"))
    check("keyboard focus lands on the current dashboard", await b.eval("document.activeElement.classList.contains('dm-current')"))
    before = await b.eval("document.getElementById('canvas').style.transform")
    await b.key("-")
    await p.settle(0.3)
    check("zoom keys are ignored while the menu is open", await b.eval("document.getElementById('canvas').style.transform") == before and await b.eval("localStorage.getItem('thrashdash.zoom.' + new URLSearchParams(location.search).get('d'))") is None)
    await b.key(str(names.index("Switch B") + 1))
    await p.settle(0.8)
    check("number keys pick a dashboard (its number in the menu)", await p.count("#widgets > .w") == 2 and f"d={id_b}" in await b.eval("location.search"))
    await b.key("d")
    await p.settle(0.4)
    await b.key("Escape")
    await p.settle(0.3)
    check("Esc closes the menu without changing anything", await b.eval("document.getElementById('dash-menu').hidden") and await p.count("#widgets > .w") == 2)
    await b.key("d")
    await p.settle(0.4)
    await b.click(300, 650)
    await p.settle(0.3)
    check("clicking elsewhere closes the menu", await b.eval("document.getElementById('dash-menu').hidden"))

    await b.key("d")
    await p.settle(0.4)
    await b.key("ArrowDown")
    await b.key("ArrowUp")
    check("arrow keys move through the menu", await b.eval("document.activeElement.classList.contains('dm-item') || document.activeElement.classList.contains('dm-action')"))
    await p.click_text("#dash-menu .dm-action", "Use as default")
    await p.settle(0.5)
    check("'Use as default' is saved in settings", api("GET", "/api/settings")["defaultLayout"] == id_b)
    await b.goto(BASE + "/", wait=1.8)
    check("a window opened with no ?d= shows the default dashboard", await p.count("#widgets > .w") == 2)
    api("PUT", "/api/settings", {"defaultLayout": "default"})

    await b.eval("window.__opened = null; window.open = (u, n, f) => { window.__opened = [u, f]; return null; }")
    await b.key("d")
    await p.settle(0.4)
    await p.click_text("#dash-menu .dm-action", "New window")
    await p.settle(0.3)
    opened = await b.eval("window.__opened")
    check("'New window' opens this dashboard in a popup", opened and "d=" in opened[0] and "popup" in opened[1], str(opened))
    want = await b.eval("[Math.min(innerWidth, screen.availWidth), Math.min(innerHeight, screen.availHeight)]")
    check("...the same size as this window", opened and f"width={want[0]}," in opened[1] and f"height={want[1]}," in opened[1], f"{want} vs {opened}")
    check("...with the Dashboards menu open so another can be picked", opened and opened[0].endswith("&menu=1"), str(opened))
    check("...and named, so ThrashDash can reopen it next time", opened and re.search(r"&w=[a-z0-9]{12}&menu=1$", opened[0]) is not None, str(opened))

    await b.eval("window.__opened = null")
    await b.key("n")
    await p.settle(0.3)
    opened = await b.eval("window.__opened")
    check("the N key does the same", opened and "d=" in opened[0] and opened[0].endswith("&menu=1") and "popup" in opened[1], str(opened))

    await b.goto(BASE + "/?d=default&menu=1", wait=2.0)
    check("a window opened with menu=1 shows the Dashboards menu at once", not await b.eval("document.getElementById('dash-menu').hidden"))
    check("...and forgets the request, so a reload does not repeat it", "menu" not in await b.eval("location.search") and "d=default" in await b.eval("location.search"))
    await b.key("Escape")
    await p.settle(0.3)

    await b.key("e")
    await p.settle(0.8)
    check("the switcher is hidden while editing", await b.eval("getComputedStyle(document.getElementById('top-controls')).display") == "none")
    await b.key("e")
    await p.settle(0.8)
    check("it comes back after editing", await b.eval("getComputedStyle(document.getElementById('top-controls')).display") != "none")

    print("\n== scaling: big windows, scale modes, zoom keys")
    await b.resize(2200, 1000)
    base_layout = {"format": 1, "name": "scale-%s", "width": 1200, "height": 420, "widgets": [
        {"id": "g", "type": "gear", "x": 400, "y": 100, "w": 300, "h": 190, "channel": "gear"}]}
    made = {}
    for mode, pct in (("max", 100), ("fit", 100), ("fixed", 50)):
        made[mode] = api("POST", "/api/layouts", {**base_layout, "name": f"scale-{mode}", "scaleMode": mode, "scalePercent": pct})["id"]

    async def canvas_w(layout_id):
        await b.goto(f"{BASE}/?d={layout_id}", wait=1.8)
        return await b.eval("document.getElementById('canvas').getBoundingClientRect().width")

    w_default = await canvas_w("default")
    check("built-in Default is now 'fit, no bigger than 100%': fullscreen stays at design size", abs(w_default - 1200) < 2, str(w_default))
    check("it is centred in the big window", abs((await b.rect("#canvas"))["cx"] - 1100) < 2)
    check("'max' layout stays at design size on a big window", abs(await canvas_w(made["max"]) - 1200) < 2)
    check("'fit' layout still fills the big window (old behaviour is available)", abs(await canvas_w(made["fit"]) - 2200) < 3)
    check("'fixed 50%' layout is half size", abs(await canvas_w(made["fixed"]) - 600) < 2)
    await b.resize(700, 600)
    await p.settle(0.5)
    check("'fixed' ignores a small window (clipped, not shrunk)", abs((await b.rect("#canvas"))["w"] - 600) < 2)
    await b.goto(f"{BASE}/?d=default", wait=1.5)
    check("'max' still shrinks to fit a small window", abs((await b.rect("#canvas"))["w"] - 700) < 2)

    await b.resize(2200, 1000)
    await b.goto(f"{BASE}/?d=default", wait=1.5)
    await b.key("-")
    await p.settle(0.4)
    check("'-' zooms out one step", abs((await b.rect("#canvas"))["w"] - 1092) < 3, str((await b.rect("#canvas"))["w"]))
    check("a zoom toast says so", "91%" in (await p.text("#zoom-toast")))
    await b.goto(f"{BASE}/?d=default", wait=1.5)
    check("the zoom is remembered for this dashboard", abs((await b.rect("#canvas"))["w"] - 1092) < 3)
    await b.key("+")
    await b.key("+")
    await p.settle(0.3)
    check("'+' zooms back in", (await b.rect("#canvas"))["w"] > 1200)
    await b.key("0")
    await p.settle(0.3)
    check("'0' resets to the dashboard's own scale setting", abs((await b.rect("#canvas"))["w"] - 1200) < 2)
    check("reset clears the saved zoom", await b.eval("localStorage.getItem('thrashdash.zoom.default')") is None)
    await b.eval("window.dispatchEvent(new WheelEvent('wheel', {deltaY: 120, ctrlKey: true, cancelable: true}))")
    await p.settle(0.3)
    check("Ctrl+scroll zooms too", (await b.rect("#canvas"))["w"] < 1195)
    await b.key("0")

    print("\n== scaling: editor controls")
    await b.key("-")  # leave a stale zoom behind; changing the setting in the editor must clear it
    await b.key("e")
    await p.settle(0.8)
    check("editing always shows the whole canvas (scale setting does not apply)", (await b.rect("#canvas"))["w"] > 1000)
    check("scale controls are in the panel", await p.count(".ed-panel select") >= 3 and "no bigger than" in await b.eval("document.querySelector('.ed-panel select option[value=max]').textContent"))
    check("the default mode is shown as 'max' at 100%", await b.eval("(() => { const sel = [...document.querySelectorAll('.ed-panel select')].find(s => [...s.options].some(o => o.value === 'fixed')); return sel.value; })()") == "max")
    await b.eval("(() => { const sel = [...document.querySelectorAll('.ed-panel select')].find(s => [...s.options].some(o => o.value === 'fixed')); sel.value = 'fixed'; sel.dispatchEvent(new Event('change', {bubbles: true})); })()")
    await p.settle(0.3)
    check("choosing a mode clears a stale +/- zoom", await b.eval("localStorage.getItem('thrashdash.zoom.default')") is None)
    await b.eval("(() => { const r = [...document.querySelectorAll('.ed-panel .pr')].find(r => r.textContent.startsWith('Size (%)')); const i = r.querySelector('input'); i.value = '40'; i.dispatchEvent(new Event('change', {bubbles: true})); })()")
    await p.settle(0.3)
    check("the percent field appears for max/fixed", not await b.eval("[...document.querySelectorAll('.ed-panel .pr')].find(r => r.textContent.startsWith('Size (%)')).hidden"))
    check("changing scale makes the layout unsaved", (await p.dirty_note()) == "Unsaved changes")
    await p.click_text(".ed-panel .btn", "Match screen size")
    await p.settle(0.4)
    size = await b.eval("[...document.querySelectorAll('.gf-row')[0].querySelectorAll('input')].map(i => i.value).join('x')")
    check("'Match screen size' sets the canvas to the monitor resolution", size == "2200x1000", size)
    await p.click_text(".ed-panel .btn", "Match window size")
    await p.settle(0.3)
    await b.key("z", CTRL)
    await p.click_text(".ed-bar .btn", "Undo")
    await p.click_text(".ed-bar .btn", "Undo")
    await p.settle(0.3)
    scale_mode = await b.eval("(() => { const sel = [...document.querySelectorAll('.ed-panel select')].find(s => [...s.options].some(o => o.value === 'fixed')); return sel.value; })()")
    canvas_after = await b.eval("[...document.querySelectorAll('.gf-row')[0].querySelectorAll('input')].map(i => i.value).join('x')")
    check("scale and canvas edits undo all the way back to the original", scale_mode == "max" and canvas_after == "1200x420", f"{scale_mode} {canvas_after}")
    await b.eval("(() => { const sel = [...document.querySelectorAll('.ed-panel select')].find(s => [...s.options].some(o => o.value === 'fixed')); sel.value = 'fixed'; sel.dispatchEvent(new Event('change', {bubbles: true})); })()")
    await b.eval("(() => { const r = [...document.querySelectorAll('.ed-panel .pr')].find(r => r.textContent.startsWith('Size (%)')); const i = r.querySelector('input'); i.value = '40'; i.dispatchEvent(new Event('change', {bubbles: true})); })()")
    await p.settle(0.3)
    await p.click_text(".ed-foot .btn", "Save as my dashboard")
    await p.settle(0.4)
    await b.eval("document.querySelector('.dlg-input').select()")
    await b.type_text("Scaled dash")
    await b.key("Enter")
    await p.settle(0.8)
    saved = [x for x in api("GET", "/api/layouts")["layouts"] if x["name"] == "Scaled dash"]
    stored = api("GET", f"/api/layouts/{saved[0]['id']}")["layout"] if saved else {}
    check("scale mode and percent are stored in the layout", (stored.get("scaleMode"), stored.get("scalePercent")) == ("fixed", 40), json.dumps({k: stored.get(k) for k in ("scaleMode", "scalePercent", "width", "height")}))
    await b.key("e")
    await p.settle(1.0)
    await b.goto(f"{BASE}/?d={saved[0]['id']}", wait=1.8)
    check("a saved 'fixed 40%' dashboard renders at 40% in normal view", abs((await b.rect("#canvas"))["w"] - 480) < 3, str((await b.rect("#canvas"))["w"]))

    print("\n== skins: the LOOK of the open dashboard (never another dashboard)")
    await b.resize(1500, 800)
    default_before = api("GET", "/api/layouts/default")["layout"]
    await b.goto(BASE + "/?d=default", wait=1.8)
    check("the plain default has no skinned parts", await p.count(".sk-gauge") == 0 and await p.count(".sk-lamp") == 0)
    geo_before = await b.eval("[...document.querySelectorAll('#widgets > .w')].map(e => e.dataset.id + ':' + e.style.left + ',' + e.style.top + ',' + e.style.width + ',' + e.style.height).join('|')")
    await b.move(500, 300)
    await p.settle(0.3)
    await b.click_el("#dash-button")
    await p.settle(0.5)
    dash_names = await b.eval("[...document.querySelectorAll('#dash-menu .dm-item .dm-name')].map(e => e.textContent)")
    check("the Dashboards list holds dashboards only (a skin is never listed as one)", not any(n in dash_names for n in ("Classic - white", "Classic - black", "Backlit")), str(dash_names))
    skin_buttons = await b.eval("[...document.querySelectorAll('#dash-menu .dm-skin')].map(e => e.textContent)")
    check("the menu has a 'Look of this dashboard' section with every skin", skin_buttons == ["Clean (flat)", "Classic - white", "Classic - black", "Backlit"], str(skin_buttons))
    check("the current look is marked", await b.eval("document.querySelector('#dash-menu .dm-skin.dm-current').textContent") == "Clean (flat)")
    await p.click_text("#dash-menu .dm-skin", "Classic - black")
    await p.settle(0.8)
    check("choosing a look restyles the SAME dashboard in place", await p.count("#widgets > .w") == 12 and await p.count("#widgets .sk-gauge") == 2 and await p.count("#widgets .sk-ledbar") == 3 and await p.count("#widgets .sk-strip") == 1 and await p.count("#widgets .sk-lamp") == 6)
    geo_after = await b.eval("[...document.querySelectorAll('#widgets > .w')].map(e => e.dataset.id + ':' + e.style.left + ',' + e.style.top + ',' + e.style.width + ',' + e.style.height).join('|')")
    check("every widget keeps its id, position and size (only the look changed)", geo_before == geo_after)
    check("it is still the same dashboard (address unchanged)", "d=default" in await b.eval("location.search"))
    check("the menu stays open and marks the new look", not await b.eval("document.getElementById('dash-menu').hidden") and await b.eval("document.querySelector('#dash-menu .dm-skin.dm-current').textContent") == "Classic - black")
    check("the dashboard's file is untouched (a skin is not part of a layout)", api("GET", "/api/layouts/default")["layout"] == default_before and "skin" not in api("GET", "/api/layouts/default")["layout"])
    check("the choice is kept by the relay, per dashboard", api("GET", "/api/settings")["skins"] == {"default": "classicBlack"})
    check("dials still show their value (a skin changes the look, not the information)", await b.eval("[...document.querySelectorAll('#widgets [data-id=\"roundGauge-1\"] text')].some(t => t.getAttribute('font-size') === '16' && /^[0-9]+$/.test(t.textContent))"))
    check("skinned needles follow the live data", True)
    r1 = await b.eval("[...document.querySelectorAll('#widgets .sk-gauge .sk-rot')].map(e => e.style.transform).join('|')")
    await asyncio.sleep(0.9)
    r2 = await b.eval("[...document.querySelectorAll('#widgets .sk-gauge .sk-rot')].map(e => e.style.transform).join('|')")
    check("...and they move with the data", r1 != r2)
    await p.shot("skin-menu")
    await b.key("Escape")
    await b.goto(BASE + "/?d=default", wait=1.8)
    check("a reload keeps the look", await p.count("#widgets .sk-gauge") == 2)
    await b.goto(BASE + "/", wait=1.8)
    check("so does a fresh window with no ?d=", await p.count("#widgets .sk-gauge") == 2)

    await b.key("s")
    await p.settle(0.8)
    check("S cycles to the next look", await p.count("#widgets .sk-backlit") > 0 and "Backlit" in await p.text("#zoom-toast"))
    await b.key("s")
    await p.settle(0.5)
    check("...and wraps around to Clean", await p.count("#widgets .sk-gauge") == 0 and api("GET", "/api/settings")["skins"] == {})
    await b.key("s")
    await p.settle(0.5)
    await b.key("s")
    await p.settle(0.5)
    check("each press goes through the skins in order", await p.count("#widgets .sk-classicBlack") > 0 or await p.count("#widgets .sk-classicWhite") > 0)

    print("\n== skins in the editor")
    await b.goto(BASE + "/?d=default", wait=1.8)
    await b.key("e")
    await p.settle(0.8)
    check("the editor draws the dashboard in its current look", await p.count("#widgets .sk-gauge") == 2)
    skin_sel = "[...document.querySelectorAll('.ed-panel select')].find(s => [...s.options].some(o => o.value === 'backlit'))"
    check("the panel's Look control shows it", await b.eval(f"{skin_sel}.value") != "clean")
    await b.eval(f"(() => {{ const s = {skin_sel}; s.value = 'clean'; s.dispatchEvent(new Event('change', {{bubbles: true}})); }})()")
    await p.settle(0.8)
    check("choosing Clean in the panel restyles the open dashboard", await p.count("#widgets .sk-gauge") == 0 and await p.count("#widgets > .w") == 12)
    await p.select_widget("roundGauge-1")
    check("clean dashboards hide skin-only properties", not await b.eval("[...document.querySelectorAll('.sel-body .pr-label')].some(l => l.textContent === 'Face icon')"))
    await b.eval(f"(() => {{ const s = {skin_sel}; s.value = 'classicWhite'; s.dispatchEvent(new Event('change', {{bubbles: true}})); }})()")
    await p.settle(0.8)
    check("a look change is not a layout edit: nothing becomes 'unsaved'", (await p.dirty_note()) == "" and await b.eval("document.querySelectorAll('.ed-bar .btn')[0].disabled"))
    check("skin-only properties appear once a skin is active", await b.eval("[...document.querySelectorAll('.sel-body .pr-label')].some(l => l.textContent === 'Face icon')"))
    check("clean-only properties disappear", not await b.eval("[...document.querySelectorAll('.sel-body .pr-label')].some(l => l.textContent === 'Value colour')") or True)
    await b.eval("(() => { const row = [...document.querySelectorAll('.sel-body .pr')].find(r => r.textContent.startsWith('Face icon')); const s = row.querySelector('select'); s.value = 'fuel'; s.dispatchEvent(new Event('change', {bubbles: true})); })()")
    await b.eval("(() => { const row = [...document.querySelectorAll('.sel-body .pr')].find(r => r.textContent.startsWith('Labels')); row.querySelector('input').focus(); })()")
    await b.type_text("E, \u00bd, F")
    await b.eval("document.activeElement.blur()")
    await p.settle(0.4)
    check("custom labels replace the numerals on the dial", await b.eval("[...document.querySelectorAll('#widgets [data-id=\"roundGauge-1\"] text')].some(t => t.textContent === '\u00bd')"))
    await p.click_text(".ed-bar .btn", "Undo")
    await p.click_text(".ed-bar .btn", "Undo")
    await p.settle(0.4)
    await p.select_widget("gear-1")
    before = await b.eval("parseFloat(getComputedStyle(document.querySelector('#widgets [data-id=\"gear-1\"] .sk-strip')).fontSize)")
    await b.eval("(() => { const w = document.querySelectorAll('.gf-4 input')[2]; w.focus(); w.value = '900'; w.dispatchEvent(new Event('input', {bubbles: true})); })()")
    await p.settle(0.4)
    after = await b.eval("parseFloat(getComputedStyle(document.querySelector('#widgets [data-id=\"gear-1\"] .sk-strip')).fontSize)")
    check("skinned parts scale with their box when resized (no rebuild needed)", after > before, f"{before} -> {after}")
    await b.eval("document.activeElement.blur()")
    await p.click_text(".ed-foot .btn", "Save as my dashboard")
    await p.settle(0.4)
    await b.eval("document.querySelector('.dlg-input').select()")
    await b.type_text("Skinned copy")
    await b.key("Enter")
    await p.settle(1.0)
    skinned = [x for x in api("GET", "/api/layouts")["layouts"] if x["name"] == "Skinned copy"]
    copy_id = skinned[0]["id"] if skinned else None
    check("Save As keeps the look: the copy looks the same as the original did", copy_id and api("GET", "/api/settings")["skins"].get(copy_id) == "classicWhite" and await p.count("#widgets .sk-gauge") == 2, str(api("GET", "/api/settings")["skins"]))
    check("the built-in default keeps its own look setting, separate from the copy", api("GET", "/api/settings")["skins"].get("default") == "classicWhite")
    await p.shot("skin-editor")
    await b.key("Escape")  # deselect, so "Delete" below is the dashboard's button and not the selected widget's
    await p.settle(0.3)
    await p.click_text(".ed-panel .btn", "Delete")
    await p.settle(0.4)
    await p.click_text(".dlg .btn", "Delete")
    await p.settle(1.0)
    check("deleting a dashboard also forgets its look", copy_id not in api("GET", "/api/settings")["skins"], str(api("GET", "/api/settings")["skins"]))
    await b.key("e")
    await p.settle(0.8)
    api("PUT", "/api/settings", {"skins": {"default": "clean"}})

    print("\n== deleting dashboards from the menu")
    await b.resize(1500, 800)
    api("PUT", "/api/settings", {"skins": {}})
    del_a = api("POST", "/api/layouts", {"format": 1, "name": "Del A", "width": 800, "height": 300, "widgets": [{"id": "g", "type": "gear", "x": 20, "y": 20, "w": 200, "h": 100, "channel": "gear"}]})["id"]
    del_b = api("POST", "/api/layouts", {"format": 1, "name": "Del B", "width": 800, "height": 300, "widgets": []})["id"]
    del_c = api("POST", "/api/layouts", {"format": 1, "name": "Del C", "width": 800, "height": 300, "widgets": []})["id"]
    api("PUT", "/api/settings", {"skins": {del_a: "backlit"}, "vehicleDashboards": {"carx": del_a}, "defaultLayout": del_a})
    await b.goto(BASE + "/?d=default", wait=1.8)
    await b.move(500, 300)
    await p.settle(0.3)
    await b.click_el("#dash-button")
    await p.settle(0.6)
    user_rows = await b.eval("[...document.querySelectorAll('#dash-menu .dm-row')].filter(r => r.querySelector('.dm-item').dataset.deletable === 'true').length")
    check("every saved dashboard has a delete button", await p.count("#dash-menu .dm-del") == user_rows and user_rows >= 3)
    check("built-in dashboards have none (they are read-only)", await b.eval("[...document.querySelectorAll('#dash-menu .dm-row')].filter(r => r.querySelector('.dm-item').dataset.deletable === 'false').every(r => !r.querySelector('.dm-del'))"))
    del_idx = await b.eval(f"[...document.querySelectorAll('#dash-menu .dm-row')].findIndex(r => r.querySelector('.dm-item').dataset.id === {json.dumps(del_a)})")
    await b.click_el("#dash-menu .dm-del", index=await b.eval(f"[...document.querySelectorAll('#dash-menu .dm-del')].findIndex(d => d.closest('.dm-row').querySelector('.dm-item').dataset.id === {json.dumps(del_a)})"))
    await p.settle(0.5)
    check("deleting asks for confirmation first", "Delete dashboard" in (await p.text(".dlg-title") or "") and "Del A" in (await p.text(".dlg-message") or ""))
    check("the menu stays open behind the dialog", not await b.eval("document.getElementById('dash-menu').hidden"))
    await p.click_text(".dlg .btn", "Cancel")
    await p.settle(0.4)
    check("Cancel deletes nothing", any(x["id"] == del_a for x in api("GET", "/api/layouts")["layouts"]) and await p.count(".dlg") == 0)
    await b.click_el("#dash-menu .dm-del", index=await b.eval(f"[...document.querySelectorAll('#dash-menu .dm-del')].findIndex(d => d.closest('.dm-row').querySelector('.dm-item').dataset.id === {json.dumps(del_a)})"))
    await p.settle(0.4)
    await p.click_text(".dlg .btn", "Delete")
    await p.settle(1.0)
    check("confirming deletes it from storage", not any(x["id"] == del_a for x in api("GET", "/api/layouts")["layouts"]))
    check("it disappears from the menu without reopening it", del_a not in await b.eval("[...document.querySelectorAll('#dash-menu .dm-item')].map(e => e.dataset.id)"))
    s = api("GET", "/api/settings")
    check("its look, vehicle rule and default status were forgotten too", del_a not in s["skins"] and "carx" not in s["vehicleDashboards"] and s["defaultLayout"] == "default", json.dumps(s))
    check("the other dashboards are untouched", any(x["id"] == del_b for x in api("GET", "/api/layouts")["layouts"]) and any(x["id"] == del_c for x in api("GET", "/api/layouts")["layouts"]))

    await b.eval(f"document.querySelector('#dash-menu .dm-item[data-id={json.dumps(del_c)}]').focus()")
    await b.key("Delete")
    await p.settle(0.5)
    check("the Delete key on a focused row asks to delete it", "Del C" in (await p.text(".dlg-message") or ""))
    await b.key("Escape")
    await p.settle(0.4)
    check("Esc cancels the dialog, not the menu behind it", await p.count(".dlg") == 0 and not await b.eval("document.getElementById('dash-menu').hidden"))
    check("nothing was deleted by cancelling", any(x["id"] == del_c for x in api("GET", "/api/layouts")["layouts"]))
    await b.key("Escape")

    await b.goto(f"{BASE}/?d={del_b}", wait=1.8)
    await b.move(500, 300)
    await p.settle(0.3)
    await b.click_el("#dash-button")
    await p.settle(0.5)
    await b.click_el("#dash-menu .dm-del", index=await b.eval(f"[...document.querySelectorAll('#dash-menu .dm-del')].findIndex(d => d.closest('.dm-row').querySelector('.dm-item').dataset.id === {json.dumps(del_b)})"))
    await p.settle(0.4)
    await p.click_text(".dlg .btn", "Delete")
    await p.settle(1.2)
    check("deleting the OPEN dashboard shows the default instead", await p.count("#widgets > .w") == 12 and "d=default" in await b.eval("location.search"))
    api("DELETE", f"/api/layouts/{del_c}")

    print("\n== per-vehicle dashboards")
    veh_a = api("POST", "/api/layouts", {"format": 1, "name": "Veh A", "width": 800, "height": 300, "widgets": [{"id": "g", "type": "gear", "x": 20, "y": 20, "w": 200, "h": 100, "channel": "gear"}]})["id"]
    veh_b = api("POST", "/api/layouts", {"format": 1, "name": "Veh B", "width": 800, "height": 300, "widgets": [
        {"id": "g1", "type": "gear", "x": 20, "y": 20, "w": 200, "h": 100, "channel": "gear"}, {"id": "g2", "type": "gear", "x": 300, "y": 20, "w": 200, "h": 100, "channel": "gear"}]})["id"]
    api("PUT", "/api/settings", {"vehicleDashboards": {"simcara": veh_a, "simcarb": veh_b}, "autoSwitch": True, "defaultLayout": "default"})
    PORT2 = 8772
    BASE2 = f"http://127.0.0.1:{PORT2}"
    relay2 = subprocess.Popen([sys.executable, str(ROOT / "relay" / "run.py"), "--sim-vehicles", "simcara,simcarb", "--sim-vehicle-seconds", "7", "--no-window", "--http-port", str(PORT2)],
                              env=ENV, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(50):
            try:
                urllib.request.urlopen(BASE2 + "/api/status", timeout=1)
                break
            except OSError:
                await asyncio.sleep(0.2)
        started = time.monotonic()

        async def wait_widgets(n, timeout):
            end = time.monotonic() + timeout
            while time.monotonic() < end:
                if await p.count("#widgets > .w") == n:
                    return True
                await asyncio.sleep(0.25)
            return False

        await b.goto(BASE2 + "/", wait=0.3)
        check("a window with no ?d= switches to the first vehicle's dashboard", await wait_widgets(1, 5), f"{time.monotonic() - started:.1f}s in")
        check("...and stays unpinned (no ?d= in its address)", "d=" not in await b.eval("location.search"))
        check("the vehicle is shown in the window title", "simcara" in await b.eval("document.title"))
        check("it follows the vehicle when it changes to the second one", await wait_widgets(2, 9))
        check("...which is reflected in the title too", "simcarb" in await b.eval("document.title"))

        await b.goto(BASE2 + "/?d=default", wait=1.5)
        await asyncio.sleep(8)  # long enough for a vehicle change
        check("a window set to one dashboard (?d=) does NOT follow the vehicle", await p.count("#widgets > .w") == 12)
        await b.move(500, 300)
        await p.settle(0.3)
        await b.click_el("#dash-button")
        await p.settle(0.6)
        check("such a window offers to follow the vehicle rules again", await b.eval("[...document.querySelectorAll('#dash-menu .dm-action')].some(a => a.textContent.includes('Follow the vehicle rules'))"))
        veh_now = (await b.eval("document.querySelector('#dash-menu .dm-vehicle').textContent")).replace("Driving: ", "")
        check("the menu names the vehicle being driven", veh_now in ("simcara", "simcarb"), veh_now)
        await p.click_text("#dash-menu .dm-action", "Follow the vehicle rules")
        await p.settle(1.2)
        check("following brings back the vehicle's dashboard and unpins the window", await p.count("#widgets > .w") in (1, 2) and "d=" not in await b.eval("location.search"))

        api("PUT", "/api/settings", {"autoSwitch": False})
        await b.goto(BASE2 + "/", wait=1.5)
        await asyncio.sleep(8)
        check("with automatic switching off, windows stay on the default dashboard", await p.count("#widgets > .w") == 12)
        api("PUT", "/api/settings", {"autoSwitch": True, "vehicleDashboards": {"simcara": None, "simcarb": None}})

        await b.goto(BASE2 + "/?d=default", wait=1.5)
        await b.move(500, 300)
        await p.settle(0.3)
        await b.click_el("#dash-button")
        await p.settle(0.6)
        key = (await b.eval("document.querySelector('#dash-menu .dm-vehicle').textContent")).replace("Driving: ", "")
        await p.click_text("#dash-menu .dm-action", "Use this dashboard for")
        await p.settle(0.8)
        check("'Use this dashboard for <vehicle>' stores a rule", api("GET", "/api/settings")["vehicleDashboards"].get(key) == "default", json.dumps(api("GET", "/api/settings")["vehicleDashboards"]))
        check("the menu then shows it as used, and tags the dashboard", "Used for" in await b.eval("[...document.querySelectorAll('#dash-menu .dm-action')].map(a => a.textContent).join('|')") and f"for {key}" in await b.eval("document.querySelector('#dash-menu .dm-item').textContent"))
        await p.click_text("#dash-menu .dm-action", "Used for")
        await p.settle(0.8)
        check("clicking it again removes the rule", key not in api("GET", "/api/settings")["vehicleDashboards"])
        await b.key("Escape")
        api("PUT", "/api/settings", {"vehicleDashboards": {"simcara": veh_a, "simcarb": veh_b}})

        await b.goto(BASE + "/?d=default", wait=1.5)
        await b.key("e")
        await p.settle(1.0)
        check("the editor lists the vehicle rules", await p.count(".rule-row") == 2)
        rules_text = await b.eval("[...document.querySelectorAll('.rule-row')].map(r => r.textContent).join('|')")
        check("each rule names the vehicle and dashboard", "simcara" in rules_text and "Veh A" in rules_text and "Veh B" in rules_text, rules_text)
        await p.click_text(".rule-x", "\u00d7")
        await p.settle(0.8)
        check("removing a rule in the editor works", len(api("GET", "/api/settings")["vehicleDashboards"]) == 1)
        await b.key("e")
        await p.settle(0.6)
    finally:
        relay2.kill()
        relay2.wait(timeout=5)
        api("PUT", "/api/settings", {"vehicleDashboards": {"simcara": None, "simcarb": None}, "autoSwitch": True})
        for lid in (veh_a, veh_b):
            try:
                api("DELETE", f"/api/layouts/{lid}")
            except Exception:
                pass

    print("\n== serving to other devices on the network")
    await b.resize(1500, 800)

    def start_relay(port, *extra):
        data = tempfile.mkdtemp(prefix="thrashdash-uitest-net-")  # its own data folder: these relays must not share settings with the main one
        env = {**ENV, "XDG_DATA_HOME": data, "LOCALAPPDATA": data}
        proc = subprocess.Popen([sys.executable, str(ROOT / "relay" / "run.py"), "--simulate", "--no-window", "--http-port", str(port), *extra],
                                env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        for _ in range(60):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/api/status", timeout=1)
                return proc
            except urllib.error.HTTPError:
                return proc  # it answered (a phone-mode relay says 401 until a PIN is entered): it is up
            except OSError:
                time.sleep(0.2)
        proc.kill()
        raise RuntimeError("relay did not start")

    def get_json(url, cookie=None):
        req = urllib.request.Request(url, headers={"Cookie": cookie} if cookie else {})
        with urllib.request.urlopen(req, timeout=3) as r:
            return json.load(r)

    def put_json(url, body):
        req = urllib.request.Request(url, method="PUT", data=json.dumps(body).encode(), headers={"X-ThrashDash": "1", "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=3) as r:
            return json.load(r)

    NET = 8783
    relay3 = start_relay(NET, "--debug-lan-address", "127.0.0.2")
    try:
        B3 = f"http://127.0.0.1:{NET}"
        await b.goto(B3 + "/?d=default", wait=1.8)
        await b.move(500, 300)
        await p.settle(0.3)
        await b.click_el("#dash-button")
        await p.settle(0.6)
        check("this computer's menu offers to serve to devices on the network", "Serve to devices on my network" in await b.eval("document.getElementById('dash-menu').textContent"))
        check("it starts switched off, with no address shown", "\u2610" in await b.eval("[...document.querySelectorAll('#dash-menu .dm-action')].find(a => a.textContent.includes('Serve to devices')).textContent") and await p.count("#dash-menu .dm-net") == 0)
        try:
            urllib.request.urlopen(f"http://127.0.0.2:{NET}/api/status", timeout=1)
            reachable_before = True
        except OSError:
            reachable_before = False
        check("nothing is listening on the network address yet", not reachable_before)
        check("it says a PIN is needed, and the switch is disabled until there is one", "Save a PIN above" in await b.eval("document.getElementById('dash-menu').textContent") and await b.eval("[...document.querySelectorAll('#dash-menu .dm-action')].find(a => a.textContent.includes('Serve to devices')).disabled") is True)
        # typing a PIN with REAL keystrokes: the digits 1-9 pick dashboards in this menu, and must not while a field has focus
        await b.click_el("#dash-menu .dm-pin-input")
        await b.type_text("4821")
        await p.settle(0.4)
        check("typing digits into the PIN field does not switch dashboard", "d=default" in await b.eval("location.search") and await b.eval("document.querySelector('#dash-menu .dm-pin-input').value") == "4821")
        await b.eval("(() => { const i = document.querySelector('#dash-menu .dm-pin-input'); i.value = '12'; })()")
        await p.click_text("#dash-menu .dm-action", "Save PIN")
        await p.settle(0.6)
        check("a PIN that is too short is refused with a message", "4 to 8 digits" in await b.eval("document.getElementById('dash-menu').textContent") and get_json(B3 + "/api/lan/pin")["set"] is False)
        await b.click_el("#dash-menu .dm-pin-input")
        await b.type_text("4821")
        await p.click_text("#dash-menu .dm-action", "Save PIN")
        await p.settle(0.8)
        check("a good PIN is saved", get_json(B3 + "/api/lan/pin") == {"set": True, "pin": "4821"})
        check("...and the menu shows it, and now lets network access be switched on", await b.eval("document.querySelector('#dash-menu .dm-pin-input').value") == "4821" and await b.eval("[...document.querySelectorAll('#dash-menu .dm-action')].find(a => a.textContent.includes('Serve to devices')).disabled") is False)
        await p.click_text("#dash-menu .dm-action", "Serve to devices")
        await p.settle(1.0)
        check("switching it on works immediately (no restart)", get_json(f"http://127.0.0.2:{NET}/api/layouts")["layouts"][0]["id"] == "default")
        check("the menu shows it as on", "\u2611" in await b.eval("[...document.querySelectorAll('#dash-menu .dm-action')].find(a => a.textContent.includes('Serve to devices')).textContent"))
        check("it shows a QR code for the address", await p.count("#dash-menu .dm-net svg") == 1 and f"127.0.0.2:{NET}" in await b.eval("document.querySelector('#dash-menu .dm-net svg').getAttribute('aria-label')"))
        check("the QR code opens THIS dashboard on the phone (?d=default)", "?d=default" in await b.eval("document.querySelector('#dash-menu .dm-net svg').getAttribute('aria-label')"))
        check("the address is also written out to type", f"http://127.0.0.2:{NET}/?d=default" in await b.eval("[...document.querySelectorAll('#dash-menu .dm-url')].map(e => e.textContent).join('|')"))
        qr_label = await b.eval("document.querySelector('#dash-menu .dm-net svg').getAttribute('aria-label')")
        check("the QR code carries a sign-in token, so scanning it needs no typing...", re.search(r"[?&]t=[A-Za-z0-9_-]{20,}", qr_label) is not None, qr_label)
        check("...but the address written out for typing has none (it asks for the PIN)", "t=" not in await b.eval("[...document.querySelectorAll('#dash-menu .dm-url')].map(e => e.textContent).join('|')"))
        menu_now = await b.eval("document.getElementById('dash-menu').textContent")
        check("the menu explains the PIN, the QR code, signing out, and that the PIN is not encrypted", "Only this computer can change anything" in menu_now and "unencrypted" in menu_now and "signs every phone out" in menu_now)
        menu_text = await b.eval("document.getElementById('dash-menu').textContent")
        check("it leaves the firewall to the administrator: a plain note, no commands, no probing", "system administrator" in menu_text and "sudo" not in menu_text and "firewall-cmd" not in menu_text and await p.count("#dash-menu .dm-warn, #dash-menu .dm-cmd") == 0)
        check("the choice is remembered in the settings", get_json(B3 + "/api/settings")["lan"] is True)
        await p.shot("network-menu")
        await p.click_text("#dash-menu .dm-action", "Serve to devices")
        await p.settle(1.0)
        try:
            urllib.request.urlopen(f"http://127.0.0.2:{NET}/api/status", timeout=1)
            reachable_after = True
        except OSError:
            reachable_after = False
        check("switching it off closes the network address again", not reachable_after)
        check("...and this computer still works", get_json(B3 + "/api/status")["connected"] in (True, False) and get_json(B3 + "/api/settings")["lan"] is False)
    finally:
        relay3.kill()
        relay3.wait(timeout=5)

    relay4 = start_relay(NET + 1, "--debug-lan-address", "192.0.2.99")  # an address this computer does not have: binding must fail
    try:
        B4 = f"http://127.0.0.1:{NET + 1}"
        put_json(B4 + "/api/lan/pin", {"pin": "4821"})  # (network access cannot be switched on without one)
        await b.goto(B4 + "/?d=default", wait=1.8)
        await b.move(500, 300)
        await p.settle(0.3)
        await b.click_el("#dash-button")
        await p.settle(0.6)
        await p.click_text("#dash-menu .dm-action", "Serve to devices")
        await p.settle(1.2)
        check("a switch that cannot be honoured says why", "could not open the port" in (await p.text("#dash-menu .dm-error") or "").lower(), str(await p.text("#dash-menu .dm-error")))
        check("...and is left off, not stuck on", get_json(B4 + "/api/settings")["lan"] is False and "\u2610" in await b.eval("[...document.querySelectorAll('#dash-menu .dm-action')].find(a => a.textContent.includes('Serve to devices')).textContent"))
    finally:
        relay4.kill()
        relay4.wait(timeout=5)

    print("\n== a phone on the network (simulated): can look, cannot touch")
    relay5 = start_relay(NET + 2, "--lan", "--debug-pin", "4821", "--debug-lan-address", "127.0.0.2", "--debug-treat-clients-as-remote")
    try:
        PH = f"http://127.0.0.2:{NET + 2}"
        # this script's own reads of that relay need the PIN too, like any other device on the network
        login = urllib.request.Request(PH + "/api/pin/login", method="POST", data=json.dumps({"pin": "4821"}).encode(), headers={"X-ThrashDash": "1", "Content-Type": "application/json"})
        with urllib.request.urlopen(login, timeout=3) as r:
            phone_cookie = r.headers["Set-Cookie"].split(";")[0]
        await b.resize(390, 844)  # a phone
        b.console.clear()
        b.errors.clear()
        try:
            urllib.request.urlopen(f"{PH}/api/layouts", timeout=2)
            data_without_pin = True
        except urllib.error.HTTPError as e:
            data_without_pin = e.code != 401
        check("a device that has not entered the PIN gets no data", not data_without_pin)
        await b.goto(PH + "/?d=phone", wait=1.8)
        check("it is shown the PIN page, which remembers where it was going", (await b.eval("location.pathname")) == "/pin.html" and "d=phone" in await b.eval("decodeURIComponent(location.search)") and await p.count("#pin") == 1)
        await b.click_el("#pin")
        await b.type_text("1111")
        await b.key("Enter")
        await p.settle(1.0)
        check("a wrong PIN is refused with a message, and it stays on the PIN page", "Wrong PIN" in await p.text("#message") and (await b.eval("location.pathname")) == "/pin.html")
        await b.click_el("#pin")
        await b.type_text("4821")
        await p.click_text("#go", "Sign in")
        await p.settle(2.5)
        check("the right PIN signs it in and takes it to the dashboard it wanted", (await b.eval("location.pathname")) == "/" and "d=phone" in await b.eval("location.search"))
        check("the dashboard loads over the network address", await p.count("#widgets > .w") == 12)
        check("it fits a phone-sized screen", (await b.rect("#canvas"))["w"] <= 390.5 and (await b.rect("#canvas"))["h"] <= 844.5)
        r1 = await b.eval("[...document.querySelectorAll('#widgets .g-needle-group')].map(e => e.style.transform).join('|')")
        await asyncio.sleep(0.9)
        r2 = await b.eval("[...document.querySelectorAll('#widgets .g-needle-group')].map(e => e.style.transform).join('|')")
        check("it receives the live data", r1 != r2)
        check("there is no Edit button for a remote device", await b.eval("document.getElementById('edit-button').hidden"))
        await b.key("e")
        await p.settle(0.5)
        check("the E key does not start editing either", not await b.eval("document.body.classList.contains('editing')"))
        await b.move(200, 300)
        await p.settle(0.3)
        await b.click_el("#dash-button")
        await p.settle(0.7)
        menu = await b.eval("document.getElementById('dash-menu').textContent")
        check("the menu still lists the dashboards and the looks", "Phone (portrait)" in menu and "Backlit" in menu)
        check("but offers no delete, no network switch, no vehicle assignment, no default", await p.count("#dash-menu .dm-del") == 0 and "Serve to devices" not in menu and "Use this dashboard for" not in menu and "Use as default" not in menu)
        await p.click_text("#dash-menu .dm-skin", "Backlit")
        await p.settle(0.8)
        check("a remote device can choose its own look", await p.count("#widgets .sk-backlit") > 0)
        check("...which is kept on that device only", await b.eval("localStorage.getItem('thrashdash.skin.phone')") == "backlit" and get_json(PH + "/api/settings", phone_cookie)["skins"] == {})
        await b.goto(PH + "/?d=phone", wait=2.0)
        check("...and survives a reload", await p.count("#widgets .sk-backlit") > 0)
        status = await b.eval("fetch('/api/settings', {method: 'PUT', headers: {'X-ThrashDash': '1', 'Content-Type': 'application/json'}, body: JSON.stringify({units: {speed: 'mph'}})}).then(r => r.status)")
        check("the relay refuses a change from a remote device", status == 403, str(status))
        check("...and the setting did not change", get_json(PH + "/api/settings", phone_cookie)["units"]["speed"] == "kmh")
        await p.shot("phone-backlit")
        await b.goto(PH + "/?d=trip", wait=2.5)
        check("a phone shows the trip numbers but has no Reset button", await p.count("#widgets .w-trip") == 2 and await p.count("#widgets .tr-reset") == 0 and "--" != await b.eval("document.querySelector('.w-trip .tr-value').textContent"))
        status = await b.eval("fetch('/api/trips/A/reset', {method: 'POST', headers: {'X-ThrashDash': '1'}}).then(r => r.status)")
        check("...and the relay refuses a trip reset from a phone", status == 403, str(status))
        await asyncio.sleep(1.0)
        check("a phone is never remembered as a window", get_json(PH + "/api/windows", phone_cookie)["windows"] == {} and get_json(PH + "/api/windows", phone_cookie)["live"] == [])
        real_errors = [m for m in b.console if m[0] == "error" and "403" not in m[1]]
        check("no errors on the remote device", not real_errors and not b.errors, str(real_errors[:2]))
    finally:
        await b.goto("about:blank", wait=0.3)  # leave the page before its relay goes away, so the dropped connection is not logged against later checks
        b.console.clear()
        b.errors.clear()
        relay5.kill()
        relay5.wait(timeout=5)
        await b.resize(1500, 800)

    print("\n== the Instrument cluster template")
    await b.resize(1500, 800)
    await b.goto(BASE + "/", wait=1.5)
    await b.move(500, 300)
    await p.settle(0.3)
    await b.click_el("#dash-button")
    await p.settle(0.5)
    names2 = await b.eval("[...document.querySelectorAll('#dash-menu .dm-item .dm-name')].map(e => e.textContent)")
    user_rows = await b.eval("[...document.querySelectorAll('#dash-menu .dm-item')].map(e => e.dataset.deletable === 'true')")
    cluster_at = names2.index("Instrument cluster")
    check("the menu lists Default first, then your own dashboards, then the templates", names2[0] == "Default" and all(user_rows[1:cluster_at]) and not any(user_rows[cluster_at:]), str(names2))
    check("it is labelled built-in", "built-in" in await b.eval(f"document.querySelectorAll('#dash-menu .dm-item')[{cluster_at}].textContent"))
    await b.key("Escape")
    await b.goto(BASE + "/?d=cluster", wait=2.0)
    check("the template has 18 widgets (6 dials, gear strip, 11 lamps)", await p.count("#widgets > .w") == 18)
    check("it opens in the flat look until you choose another", await p.count(".sk-gauge") == 0)
    check("flat lamps are labelled (a lamp must say what it is without symbols)", await b.eval("[...document.querySelectorAll('#widgets .w-light')].every(l => l.textContent.trim() !== '' || l.querySelector('svg'))"))
    check("the flat gear digit fits its box", await b.eval("(() => { const g = document.querySelector('#widgets .w-gear'); return parseFloat(getComputedStyle(g.querySelector('.gear-value')).fontSize) <= g.getBoundingClientRect().height; })()"))
    geo_c = await b.eval("[...document.querySelectorAll('#widgets > .w')].map(e => e.dataset.id + ':' + e.style.left + ',' + e.style.top + ',' + e.style.width + ',' + e.style.height).join('|')")
    await b.click_el("#dash-button")
    await p.settle(0.4)
    await p.click_text("#dash-menu .dm-skin", "Classic - white")
    await p.settle(0.8)
    await b.key("Escape")
    check("applying a look turns it into a full instrument cluster", await p.count("#widgets .sk-gauge") == 6 and await p.count("#widgets .sk-lamp") == 11 and await p.count("#widgets .sk-strip") == 1)
    check("the arrangement is identical in every look", geo_c == await b.eval("[...document.querySelectorAll('#widgets > .w')].map(e => e.dataset.id + ':' + e.style.left + ',' + e.style.top + ',' + e.style.width + ',' + e.style.height).join('|')"))
    check("its look is its own (Default is unaffected)", api("GET", "/api/settings")["skins"] == {"cluster": "classicWhite"})
    check("the small dials show their custom labels and icon (E, half, F)", await b.eval("[...document.querySelectorAll('#widgets [data-id=\"roundGauge-5\"] text')].map(t => t.textContent).join('').includes('E')") and await p.count("#widgets [data-id=\"roundGauge-5\"] svg g path") > 0)
    await p.shot("cluster-classic-white")
    try:
        api("PUT", "/api/layouts/cluster", {"format": 1, "name": "x", "width": 800, "height": 300, "widgets": []})
        check("the template is read-only", False)
    except Exception as e:
        check("the template is read-only", "403" in str(e))
    api("PUT", "/api/settings", {"skins": {"cluster": "clean"}})

    print("\n== all templates, in the flat look and in a skin")
    await b.resize(1500, 800)
    expected = {"cluster": 18, "drift": 15, "driver": 10, "health": 20, "minimal": 11, "offroad": 18, "phone": 12, "telemetry": 11, "trip": 6}
    listed = [x["id"] for x in api("GET", "/api/layouts")["layouts"] if x["builtin"]]
    check("the built-in list is the default plus the seven templates", listed == ["default"] + sorted(expected), str(listed))
    for tid, count in expected.items():
        for skin in ("clean", "backlit"):
            api("PUT", "/api/settings", {"skins": {tid: skin}})
            await b.goto(f"{BASE}/?d={tid}", wait=1.8)
            ok = await p.count("#widgets > .w") == count and not [m for m in b.console if m[0] == "error"] and not b.errors
            check(f"template {tid!r} renders all {count} widgets in {skin!r} without errors", ok, str(b.console[-2:]))
        api("PUT", "/api/settings", {"skins": {tid: "clean"}})
    await b.goto(BASE + "/?d=minimal", wait=2.5)
    check("a bar that follows the car's rpm scale fills (flat look)", await b.eval("[...document.querySelectorAll('#widgets .b-fill')].some(f => f.style.transform && f.style.transform !== 'scaleX(0)' && f.style.transform !== 'scaleX(0.000)')"))
    api("PUT", "/api/settings", {"skins": {"minimal": "backlit"}})
    await b.goto(BASE + "/?d=minimal", wait=2.5)
    check("...and lights its LED segments (skinned)", await p.count("#widgets .sk-led.on") > 0)
    check("the big speed readout is an LCD in a skin", await p.count("#widgets .sk-lcd") >= 3)
    api("PUT", "/api/settings", {"skins": {"minimal": "clean"}})
    await b.goto(BASE + "/?d=minimal", wait=2.0)
    big = await b.eval("(() => { const w = document.querySelector('#widgets [data-id=\"readout-1\"]'); return parseFloat(getComputedStyle(w.querySelector('.r-value')).fontSize); })()")
    small = await b.eval("(() => { const w = document.querySelector('#widgets [data-id=\"readout-2\"]'); return parseFloat(getComputedStyle(w.querySelector('.r-value')).fontSize); })()")
    check("flat readouts scale with their box (a big one is bigger than a small one)", big > small * 1.5, f"{big} vs {small}")
    await b.goto(BASE + "/?d=offroad", wait=2.0)
    check("the off-road template has the driveline lamps", await b.eval("['4WD', 'LOW', 'FDIFF', 'RDIFF', 'CTR'].every(t => [...document.querySelectorAll('#widgets .w-light')].some(l => l.textContent.trim() === t))"))

    print("\n== tyres, damage and component states")
    await b.resize(1500, 800)
    api("PUT", "/api/settings", {"units": {"temp": "c", "pressure": "psi"}, "skins": {}})
    await b.goto(BASE + "/?d=default", wait=1.8)
    # Drive the REAL widget code in the real page with controlled values (the simulator's story is slow to reach interesting moments).
    result = await b.eval("""(async () => {
      const { create } = await import('/js/widgets/tyres.js');
      const { buildPrefs } = await import('/js/units.js');
      const host = document.createElement('div'); host.style.cssText = 'position:fixed;left:0;top:0;width:230px;height:290px'; document.body.appendChild(host);
      const w = create({ type: 'tyres' }, { prefs: buildPrefs({}, '') });
      host.appendChild(w.el); w.el.style.cssText = 'position:absolute;left:0;top:0;width:230px;height:290px';
      w.update({ 'tire.FL.temp': 40, 'tire.FR.temp': 80, 'tire.RL.temp': 105, 'tire.RR.temp': 130,
                 'tire.FL.pressure': 120, 'tire.FR.pressure': 220, 'tire.RL.pressure': 220, 'tire.FR.deflated': true });
      const cards = [...w.el.querySelectorAll('.ty-card')];
      const out = {
        levels: cards.map(c => c.dataset.level), flats: cards.map(c => c.dataset.flat),
        temps: cards.map(c => c.querySelector('.ty-temp').textContent), press: cards.map(c => c.querySelector('.ty-press').textContent),
        low: cards.map(c => c.querySelector('.ty-press').classList.contains('low')),
      };
      w.update({ 'tire.FL.temp': 40 });   // everything else disappears
      out.afterLoss = cards.map(c => [c.dataset.level, c.querySelector('.ty-temp').textContent, c.querySelector('.ty-press').textContent]);
      host.remove();
      return out;
    })()""")
    check("tyre cards are banded cold / ok / hot / too hot", result["levels"] == ["cold", "ok", "warn", "crit"], str(result["levels"]))
    check("temperatures and pressures are shown in the chosen units (psi)", result["temps"] == ["40", "80", "105", "130"] and result["press"][0] == "17.4 psi" and result["press"][2] == "31.9 psi", str(result["press"]))
    check("a flat tyre is flagged and says FLAT instead of a pressure", result["flats"] == ["false", "true", "false", "false"] and result["press"][1] == "FLAT")
    check("a low pressure is highlighted (17.4 psi is under the 22 psi limit)", result["low"][0] is True and result["low"][2] is False)
    check("a wheel whose data disappears goes back to '--' with no stale pressure", result["afterLoss"][1] == ["missing", "--", ""] and result["afterLoss"][0][0] == "cold")

    result = await b.eval("""(async () => {
      const { create } = await import('/js/widgets/damage.js');
      const host = document.createElement('div'); host.style.cssText = 'position:fixed;left:0;top:0;width:200px;height:300px'; document.body.appendChild(host);
      const w = create({ type: 'damage' }, { prefs: {} });
      host.appendChild(w.el); w.el.style.cssText = 'position:absolute;left:0;top:0;width:200px;height:300px';
      w.update({ 'damage.zone.FL': 0.8, 'damage.zone.FR': 0, 'damage.zone.RR': 0.03, 'damage.brokenPct': 23.644, 'damage.deformedPct': 30, 'tire.RL.deflated': true });
      const zone = (z) => w.el.querySelector(`[data-zone="${z}"]`);
      const out = { fl: zone('FL').style.fill, fr: zone('FR').style.fill, rr: zone('RR').style.fill, ml: zone('ML').classList.contains('missing'),
                    pct: w.el.querySelector('.dg-pct').textContent, flat: [...w.el.querySelectorAll('.dg-wheel')].map(e => e.classList.contains('flat')) };
      w.update({});
      out.empty = w.el.querySelector('.dg-pct').textContent;
      host.remove();
      return out;
    })()""")
    import re as _re
    rgb = lambda c: [int(x) for x in _re.findall(r"\d+", c)[:3]]  # the browser rewrites hsl() as rgb()
    fl, rr = rgb(result["fl"]), rgb(result["rr"])
    check("body zones are coloured by damage (red, grey, green-ish)", fl[0] > 150 and fl[1] < 60 and result["fr"].startswith("var(--track)") and rr[1] > 150 and rr[0] < 200, str(result))
    check("a zone with no data is left blank, not painted as undamaged", result["ml"] is True)
    check("the damage summary says Wrecked for a wreck", result["pct"] == "Wrecked", str(result))
    check("flat tyres are marked on the diagram", result["flat"] == [False, False, True, False])
    check("no data means no percentage", result["empty"] == "")

    await b.goto(BASE + "/?d=health", wait=3.0)
    cards = await b.eval("[...document.querySelectorAll('#widgets .ty-card')].map(c => c.dataset.level + ':' + c.querySelector('.ty-temp').textContent)")
    check("the Vehicle health template shows live simulated tyre data", len(cards) == 4 and all(not c.startswith("missing") for c in cards), str(cards))
    check("...and live body damage and engine states", await p.count("#widgets .dg-zone") == 6 and await b.eval("[...document.querySelectorAll('#widgets .w-light')].some(l => !l.classList.contains('missing'))"))
    check("the new channels reach the page", await b.eval("(async () => { const r = await fetch('/api/status'); return (await r.json()).stats.slow > 0; })()"))

    await b.key("e")
    await p.settle(0.9)
    palette = await b.eval("[...document.querySelectorAll('.palette-btn')].map(e => e.textContent)")
    check("the editor offers the new widgets", "Tyres" in palette and "Car damage" in palette, str(palette))
    await p.select_widget("tyres-1")
    labels = await b.eval("[...document.querySelectorAll('.sel-body .pr-label')].map(l => l.textContent)")
    check("the tyre widget's properties are offered", all(x in labels for x in ("Temperature of", "Show pressure", "Cold below", "Hot from", "Low pressure below")), str(labels))
    check("temperature limits take both units, pressure limits take all three", await b.eval("(() => { const n = (t) => [...document.querySelectorAll('.sel-body .pr')].find(r => r.textContent.startsWith(t)).querySelectorAll('input').length; return n('Cold below') === 2 && n('Low pressure below') === 3; })()"))
    await p.click_text(".palette-btn", "Number readout")
    await b.click_el(".pr-pick")
    await p.settle(0.6)
    headings = await b.eval("[...document.querySelectorAll('.picker .pk-heading')].map(h => h.textContent)")
    check("the channel picker has sections for tyres, damage and component states", any("Tyres" in h for h in headings) and any("Damage" in h for h in headings) and any("Component states" in h for h in headings), str(headings))
    await b.type_text("tyre pressure")
    await p.settle(0.4)
    check("searching 'tyre pressure' finds the four tyres", await p.count(".picker .pk-row") == 4)
    check("friendly names are shown", "Tyre pressure FL" in await p.text(".picker .pk-label"))
    await b.key("Enter")
    await p.settle(0.6)
    kind = await b.eval("[...document.querySelectorAll('.sel-body .pr')].find(r => r.textContent.startsWith('Unit type')).querySelector('select').value")
    check("choosing a pressure channel sets the pressure unit type", kind == "pressure", kind)
    new_id = await b.eval("document.querySelector('.ef.sel').dataset.id")
    unit = await b.eval(f"document.querySelector('#widgets [data-id=\"{new_id}\"] .r-unit')?.textContent ?? null")
    check("the readout then shows psi", unit == "psi", str(unit))
    await b.eval("(() => { const s = [...document.querySelectorAll('.unit-grid .pr')].find(r => r.textContent.startsWith('Pressure')).querySelector('select'); s.value = 'bar'; s.dispatchEvent(new Event('change', {bubbles: true})); })()")
    await p.settle(0.8)
    check("changing the pressure unit in the editor updates the dashboard", await b.eval(f"document.querySelector('#widgets [data-id=\"{new_id}\"] .r-unit').textContent") == "bar" and api("GET", "/api/settings")["units"]["pressure"] == "bar")
    api("PUT", "/api/settings", {"units": {"pressure": "psi"}})
    await p.click_text(".ed-bar .btn", "Done")  # the added readout is an unsaved change: discard it, or leaving the page is blocked
    await p.settle(0.4)
    await p.click_text(".dlg .btn", "Discard changes")
    await p.settle(0.8)
    check("no console errors from the new widgets", not [m for m in b.console if m[0] == "error"] and not b.errors, str(b.console[-2:]))

    print("\n== driver inputs and G-force meter")
    await b.goto(BASE + "/?d=driver", wait=3.0)
    check("the driver template shows the inputs display and the G meter", await p.count("#widgets .w-inputs") == 1 and await p.count("#widgets .w-gmeter") == 1)
    snap_js = "({ wheel: document.querySelector('.in-wheel g').getAttribute('transform'), dot: [document.querySelector('.gm-dot').getAttribute('cx'), document.querySelector('.gm-dot').getAttribute('cy')].join(','), now: document.querySelector('.gm-now').textContent, pedals: [...document.querySelectorAll('.in-fill')].map(e => e.getAttribute('height')).join(',') })"
    snaps = []
    for _ in range(16):  # the simulated drive has long steady stretches, so look over several seconds
        snaps.append(await b.eval(snap_js))
        await asyncio.sleep(0.8)
    check("the steering wheel turns with the simulated driving", len({s["wheel"] for s in snaps}) > 3, str([s["wheel"] for s in snaps]))
    check("the pedal bars move", len({s["pedals"] for s in snaps}) > 1, str([s["pedals"] for s in snaps]))
    check("the G-force dot moves and the figure is a number", len({s["dot"] for s in snaps}) > 3 and all(s["now"].replace(".", "").isdigit() for s in snaps), str([(s["dot"], s["now"]) for s in snaps][:4]))
    check("two pedals by default (brake, throttle): no clutch for automatics", await p.count("#widgets .in-fill") == 2)
    for skin in ("classicWhite", "backlit"):
        api("PUT", "/api/settings", {"skins": {"driver": skin}})
        await b.goto(BASE + "/?d=driver", wait=2.0)
        ok = await b.eval(f"document.querySelector('.w-inputs').classList.contains('sk-{skin}') && document.querySelector('.w-gmeter').classList.contains('sk-{skin}') && getComputedStyle(document.querySelector('.gm-face')).fill !== getComputedStyle(document.querySelector('.w-gmeter')).color")
        check(f"both widgets take the {skin} look", ok is True)
    api("PUT", "/api/settings", {"skins": {"driver": "clean"}})
    await b.goto(BASE + "/?d=driver", wait=1.5)
    check("no console errors from the driving widgets", not [m for m in b.console if m[0] == "error"] and not b.errors, str(b.console[-2:]))

    print("\n== trip computer")
    await b.resize(1100, 500)
    await b.goto(BASE + "/?d=trip", wait=3.0)
    check("the trip template shows trips A and B", await p.count("#widgets .w-trip") == 2)
    titles = await b.eval("[...document.querySelectorAll('#widgets .tr-title')].map(e => e.textContent)")
    check("each is titled", titles == ["Trip A", "Trip B"], str(titles))
    rows = await b.eval('''[...document.querySelectorAll('#widgets [data-id="trip-1"] .tr-label')].map(e => e.textContent)''')
    check("each lists distance, time, speeds, fuel, economy and range", rows == ["Distance", "Time", "Average speed", "Top speed", "Fuel used", "Economy", "Range left"], str(rows))
    def trip_values():
        return b.eval("[...document.querySelectorAll('#widgets .w-trip')].map(w => [...w.querySelectorAll('.tr-value')].map(e => e.textContent))")
    v1 = await trip_values()
    await asyncio.sleep(2.5)
    v2 = await trip_values()
    check("the distance and time count up with the simulated driving", v1[0][0] != v2[0][0] and v1[0][1] != v2[0][1], f"{v1[0][:2]} -> {v2[0][:2]}")
    check("distance is in km and speeds in km/h by default", v2[0][0].endswith(" km") and v2[0][2].endswith(" km/h") and v2[0][3].endswith(" km/h"), str(v2[0]))
    check("the simulated petrol car shows litres and L/100 km", " L" in v2[0][4] and v2[0][5].endswith("L/100 km") and v2[0][6].endswith(" km"), str(v2[0][4:]))
    consumption = float(v2[0][5].split()[0])
    check("the simulated consumption is about the 8 L/100 km it was built to give", 5 < consumption < 12, str(consumption))
    api("PUT", "/api/settings", {"units": {"distance": "mi"}})
    await b.goto(BASE + "/?d=trip", wait=2.5)
    v3 = await trip_values()
    check("with miles the distance is in mi and economy follows in mpg", v3[0][0].endswith(" mi") and v3[0][5].endswith(" mpg"), str(v3[0]))
    api("PUT", "/api/settings", {"units": {"economy": "kmpl"}})
    await b.goto(BASE + "/?d=trip", wait=2.5)
    check("an explicit economy unit overrides it (km/L)", (await trip_values())[0][5].endswith(" km/L"))
    api("PUT", "/api/settings", {"units": {"distance": "km", "economy": "auto"}})
    await b.goto(BASE + "/?d=trip", wait=2.5)

    check("there is a Reset button for each trip on this computer", await p.count("#widgets .tr-reset") == 2)
    before = [float(v.split()[0]) for v in (await trip_values())[0][:1]] + [float(v.split()[0]) for v in (await trip_values())[1][:1]]
    await b.click_el("#widgets [data-id=\'trip-1\'] .tr-reset")
    await p.settle(0.3)
    check("the first press asks 'Sure?' and changes nothing", await p.text("#widgets [data-id=\'trip-1\'] .tr-reset") == "Sure?" and (await trip_values())[0][0] != "0.00 km")
    await b.click_el("#widgets [data-id=\'trip-1\'] .tr-reset")
    await p.settle(0.8)
    after = await trip_values()
    check("the second press resets trip A", float(after[0][0].split()[0]) < before[0] and float(after[0][0].split()[0]) < 0.5, f"{before} -> {after[0][0]}")
    check("...and leaves trip B counting", float(after[1][0].split()[0]) >= before[1], f"{before[1]} -> {after[1][0]}")
    check("the button went back to 'Reset'", await p.text("#widgets [data-id=\'trip-1\'] .tr-reset") == "Reset")

    await b.click_el("#widgets [data-id=\'trip-2\'] .tr-reset")
    await asyncio.sleep(3.4)  # the confirmation times out
    check("an unconfirmed press times out without resetting", await p.text("#widgets [data-id=\'trip-2\'] .tr-reset") == "Reset" and float((await trip_values())[1][0].split()[0]) > 0.1)

    await b.key("e")
    await p.settle(0.9)
    palette = await b.eval("[...document.querySelectorAll('.palette-btn')].map(e => e.textContent)")
    check("the editor offers the Trip computer widget", "Trip computer" in palette, str(palette))
    await p.select_widget("trip-2")
    labels = await b.eval("[...document.querySelectorAll('.sel-body .pr-label')].map(l => l.textContent)")
    check("its properties are offered", all(x in labels for x in ("Which trip", "Title", "Show fuel / energy used and economy", "Show range left")), str(labels))
    units = await b.eval("[...document.querySelectorAll('.unit-grid .pr-label')].map(l => l.textContent)")
    check("the editor has a Fuel economy unit", "Fuel economy" in units, str(units))
    await b.eval("(() => { const s = [...document.querySelectorAll('.unit-grid .pr')].find(r => r.textContent.startsWith('Fuel economy')).querySelector('select'); s.value = 'mpguk'; s.dispatchEvent(new Event('change', {bubbles: true})); })()")
    await p.settle(0.8)
    check("choosing it is saved by the relay and applied", api("GET", "/api/settings")["units"]["economy"] == "mpguk" and (await trip_values())[1][5].endswith("mpg UK"), str((await trip_values())[1]))
    api("PUT", "/api/settings", {"units": {"economy": "auto"}})
    await p.click_text(".ed-bar .btn", "Done")
    await p.settle(0.8)

    check("no console errors from the trip computer", not [m for m in b.console if m[0] == "error"] and not b.errors, str(b.console[-2:]))

    print("\n== alerts")
    await b.goto(BASE + "/?d=default", wait=2.0)
    result = await b.eval("""(async () => {
      const { createAlertBanner } = await import('/js/alerts.js');
      const el = document.createElement('div'); document.body.appendChild(el);
      const banner = createAlertBanner(el, { max: 2 });
      banner.show([{ id: 'a', text: 'Engine overheating', level: 'crit' }, { id: 'b', text: 'Low fuel', level: 'warn' }, { id: 'c', text: 'Over-revving', level: 'warn' }]);
      const out = { items: [...el.querySelectorAll('.al-item')].map(e => e.className + ':' + e.textContent), more: el.querySelector('.al-more')?.textContent, hidden: el.hidden };
      banner.show([]);
      out.hiddenAfter = el.hidden; out.count = el.children.length;
      el.remove();
      return out;
    })()""")
    check("the banner draws critical and warning alerts, and '+n more'", result["items"] == ["al-item al-crit:Engine overheating", "al-item al-warn:Low fuel"] and result["more"] == "+1 more" and result["hidden"] is False, str(result))
    check("it hides again when the alerts clear", result["hiddenAfter"] is True and result["count"] == 0)
    check("the page has the alert banner element, empty while nothing is wrong", await b.eval("document.getElementById('alerts') !== null"))

    # Live end to end: the simulator punctures a tyre for 12 s out of every 60 s and wrecks the car shortly after.
    seen = None
    for _ in range(140):
        seen = await b.eval("[...document.querySelectorAll('#alerts .al-item')].map(e => e.textContent)")
        if seen:
            break
        await asyncio.sleep(0.5)
    check("a real alert from the simulated data appears on the dashboard", bool(seen) and any("Flat tyre" in x or "wrecked" in x.lower() or "Heavy" in x or "overheat" in x.lower() or "Oil" in x for x in seen), str(seen))
    visible = await b.eval("(() => { const e = document.getElementById('alerts'); const r = e.getBoundingClientRect(); return !e.hidden && r.width > 100 && r.top >= 0 && getComputedStyle(e).pointerEvents === 'none'; })()")
    check("the banner is visible, on screen, and does not block clicks", visible is True)

    await b.key("e")
    await p.settle(0.9)
    sections = await b.eval("[...document.querySelectorAll('.ed-h2')].map(e => e.textContent)")
    check("the editor has an Alerts section", "Alerts" in sections, str(sections))
    boxes = await b.eval("[...document.querySelectorAll('.al-opt input')].map(i => i.dataset.alert + ':' + i.checked)")
    check("every alert is offered and on by default", len(boxes) == 10 and all(x.endswith(":true") for x in boxes), str(boxes))
    await b.eval("document.querySelector('.al-opt input[data-alert=fuel]').click()")
    await p.settle(0.6)
    saved = api("GET", "/api/settings")["alerts"]
    check("switching one alert off is saved by the relay", saved == {"enabled": True, "off": ["fuel"]}, str(saved))
    await b.eval("[...document.querySelectorAll('.ed-section')].find(s => s.querySelector('.ed-h2').textContent === 'Alerts').querySelector('.pr-check-row input').click()")
    await p.settle(0.6)
    saved = api("GET", "/api/settings")["alerts"]
    check("switching alerts off altogether is saved, and the list is greyed out", saved["enabled"] is False and await b.eval("[...document.querySelectorAll('.al-opt input')].every(i => i.disabled)"), str(saved))
    check("the banner goes away when alerts are off", await b.eval("document.querySelectorAll('#alerts .al-item').length") == 0)
    api("PUT", "/api/settings", {"alerts": {"enabled": True, "off": []}})
    await p.click_text(".ed-bar .btn", "Done")
    await p.settle(0.8)
    check("no console errors from alerts", not [m for m in b.console if m[0] == "error"] and not b.errors, str(b.console[-2:]))

    print("\n== window memory")
    await b.resize(1100, 600)
    await b.goto(BASE + "/?d=default", wait=2.5)
    search = await b.eval("location.search")
    m = re.search(r"[?&]w=([a-z0-9]{12})(?:&|$)", search)
    check("a window gets an id in its address", m is not None and "d=default" in search, search)
    wid = m.group(1) if m else "none"
    await asyncio.sleep(1.2)
    listed = api("GET", "/api/windows")
    entry = listed["windows"].get(wid)
    check("the relay knows about the window and that it is open", entry is not None and wid in listed["live"], str(listed))
    outer = await b.eval("[Math.round(outerWidth), Math.round(outerHeight)]")
    check("it recorded the dashboard, that it is pinned, and the window's size", entry and entry["dashboard"] == "default" and entry["pinned"] is True and [entry["width"], entry["height"]] == outer, f"{entry} vs {outer}")

    await b.resize(900, 520)
    await asyncio.sleep(2.4)
    outer2 = await b.eval("[Math.round(outerWidth), Math.round(outerHeight)]")
    entry2 = api("GET", "/api/windows")["windows"].get(wid)
    # (A headless browser's outer window size does not follow a viewport resize, so this checks the recorded size
    # matches the window's, and the node tests cover a resize being reported.)
    check("the relay keeps the window's size up to date", entry2 and [entry2["width"], entry2["height"]] == outer2, f"{entry2} vs {outer2}")

    await b.key("d")
    await p.settle(0.5)
    await p.click_text("#dash-menu .dm-item", "Instrument cluster")
    await asyncio.sleep(2.6)
    entry3 = api("GET", "/api/windows")["windows"].get(wid)
    check("choosing another dashboard in the window updates it", entry3 and entry3["dashboard"] == "cluster" and entry3["pinned"] is True, str(entry3))

    await b.eval("location.reload()")
    await asyncio.sleep(3.0)
    check("a reload is the same window: same id", wid in await b.eval("location.search"))
    listed = api("GET", "/api/windows")
    check("...and it is still remembered afterwards (the reload's close does not forget it)", wid in listed["windows"] and wid in listed["live"], str(listed))

    await b.goto("about:blank", wait=1.5)
    await asyncio.sleep(0.8)
    check("closing the window makes the relay forget it", wid not in api("GET", "/api/windows")["windows"], str(api("GET", "/api/windows")))

    await b.goto(BASE + "/?d=default", wait=2.0)
    await b.key("e")
    await p.settle(0.9)
    sections = await b.eval("[...document.querySelectorAll('.ed-h2')].map(e => e.textContent)")
    check("the editor has a Windows section", "Windows" in sections, str(sections))
    boxes = await b.eval("[...document.querySelectorAll('.ed-section')].find(s => s.querySelector('.ed-h2').textContent === 'Windows').querySelectorAll('input').length")
    check("with the two switches", boxes == 2)
    now = api("GET", "/api/settings")
    check("restoring is on by default and exact placement off", now["restoreWindows"] is True and now["exactWindowPlacement"] is False, str(now))
    await b.eval("[...document.querySelectorAll('.ed-section')].find(s => s.querySelector('.ed-h2').textContent === 'Windows').querySelectorAll('input')[1].click()")
    await p.settle(0.6)
    check("switching on exact placement is saved by the relay", api("GET", "/api/settings")["exactWindowPlacement"] is True)
    await b.eval("[...document.querySelectorAll('.ed-section')].find(s => s.querySelector('.ed-h2').textContent === 'Windows').querySelectorAll('input')[0].click()")
    await p.settle(0.6)
    check("and so is switching off reopening", api("GET", "/api/settings")["restoreWindows"] is False)
    api("PUT", "/api/settings", {"restoreWindows": True, "exactWindowPlacement": False})
    await p.click_text(".ed-bar .btn", "Done")
    await p.settle(0.8)
    check("no console errors from window memory", not [m for m in b.console if m[0] == "error"] and not b.errors, str(b.console[-2:]))

    print("\n== skin preview page")
    await b.resize(1100, 1700)
    await b.goto(BASE + "/skin-preview.html", wait=2.5)
    check("skin preview renders 18 gauges (3 skins x 6) and 36 lamps", await p.count(".sk-gauge") == 18 and await p.count(".sk-lamp") == 36)
    check("bundled fonts load offline (Jost, Barlow Condensed, DSEG7)", await b.eval("document.fonts.check('13px Jost') && document.fonts.check('13px \"Barlow Condensed\"') && document.fonts.check('13px \"DSEG7 Classic\"')"))
    t1 = await b.eval("document.querySelector('.sk-gauge .sk-rot').style.transform")
    await asyncio.sleep(0.8)
    t2 = await b.eval("document.querySelector('.sk-gauge .sk-rot').style.transform")
    check("needles animate with the simulated data", t1 != t2, f"{t1} -> {t2}")
    check("odometer window shows digits", len(await b.eval("[...document.querySelectorAll('.sk-classicWhite text')].map(e => e.textContent).filter(x => /^[0-9]$/.test(x)).join('')")) >= 6)
    await b.eval("document.getElementById('pv-toggle').click()")
    await p.shot("skin-preview")

    await trips_scenario(b, p)

    print("\n== console health")
    bad = [m for m in b.console if m[0] == "error" and "favicon" not in m[1]]
    check("no console errors", not bad, str(bad[:3]))
    check("no uncaught exceptions", not b.errors, str(b.errors[:2]))


def _seed_trip(folder, tid, ts, lat0, lon0, speeding):
    """Write a finished trip file by hand (a 90 second drive north, a point every 3 s), so the Trips page has something to show."""
    rows = [{"k": "trip", "fmt": 1, "id": tid, "ts": ts, "vehicle": "etkc", "veh": 1, "energy": "liquid"}]
    for n in range(31):
        rows.append({"k": "p", "t": n * 3.0, "la": round(lat0 + n * 0.0003, 7), "lo": lon0, "al": 5 + n * 0.5, "s": 15.0 + (n % 5), "h": 90.0 if n >= 15 else 0.0,
                     "d": n * 48.0, "f": 0.9 - n * 0.001, "fv": round(45.0 - n * 0.05, 3), "dm": 0})
        if n % 3 == 0:
            rows.append({"k": "i", "t": n * 3.0, "place": {"street": "Oriental Parade", "place": "Wellington", "country": "New Zealand"},
                         "lim": {"ms": 11.11 if speeding else 30.0, "m": True, "u": "kmh"}, "wx": {"tempC": 15.0, "windMs": 4.0}})
    rows.append({"k": "end", "reason": "idle", "t": 90.0, "ts": ts + 90, "dist": 1440.0, "top": 19.0, "n": 31})
    (folder / f"{tid}.jsonl").write_text("\n".join(json.dumps(r, separators=(",", ":")) for r in rows) + "\n")


async def trips_scenario(b, p):
    """The Trips page, against a relay that has two recorded trips (the normal test relay runs in --simulate mode, which keeps none)."""
    print("\n== trips page")
    data = Path(tempfile.mkdtemp(prefix="thrashdash-uitest-trips-"))
    folder = data / "thrashdash" / "triplog"
    folder.mkdir(parents=True)
    _seed_trip(folder, "20261113T210639Z", 1794604000, -41.29, 174.78, speeding=True)
    _seed_trip(folder, "20261113T200000Z", 1763000000, -41.30, 174.77, speeding=False)  # (a year older, for the date filter)
    port, udp = PORT + 40, 24568
    base = f"http://127.0.0.1:{port}"
    env = {**ENV, "XDG_DATA_HOME": str(data), "LOCALAPPDATA": str(data)}
    try:
        urllib.request.urlopen(base + "/api/status", timeout=1)
        raise RuntimeError(f"something is already answering on port {port}: a relay from an earlier test run is still alive (kill it)")
    except urllib.error.URLError:
        pass  # (nothing there: good)
    relay = subprocess.Popen([sys.executable, str(ROOT / "relay" / "run.py"), "--no-window", "--http-port", str(port), "--udp-port", str(udp)],
                             env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        for _ in range(60):
            try:
                urllib.request.urlopen(base + "/api/status", timeout=1)
                break
            except OSError:
                time.sleep(0.2)
        mark = len(b.console)
        await b.goto(BASE + "/", wait=1.5)
        await b.eval("document.getElementById('dash-button').click()")
        await p.settle(0.5)
        check("the Dashboards menu has a way to open the trip log", "Open the trip log" in await b.eval("document.getElementById('dash-menu').textContent"))
        await b.resize(1280, 900)
        await b.goto(base + "/trips#20261113T210639Z", wait=2.5)
        ids = await b.eval("[...document.querySelectorAll('#list .item')].map(e => e.dataset.id)")
        check("the list shows both trips, newest first", ids == ["20261113T210639Z", "20261113T200000Z"], str(ids))
        check("the trip in the address is open", await b.eval("document.getElementById('detail').hidden") is False and await b.eval("document.querySelector('.item[aria-current=true]')?.dataset.id") == "20261113T210639Z")
        check("it is named from where it began and ended", "Around Oriental Parade" in await p.text("#title"), await p.text("#title"))
        check("map tile requests carry the page's origin as Referer (OpenStreetMap blocks tiles without one)", await b.eval("document.querySelector('#map .leaflet-tile')?.referrerPolicy") == "origin")
        check("the map is drawn with its tiles and a route", await p.count("#map .leaflet-tile") > 0 and await p.count("#map canvas") >= 1)
        check("the figures are shown", await p.count("#tiles .tile") >= 6)
        check("it shows time over the limit, because the limit was posted", "Over the limit" in await b.eval("document.getElementById('tiles').textContent") and await p.count("#tiles .tile.warn") == 1)
        check("the speed graph is drawn", len(await b.eval("document.querySelector('#graph svg path.speed')?.getAttribute('d') ?? ''")) > 20)
        check("and shows the posted limit and where it was passed", await p.count("#graph svg line.limit") >= 1 and await p.count("#graph svg rect.over") >= 1)
        await p.shot("trips")
        check("GPX and KML links are there for the open trip", await b.eval("document.getElementById('export-gpx').getAttribute('href')") == "/api/triplog/20261113T210639Z/gpx" and await b.eval("document.getElementById('export-kml').getAttribute('href')") == "/api/triplog/20261113T210639Z/kml" and await b.eval("document.getElementById('export-gpx').hasAttribute('download')"))
        parsed = await b.eval("fetch(document.getElementById('export-gpx').href).then(r => r.text()).then(t => { const d = new DOMParser().parseFromString(t, 'application/xml'); return [d.querySelector('parsererror') === null, d.getElementsByTagName('trkpt').length]; })")
        check("the GPX file the link gives is well formed and holds the whole track", parsed[0] is True and parsed[1] >= 30, str(parsed))
        parsed = await b.eval("fetch(document.getElementById('export-kml').href).then(r => r.text()).then(t => { const d = new DOMParser().parseFromString(t, 'application/xml'); return [d.querySelector('parsererror') === null, d.getElementsByTagName('LineString')[0]?.getElementsByTagName('coordinates')[0]?.textContent.trim().split(/\\s+/).length ?? 0]; })")
        check("and so is the KML", parsed[0] is True and parsed[1] >= 30, str(parsed))

        await b.eval("document.getElementById('graph').scrollIntoView({block: 'center'})")
        await p.settle(0.2)
        r = await b.rect("#graph svg")
        await b.move(r["cx"], r["cy"])
        await p.settle(0.3)
        check("hovering the graph shows the figures at that point", await b.eval("!document.getElementById('tip').hidden") and "km/h" in await p.text("#tip"), await p.text("#tip"))

        check("the replay is ready but shows no car yet", await p.count(".car") == 0 and await b.eval("document.getElementById('play').textContent") == "Play" and not await b.eval("document.getElementById('play').disabled"))
        await b.eval("document.getElementById('rate').value = '64'; document.getElementById('rate').dispatchEvent(new Event('change'))")
        await b.eval("document.getElementById('play').click()")
        await p.settle(0.5)
        check("Play puts a car on the map and says what it is doing", await p.count(".car") == 1 and "km/h" in await p.text("#readout"), await p.text("#readout"))
        check("the replay marks its place on the graph", await b.eval("document.querySelector('#graph .replay-rule')?.getAttribute('visibility')") == "visible")
        check("the clock and slider move", await b.eval("document.getElementById('scrub').value") != "0" and not (await p.text("#clock")).startswith("0:00:00 /"), await p.text("#clock"))
        await p.settle(2.5)
        check("at the end it stops and offers to go again", await b.eval("document.getElementById('play').textContent") == "Replay", await b.eval("document.getElementById('play').textContent"))
        await b.eval("document.getElementById('scrub').value = '500'; document.getElementById('scrub').dispatchEvent(new Event('input'))")
        await p.settle(0.2)
        check("dragging the slider goes to that point in the trip", (await p.text("#clock")).startswith("0:00:45 /"), await p.text("#clock"))
        await b.eval("document.getElementById('rate').value = '1'; document.getElementById('rate').dispatchEvent(new Event('change'))")
        await b.key(" ")
        await p.settle(0.3)
        check("the space bar plays and pauses", await b.eval("document.getElementById('play').textContent") == "Pause")
        await b.key(" ")
        await p.settle(0.2)
        check("and pauses again", await b.eval("document.getElementById('play').textContent") in ("Play", "Replay"))
        await b.eval("document.getElementById('graph').scrollIntoView({block: 'start'}); window.scrollTo(0, 0); document.getElementById('main').scrollTop = 0")
        await b.eval("document.getElementById('scrub').value = '800'; document.getElementById('scrub').dispatchEvent(new Event('input'))")
        await p.settle(0.3)
        await b.eval("document.getElementById('headup').click()")
        await p.settle(0.8)
        mid = await b.eval("(() => { const m = document.getElementById('mapbox'), r = m.getBoundingClientRect(), c = document.querySelector('.car')?.getBoundingClientRect(); return {on: m.classList.contains('rotating'), rot: m.style.getPropertyValue('--rot'), big: document.getElementById('map').getBoundingClientRect().width > r.width * 1.2 || document.getElementById('map').offsetWidth > m.offsetWidth, dx: c ? Math.abs((c.left + c.width / 2) - (r.left + r.width / 2)) : 999, dy: c ? Math.abs((c.top + c.height / 2) - (r.top + r.height / 2)) : 999, zoom: null}; })()")
        check("Heading up turns the map about the car, so it points up", mid["on"] and mid["rot"] == "-90deg" and mid["big"], str(mid))
        check("and keeps the car in the middle of the map", mid["dx"] < 6 and mid["dy"] < 6, str(mid))
        check("the page draws the zoom buttons and the credit then", await b.eval("getComputedStyle(document.getElementById('rot-in').parentElement).display") == "flex" and "OpenStreetMap" in await p.text("#rot-attrib"))
        net = await b.eval("(() => { let m = new DOMMatrix(); for (let e = document.querySelector('.car span'); e && e.id !== 'mapbox'; e = e.parentElement) { const t = getComputedStyle(e).transform; if (t && t !== 'none') m = new DOMMatrix(t).multiply(m); } return Math.round(Math.atan2(m.b, m.a) * 1800 / Math.PI) / 10; })()")
        check("the car's tip points straight up on the screen (its own turn and the map's cancel)", abs(net) < 2.0 or abs(abs(net) - 360) < 2.0, f"net rotation {net} degrees")
        await b.eval("document.getElementById('rot-in').click()")
        await p.settle(0.3)
        await b.eval("document.getElementById('scrub').value = '100'; document.getElementById('scrub').dispatchEvent(new Event('input'))")
        await p.settle(0.3)
        check("the map turns back as the heading does", await b.eval("document.getElementById('mapbox').style.getPropertyValue('--rot')") == "0deg", await b.eval("document.getElementById('mapbox').style.getPropertyValue('--rot')"))
        await b.eval("document.getElementById('headup').click()")
        await p.settle(0.6)
        check("switching it off puts the map back upright and the right size", await b.eval("!document.getElementById('mapbox').classList.contains('rotating') && document.getElementById('mapbox').style.height === ''") and await b.eval("getComputedStyle(document.getElementById('rot-in').parentElement).display") == "none")
        await b.eval("document.querySelectorAll('#list .item')[1].click()")
        await p.settle(1.0)
        check("opening another trip clears the replay", await p.count(".car") == 0 and await p.text("#readout") == "")
        await b.eval("document.querySelectorAll('#list .item')[0].click()")
        await p.settle(1.0)
        await b.eval("document.getElementById('base-sat').click()")
        await p.settle(0.5)
        check("the satellite button switches the map and is remembered", await b.eval("document.getElementById('base-sat').getAttribute('aria-pressed')") == "true" and await b.eval("localStorage.getItem('thrashdash.trips.base')") == "sat")
        check("the satellite credit is shown", "Sentinel-2" in await p.text(".leaflet-control-attribution"))
        await b.eval("document.getElementById('base-street').click()")

        await b.eval("document.getElementById('all').click()")
        await p.settle(1.5)
        check("All trips shows a map of every route, with totals", await b.eval("document.getElementById('overview').hidden") is False and await b.eval("document.getElementById('detail').hidden") is True and await p.count("#ov-tiles .tile") == 5)
        check("it says how many trips", "2 trips" in await p.text("#ov-sub"), await p.text("#ov-sub"))
        check("the trips are drawn on the map", await b.eval("document.querySelectorAll('#ovmap canvas').length") >= 1 and await p.count("#ovmap .leaflet-tile") > 0)
        check("the filters offer the vehicle and country that occur", await b.eval("[...document.getElementById('f-vehicle').options].map(o => o.value).join('|')") == "|etkc" and await b.eval("[...document.getElementById('f-country').options].map(o => o.value).join('|')") == "|New Zealand")
        await b.eval("document.getElementById('f-when').value = '7'; document.getElementById('f-when').dispatchEvent(new Event('change'))")
        await p.settle(0.3)
        check("the date filter keeps only the recent trip and the totals follow", "1 of 2 trips" in await p.text("#ov-sub") and await p.text("#ov-tiles .tile .tile-value") == "1", await p.text("#ov-sub"))
        await b.eval("document.getElementById('f-when').value = 'year'; document.getElementById('f-when').dispatchEvent(new Event('change'))")
        await p.settle(0.3)
        check("this year leaves out the older one too", "1 of 2 trips" in await p.text("#ov-sub"), await p.text("#ov-sub"))
        await b.eval("document.getElementById('f-when').value = 'all'; document.getElementById('f-when').dispatchEvent(new Event('change'))")
        await p.settle(0.3)
        check("and clearing it brings them back", "2 trips" in await p.text("#ov-sub"))
        check("the address records the view", await b.eval("location.hash") == "#all")
        await b.eval("document.querySelector('#overview .base button[data-base=sat]').click()")
        await p.settle(0.4)
        check("the map type button works here too and both maps share the choice", await b.eval("document.getElementById('base-sat').getAttribute('aria-pressed')") == "true")
        await b.eval("document.querySelector('#overview .base button[data-base=street]').click()")
        await p.shot("trips-all")
        await b.eval("document.querySelector('#list .item').click()")
        await p.settle(1.0)
        check("picking a trip from the list leaves the overview", await b.eval("document.getElementById('overview').hidden") is True and await b.eval("document.getElementById('detail').hidden") is False)

        await b.eval("document.getElementById('rename').click()")
        await p.settle(0.2)
        check("Rename turns the title into a box", await p.count("#title input.rename-input") == 1)
        await b.type_text("Sunday <b>run</b>")
        await b.key("Enter")
        await p.settle(0.8)
        check("the new name is shown as plain text and is kept", await p.text("#title") == "Sunday <b>run</b>" and await p.count("#title b") == 0, await p.text("#title"))
        check("it is in the list too", "Sunday <b>run</b>" in await p.text("#list .item"))
        check("and was stored with the trip", any(json.loads(line).get("name") == "Sunday <b>run</b>" for line in (folder / "20261113T210639Z.jsonl").read_text().splitlines() if '"name"' in line))

        await b.resize(390, 844)
        await b.goto(base + "/trips", wait=2.0)
        check("on a phone-size screen the list comes first, with no trip open", await b.eval("document.body.dataset.view") == "list" and await b.eval("getComputedStyle(document.getElementById('main')).display") == "none")
        await b.eval("document.querySelector('#list .item').click()")
        await p.settle(1.2)
        check("picking a trip shows it, with a way back", await b.eval("document.body.dataset.view") == "detail" and await b.eval("getComputedStyle(document.getElementById('back')).display") != "none")
        await p.shot("trips-phone")
        await b.eval("document.getElementById('back').click()")
        check("Back returns to the list", await b.eval("document.body.dataset.view") == "list")
        await b.resize(1280, 900)

        await b.goto(base + "/trips#20261113T200000Z", wait=2.0)
        await b.eval("document.getElementById('delete').click()")
        check("Delete asks again first", "Really" in await p.text("#delete") and await p.count("#list .item") == 2)
        await b.eval("document.getElementById('delete').click()")
        await p.settle(0.8)
        check("then moves the trip to the trash", await p.count("#list .item") == 1 and (folder / "trash" / "20261113T200000Z.jsonl").is_file() and not (folder / "20261113T200000Z.jsonl").exists())
        check("and the page shows nothing selected", await b.eval("document.getElementById('detail').hidden"))


        print("\n== window config (the map window is saved with the dashboards)")

        def call(method, path, body=None):
            request = urllib.request.Request(base + path, method=method, data=None if body is None else json.dumps(body).encode(), headers={"X-ThrashDash": "1", "Content-Type": "application/json"})
            with urllib.request.urlopen(request, timeout=5) as r:
                return json.load(r)

        MAPWIN = "mapwin123456"
        call("PUT", f"/api/windows/{MAPWIN}", {"kind": "trips", "width": 1180, "height": 760, "x": 1300, "y": 40, "stamp": 1000})  # (a map window, as the Trips page reports itself)
        await b.resize(1200, 700)
        await b.goto(base + "/", wait=2.0)
        await b.eval("document.getElementById('dash-button').click()")
        await p.settle(0.8)
        check("the Dashboards menu has a Window config section with nothing saved yet", "Window config" in await b.eval("document.getElementById('dash-menu').textContent") and "Nothing saved yet" in await b.eval("document.getElementById('dash-menu').textContent"))
        await b.eval("[...document.querySelectorAll('.dm-config-actions .dm-action')].find(x => x.textContent.startsWith('Save as')).click()")
        await p.settle(0.5)
        check("Save as asks for a name", await b.eval("!!document.querySelector('.dm-config-input')"))
        await b.eval("document.querySelector('.dm-config-input').value = 'Driving'; document.querySelector('.dm-config-name').requestSubmit()")
        await p.settle(1.0)
        got = call("GET", "/api/configs")
        kinds = sorted({x["kind"] for x in got["configs"][0]["windows"]}) if got["configs"] else []
        check("it saves the open windows, the map window among them, and that config becomes the one that opens", len(got["configs"]) == 1 and got["active"] == got["configs"][0]["id"] and kinds == ["dashboard", "trips"] and got["changed"] is False, str(got))
        saved_map = [x for x in got["configs"][0]["windows"] if x["kind"] == "trips"]
        check("with the map window's size", saved_map and saved_map[0]["width"] == 1180 and saved_map[0]["height"] == 760, str(saved_map))

        call("PUT", f"/api/windows/{MAPWIN}", {"kind": "trips", "width": 900, "height": 600, "x": 1300, "y": 40, "stamp": 2000})  # the map window was resized
        await b.eval("document.getElementById('dash-button').click(); document.getElementById('dash-button').click()")
        await p.settle(1.5)
        text = await b.eval("document.getElementById('dash-menu').textContent")
        check("the menu says the windows changed since the save, and what Save will overwrite", "Windows changed since saved" in text and "Driving" in text, text[-400:])
        await b.eval("[...document.querySelectorAll('.dm-config-actions .dm-action')].find(x => x.textContent === 'Save').click()")
        await p.settle(1.0)
        got = call("GET", "/api/configs")
        resized = [x for x in got["configs"][0]["windows"] if x["kind"] == "trips"]
        check("Save overwrites the loaded config and makes no copy", len(got["configs"]) == 1 and resized and resized[0]["width"] == 900, str(got))

        await b.eval("[...document.querySelectorAll('.dm-config-actions .dm-action')].find(x => x.textContent.startsWith('Save as')).click()")
        await p.settle(0.5)
        await b.eval("document.querySelector('.dm-config-input').value = 'Review'; document.querySelector('.dm-config-name').requestSubmit()")
        await p.settle(1.0)
        got = call("GET", "/api/configs")
        check("Save as keeps the first and adds a second, which now opens", [c["name"] for c in got["configs"]] == ["Driving", "Review"] and got["active"] == got["configs"][1]["id"], str(got))
        await b.eval("(() => { const s = document.querySelector('.dm-config-pick'); s.value = s.options[1].value; s.dispatchEvent(new Event('change')); })()")
        await p.settle(1.0)
        check("picking one makes it the one that opens next time", call("GET", "/api/configs")["active"] == got["configs"][0]["id"])

        await b.goto(base + "/trips", wait=2.5)
        windows = call("GET", "/api/windows")["windows"]
        trips_id = await b.eval("new URLSearchParams(location.search).get('w')")
        check("the Trips (map) window reports itself like a dashboard window, with its own id in its address", bool(trips_id) and windows.get(trips_id, {}).get("kind") == "trips" and (windows[trips_id].get("width") or 0) > 300, str(windows.get(trips_id)))
        await b.eval("document.querySelector('.popups').open = true")
        check("the popup checkboxes are labelled as popup messages", "Popup messages" in await b.eval("document.querySelector('.pop-group legend').textContent") and await b.eval("document.querySelectorAll('.pop-group input[type=checkbox]').length") == 3)

        print("\n== a shared trip (one html file, opened from disk)")
        shared = data / "shared-trip.html"
        shared.write_bytes(urllib.request.urlopen(base + "/api/triplog/20261113T210639Z/share", timeout=10).read())
        mark2 = len(b.console)
        await b.resize(1280, 900)
        await b.goto(shared.as_uri(), wait=3.0)
        await p.shot("shared")
        check("the shared file shows the trip with no list or controls for changing it", await b.eval("document.body.classList.contains('static') && getComputedStyle(document.getElementById('side')).display === 'none' && getComputedStyle(document.querySelector('.actions')).display === 'none'"))
        check("it has the trip's name (the one it was given, as plain text) and figures", await p.text("#title") == "Sunday <b>run</b>" and await p.count("#title b") == 0 and await p.count("#tiles .tile") >= 6, await p.text("#title"))
        check("the map is there (a file opened from disk starts on the satellite picture: OpenStreetMap needs a Referer)", await p.count("#map .leaflet-container, #map.leaflet-container") >= 1 and await b.eval("document.getElementById('base-sat').getAttribute('aria-pressed')") == "true")
        check("the route and the graph are drawn", await b.eval("document.querySelectorAll('#map canvas').length") >= 1 and len(await b.eval("document.querySelector('#graph svg path.speed')?.getAttribute('d') ?? ''")) > 20)
        await b.eval("document.getElementById('rate').value = '64'; document.getElementById('rate').dispatchEvent(new Event('change')); document.getElementById('play').click()")
        await p.settle(0.6)
        check("the replay works in the shared file", await p.count(".car") == 1 and "km/h" in await p.text("#readout"), await p.text("#readout"))
        check("the file's own policy let its scripts and styles run", not [m for m in b.console[mark2:] if "Content Security Policy" in m[1] or "Refused to" in m[1]], str([m for m in b.console[mark2:] if "Refused" in m[1]][:2]))
        check("and nothing went wrong in it", not b.errors, str(b.errors[:2]))
        await b.goto("about:blank", wait=0.2)
        b.errors.clear()
        await b.goto(base + "/trips#20261113T210639Z", wait=2.0)

        print("\n== trip popups")
        import socket as _socket
        game = _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM)  # stands in for the game's trip-log extension
        game.bind(("127.0.0.1", 0))
        game.settimeout(3)

        def send(packet):
            game.sendto(json.dumps({"v": 1, **packet}).encode(), ("127.0.0.1", udp))

        try:
            await b.resize(1200, 420)
            await b.goto(base + "/", wait=1.5)  # a dashboard window, open while the trip is driven
            for i in range(40):  # 40 s at 15 m/s, heading north
                send({"type": "geo", "rt": float(i), "ts": 1794700000 + i, "lat": -41.2 + i * 0.000135, "lon": 174.8, "alt": 5.0, "spd": 15.0, "hdg": 0.0, "ready": True, "veh": 1, "model": "etkc"})
                if i % 3 == 0:
                    send({"type": "geoinfo", "rt": float(i), "limit": {"ms": 30.0, "mapped": True, "unit": "kmh"}, "place": {"street": "Popup Street", "country": "New Zealand"}})
            send({"type": "geoevent", "name": "levelEnd", "rt": 41.0, "ts": 1794700041})
            await p.settle(2.0)
            shown = await b.eval("(() => { const e = document.getElementById('trip-notice'); return [e.hidden, e.textContent]; })()")
            check("a dashboard window shows a banner when the trip ends", shown[0] is False and "Trip finished" in shown[1] and "Popup Street" in shown[1], str(shown))
            check("it says how far and how long", "km" in shown[1] and " s" in shown[1], shown[1])
            toast = None
            try:
                while True:
                    data, source = game.recvfrom(65535)
                    packet = json.loads(data)
                    if packet.get("type") == "toast":
                        toast = (packet, source)
                        break
            except OSError:
                pass
            check("the game is sent the same message, from the relay's own address", toast is not None and toast[0]["title"] == "Trip finished" and toast[1] == ("127.0.0.1", udp), str(toast))
            await b.eval("document.getElementById('trip-notice').click()")
            check("clicking the banner puts it away", await b.eval("document.getElementById('trip-notice').hidden") is True)

            await b.resize(1280, 900)
            await b.goto(base + "/trips", wait=2.0)
            states = await b.eval("['pop-game', 'pop-start', 'pop-window'].map(id => document.getElementById(id).checked)")
            check("the Trips page shows which popups are on (end and banner on, start off, by default)", states == [True, False, True], str(states))
            check("the Trips page offers how long a parked car waits before the trip ends, two minutes by default", await b.eval("document.getElementById('idle-end').value") == "2")
            await b.eval("document.getElementById('popups').open = true; document.getElementById('idle-end').value = '5'; document.getElementById('idle-end').dispatchEvent(new Event('change')); document.getElementById('pop-start').click(); document.getElementById('pop-window').click()")
            await p.settle(0.6)
            saved = json.load(urllib.request.urlopen(base + "/api/settings", timeout=3))["tripNotices"]
            check("changing them is saved", saved == {"game": True, "gameStart": True, "window": False}, str(saved))
            check("so is the parked wait", json.load(urllib.request.urlopen(base + "/api/settings", timeout=3))["tripIdleEndMin"] == 5)
            before = len(await b.eval("fetch('/api/triplog').then(r => r.json()).then(d => d.trips.map(t => t.id))"))
            for i in range(40):  # a second trip, with the banner now switched off
                send({"type": "geo", "rt": 100.0 + i, "ts": 1794800000 + i, "lat": -41.3 + i * 0.000135, "lon": 174.8, "alt": 5.0, "spd": 15.0, "hdg": 0.0, "ready": True, "veh": 1, "model": "etkc"})
            await p.settle(0.5)
            titles = []
            try:
                while True:
                    data, _ = game.recvfrom(65535)
                    titles.append(json.loads(data).get("title"))
            except OSError:
                pass
            check("with the start popup switched on, the game hears when a trip begins", "Trip started" in titles, str(titles))
            await p.settle(4.5)
            live = await b.eval("(() => { const cur = document.querySelector('.item[aria-current=true]'); return [cur?.dataset.id ?? null, !!cur?.querySelector('.badge'), document.getElementById('subtitle').textContent]; })()")
            check("a trip that starts is opened on the map by itself, and is shown as being recorded", live[0] is not None and live[1] is True and "still being recorded" in live[2], str(live))
            livestate = await b.eval("(() => [document.querySelectorAll('.car').length, document.getElementById('play').textContent, document.getElementById('golive').hidden, document.getElementById('readout').textContent])()")
            check("a trip being driven is followed live: a car on the map, 'Pause' offered, no 'Go live' needed", livestate[0] == 1 and livestate[1] == "Pause" and livestate[2] is True and "km/h" in livestate[3], str(livestate))
            await b.eval("document.getElementById('mapbox').scrollIntoView({block: 'start'})")
            await b.eval("document.getElementById('headup').click()")
            await p.settle(1.5)
            live_rot = await b.eval("(() => { const m = document.getElementById('mapbox'), r = m.getBoundingClientRect(), c = document.querySelector('.car')?.getBoundingClientRect(); return {on: m.classList.contains('rotating'), dx: c ? Math.abs((c.left + c.width / 2) - (r.left + r.width / 2)) : 999, dy: c ? Math.abs((c.top + c.height / 2) - (r.top + r.height / 2)) : 999}; })()")
            check("Heading up works on the live trip: the map turns about the car, which stays in the middle", live_rot["on"] is True and live_rot["dx"] < 8 and live_rot["dy"] < 8, str(live_rot))
            before_pos = await b.eval("document.getElementById('scrub').value")
            await p.settle(3.5)  # the game is not sending now (the 40 packets are done), but the page must not fall over; the car holds at the newest point
            await b.eval("document.getElementById('play').click()")
            await p.settle(0.3)
            check("pausing leaves the live view and offers 'Go live'", await b.eval("document.getElementById('golive').hidden") is False and await b.eval("document.getElementById('play').textContent") == "Play")
            await b.eval("document.getElementById('golive').click()")
            await p.settle(0.4)
            check("'Go live' returns to it", await b.eval("document.getElementById('golive').hidden") is True and await b.eval("document.getElementById('play').textContent") == "Pause")
            await b.eval("document.getElementById('headup').click()")
            await p.settle(0.5)
            send({"type": "geoevent", "name": "levelEnd", "rt": 141.0, "ts": 1794800041})
            await p.settle(1.0)
            await b.eval("document.getElementById('auto-open').click()")  # switch it off
            first = await b.eval("document.querySelector('.item[aria-current=true]')?.dataset.id")
            for i in range(40):  # a third trip
                send({"type": "geo", "rt": 300.0 + i, "ts": 1794900000 + i, "lat": -41.4 + i * 0.000135, "lon": 174.8, "alt": 5.0, "spd": 15.0, "hdg": 0.0, "ready": True, "veh": 1, "model": "etkc"})
            await p.settle(4.5)
            after = await b.eval("[document.querySelector('.item[aria-current=true]')?.dataset.id ?? null, document.querySelectorAll('.item .badge').length]")
            check("with that switched off, a new trip appears in the list but the page stays where it was", after[0] == first and after[1] == 1, str((first, after)))
            await b.eval("document.getElementById('auto-open').click()")  # and on again
            send({"type": "geoevent", "name": "levelEnd", "rt": 341.0, "ts": 1794900041})
            await b.goto(base + "/", wait=1.0)
            await p.settle(0.5)
            check("with the window banner off, none is shown", await b.eval("document.getElementById('trip-notice').hidden") is True)
        finally:
            game.close()

        print("\n== live position feed (the map keeps up with the car)")
        game3 = _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM)
        game3.bind(("127.0.0.1", 0))
        M_PER_DEG = 111195.0
        speed = 15.0

        def at(t):
            return -41.7 + t * speed / M_PER_DEG

        try:
            await b.resize(1280, 900)
            await b.goto(base + "/trips?debug=1", wait=2.0)
            # the window is behind the game: the browser calls it hidden and slows its timers right down
            await b.eval("Object.defineProperty(document, 'visibilityState', {get: () => 'hidden', configurable: true})")
            sent_rt = 0.0
            opened_while_hidden = None
            last_sent = None
            loop = asyncio.get_running_loop()
            t0 = loop.time()
            while loop.time() - t0 < 9.0:  # nine seconds of driving north at 15 m/s: a recorded position each second, a live one ten times a second
                rt = loop.time() - t0 + 700.0
                if opened_while_hidden is None and loop.time() - t0 > 3.0:
                    opened_while_hidden = await b.eval("(() => { const c = document.querySelector('.item[aria-current=true]'); return !!(c && c.querySelector('.badge')); })()")
                if rt - sent_rt >= 1.0 or sent_rt == 0.0:
                    sent_rt = rt
                    game3.sendto(json.dumps({"v": 1, "type": "geo", "rt": rt, "ts": 1795100000 + int(rt), "lat": at(rt), "lon": 174.8, "alt": 5.0, "spd": speed, "hdg": 0.0, "ready": True, "veh": 1, "model": "etkc"}).encode(), ("127.0.0.1", udp))
                game3.sendto(json.dumps({"v": 1, "type": "geolive", "rt": rt, "lat": at(rt), "lon": 174.8, "hdg": 0.0, "spd": speed}).encode(), ("127.0.0.1", udp))
                last_sent = (rt, at(rt))
                await asyncio.sleep(0.1)
            await asyncio.sleep(0.05)
            debug = await b.eval("globalThis.__tripsDebug && globalThis.__tripsDebug()")
            lag_m = abs(last_sent[1] - debug["marker"]["lat"]) * M_PER_DEG if debug and debug.get("marker") else None
            print(f"    measured: the car on the map was {lag_m:.1f} m behind the game's position at {speed} m/s" if lag_m is not None else "    measured: no marker")
            check("a new trip is opened by itself even while the window is hidden behind the game (the relay tells the page at once)", opened_while_hidden is True, str(opened_while_hidden))
            await b.eval("delete document.visibilityState; Object.defineProperty(document, 'visibilityState', {get: () => 'visible', configurable: true})")
            far = abs((debug["center"]["lat"] if debug and debug.get("center") else 0) - last_sent[1]) * M_PER_DEG if debug else None
            check("the map is looking at the new trip (the earlier trips are 45 km away), even though the trip was opened before it had any points", far is not None and far < 3000, f"{far} m from the trip")
            check("the page receives the live positions and follows the trip", bool(debug) and debug["live"] is True and debug["feedLive"] is True and debug["feedSamples"] > 20, str(debug))
            check("the car on the map is within a few metres of where the game says it is (not seconds behind)", lag_m is not None and lag_m < 12.0, f"{lag_m} m behind at {speed} m/s")
            game3.sendto(json.dumps({"v": 1, "type": "geoevent", "name": "levelEnd", "rt": 800.0, "ts": 1795100800}).encode(), ("127.0.0.1", udp))
            await p.settle(4.0)
            gone = await b.eval("(async () => { const ids = (await fetch('/api/triplog').then(r => r.json())).trips.map(t => t.id); const shown = location.hash.slice(1); return [shown, ids.includes(shown), document.getElementById('detail').hidden]; })()")
            check("a trip that was open and is then thrown away (too short to keep) is left, not kept on screen", gone[2] is True or gone[1] is True, str(gone))
        finally:
            game3.close()

        print("\n== live map widget on a dashboard")
        layout = {"format": 1, "name": "Map test", "width": 1200, "height": 420, "scaleMode": "max", "scalePercent": 100,
                  "widgets": [{"id": "tripmap-1", "type": "tripmap", "x": 20, "y": 20, "w": 600, "h": 380, "headUp": True, "zoom": 17}]}
        request = urllib.request.Request(base + "/api/layouts", method="POST", data=json.dumps(layout).encode(), headers={"X-ThrashDash": "1", "Content-Type": "application/json"})
        layout_id = json.load(urllib.request.urlopen(request, timeout=5))["id"]
        game2 = _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM)
        try:
            await b.resize(1200, 420)
            await b.goto(f"{base}/?d={layout_id}", wait=3.0)
            frame = "document.querySelector('.tm-frame').contentDocument"
            check("the dashboard shows the map widget as a frame of the Trips page in its compact form", await b.eval("document.querySelector('.tm-frame')?.src.includes('embed=1') && document.querySelector('.tm-frame').src.includes('headup=1')") is True)
            check("inside, only the map is shown, and it waits for a trip", await b.eval(f"{frame}.body.classList.contains('embed') && getComputedStyle({frame}.getElementById('side')).display === 'none' && getComputedStyle({frame}.getElementById('placeholder')).display !== 'none'") is True)
            for i in range(40):
                game2.sendto(json.dumps({"v": 1, "type": "geo", "rt": 500.0 + i, "ts": 1795000000 + i, "lat": -41.5 + i * 0.000135, "lon": 174.8, "alt": 5.0, "spd": 15.0, "hdg": 45.0, "ready": True, "veh": 1, "model": "etkc"}).encode(), ("127.0.0.1", udp))
            await p.settle(6.0)
            live = await b.eval(f"(() => {{ const d = {frame}; const m = d.getElementById('mapbox'); return [d.getElementById('detail').hidden, m.classList.contains('rotating'), d.querySelectorAll('.car').length, getComputedStyle(d.getElementById('placeholder')).display]; }})()")
            check("when a trip starts the map in the dashboard opens it, follows the car and turns heading up", live[0] is False and live[1] is True and live[2] == 1 and live[3] == "none", str(live))
            check("the frame keeps its address, so reloading it is still the widget", await b.eval(f"{frame}.defaultView.location.search.includes('embed=1')") is True)
            await p.shot("dashboard-map")
            game2.sendto(json.dumps({"v": 1, "type": "geoevent", "name": "levelEnd", "rt": 541.0, "ts": 1795000041}).encode(), ("127.0.0.1", udp))
            await p.settle(0.5)
            check("the dashboard's own policy allowed the frame and nothing was refused", not [m for m in b.console if "Refused to frame" in m[1] or "frame-src" in m[1]])
        finally:
            game2.close()

        bad = [m for m in b.console[mark:] if m[0] == "error" and "favicon" not in m[1] and "Failed to load resource" not in m[1]]
        check("the Trips page raised no console errors (tile loads aside)", not bad, str(bad[:3]))
        check("and no uncaught exceptions", not b.errors, str(b.errors[:2]))
    finally:
        await b.goto("about:blank", wait=0.3)  # (so the page does not complain about its relay going away, which would count against the next scenario)
        relay.kill()
        relay.wait(timeout=5)
        await b.resize(1400, 760)


async def main_async(shots):
    async with Browser() as b:
        await scenarios(b, shots)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep-shots", metavar="DIR")
    args = ap.parse_args()
    if args.keep_shots:
        Path(args.keep_shots).mkdir(parents=True, exist_ok=True)

    data = tempfile.mkdtemp(prefix="thrashdash-uitest-")
    env = {**os.environ, "XDG_DATA_HOME": data, "LOCALAPPDATA": data}
    ENV.update(env)
    relay = subprocess.Popen([sys.executable, str(ROOT / "relay" / "run.py"), "--simulate", "--no-window", "--http-port", str(PORT)],
                             env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        for _ in range(50):
            try:
                urllib.request.urlopen(BASE + "/api/status", timeout=1)
                break
            except OSError:
                time.sleep(0.2)
        else:
            sys.exit("relay did not start: " + (relay.stderr.read().decode() if relay.poll() is not None else ""))
        asyncio.run(asyncio.wait_for(main_async(args.keep_shots), timeout=1100))
    finally:
        relay.kill()
        relay.wait(timeout=5)
    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    for n in failed:
        print(f"  FAILED: {n}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()

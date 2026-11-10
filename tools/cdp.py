"""Minimal Chromium automation over the DevTools protocol, using only aiohttp (already a dependency).

Used by tools/shot.py and tools/ui_test.py. Real input events (mouse, keyboard) are dispatched, so
tests exercise the page the way a person would. Every wait has a timeout and the browser process is
always killed on exit, so a failing probe cannot hang the terminal.
"""
import asyncio
import base64
import json
import shutil
import subprocess
import tempfile

import aiohttp

ALT, CTRL, META, SHIFT = 1, 2, 4, 8  # CDP modifier bit flags

KEYS = {  # key -> (code, windowsVirtualKeyCode)
    "Escape": ("Escape", 27), "Enter": ("Enter", 13), "Delete": ("Delete", 46), "Backspace": ("Backspace", 8),
    "ArrowLeft": ("ArrowLeft", 37), "ArrowUp": ("ArrowUp", 38), "ArrowRight": ("ArrowRight", 39), "ArrowDown": ("ArrowDown", 40),
    "[": ("BracketLeft", 219), "]": ("BracketRight", 221),
}


def find_browser():
    for name in ("chromium", "google-chrome-stable", "google-chrome", "chromium-browser", "microsoft-edge", "brave-browser"):
        path = shutil.which(name)
        if path:
            return path
    raise RuntimeError("no Chromium-based browser found")


class Browser:
    def __init__(self, size=(1400, 760), port=9333, browser=None):
        self.size = size
        self.port = port
        self.exe = browser or find_browser()
        self.console = []  # (level, text)
        self.errors = []  # uncaught exceptions
        self._next = 0
        self._pending = {}
        self._proc = None
        self._profile = None

    async def __aenter__(self):
        self._profile = tempfile.mkdtemp(prefix="thrashdash-cdp-")
        w, h = self.size
        self._proc = subprocess.Popen(
            [self.exe, "--headless=new", "--no-sandbox", "--disable-gpu", f"--user-data-dir={self._profile}",
             f"--remote-debugging-port={self.port}", f"--window-size={w},{h}", "--hide-scrollbars", "about:blank"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        self._http = aiohttp.ClientSession()
        target = None
        for _ in range(60):
            try:
                async with self._http.get(f"http://127.0.0.1:{self.port}/json/list", timeout=aiohttp.ClientTimeout(total=1)) as r:
                    pages = [t for t in await r.json() if t.get("type") == "page"]
                    if pages:
                        target = pages[0]["webSocketDebuggerUrl"]
                        break
            except (aiohttp.ClientError, asyncio.TimeoutError):
                pass
            await asyncio.sleep(0.2)
        if not target:
            raise RuntimeError("could not reach the browser debug port")
        self._ws = await self._http.ws_connect(target, max_msg_size=0)
        self._reader = asyncio.create_task(self._read())
        await self.send("Page.enable")
        await self.send("Runtime.enable")
        await self.send("Log.enable")
        await self.send("Emulation.setDeviceMetricsOverride", width=w, height=h, deviceScaleFactor=1, mobile=False)
        return self

    async def __aexit__(self, *exc):
        try:
            self._reader.cancel()
            await self._ws.close()
            await self._http.close()
        finally:
            if self._proc:
                self._proc.kill()
                self._proc.wait(timeout=5)
            shutil.rmtree(self._profile, ignore_errors=True)

    async def _read(self):
        async for msg in self._ws:
            if msg.type != aiohttp.WSMsgType.TEXT:
                continue
            data = json.loads(msg.data)
            if "id" in data:
                fut = self._pending.pop(data["id"], None)
                if fut and not fut.done():
                    fut.set_result(data)
            elif data.get("method") == "Runtime.consoleAPICalled":
                p = data["params"]
                self.console.append((p["type"], " ".join(str(a.get("value", a.get("description", ""))) for a in p["args"])))
            elif data.get("method") == "Runtime.exceptionThrown":
                d = data["params"]["exceptionDetails"]
                self.errors.append(d.get("exception", {}).get("description") or d.get("text", "exception"))
            elif data.get("method") == "Log.entryAdded":
                e = data["params"]["entry"]
                if e["level"] in ("error", "warning"):
                    self.console.append((e["level"], e["text"] + " " + e.get("url", "")))

    async def send(self, method, **params):
        self._next += 1
        fut = asyncio.get_running_loop().create_future()
        self._pending[self._next] = fut
        await self._ws.send_json({"id": self._next, "method": method, "params": params})
        res = await asyncio.wait_for(fut, timeout=15)
        if "error" in res:
            raise RuntimeError(f"{method}: {res['error']}")
        return res["result"]

    # ---- page ------------------------------------------------------------------------------

    async def resize(self, width, height):
        """Pretend the window (and its monitor) is width x height; window.screen follows, as in fullscreen."""
        await self.send("Emulation.setDeviceMetricsOverride", width=width, height=height, deviceScaleFactor=1, mobile=False,
                        screenWidth=width, screenHeight=height)
        await asyncio.sleep(0.3)

    async def goto(self, url, wait=1.0):
        await self.send("Page.navigate", url=url)
        await asyncio.sleep(wait)

    async def eval(self, expression):
        res = await self.send("Runtime.evaluate", expression=expression, returnByValue=True, awaitPromise=True)
        if "exceptionDetails" in res:
            raise RuntimeError(f"JS error in {expression!r}: {res['exceptionDetails'].get('exception', {}).get('description')}")
        return res["result"].get("value")

    async def screenshot(self, path):
        shot = await self.send("Page.captureScreenshot", format="png")
        with open(path, "wb") as f:
            f.write(base64.b64decode(shot["data"]))

    async def rect(self, selector, index=0):
        """Viewport rectangle of the index-th element matching selector, or None."""
        return await self.eval(
            f"(() => {{ const el = document.querySelectorAll({json.dumps(selector)})[{index}]; if (!el) return null;"
            "const r = el.getBoundingClientRect(); return {x: r.x, y: r.y, w: r.width, h: r.height, cx: r.x + r.width / 2, cy: r.y + r.height / 2}; })()"
        )

    # ---- input -----------------------------------------------------------------------------

    async def mouse(self, kind, x, y, buttons=0, modifiers=0, click_count=0):
        button = "left" if kind in ("mousePressed", "mouseReleased") or buttons else "none"
        await self.send("Input.dispatchMouseEvent", type=kind, x=x, y=y, button=button, buttons=buttons, modifiers=modifiers, clickCount=click_count)

    async def move(self, x, y):
        await self.mouse("mouseMoved", x, y)

    async def click(self, x, y, modifiers=0):
        await self.mouse("mouseMoved", x, y, modifiers=modifiers)
        await self.mouse("mousePressed", x, y, buttons=1, modifiers=modifiers, click_count=1)
        await self.mouse("mouseReleased", x, y, modifiers=modifiers, click_count=1)

    async def click_el(self, selector, index=0, modifiers=0):
        await self.eval(f"document.querySelectorAll({json.dumps(selector)})[{index}]?.scrollIntoView({{block: 'center'}})")
        await asyncio.sleep(0.05)
        r = await self.rect(selector, index)
        if not r:
            raise RuntimeError(f"nothing matches {selector!r}")
        await self.click(r["cx"], r["cy"], modifiers)

    async def drag(self, x1, y1, x2, y2, steps=8, modifiers=0, release=True):
        await self.mouse("mouseMoved", x1, y1, modifiers=modifiers)
        await self.mouse("mousePressed", x1, y1, buttons=1, modifiers=modifiers, click_count=1)
        for i in range(1, steps + 1):
            await self.mouse("mouseMoved", x1 + (x2 - x1) * i / steps, y1 + (y2 - y1) * i / steps, buttons=1, modifiers=modifiers)
        if release:
            await self.mouse("mouseReleased", x2, y2, modifiers=modifiers, click_count=1)

    async def key(self, key, modifiers=0):
        code, vk = KEYS.get(key, (f"Key{key.upper()}" if len(key) == 1 and key.isalpha() else key, ord(key.upper()) if len(key) == 1 else 0))
        text = key if len(key) == 1 and not modifiers & (CTRL | META) else None
        base = dict(key=key, code=code, windowsVirtualKeyCode=vk, nativeVirtualKeyCode=vk, modifiers=modifiers)
        await self.send("Input.dispatchKeyEvent", type="keyDown", **base, **({"text": text} if text else {}))
        await self.send("Input.dispatchKeyEvent", type="keyUp", **base)

    async def type_text(self, text):
        await self.send("Input.insertText", text=text)

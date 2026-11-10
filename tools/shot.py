#!/usr/bin/env python3
"""Developer tool: load a URL in headless Chromium in REAL time, optionally evaluate JS, save a screenshot.

    .venv/bin/python tools/shot.py URL out.png [--size 1200x420] [--wait 6] [--eval "JS expression"]...

Why this instead of `chromium --screenshot`: that mode runs on virtual time, which distorts CSS
transitions and live WebSocket pages. This drives the browser over its debugging protocol (tools/cdp.py).
"""
import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from cdp import Browser  # noqa: E402


async def run(args):
    w, h = (int(x) for x in args.size.split("x"))
    async with Browser(size=(w, h), browser=args.browser) as b:
        await b.goto(args.url, wait=args.wait)
        for expr in args.eval:
            print(f"eval {expr!r} -> {json.dumps(await b.eval(expr))}")
        await b.screenshot(args.out)
        print(f"saved {args.out}")
        for level, text in b.console:
            print(f"console.{level}: {text}")
        for err in b.errors:
            print(f"EXCEPTION: {err}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    ap.add_argument("out")
    ap.add_argument("--size", default="1200x420")
    ap.add_argument("--wait", type=float, default=5.0, help="seconds to let the page run before capturing")
    ap.add_argument("--eval", action="append", default=[], help="JS expression to evaluate before the screenshot (repeatable)")
    ap.add_argument("--browser")
    args = ap.parse_args()
    try:
        asyncio.run(asyncio.wait_for(run(args), timeout=60))
    except asyncio.TimeoutError:
        sys.exit("probe timed out")


if __name__ == "__main__":
    main()

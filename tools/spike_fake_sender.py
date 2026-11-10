#!/usr/bin/env python3
"""Phase 0 spike: pretend to be the ThrashDash mod, so the listener can be tested without the game.

Sends the same JSON shapes the Lua mod sends (catalog every 2 s, frames at 30 Hz).
Standard library only.
"""
import json
import math
import socket
import time

ADDR = ("127.0.0.1", 4568)
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

n = 0
t0 = time.monotonic()
next_catalog = 0.0
while True:
    t = time.monotonic() - t0
    electrics = {
        "rpm": 3000 + 2500 * math.sin(t),
        "wheelspeed": 20 + 15 * math.sin(t / 3),
        "gear": "3",
        "gearIndex": 3,
        "fuel": max(0.0, 1 - t / 600),
        "watertemp": 88.0,
        "oiltemp": 95.0,
        "signal_L": 1 if int(t * 2) % 2 else 0,
        "wheelThermals": {"FL": {"brakeSurfaceTemperature": 150 + 20 * math.sin(t)}},
    }
    if t >= next_catalog:
        next_catalog = t + 2
        chans = {k: type(v).__name__.replace("float", "number").replace("int", "number").replace("dict", "table")
                 for k, v in electrics.items()}
        sock.sendto(json.dumps({"v": 1, "type": "catalog", "vehicle": "vehicles/fake/", "channels": chans}).encode(), ADDR)
    n += 1
    sock.sendto(json.dumps({"v": 1, "type": "frame", "n": n, "vehicle": "vehicles/fake/", "electrics": electrics}).encode(), ADDR)
    time.sleep(1 / 30)

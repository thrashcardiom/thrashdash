#!/usr/bin/env python3
"""Phase 0 spike: listen for ThrashDash UDP packets and show what the game is sending.

Usage:
    python3 tools/spike_listen.py              # live one-line summary, catalog dump on first catalog
    python3 tools/spike_listen.py --dump out.json   # also save the first frame, catalog and slow packet for inspection
    python3 tools/spike_listen.py --slow            # print each tyre/damage/state ("slow") packet in full
    python3 tools/spike_listen.py --port 4568

Standard library only, so it runs unchanged on Windows and Linux.
"""
import argparse
import json
import socket
import sys
import time


def flatten(prefix, value, out):
    """Flatten nested dicts to dotted names (wheelThermals.FL.brakeSurfaceTemperature)."""
    if isinstance(value, dict):
        for k, v in value.items():
            flatten(f"{prefix}.{k}" if prefix else str(k), v, out)
    else:
        out[prefix] = value


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=4568)
    ap.add_argument("--dump", metavar="FILE", help="write first catalog + first frame (flattened) + first slow packet to FILE")
    ap.add_argument("--slow", action="store_true", help="print every slow packet (tyres, damage, component states) in full")
    args = ap.parse_args()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((args.host, args.port))
    sock.settimeout(1.0)
    print(f"Listening on udp://{args.host}:{args.port}  (Ctrl-C to stop)", file=sys.stderr)

    first_catalog = first_frame = first_slow = None
    count = 0
    t0 = time.monotonic()
    last_report = t0
    try:
        while True:
            try:
                data, _ = sock.recvfrom(65535)
            except socket.timeout:
                if time.monotonic() - last_report > 3:
                    print("... no packets yet (is a car spawned and seated? is the mod installed?)", file=sys.stderr)
                    last_report = time.monotonic()
                continue
            try:
                msg = json.loads(data)
            except ValueError as e:
                print(f"bad JSON ({e}): {data[:80]!r}", file=sys.stderr)
                continue

            kind = msg.get("type")
            if kind == "catalog" and first_catalog is None:
                first_catalog = msg
                chans = msg.get("channels", {})
                print(f"\nCATALOG v{msg.get('v')} vehicle={msg.get('vehicle')} - {len(chans)} top-level channels:")
                for name in sorted(chans):
                    print(f"  {name:32s} {chans[name]}")
                print()
            elif kind == "slow":
                if first_slow is None:
                    first_slow = msg
                    print(f"\nFirst SLOW packet ({len(data)} B): sections {sorted(k for k in msg if k in ('tire', 'damage', 'state'))}")
                    print(json.dumps(msg, indent=2, sort_keys=True))
                elif args.slow:
                    print("\n" + json.dumps(msg, sort_keys=True))
            elif kind == "frame":
                count += 1
                if first_frame is None:
                    first_frame = msg
                e = msg.get("electrics", {})
                now = time.monotonic()
                if now - last_report >= 0.25:
                    hz = count / (now - t0)
                    print(
                        f"frame {msg.get('n'):>6}  {hz:5.1f} Hz  {len(data):>5} B  "
                        f"rpm={e.get('rpm', 0):7.0f}  speed={e.get('wheelspeed', 0) * 3.6:6.1f} km/h  "
                        f"gear={e.get('gear')}  fuel={e.get('fuel', 0):.2f}  water={e.get('watertemp', 0):5.1f}C",
                        end="\r",
                    )
                    last_report = now
            if args.dump and first_catalog and first_frame and first_slow:
                flat = {}
                flatten("", first_frame.get("electrics", {}), flat)
                with open(args.dump, "w", encoding="utf-8") as f:
                    json.dump({"catalog": first_catalog, "flat_first_frame": flat, "first_slow": first_slow}, f, indent=2, sort_keys=True)
                print(f"\nwrote {args.dump} ({len(flat)} flattened channels)", file=sys.stderr)
                args.dump = None
    except KeyboardInterrupt:
        print("\nstopped", file=sys.stderr)


if __name__ == "__main__":
    main()

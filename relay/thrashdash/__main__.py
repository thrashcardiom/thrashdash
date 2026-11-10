"""Command line entry point:  python -m thrashdash  (or  python relay/run.py)."""
import argparse
import asyncio
import sys

from aiohttp import web

from . import __version__, simulator
from .hub import Hub
from .lan import LanController, LanError
from .lifecycle import QUIT_GRACE_S, STARTUP_TIMEOUT_S, QuitWatcher, quit_when_windows_close
from .modinstall import install_mod
from .launcher import find_browser, open_in_default_browser, open_window, profile_in_use
from .netinfo import is_local_client, lan_addresses
from .paths import migrate_legacy_data, user_data_dir, web_root
from .server import WINDOW_COUNTER, make_app
from .store import LayoutStore, SettingsStore
from .windows import WindowRegistry, open_windows
from .udp import start_listener
from .validate import VEHICLE_RE

DEFAULT_HTTP_PORT = 8765
DEFAULT_UDP_PORT = 4568  # must match PORT in the Lua mod


def parse_args(argv=None):
    p = argparse.ArgumentParser(prog="thrashdash", description="BeamNG telemetry dashboard relay")
    p.add_argument("--http-port", type=int, default=DEFAULT_HTTP_PORT, help="dashboard web port (default %(default)s)")
    p.add_argument("--udp-port", type=int, default=DEFAULT_UDP_PORT, help="telemetry port the BeamNG mod sends to (default %(default)s)")
    p.add_argument("--simulate", action="store_true", help="use fake telemetry instead of listening for the game")
    p.add_argument("--sim-vehicles", metavar="A,B", help="with --simulate: comma-separated vehicle names that take turns, to try per-vehicle dashboards")
    p.add_argument("--sim-vehicle-seconds", type=float, default=15.0, metavar="N", help="with --sim-vehicles: seconds per vehicle (default %(default)s)")
    p.add_argument("--no-window", action="store_true", help="do not open the dashboard window automatically")
    p.add_argument("--browser", metavar="PATH", help="Chromium-based browser to use for the window (Chrome, Edge, Brave, Chromium)")
    p.add_argument("--quit-when-windows-close", action="store_true", help="end ThrashDash a moment after the last dashboard window is closed (what the packaged app uses, as it has no terminal to stop it in)")
    p.add_argument("--lan", action="store_true", help="serve to other devices on your network (a phone or tablet) from the start; it can also be switched on from the Dashboards menu while running")
    p.add_argument("--debug-quit-grace", type=float, default=QUIT_GRACE_S, help=argparse.SUPPRESS)  # testing only: seconds to wait before quitting
    p.add_argument("--debug-quit-startup", type=float, default=STARTUP_TIMEOUT_S, help=argparse.SUPPRESS)  # testing only: seconds to wait for a first window
    p.add_argument("--debug-lan-address", metavar="IP", help=argparse.SUPPRESS)  # testing only: pretend this is the computer's network address
    p.add_argument("--debug-treat-clients-as-remote", action="store_true", help=argparse.SUPPRESS)  # testing only: makes even this computer look like a phone
    p.add_argument("--install-mod", nargs="?", const="", metavar="PATH", help="copy the BeamNG mod into the game's mods folder and exit; PATH is the game's user folder if it cannot be found by itself")
    p.add_argument("--version", action="version", version=f"thrashdash {__version__}")
    return p.parse_args(argv)


async def amain(args):
    sim_vehicles = tuple(v.strip() for v in (args.sim_vehicles or "simulator").split(",") if v.strip())
    if not sim_vehicles or not all(VEHICLE_RE.match(v) for v in sim_vehicles):
        print("--sim-vehicles needs plain names such as etkc,midtruck", file=sys.stderr)
        return 2
    args.simulate = args.simulate or bool(args.sim_vehicles)  # naming simulated vehicles implies --simulate
    migrate_legacy_data()  # first run under the new name: carry over what was saved under the old one
    data_dir = user_data_dir()
    hub = Hub(simulated=args.simulate, trip_path=None if args.simulate else data_dir / "trips.json")  # (the simulator's fake drives are not worth keeping)
    layouts = LayoutStore(data_dir / "layouts", web_root() / "layouts", on_skip=lambda path, why: print(f"  skipped unreadable layout {path.name}: {why}", file=sys.stderr))
    settings = SettingsStore(data_dir / "settings.json")
    lan = LanController(args.http_port, addresses=(lambda: [args.debug_lan_address]) if args.debug_lan_address else lan_addresses)
    windows = WindowRegistry(None if args.simulate else data_dir / "windows.json")  # (as with trips, a simulated run is not remembered)
    app = make_app(hub, args.http_port, layouts, settings, lan=lan, local_check=(lambda remote: False) if args.debug_treat_clients_as_remote else is_local_client, windows=windows)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    lan.attach(runner)
    # Always listen on this computer only to begin with. Other devices are served only once network access is switched
    # on (--lan, or the switch in the Dashboards menu, remembered in the settings).
    try:
        await web.TCPSite(runner, "127.0.0.1", args.http_port).start()
    except OSError as e:
        print(f"Cannot listen on port {args.http_port}: {e}\nIs ThrashDash already running? Use --http-port to pick another.", file=sys.stderr)
        await runner.cleanup()
        return 2

    background = []
    transport = None
    if args.simulate:
        background.append(asyncio.create_task(simulator.run(hub, vehicles=sim_vehicles, period=max(1.0, args.sim_vehicle_seconds))))
        source = "SIMULATED telemetry"
    else:
        try:
            transport = await start_listener(hub, "127.0.0.1", args.udp_port)
        except OSError as e:
            print(f"Cannot listen for BeamNG on UDP port {args.udp_port}: {e}\nIs another listener (or a second ThrashDash) running?", file=sys.stderr)
            await runner.cleanup()
            return 2
        source = f"BeamNG on udp://127.0.0.1:{args.udp_port}"

    url = f"http://127.0.0.1:{args.http_port}/"
    print(f"ThrashDash {__version__}\n  source   : {source}\n  dashboard: {url}\n  data     : {data_dir}")
    if args.lan or settings.get()["lan"]:
        try:
            urls = await lan.enable()
            print("  network  : " + "  ".join(urls))
            print("  NOTE     : anyone on this network can VIEW your dashboards (only this computer can change them).")
        except LanError as e:
            print(f"  network  : not available. {e}", file=sys.stderr)

    if not args.no_window:
        browser = find_browser(args.browser)
        if browser is None:
            hint = f" ('{args.browser}' not found)" if args.browser else ""
            print(f"  No Chromium-based browser found{hint}; pass --browser PATH for the borderless app window.")
            if open_in_default_browser(url):
                print(f"  window   : opened {url} in your default browser")
            else:
                print(f"  Open {url} in Chrome, Edge, Brave or Chromium yourself.")
        else:
            def launch(window_url, size, position, fullscreen, x11):
                return open_window(window_url, args.browser, size=size, position=position, fullscreen=fullscreen, x11=x11)

            # In the background: it may wait a moment for windows left open by the previous run to reconnect.
            background.append(asyncio.create_task(report_windows(windows, settings, launch, url.rstrip("/"), browser)))
    stop = asyncio.Event()
    if args.quit_when_windows_close:
        # With --no-window the user is opening the page themselves, so there is no "the window never opened" to quit for.
        watcher = QuitWatcher(app[WINDOW_COUNTER], grace=args.debug_quit_grace, startup_timeout=float("inf") if args.no_window else args.debug_quit_startup)
        background.append(asyncio.create_task(quit_when_windows_close(watcher, stop)))
        print("Closing the last dashboard window quits ThrashDash.")
    else:
        print("Press Ctrl-C to stop.")

    try:
        await stop.wait()
    finally:
        for task in background:
            task.cancel()
        if transport:
            transport.close()
        await runner.cleanup()
    return 0


async def report_windows(windows, settings, launch, base_url, browser):
    opened = await open_windows(windows, settings, launch, base_url, profile_in_use, asyncio.sleep)
    print(f"  window{'s' if opened != 1 else ' '}  : {opened} opened with {browser}")


def main(argv=None):
    # Messages must reach a log file (or a pipe) as they happen, not when a buffer fills: when ThrashDash is started from a
    # desktop its output goes to thrashdash.log, which would otherwise stay empty until it ends.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(line_buffering=True)
        except (AttributeError, ValueError):
            pass
    args = parse_args(argv)
    if args.install_mod is not None:
        return install_mod(args.install_mod or None)
    try:
        return asyncio.run(amain(args))
    except KeyboardInterrupt:
        print("\nstopped")
        return 0


if __name__ == "__main__":
    sys.exit(main())

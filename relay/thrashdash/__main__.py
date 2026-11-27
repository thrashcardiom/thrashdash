"""Command line entry point:  python -m thrashdash  (or  python relay/run.py)."""
import argparse
import asyncio
import sys

from aiohttp import web

from . import __version__, simulator
from .hub import Hub
from .lan import LanController, LanError
from .lifecycle import QUIT_GRACE_S, STARTUP_TIMEOUT_S, QuitWatcher, quit_when_windows_close
from .auth import AuthStore, valid_pin
from .modinstall import install_mod
from .output import ensure_output
from .launcher import find_browser, open_in_default_browser, open_window, profile_in_use, stop_profile_browser
from .netinfo import is_local_client, lan_addresses
from .paths import migrate_legacy_data, user_data_dir, web_root
from .api import QUIT, WINDOW_OPENER
from .server import WINDOW_COUNTER, make_app
from .store import LayoutStore, SettingsStore
from .triplog import TripLog
from .configs import ConfigStore
from .laps import CircuitStore, LapStore, LapTimer
from .routeplan import DestinationStore, RoutePlanner
from .windows import WindowRegistry, open_config_windows, open_windows
from .tripnotice import TripNotices
from . import earthlaunch
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
    p.add_argument("--keep-running", action="store_true", help="the packaged app ends when its last dashboard window is closed; this keeps it running until it is stopped")
    p.add_argument("--lan", action="store_true", help="serve to other devices on your network (a phone or tablet) from the start; it can also be switched on from the Dashboards menu while running")
    p.add_argument("--debug-quit-grace", type=float, default=QUIT_GRACE_S, help=argparse.SUPPRESS)  # testing only: seconds to wait before quitting
    p.add_argument("--debug-quit-startup", type=float, default=STARTUP_TIMEOUT_S, help=argparse.SUPPRESS)  # testing only: seconds to wait for a first window
    p.add_argument("--debug-pin", metavar="PIN", help=argparse.SUPPRESS)  # testing only: set the PIN for devices on the network at start
    p.add_argument("--debug-lan-address", metavar="IP", help=argparse.SUPPRESS)  # testing only: pretend this is the computer's network address
    p.add_argument("--debug-treat-clients-as-remote", action="store_true", help=argparse.SUPPRESS)  # testing only: makes even this computer look like a phone
    p.add_argument("--with-earth", metavar="PATH", help="start BeamNG.Earth along with ThrashDash and quit when it closes; PATH is the folder Earth is kept in (the newest version in it is started) or its file. Overrides the setting on the Trips page")
    p.add_argument("--no-earth", action="store_true", help="do not start BeamNG.Earth this time, whatever the setting says")
    p.add_argument("--earth-port", type=int, default=earthlaunch.EARTH_PORT, help=argparse.SUPPRESS)  # testing only: where Earth's local service is
    p.add_argument("--debug-earth-timing", metavar="POLL,GONE", help=argparse.SUPPRESS)  # testing only: seconds between looks at Earth, and silent for how long means closed
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
    settings = SettingsStore(data_dir / "settings.json")
    notices = TripNotices(settings)  # popups about trips, in the game and on the dashboard windows
    triplog = None if args.simulate else TripLog(data_dir / "triplog", on_notice=notices.publish)  # where the car has been, from BeamNG.Earth (a simulated run keeps none)
    if triplog is not None:
        triplog.set_idle_end(settings.get()["tripIdleEndMin"] * 60)
        triplog.price_source = lambda: settings.get()["fuelPrice"]  # (each trip is given the price of fuel as it is when it starts)
    laps = LapStore(None if args.simulate else data_dir / "laps")  # finished laps and bests (a simulated run keeps none)
    circuits = CircuitStore(data_dir / "circuits.json", laps)  # the lines that make a circuit; the simulator's lap timing uses the same
    hub = Hub(simulated=args.simulate, trip_path=None if args.simulate else data_dir / "trips.json", triplog=triplog, lap_timer=LapTimer(circuits, laps))  # (the simulator's fake drives are not worth keeping)
    if triplog is not None:
        triplog.on_change = hub.add_trip_event  # (a page watching the trip list is told the moment a trip starts or ends)
    layouts = LayoutStore(data_dir / "layouts", web_root() / "layouts", on_skip=lambda path, why: print(f"  skipped unreadable layout {path.name}: {why}", file=sys.stderr))
    lan = LanController(args.http_port, addresses=(lambda: [args.debug_lan_address]) if args.debug_lan_address else lan_addresses)
    configs = ConfigStore(data_dir / "configs.json")  # saved sets of windows: the last one saved or picked is opened at start-up
    windows = WindowRegistry(None if args.simulate else data_dir / "windows.json")  # (as with trips, a simulated run is not remembered)
    auth = AuthStore(data_dir / "auth.json")
    if args.debug_pin:
        if not valid_pin(args.debug_pin):
            print("--debug-pin must be 4 to 8 digits", file=sys.stderr)
            return 2
        auth.set_pin(args.debug_pin)
    app = make_app(hub, args.http_port, layouts, settings, lan=lan, auth=auth, local_check=(lambda remote: False) if args.debug_treat_clients_as_remote else is_local_client, windows=windows, notices=notices, configs=configs, circuits=circuits, laps=laps, route_planner=RoutePlanner(notices, hub), destinations=DestinationStore(data_dir / "destinations.json"))
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

    stop = asyncio.Event()
    background = []
    earth_setting = None if (args.no_earth or args.simulate) else (args.with_earth if args.with_earth is not None else settings.get()["earthPath"])
    if earth_setting:
        earth_path, earth_message = earthlaunch.pick_earth(earth_setting)
        if earth_path is None:
            print(f"  earth    : not started. {earth_message}", file=sys.stderr)
        else:
            print(f"  earth    : {earth_message}")
            timing = {}
            if args.debug_earth_timing:
                poll, gone = (float(x) for x in args.debug_earth_timing.split(","))
                timing = {"poll_s": poll, "gone_after_s": gone, "never_up_s": gone * 4}
            background.append(asyncio.create_task(earthlaunch.keep_with_earth(
                earth_path, stop.set, say=lambda text: print(f"  earth    : {text}"), log_path=data_dir / "earth.log", port=args.earth_port, **timing)))
    if triplog is not None:
        background.append(asyncio.create_task(tick_triplog(triplog)))
    transport = None
    if args.simulate:
        background.append(asyncio.create_task(simulator.run(hub, vehicles=sim_vehicles, period=max(1.0, args.sim_vehicle_seconds))))
        source = "SIMULATED telemetry"
    else:
        try:
            transport = await start_listener(hub, "127.0.0.1", args.udp_port, notices)
        except OSError as e:
            print(f"Cannot listen for BeamNG on UDP port {args.udp_port}: {e}\nIs another listener (or a second ThrashDash) running?", file=sys.stderr)
            await runner.cleanup()
            return 2
        source = f"BeamNG on udp://127.0.0.1:{args.udp_port}"

    url = f"http://127.0.0.1:{args.http_port}/"
    print(f"ThrashDash {__version__}\n  source   : {source}\n  dashboard: {url}\n  data     : {data_dir}")
    if args.lan or settings.get()["lan"]:
        if not auth.has_pin:
            # Network access always needs a PIN. It stays off until one is chosen in the menu; --lan is honoured then.
            lan.start_when_pin_set = args.lan
            print("  network  : off until you choose a PIN: open the Dashboards menu on this computer, under 'Phones and other devices'.")
        else:
            try:
                urls = await lan.enable()
                print("  network  : " + "  ".join(urls))
                print("  NOTE     : devices on this network need the PIN to VIEW your dashboards (only this computer can change them).")
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

            async def open_for_config(config_windows):
                return await open_config_windows(windows, config_windows, settings.get().get("exactWindowPlacement") is True, launch, url.rstrip("/"), asyncio.sleep)

            app[WINDOW_OPENER]["open"] = open_for_config  # (picking a config while running opens its windows)
            # In the background: it may wait a moment for windows left open by the previous run to reconnect.
            background.append(asyncio.create_task(report_windows(windows, settings, launch, url.rstrip("/"), browser, configs)))
    app[QUIT]["stop"] = stop.set  # (the Quit button in the Dashboards menu)
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
        if not args.no_window and settings.get().get("closeWindowsOnExit") is not False:
            await asyncio.sleep(1.0)  # (the windows were told to close; give them a moment)
            if profile_in_use():
                stop_profile_browser()  # one did not: the browser that runs ThrashDash's own windows is asked to quit
    return 0


async def tick_triplog(triplog, period=5.0):
    """Ends the trip being recorded when the game stops sending positions (it was closed)."""
    while True:
        await asyncio.sleep(period)
        triplog.tick()


async def report_windows(windows, settings, launch, base_url, browser, configs=None):
    opened = await open_windows(windows, settings, launch, base_url, profile_in_use, asyncio.sleep, configs=configs)
    print(f"  window{'s' if opened != 1 else ' '}  : {opened} opened with {browser}")


def apply_packaged_defaults(args, frozen=None):
    """A packaged app (the AppImage, the Windows exe) is started by double-clicking, with no terminal to press Ctrl-C in, so it
    ends when its last dashboard window closes unless told not to (--keep-running). Run from the source it keeps running."""
    frozen = getattr(sys, "frozen", False) if frozen is None else frozen
    if frozen and not args.keep_running:
        args.quit_when_windows_close = True
    return args


def main(argv=None):
    ensure_output()  # first: a program without a console (the Windows exe) has no stdout, and argparse and print need one
    # Messages must reach a log file (or a pipe) as they happen, not when a buffer fills: when ThrashDash is started from a
    # desktop its output goes to thrashdash.log, which would otherwise stay empty until it ends.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(line_buffering=True)
        except (AttributeError, ValueError):
            pass
    args = apply_packaged_defaults(parse_args(argv))
    if args.install_mod is not None:
        return install_mod(args.install_mod or None)
    try:
        return asyncio.run(amain(args))
    except KeyboardInterrupt:
        print("\nstopped")
        return 0


if __name__ == "__main__":
    sys.exit(main())

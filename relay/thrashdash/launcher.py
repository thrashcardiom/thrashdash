"""Open the dashboard in its own borderless-looking app window.

A Chromium-based browser started with --app=URL shows the page with no address bar, tabs or
bookmarks, in an ordinary resizable, draggable window that can be fullscreened (F11) and moved to
any monitor. Edge ships with Windows and Chrome/Chromium/Brave are common on Linux, so this works
without bundling a browser. Firefox has no equivalent mode, so it is not used.

We give the window its own browser profile directory. That keeps it from touching the user's real
browser profile, and lets Chromium remember this window's size and position between runs.
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

from .paths import user_data_dir

_LINUX_NAMES = [
    "google-chrome-stable", "google-chrome", "chromium", "chromium-browser",
    "brave-browser", "brave", "microsoft-edge-stable", "microsoft-edge",
]


def _windows_candidates():
    roots = [os.environ.get(v) for v in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA")]
    rel = [
        r"Microsoft\Edge\Application\msedge.exe",  # first: present on every Windows 10/11
        r"Google\Chrome\Application\chrome.exe",
        r"BraveSoftware\Brave-Browser\Application\brave.exe",
        r"Chromium\Application\chrome.exe",
    ]
    return [Path(root) / r for r in rel for root in roots if root]


def find_browser(override=None):
    """Return the path of a Chromium-based browser, or None. `override` (or $THRASHDASH_BROWSER) wins."""
    override = override or os.environ.get("THRASHDASH_BROWSER")
    if override:
        found = shutil.which(override) or (override if Path(override).is_file() else None)
        return found
    if sys.platform == "win32":
        for path in _windows_candidates():
            if path.is_file():
                return str(path)
        return None
    if sys.platform == "darwin":
        for app in ("Google Chrome", "Microsoft Edge", "Brave Browser", "Chromium"):
            path = Path("/Applications") / f"{app}.app" / "Contents" / "MacOS" / app
            if path.is_file():
                return str(path)
        return None
    for name in _LINUX_NAMES:
        found = shutil.which(name)
        if found:
            return found
    return None


# Search-path variables a packaged app points at its own bundled libraries. A browser started with them would load those
# instead of its own and can fail to start (or crash later), so the browser gets the environment the app was started with.
_BUNDLE_PATH_VARS = ("LD_LIBRARY_PATH", "PATH", "XDG_DATA_DIRS", "PYTHONPATH", "QT_PLUGIN_PATH", "GST_PLUGIN_PATH", "GIO_EXTRA_MODULES")


def child_env(env=None, bundle_roots=None, sep=None):
    """The environment for a browser started by ThrashDash: the one ThrashDash got, minus anything pointing into its own bundle.

    PyInstaller saves the original value of each variable it changes as <NAME>_ORIG, which is put back; and any entry of
    a search path that lies inside the bundle (`bundle_roots`: the unpacked app, an AppImage's mount) is dropped.
    """
    env = dict(os.environ if env is None else env)
    sep = sep or os.pathsep
    if bundle_roots is None:
        bundle_roots = [getattr(sys, "_MEIPASS", None), env.get("APPDIR")]
    roots = [str(r).rstrip("/\\") for r in bundle_roots if r]
    for name in _BUNDLE_PATH_VARS:
        original = env.get(f"{name}_ORIG")
        if original is not None:
            if original:
                env[name] = original
            else:
                env.pop(name, None)
        value = env.get(name)
        if value and roots:
            kept = [p for p in value.split(sep) if p and not any(p == r or p.startswith(r + "/") or p.startswith(r + "\\") for r in roots)]
            if kept:
                env[name] = sep.join(kept)
            else:
                env.pop(name, None)
    for name in [k for k in env if k.endswith("_ORIG") and k[:-5] in _BUNDLE_PATH_VARS]:
        del env[name]
    return env


def window_args(exe, url, profile, size=(1200, 420), position=None, fullscreen=False, x11=False, platform=None):
    """The command line for one dashboard window.

    `position` ((x, y) or None) and `x11` go together: under Wayland a program cannot place its own windows, so exact
    placement needs the browser in X11 compatibility mode (--ozone-platform=x11, Linux only). That flag belongs to the
    browser process, so it only takes effect when this launch starts the browser, not when it joins one already running.
    """
    platform = platform or sys.platform
    args = [
        exe,
        f"--app={url}",
        f"--user-data-dir={profile}",
        f"--window-size={size[0]},{size[1]}",
        "--no-first-run",
        "--no-default-browser-check",
    ]
    if position is not None:
        args.append(f"--window-position={position[0]},{position[1]}")
    if fullscreen:
        args.append("--start-fullscreen")
    if x11 and platform.startswith("linux"):
        args.append("--ozone-platform=x11")
    return args


def profile_dir(profile_name="main"):
    return user_data_dir() / "window-profiles" / profile_name


def profile_in_use(profile_name="main"):
    """True if a browser already has the dashboard windows' profile open (so windows it left open may still be there)."""
    folder = profile_dir(profile_name)
    return any(os.path.lexists(folder / name) for name in ("SingletonLock", "lockfile"))


def open_window(url, browser=None, size=(1200, 420), profile_name="main", position=None, fullscreen=False, x11=False):
    """Launch an app window. Returns the Popen, or None if no suitable browser was found."""
    exe = find_browser(browser)
    if not exe:
        return None
    profile = profile_dir(profile_name)
    profile.mkdir(parents=True, exist_ok=True)
    args = window_args(exe, url, profile, size, position, fullscreen, x11)
    # Detach: closing the relay must not kill the window and vice versa.
    kwargs = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL, "stdin": subprocess.DEVNULL}
    if sys.platform == "win32":
        kwargs["creationflags"] = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    return subprocess.Popen(args, env=child_env(), **kwargs)


def open_in_default_browser(url):
    """Open `url` in whatever the system's default browser is. Used when no Chromium-based browser is found, so there is
    still a dashboard to look at (just in an ordinary browser window rather than a borderless app window). True if it was asked."""
    try:
        if sys.platform == "win32":
            os.startfile(url)  # noqa: S606 (the address is ours)
            return True
        opener = shutil.which("open" if sys.platform == "darwin" else "xdg-open")
        if not opener:
            return False
        subprocess.Popen([opener, url], env=child_env(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, start_new_session=True)
        return True
    except OSError:
        return False

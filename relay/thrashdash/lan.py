"""Serving the dashboard to other devices on the network (a phone, a tablet), switched on and off while running.

When enabled the relay ALSO listens on this computer's private LAN addresses, one listener per address rather than
0.0.0.0: that never clashes with the loopback listener, and it limits the exposure to ordinary home/office networks
(not VPN or public interfaces). Remote devices can look at dashboards and live data; only this computer can change
anything (see api.check_write_allowed).

ThrashDash only opens its own listening port. Whether a firewall on this computer lets other devices through to that
port is a system administrator's decision: ThrashDash neither inspects nor changes firewall settings.
"""
import asyncio

from aiohttp import web

from .netinfo import lan_addresses, prime_local_addresses


class LanError(RuntimeError):
    """Raised with a message fit to show the user when the network listeners cannot be opened."""


class LanController:
    def __init__(self, port, addresses=lan_addresses):
        self.port = port
        self._addresses = addresses
        self.runner = None
        self.sites = {}  # address -> TCPSite
        self.on_disable = None  # async callback: drop already-connected remote clients when the switch goes off
        self.start_when_pin_set = False  # --lan was given but no PIN exists yet: start as soon as one is chosen (this run only)

    def attach(self, runner):
        self.runner = runner

    @property
    def enabled(self):
        return bool(self.sites)

    def urls(self):
        return [f"http://{ip}:{self.port}/" for ip in self.sites]

    async def enable(self):
        """Start listening on the LAN addresses. Returns the URLs; raises LanError if none could be opened."""
        # Address lookup can block on a slow DNS server, so it runs in a worker thread, not on the event loop.
        addresses = await asyncio.get_running_loop().run_in_executor(None, self._addresses)
        prime_local_addresses(addresses)
        if not addresses:
            raise LanError("No private network address was found. Is this computer connected to a network?")
        problems = []
        for ip in addresses:
            if ip in self.sites:
                continue
            site = web.TCPSite(self.runner, ip, self.port)
            try:
                await site.start()
                self.sites[ip] = site
            except OSError as e:
                problems.append(f"{ip}: {e.strerror or e}")
        if not self.sites:
            raise LanError("Could not open the port on this network (" + "; ".join(problems) + "). Is another program using it?")
        return self.urls()

    async def disable(self):
        for site in self.sites.values():
            await site.stop()
        self.sites.clear()
        if self.on_disable:
            await self.on_disable()

    async def apply(self, wanted):
        if wanted:
            return await self.enable()
        await self.disable()
        return []

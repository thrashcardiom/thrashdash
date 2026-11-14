"""UDP listener for the BeamNG mod's JSON datagrams."""
import asyncio
import json

from .hub import GEO_TYPES


class TelemetryProtocol(asyncio.DatagramProtocol):
    # No explicit size limit: a UDP datagram cannot exceed ~64 KB, which is already small enough to
    # parse safely. Anything larger than the mod would ever send is bounded again in Hub.flatten
    # (channel count, nesting depth, string length).
    def __init__(self, hub, notices=None):
        self.hub = hub
        self.notices = notices  # told where the game's trip-log extension is, so a popup can be sent back to it

    def datagram_received(self, data, addr):
        try:
            packet = json.loads(data)
        except (ValueError, UnicodeDecodeError, RecursionError):
            self.hub.stats["rejected_json"] += 1
            return
        self.hub.ingest_packet(packet)
        if self.notices is not None and isinstance(packet, dict) and packet.get("type") in (*GEO_TYPES, "geolive"):
            self.notices.seen_game(addr)

    def error_received(self, exc):  # ICMP errors on a UDP socket: nothing useful to do
        self.hub.stats["udp_errors"] += 1


async def start_listener(hub, host, port, notices=None):
    """Bind the telemetry socket. Raises OSError if the port is taken. `notices` (tripnotice.TripNotices) may answer the game on it."""
    loop = asyncio.get_running_loop()
    transport, _ = await loop.create_datagram_endpoint(lambda: TelemetryProtocol(hub, notices), local_addr=(host, port))
    if notices is not None:
        notices.attach(transport)
    return transport

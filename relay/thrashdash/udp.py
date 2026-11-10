"""UDP listener for the BeamNG mod's JSON datagrams."""
import asyncio
import json


class TelemetryProtocol(asyncio.DatagramProtocol):
    # No explicit size limit: a UDP datagram cannot exceed ~64 KB, which is already small enough to
    # parse safely. Anything larger than the mod would ever send is bounded again in Hub.flatten
    # (channel count, nesting depth, string length).
    def __init__(self, hub):
        self.hub = hub

    def datagram_received(self, data, addr):
        try:
            packet = json.loads(data)
        except (ValueError, UnicodeDecodeError, RecursionError):
            self.hub.stats["rejected_json"] += 1
            return
        self.hub.ingest_packet(packet)

    def error_received(self, exc):  # ICMP errors on a UDP socket: nothing useful to do
        self.hub.stats["udp_errors"] += 1


async def start_listener(hub, host, port):
    """Bind the telemetry socket. Raises OSError if the port is taken."""
    loop = asyncio.get_running_loop()
    transport, _ = await loop.create_datagram_endpoint(lambda: TelemetryProtocol(hub), local_addr=(host, port))
    return transport

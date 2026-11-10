"""ThrashDash relay: receives telemetry from the BeamNG mod over UDP and serves the dashboard."""

__version__ = "0.1.0"

# Version of the UDP/WebSocket message schema. The Lua mod stamps every packet with "v";
# the relay rejects packets whose version it does not understand instead of guessing, so a
# future incompatible mod shows up as a clear message rather than as silently wrong gauges.
PROTOCOL_VERSION = 1

"""Network facts and the policy for which requests the relay answers.

Two separate questions are kept apart on purpose:

* Is this request *allowed at all*? (`host_allowed`: the Host header must name us, which defeats DNS rebinding.)
* Is this client *this computer*? (`is_local_client`: only this computer may change anything; phones and tablets on the
  network can look but not touch.)
"""
import ipaddress
import socket
import time

LOOPBACK_NAMES = {"127.0.0.1", "localhost", "::1"}
# Private-network names a router or the OS might hand out. A hostile web page needs a PUBLIC domain name to mount a DNS
# rebinding attack, and those always contain a dot and end in a public suffix, so they never match this list.
PRIVATE_SUFFIXES = (".local", ".lan", ".home.arpa")


def parse_host(value):
    """Split a Host header into (hostname, port or None); None if it is malformed."""
    value = (value or "").strip().lower()
    if not value or any(c in value for c in " /\\@?#\t"):
        return None
    if value.startswith("["):  # [::1]:8765
        end = value.find("]")
        if end < 0:
            return None
        host, rest = value[1:end], value[end + 1:]
    else:
        if value.count(":") > 1:  # a bare IPv6 address is not a valid Host header
            return None
        host, sep, rest = value.partition(":")
        rest = sep + rest if sep else ""
    if not host:
        return None
    if not rest:
        return host, None
    if rest.startswith(":") and rest[1:].isdigit():
        return host, int(rest[1:])
    return None


def host_allowed(header, port, lan_enabled):
    """May a request whose Host header is `header` be answered by the relay listening on `port`?"""
    parsed = parse_host(header)
    if not parsed:
        return False
    host, host_port = parsed
    if host_port != port:  # a request meant for us always names our port
        return False
    if host in LOOPBACK_NAMES:
        return True
    if not lan_enabled:
        return False
    try:
        ipaddress.ip_address(host)
        return True  # an IP address, such as the PC's LAN address typed into a phone
    except ValueError:
        pass
    return "." not in host or host.endswith(PRIVATE_SUFFIXES)  # a computer name such as "my-pc" or "my-pc.local"


def lan_addresses():
    """This computer's private IPv4 addresses (what a phone on the same network would use), the main one first."""
    found = []

    def keep(ip):
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            return
        if addr.version == 4 and addr.is_private and not addr.is_loopback and not addr.is_link_local and ip not in found:
            found.append(ip)

    try:  # the address used to reach "the internet": connecting a UDP socket sends nothing but picks the route
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))
            keep(s.getsockname()[0])
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            keep(info[4][0])
    except OSError:
        pass
    return found


_local_cache = (0.0, frozenset())


def _local_addresses():
    """Every address that belongs to this computer, cached briefly."""
    global _local_cache
    now = time.monotonic()
    if now - _local_cache[0] > 30:
        _local_cache = (now, frozenset(lan_addresses()))
    return _local_cache[1]


def prime_local_addresses(addresses):
    """Remember this computer's addresses (already looked up off the event loop) so request handling never has to."""
    global _local_cache
    _local_cache = (time.monotonic(), frozenset(addresses))


def is_local_client(remote):
    """True if the connection comes from this computer: loopback, or one of its own network addresses."""
    if not remote:
        return False
    try:
        addr = ipaddress.ip_address(remote.split("%")[0])
    except ValueError:
        return False
    if getattr(addr, "ipv4_mapped", None):
        addr = addr.ipv4_mapped
    return addr.is_loopback or str(addr) in _local_addresses()

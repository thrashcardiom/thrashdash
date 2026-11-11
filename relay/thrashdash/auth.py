"""The PIN that devices on the network must give before they may see anything.

This computer never needs it. A phone or tablet gets in by typing the PIN once (or by scanning the QR code, which carries a
short-lived single-use token), and is then recognised by a signed cookie for 30 days. Changing the PIN signs every device out.

What it is and is not: the relay speaks plain http, so the PIN and the cookie cross the network unencrypted. This keeps out
anyone who merely finds the address; it does not stop someone who can watch the network traffic. It is a lock on the door,
not a vault, and the README says so.

Nothing here knows about web requests: the server calls it, and tests drive it with a fake clock.
"""
import hmac
import json
import os
import re
import secrets
import time
from hashlib import sha256
from pathlib import Path

PIN_RE = re.compile(r"^[0-9]{4,8}$")
COOKIE_NAME = "thrashdash_session"
SESSION_SECONDS = 30 * 24 * 3600
TOKEN_SECONDS = 300  # a QR code's sign-in token is good for this long, once
MAX_TOKENS = 20

# Guessing: this many wrong tries in a row from one device, then a wait that doubles each further miss
FREE_TRIES = 5
FIRST_LOCKOUT_S = 60
MAX_LOCKOUT_S = 3600
# ...and across all devices together, so spreading the guesses over many addresses does not help
GLOBAL_WINDOW_S = 600
GLOBAL_MAX_FAILURES = 30
GLOBAL_LOCKOUT_S = 60


def valid_pin(pin):
    return isinstance(pin, str) and PIN_RE.fullmatch(pin) is not None


class Throttle:
    """Slows down guessing. Ask `wait_seconds(key)` before checking a PIN; report `failed(key)` or `succeeded(key)` after."""

    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self._failures = {}  # key -> consecutive failures
        self._locked_until = {}  # key -> clock time
        self._recent = []  # clock times of recent failures, for the all-devices limit
        self._global_until = 0.0

    def wait_seconds(self, key):
        now = self.clock()
        wait = max(self._locked_until.get(key, 0.0) - now, self._global_until - now, 0.0)
        return int(wait) + 1 if wait > 0 else 0

    def failed(self, key):
        now = self.clock()
        count = self._failures.get(key, 0) + 1
        self._failures[key] = count
        if count >= FREE_TRIES:
            lockout = min(FIRST_LOCKOUT_S * 2 ** (count - FREE_TRIES), MAX_LOCKOUT_S)
            self._locked_until[key] = now + lockout
        self._recent = [t for t in self._recent if now - t < GLOBAL_WINDOW_S] + [now]
        if len(self._recent) >= GLOBAL_MAX_FAILURES:
            self._global_until = now + GLOBAL_LOCKOUT_S
            self._recent = []
        if len(self._failures) > 1000:  # bounded: forget the quietest devices rather than grow without limit
            for stale in sorted(self._failures, key=lambda k: self._locked_until.get(k, 0.0))[:500]:
                self._failures.pop(stale, None)
                self._locked_until.pop(stale, None)

    def succeeded(self, key):
        self._failures.pop(key, None)
        self._locked_until.pop(key, None)


class AuthStore:
    """The PIN (kept in a file only this user can read), the secret that signs cookies, and the live QR tokens."""

    def __init__(self, path=None, wall_clock=time.time):
        self.path = Path(path) if path else None
        self._wall = wall_clock
        self.pin = None
        self._secret = secrets.token_bytes(32)
        self._tokens = {}  # token -> expiry (wall clock seconds)
        self.throttle = Throttle()
        self._load()

    # ---- the stored PIN ---------------------------------------------------------------------

    def _load(self):
        if self.path is None:
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            pin, secret = raw.get("pin"), raw.get("secret")
            if valid_pin(pin) and isinstance(secret, str) and re.fullmatch(r"[0-9a-f]{64}", secret):
                self.pin, self._secret = pin, bytes.fromhex(secret)
        except (OSError, ValueError, AttributeError):
            pass  # no PIN yet (or the file is unreadable: treated as none, so network access stays off)

    def _save(self):
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)  # readable by this user only, from the first byte
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"v": 1, "pin": self.pin, "secret": self._secret.hex()}, f)
        os.replace(tmp, self.path)

    @property
    def has_pin(self):
        return self.pin is not None

    def set_pin(self, pin):
        """Choose the PIN. Raises ValueError if it is not 4 to 8 digits. Signs every device out (new signing secret), even
        if the PIN is the same as before."""
        if not valid_pin(pin):
            raise ValueError("The PIN must be 4 to 8 digits.")
        self.pin = pin
        self._secret = secrets.token_bytes(32)
        self._tokens.clear()
        self._save()

    def check_pin(self, candidate):
        if not self.has_pin or not isinstance(candidate, str):
            return False
        return hmac.compare_digest(candidate.encode(), self.pin.encode())  # constant time: no hint from how long it took

    # ---- signed cookies ---------------------------------------------------------------------

    def _mac(self, payload):
        return hmac.new(self._secret, payload.encode(), sha256).hexdigest()

    def issue_cookie(self):
        """A value proving the device entered the PIN (or used a QR token): expiry, a random part, and a signature."""
        payload = f"{int(self._wall()) + SESSION_SECONDS}.{secrets.token_hex(8)}"
        return f"v1.{payload}.{self._mac(payload)}"

    def cookie_valid(self, value):
        if not isinstance(value, str) or not self.has_pin:
            return False
        parts = value.split(".")
        if len(parts) != 4 or parts[0] != "v1":
            return False
        payload = f"{parts[1]}.{parts[2]}"
        if not hmac.compare_digest(parts[3].encode(), self._mac(payload).encode()):
            return False
        try:
            return int(parts[1]) > self._wall()
        except ValueError:
            return False

    # ---- QR sign-in tokens ------------------------------------------------------------------

    def new_token(self):
        now = self._wall()
        self._tokens = {t: e for t, e in self._tokens.items() if e > now}
        while len(self._tokens) >= MAX_TOKENS:  # a person opening the menu over and over cannot grow this without limit
            self._tokens.pop(min(self._tokens, key=self._tokens.get))
        token = secrets.token_urlsafe(24)
        self._tokens[token] = now + TOKEN_SECONDS
        return token

    def use_token(self, token):
        """True once for a live token, then never again."""
        if not isinstance(token, str):
            return False
        expiry = self._tokens.pop(token, None)  # popped first: a token cannot be used twice, even by a fast second request
        return expiry is not None and expiry > self._wall()

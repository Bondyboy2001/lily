# Calibre-Web Automated – fork of Calibre-Web
# SPDX-License-Identifier: GPL-3.0-or-later

"""TOTP (RFC 6238) second factor and personal API tokens.

Standard-library only. TOTP is SHA-1, 6 digits, 30 s steps, which is what every
authenticator app defaults to. API tokens are random, shown once, and stored as
a SHA-256 hash (they carry 256 bits of entropy, so a plain hash is enough).
"""

import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote

STEP_SECONDS = 30
DIGITS = 6
WINDOW = 1  # accept the previous and next step to tolerate clock drift
TOKEN_PREFIX = "lily_"

MAX_FAILURES = 5
LOCKOUT_SECONDS = 15 * 60
MAX_LOCKOUT_SECONDS = 24 * 60 * 60


def generate_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")


def _code_for_step(secret: str, step: int) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8), casefold=True)
    digest = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    number = (struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF) % (10 ** DIGITS)
    return str(number).zfill(DIGITS)


def current_code(secret: str, now: float | None = None) -> str:
    return _code_for_step(secret, int((time.time() if now is None else now) // STEP_SECONDS))


def verify_code(secret: str, code: str, last_step: int | None = None, now: float | None = None) -> int | None:
    """Returns the matched time step, or None. Steps at or before last_step are
    refused so one code cannot be replayed."""
    code = "".join((code or "").split())
    if not secret or len(code) != DIGITS or not code.isdigit():
        return None
    step_now = int((time.time() if now is None else now) // STEP_SECONDS)
    for step in range(step_now - WINDOW, step_now + WINDOW + 1):
        if last_step is not None and step <= last_step:
            continue
        if hmac.compare_digest(_code_for_step(secret, step), code):
            return step
    return None


def provisioning_uri(secret: str, account: str, issuer: str = "Lily") -> str:
    return "otpauth://totp/%s:%s?secret=%s&issuer=%s" % (
        quote(issuer), quote(account), secret, quote(issuer))


def new_api_token() -> str:
    return TOKEN_PREFIX + secrets.token_urlsafe(32)


def hash_api_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def looks_like_api_token(value: str) -> bool:
    return bool(value) and value.startswith(TOKEN_PREFIX)


class FailureTracker:
    """Per-key failure counter with a timed lockout. Stops an attacker who knows a
    password from guessing 6-digit codes by restarting login.

    Each lockout in a row doubles the next one (up to ``max_lockout``); a success clears
    everything. ``store`` is any dict-like mapping key -> {"count", "until", "lockouts"}
    (get, item assignment, pop, iteration); the web app passes one backed by app.db so a
    restart doesn't reset a lockout."""

    def __init__(self, max_failures: int = MAX_FAILURES, lockout: int = LOCKOUT_SECONDS,
                 max_lockout: int = MAX_LOCKOUT_SECONDS, store=None):
        self.max_failures = max_failures
        self.lockout = lockout
        self.max_lockout = max_lockout
        self._state = {} if store is None else store

    def locked(self, key, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        entry = self._state.get(key)
        if not entry or not entry.get("until"):
            return False
        if now >= entry["until"]:
            # Lockout over: fresh attempts, but the next lockout will be longer
            self._state[key] = {"count": 0, "until": 0, "lockouts": entry.get("lockouts", 0)}
            return False
        return True

    def failure(self, key, now: float | None = None) -> None:
        now = time.time() if now is None else now
        entry = dict(self._state.get(key) or {"count": 0, "until": 0, "lockouts": 0})
        entry["count"] = entry.get("count", 0) + 1
        if entry["count"] >= self.max_failures:
            lockouts = entry.get("lockouts", 0)
            entry["until"] = now + min(self.lockout * 2 ** lockouts, self.max_lockout)
            entry["lockouts"] = lockouts + 1
            entry["count"] = 0
        self._state[key] = entry

    def seconds_left(self, key, now: float | None = None) -> int:
        now = time.time() if now is None else now
        if not self.locked(key, now):
            return 0
        return int(self._state.get(key)["until"] - now) + 1

    def success(self, key) -> None:
        self._state.pop(key, None)

    def locked_keys(self, now: float | None = None) -> list:
        return [k for k in list(self._state) if self.locked(k, now)]

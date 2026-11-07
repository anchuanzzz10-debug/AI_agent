"""Dashboard login: password hash, sessions, CSRF, rate limiting."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

PASSWORD_FILE = "dashboard.pass"
MIN_PASSWORD_LEN = 10
SCRYPT = {"n": 2**15, "r": 8, "p": 1}
IDLE_TIMEOUT_S = 12 * 3600
ABSOLUTE_TIMEOUT_S = 7 * 24 * 3600
MAX_FAILURES = 5
LOCKOUT_S = 15 * 60


class AuthError(ValueError):
    pass


def _hash(password: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    return hashlib.scrypt(password.encode(), salt=salt, n=n, r=r, p=p, maxmem=2**26, dklen=32)


def set_password(home: Path, password: str) -> None:
    if len(password) < MIN_PASSWORD_LEN:
        raise AuthError(f"Dashboard password must be at least {MIN_PASSWORD_LEN} characters")
    salt = os.urandom(16)
    record = {**SCRYPT, "salt": base64.b64encode(salt).decode(),
              "hash": base64.b64encode(_hash(password, salt, **SCRYPT)).decode()}
    path = Path(home) / "system" / PASSWORD_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record), encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def password_is_set(home: Path) -> bool:
    return (Path(home) / "system" / PASSWORD_FILE).exists()


def check_password(home: Path, password: str) -> bool:
    path = Path(home) / "system" / PASSWORD_FILE
    if not path.exists():
        return False
    rec = json.loads(path.read_text(encoding="utf-8"))
    expected = base64.b64decode(rec["hash"])
    got = _hash(password, base64.b64decode(rec["salt"]), rec["n"], rec["r"], rec["p"])
    return hmac.compare_digest(expected, got)


@dataclass
class Session:
    csrf: str
    created: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)


class SessionStore:
    """In-memory sessions: restarting the dashboard logs everyone out."""

    def __init__(self, clock=time.time):
        self._sessions: dict[str, Session] = {}
        self._failures: dict[str, list[float]] = {}
        self._lock = threading.Lock()
        self._clock = clock

    def create(self) -> tuple[str, Session]:
        token = secrets.token_urlsafe(32)
        session = Session(csrf=secrets.token_urlsafe(32), created=self._clock(), last_seen=self._clock())
        with self._lock:
            self._sessions[token] = session
        return token, session

    def get(self, token: str | None) -> Session | None:
        if not token:
            return None
        now = self._clock()
        with self._lock:
            session = self._sessions.get(token)
            if session is None:
                return None
            if now - session.last_seen > IDLE_TIMEOUT_S or now - session.created > ABSOLUTE_TIMEOUT_S:
                del self._sessions[token]
                return None
            session.last_seen = now
            return session

    def destroy(self, token: str | None) -> None:
        with self._lock:
            self._sessions.pop(token or "", None)

    # -- rate limiting ------------------------------------------------------
    def locked_out(self, client: str) -> bool:
        now = self._clock()
        with self._lock:
            recent = [t for t in self._failures.get(client, []) if now - t < LOCKOUT_S]
            self._failures[client] = recent
            return len(recent) >= MAX_FAILURES

    def record_failure(self, client: str) -> None:
        with self._lock:
            self._failures.setdefault(client, []).append(self._clock())

    def clear_failures(self, client: str) -> None:
        with self._lock:
            self._failures.pop(client, None)


def csrf_ok(session: Session | None, supplied: str | None) -> bool:
    return bool(session and supplied and hmac.compare_digest(session.csrf, supplied))

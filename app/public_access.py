"""Public-request admission and Cloudflare Turnstile verification helpers."""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import sqlite3
import time
import urllib.parse
import urllib.request
from pathlib import Path


SITEVERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
MAX_TOKEN_LENGTH = 2048
MAX_RESPONSE_BYTES = 16 * 1024


class AccessDenied(Exception):
    """Raised when a public client has exhausted its request allowance."""


def _canonical_ip(peer_ip: str | None) -> str:
    """Normalize an address, grouping IPv6 clients by /64; malformed is shared."""
    try:
        address = ipaddress.ip_address(peer_ip or "")
    except (ValueError, TypeError):
        return "unknown"
    if isinstance(address, ipaddress.IPv6Address):
        if address.ipv4_mapped:
            return str(address.ipv4_mapped)
        try:
            unscoped = address.compressed.split("%", 1)[0]
            return str(ipaddress.ip_network(f"{unscoped}/64", strict=False))
        except ValueError:
            return "unknown"
    return str(address)


class PublicAccess:
    """HMAC-based visitor identities, atomic rolling-hour admission, and Turnstile.

    IP addresses and raw challenge tokens are never stored. SQLite must be on a
    persistent local volume and shared by the single application instance.
    """

    def __init__(self, db_path: Path, secret: str | bytes,
                 requests_per_hour: int = 12):
        if not secret:
            raise ValueError("secret must be nonempty")
        if (not isinstance(requests_per_hour, int) or isinstance(requests_per_hour, bool)
                or requests_per_hour <= 0):
            raise ValueError("requests_per_hour must be a positive integer")
        self.db_path = str(db_path)
        self.secret = secret.encode("utf-8") if isinstance(secret, str) else bytes(secret)
        self.requests_per_hour = requests_per_hour
        with self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS public_access_attempts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    owner_hash TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS public_access_attempts_owner_time
                    ON public_access_attempts(owner_hash, created_at);
                CREATE TABLE IF NOT EXISTS public_access_challenges (
                    token_hash TEXT PRIMARY KEY,
                    created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS public_access_challenges_time
                    ON public_access_challenges(created_at);
            """)

    def _connect(self):
        db = sqlite3.connect(self.db_path, timeout=30, isolation_level=None)
        db.execute("PRAGMA busy_timeout=30000")
        return db

    @staticmethod
    def _now() -> float:
        return time.time()

    def identify(self, peer_ip: str | None) -> str:
        """Return a secret-keyed visitor ID without persisting the source IP."""
        canonical = _canonical_ip(peer_ip)
        return hmac.new(self.secret, b"leaf-public-access\0" + canonical.encode("ascii"),
                        hashlib.sha256).hexdigest()

    def admit_attempt(self, owner: str) -> None:
        """Count one upload attempt and atomically enforce the rolling-hour cap.

        Call before parsing or buffering the upload so invalid submissions also
        consume a slot. Rows older than 48 hours are pruned opportunistically.
        """
        if not isinstance(owner, str) or len(owner) != 64:
            raise ValueError("owner must be an identify() result")
        now = self._now()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM public_access_attempts WHERE created_at < ?", (now - 48 * 3600,))
            count = db.execute("SELECT COUNT(*) FROM public_access_attempts "
                               "WHERE owner_hash=? AND created_at > ?",
                               (owner, now - 3600)).fetchone()[0]
            if count >= self.requests_per_hour:
                raise AccessDenied("upload request limit reached; try again later")
            db.execute("INSERT INTO public_access_attempts(owner_hash,created_at) VALUES(?,?)",
                       (owner, now))

    def verify_challenge(self, token: str, secret: str, expected_hostname: str,
                         transport=None) -> bool:
        """Verify one Turnstile token using Cloudflare Siteverify; fail closed."""
        if (not isinstance(token, str) or not token or len(token) > MAX_TOKEN_LENGTH
                or not isinstance(secret, str) or not secret
                or not isinstance(expected_hostname, str) or not expected_hostname):
            return False
        payload = {"secret": secret, "response": token}
        try:
            result = (transport or _siteverify)(payload) if transport else _siteverify(payload)
            return (isinstance(result, dict) and result.get("success") is True
                    and result.get("hostname") == expected_hostname
                    and result.get("action") == "convert")
        except Exception:
            return False

    def consume_challenge(self, token: str) -> bool:
        """Atomically mark a verified token consumed locally; reject replays."""
        if not isinstance(token, str) or not token or len(token) > MAX_TOKEN_LENGTH:
            return False
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        now = self._now()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM public_access_challenges WHERE created_at < ?", (now - 600,))
            cursor = db.execute("INSERT OR IGNORE INTO public_access_challenges(token_hash,created_at) "
                                "VALUES(?,?)", (token_hash, now))
            return cursor.rowcount == 1


def _siteverify(payload: dict) -> dict:
    """POST form-encoded data to Cloudflare with bounded response and timeout."""
    data = urllib.parse.urlencode(payload).encode("ascii")
    request = urllib.request.Request(SITEVERIFY_URL, data=data, method="POST", headers={
        "Content-Type": "application/x-www-form-urlencoded",
    })
    with urllib.request.urlopen(request, timeout=8) as response:
        body = response.read(MAX_RESPONSE_BYTES + 1)
    if len(body) > MAX_RESPONSE_BYTES:
        raise ValueError("Siteverify response exceeded size limit")
    return json.loads(body)

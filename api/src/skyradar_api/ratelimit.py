"""Per-client rate limits for a public deployment.

The expensive endpoints are the aircraft card (one route lookup per click) and the
airport board (a few dozen route lookups per request). Limits are kept in memory: SkyRadar runs a
single API instance, and a restart resetting the counters is acceptable for a demo.

* Rules read like ``"5/minute,30/day"`` (sliding windows, all must hold).
* The client is the peer address, or the address set by our own reverse proxy in
  ``API_CLIENT_IP_HEADER`` (``X-Real-IP`` from the frontend nginx, ``Fly-Client-IP`` on
  Fly.io). The header is only honoured when the peer is a private address (the proxy):
  a client reaching the API directly cannot spoof it to dodge the limits.
"""

from __future__ import annotations

import ipaddress
import time
from collections import deque
from dataclasses import dataclass

from fastapi import HTTPException, Request, WebSocket

from skyradar_api.metrics import RATE_LIMITED

WINDOWS = {"second": 1, "minute": 60, "hour": 3600, "day": 86400}
MAX_KEYS = 50_000


@dataclass(frozen=True)
class Rule:
    limit: int
    window_s: int

    def __str__(self) -> str:
        unit = next(name for name, seconds in WINDOWS.items() if seconds == self.window_s)
        return f"{self.limit}/{unit}"


def parse_rules(spec: str) -> list[Rule]:
    """``"5/minute,30/day"`` -> rules; an empty spec or ``off`` disables the limit."""
    spec = spec.strip().lower()
    if not spec or spec == "off":
        return []
    rules = []
    for part in spec.split(","):
        count, _, unit = part.strip().partition("/")
        if unit not in WINDOWS:
            raise ValueError(f"bad rate limit {part!r}: unit must be one of {list(WINDOWS)}")
        rules.append(Rule(int(count), WINDOWS[unit]))
    return rules


class RateLimiter:
    """Sliding-window counters per key; ``hit`` raises HTTP 429 when a rule is exceeded."""

    def __init__(self, name: str, rules: list[Rule], message: str) -> None:
        self.name = name
        self.rules = rules
        self.message = message
        self._hits: dict[str, deque[float]] = {}
        self._longest = max((r.window_s for r in rules), default=0)

    def check(self, key: str, now: float | None = None) -> float | None:
        """Record a hit; return the seconds to wait if it is refused (not recorded)."""
        if not self.rules:
            return None
        now = time.monotonic() if now is None else now
        hits = self._hits.setdefault(key, deque())
        while hits and hits[0] <= now - self._longest:
            hits.popleft()
        for rule in self.rules:
            recent = [t for t in hits if t > now - rule.window_s]
            if len(recent) >= rule.limit:
                return max(1.0, recent[0] + rule.window_s - now)
        hits.append(now)
        if len(self._hits) > MAX_KEYS:
            self._prune(now)
        return None

    def hit(self, key: str) -> None:
        wait = self.check(key)
        if wait is not None:
            RATE_LIMITED.labels(self.name).inc()
            raise HTTPException(
                429,
                detail=self.message,
                headers={"Retry-After": str(round(wait))},
            )

    def _prune(self, now: float) -> None:
        for key in [k for k, v in self._hits.items() if not v or v[-1] <= now - self._longest]:
            del self._hits[key]


def _is_private(host: str | None) -> bool:
    if not host:
        return False
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return host in {"testclient", "localhost"}
    return address.is_private or address.is_loopback


def client_ip(connection: Request | WebSocket, header: str) -> str:
    peer = connection.client.host if connection.client else None
    if header and _is_private(peer):
        forwarded = connection.headers.get(header)
        if forwarded:
            return forwarded.split(",")[0].strip()
    return peer or "unknown"

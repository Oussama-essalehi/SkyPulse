"""OpenSky API credit accounting and safe polling interval computation.

Figures from the official OpenSky REST API documentation ("API credit usage"):

* ``/states/all`` costs 1 credit for an area <= 25 square degrees, 2 credits up to
  100 sq deg, 3 credits up to 400 sq deg, and 4 credits above 400 sq deg or for a
  global request (no bounding box).
* Daily quota: 400 credits for anonymous users, 4000 for registered users,
  8000 for active feeders.
* Time resolution: 10 s for anonymous users, 5 s for authenticated users.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum

SECONDS_PER_DAY = 86_400


class AccountTier(StrEnum):
    ANONYMOUS = "anonymous"
    STANDARD = "standard"
    FEEDER = "feeder"


DAILY_CREDITS: dict[AccountTier, int] = {
    AccountTier.ANONYMOUS: 400,
    AccountTier.STANDARD: 4_000,
    AccountTier.FEEDER: 8_000,
}

MIN_RESOLUTION_SECONDS: dict[AccountTier, int] = {
    AccountTier.ANONYMOUS: 10,
    AccountTier.STANDARD: 5,
    AccountTier.FEEDER: 5,
}


@dataclass(frozen=True)
class BoundingBox:
    lamin: float
    lomin: float
    lamax: float
    lomax: float

    @classmethod
    def parse(cls, raw: str) -> BoundingBox:
        """Parse ``"lamin,lomin,lamax,lomax"``."""
        parts = [float(p) for p in raw.split(",")]
        if len(parts) != 4:
            raise ValueError("OPENSKY_BBOX must be 'lamin,lomin,lamax,lomax'")
        box = cls(*parts)
        if not (-90 <= box.lamin < box.lamax <= 90 and -180 <= box.lomin < box.lomax <= 180):
            raise ValueError(f"Invalid bounding box: {raw}")
        return box

    @property
    def square_degrees(self) -> float:
        return (self.lamax - self.lamin) * (self.lomax - self.lomin)

    def as_params(self) -> dict[str, float]:
        return {"lamin": self.lamin, "lomin": self.lomin, "lamax": self.lamax, "lomax": self.lomax}


def credits_per_call(bbox: BoundingBox | None) -> int:
    """Credit cost of one ``/states/all`` call for the given area (None = global)."""
    if bbox is None:
        return 4
    area = bbox.square_degrees
    if area <= 25:
        return 1
    if area <= 100:
        return 2
    if area <= 400:
        return 3
    return 4


def safe_poll_interval(
    tier: AccountTier,
    bbox: BoundingBox | None = None,
    margin: float = 0.10,
    daily_credits: int | None = None,
) -> int:
    """Smallest polling interval (seconds) that keeps 24h usage under the quota.

    ``margin`` keeps a fraction of the quota unused (manual calls, restarts, clock
    skew). The result is never below the source time resolution for the tier.
    """
    if not 0 <= margin < 1:
        raise ValueError("margin must be in [0, 1)")
    quota = daily_credits if daily_credits is not None else DAILY_CREDITS[tier]
    usable = quota * (1 - margin)
    calls_per_day = usable / credits_per_call(bbox)
    if calls_per_day <= 0:
        raise ValueError("quota too small for a single call")
    interval = math.ceil(SECONDS_PER_DAY / calls_per_day)
    return max(interval, MIN_RESOLUTION_SECONDS[tier])


def resolve_poll_interval(
    requested: str | float | None,
    tier: AccountTier,
    bbox: BoundingBox | None = None,
    margin: float = 0.10,
    daily_credits: int | None = None,
) -> tuple[int, str]:
    """Return (interval, reason). ``auto``/empty uses the computed safe interval;
    an explicit value is honoured only if it stays within the quota."""
    safe = safe_poll_interval(tier, bbox, margin, daily_credits)
    if requested is None or str(requested).strip().lower() in {"", "auto"}:
        return safe, "auto"
    value = math.ceil(float(requested))
    if value < safe:
        return safe, f"requested {value}s would exceed the daily quota; raised to {safe}s"
    return value, "configured"

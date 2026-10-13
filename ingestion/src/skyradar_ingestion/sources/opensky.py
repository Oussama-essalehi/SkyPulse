"""OpenSky Network ``/states/all`` source.

* OAuth2 client credentials when ``OPENSKY_CLIENT_ID``/``SECRET`` are set,
  anonymous mode otherwise (much smaller quota, so a longer interval).
* Polling interval derived from the credit quota (see :mod:`quota`).
* ``X-Rate-Limit-Remaining`` is logged; HTTP 429 triggers an exponential
  backoff that honours ``X-Rate-Limit-Retry-After-Seconds``.
"""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import AsyncIterator, Sequence
from typing import Any

import httpx
import structlog
from pydantic import ValidationError

from skyradar_common.models import StateVector
from skyradar_ingestion.auth import DEFAULT_TOKEN_URL, OAuth2TokenManager, TokenError
from skyradar_ingestion.quota import AccountTier, BoundingBox, credits_per_call
from skyradar_ingestion.sources.base import FlightDataSource, Snapshot

log = structlog.get_logger(__name__)

DEFAULT_API_URL = "https://opensky-network.org/api"
# Failed calls (5xx, timeouts) are retried quickly at most this many times in a row.
MAX_TRANSIENT_RETRIES = 3

# Index of each field in an OpenSky state vector array.
_FIELDS = (
    "icao24",
    "callsign",
    "origin_country",
    "time_position",
    "last_contact",
    "longitude",
    "latitude",
    "baro_altitude",
    "on_ground",
    "velocity",
    "true_track",
    "vertical_rate",
    "sensors",
    "geo_altitude",
    "squawk",
    "spi",
    "position_source",
    "category",
)


class RateLimitedError(RuntimeError):
    def __init__(self, retry_after: float | None) -> None:
        super().__init__(f"rate limited (retry after {retry_after}s)")
        self.retry_after = retry_after


def parse_states(
    payload: dict[str, Any], ingested_at: float, source: str = "opensky"
) -> tuple[int, list[StateVector], int]:
    """Convert an OpenSky ``/states/all`` JSON payload into validated states.

    Returns ``(snapshot_time, states, skipped)``; rows without a position or that
    fail validation are skipped (and counted).
    """
    snapshot_time = int(payload.get("time") or ingested_at)
    rows: Sequence[Sequence[Any]] = payload.get("states") or []
    states: list[StateVector] = []
    skipped = 0
    for row in rows:
        record = dict(zip(_FIELDS, row, strict=False))
        if record.get("longitude") is None or record.get("latitude") is None:
            skipped += 1
            continue
        record.pop("sensors", None)
        record["last_contact"] = record.get("last_contact") or snapshot_time
        record["on_ground"] = bool(record.get("on_ground"))
        record["spi"] = bool(record.get("spi"))
        record["position_source"] = record.get("position_source") or 0
        record["snapshot_time"] = snapshot_time
        record["ingested_at"] = ingested_at
        record["source"] = source
        try:
            states.append(StateVector.model_validate(record))
        except ValidationError:
            skipped += 1
    return snapshot_time, states, skipped


class OpenSkySource(FlightDataSource):
    name = "opensky"

    def __init__(
        self,
        poll_interval_seconds: float,
        client_id: str | None = None,
        client_secret: str | None = None,
        bbox: BoundingBox | None = None,
        api_url: str = DEFAULT_API_URL,
        token_url: str = DEFAULT_TOKEN_URL,
        max_backoff_seconds: float = 3600.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._interval = poll_interval_seconds
        self._bbox = bbox
        self._api_url = api_url.rstrip("/")
        self._max_backoff = max_backoff_seconds
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(60.0, connect=15.0),
            headers={"User-Agent": "SkyRadar/0.1 (+portfolio project; non-commercial)"},
        )
        self._tokens = (
            OAuth2TokenManager(self._client, client_id, client_secret, token_url)
            if client_id and client_secret
            else None
        )
        self.credits_remaining: int | None = None

    @property
    def tier(self) -> AccountTier:
        return AccountTier.STANDARD if self._tokens else AccountTier.ANONYMOUS

    @property
    def poll_interval_seconds(self) -> float:
        return self._interval

    @property
    def cost_per_call(self) -> int:
        return credits_per_call(self._bbox)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _headers(self) -> dict[str, str]:
        if self._tokens is None:
            return {}
        return {"Authorization": f"Bearer {await self._tokens.get_token()}"}

    async def fetch(self) -> Snapshot:
        """Perform a single ``/states/all`` call."""
        params: dict[str, Any] = {"extended": 1}
        if self._bbox:
            params.update(self._bbox.as_params())
        started = time.perf_counter()
        response = await self._client.get(
            f"{self._api_url}/states/all", params=params, headers=await self._headers()
        )
        if response.status_code == 401 and self._tokens is not None:
            # Token revoked or expired early: refresh once and retry.
            self._tokens.invalidate()
            response = await self._client.get(
                f"{self._api_url}/states/all", params=params, headers=await self._headers()
            )
        latency_ms = (time.perf_counter() - started) * 1000

        remaining = response.headers.get("X-Rate-Limit-Remaining")
        if remaining is not None and remaining.lstrip("-").isdigit():
            self.credits_remaining = int(remaining)
        if response.status_code == 429:
            retry_after = response.headers.get("X-Rate-Limit-Retry-After-Seconds")
            raise RateLimitedError(float(retry_after) if retry_after else None)
        response.raise_for_status()

        ingested_at = time.time()
        snapshot_time, states, skipped = parse_states(response.json(), ingested_at, self.name)
        log.info(
            "opensky_snapshot",
            aircraft=len(states),
            skipped=skipped,
            latency_ms=round(latency_ms, 1),
            credits_remaining=self.credits_remaining,
            credits_cost=self.cost_per_call,
            tier=self.tier.value,
        )
        return Snapshot(
            snapshot_time=snapshot_time,
            states=states,
            source=self.name,
            poll_interval_seconds=self._interval,
            latency_ms=latency_ms,
            credits_remaining=self.credits_remaining,
            skipped_rows=skipped,
        )

    def _backoff(self, attempt: int, hint: float | None = None) -> float:
        """Quota exhausted (429): exponential from the polling interval, honouring the
        server hint. Never shorter than the interval, so retries cannot overspend."""
        base = max(self._interval, 10.0)
        delay: float = min(self._max_backoff, base * 2.0 ** (attempt - 1))
        if hint is not None:
            delay = max(delay, hint)
        return delay + random.uniform(0, 0.1 * delay)

    def _transient_backoff(self, attempt: int) -> float:
        """Network / 5xx errors: retry sooner (30 s, 60 s, ...) but never more often
        than ``MAX_TRANSIENT_RETRIES`` times per polling interval."""
        delay: float = min(self._interval, 30.0 * 2.0 ** (attempt - 1))
        if attempt > MAX_TRANSIENT_RETRIES:
            delay = self._interval
        return delay + random.uniform(0, 0.1 * delay)

    async def stream(self) -> AsyncIterator[Snapshot]:
        failures = 0
        loop = asyncio.get_running_loop()
        while True:
            cycle_start = loop.time()
            try:
                snapshot = await self.fetch()
            except RateLimitedError as exc:
                failures += 1
                delay = self._backoff(failures, exc.retry_after)
                log.warning(
                    "opensky_rate_limited", retry_after=exc.retry_after, sleep_s=round(delay)
                )
                await asyncio.sleep(delay)
                continue
            except (httpx.HTTPError, TokenError, ValueError) as exc:
                failures += 1
                delay = self._transient_backoff(failures)
                log.warning(
                    "opensky_fetch_failed", error=str(exc), attempt=failures, sleep_s=round(delay)
                )
                await asyncio.sleep(delay)
                continue
            failures = 0
            yield snapshot
            # Fixed cadence: the interval is measured from the start of the call.
            elapsed = loop.time() - cycle_start
            await asyncio.sleep(max(0.0, self._interval - elapsed))

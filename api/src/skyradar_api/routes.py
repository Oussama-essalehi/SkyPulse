"""Scheduled route of a callsign and aircraft details from adsbdb (public, no key).

ADS-B carries neither origin nor destination. The pipeline reconstructs them from
history, which takes hours; adsbdb (https://www.adsbdb.com, community-maintained
"standing data") knows the published route of most airline callsigns right away:
BAW131 -> EGLL to VOBL. Callsigns get reused and the data can be stale, so every route
is checked against the live position before being trusted (:func:`check_route`).

Lookups go through the API (cached, never from the browser) and can be disabled with
``ROUTE_LOOKUP=off`` for a fully offline stack.
"""

from __future__ import annotations

import asyncio
import math
import os
import time
from dataclasses import dataclass
from typing import Any

import httpx
import structlog

log = structlog.get_logger(__name__)

EARTH_RADIUS_KM = 6371.0
CACHE_TTL_S = 6 * 3600
NEGATIVE_TTL_S = 3600  # unknown callsigns / aircraft are asked again after an hour
CACHE_SIZE = 20_000


@dataclass(frozen=True)
class RouteAirport:
    icao: str
    iata: str | None
    name: str
    city: str | None
    country: str | None
    latitude: float
    longitude: float

    @classmethod
    def from_adsbdb(cls, data: dict[str, Any]) -> RouteAirport:
        return cls(
            icao=data["icao_code"],
            iata=data.get("iata_code") or None,
            name=data["name"],
            city=data.get("municipality") or None,
            country=data.get("country_name") or None,
            latitude=float(data["latitude"]),
            longitude=float(data["longitude"]),
        )


@dataclass(frozen=True)
class FlightRoute:
    callsign: str
    origin: RouteAirport
    destination: RouteAirport
    airline: str | None


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(a)))


def bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def check_route(
    route: FlightRoute,
    latitude: float,
    longitude: float,
    track: float | None,
    on_ground: bool,
) -> bool:
    """Is the published route plausible for an aircraft here, flying this track?

    * the position lies near the origin -> destination corridor (detour < 25 % + 150 km),
      which rejects stale routes (callsign reused on another city pair) and the return
      leg (same callsign, other direction, is caught by the heading test);
    * an airborne aircraft far from both ends heads roughly towards the destination.
    """
    o, d = route.origin, route.destination
    total = haversine_km(o.latitude, o.longitude, d.latitude, d.longitude)
    flown = haversine_km(o.latitude, o.longitude, latitude, longitude)
    remaining = haversine_km(latitude, longitude, d.latitude, d.longitude)
    if flown + remaining > 1.25 * total + 150:
        return False
    if on_ground or track is None or min(flown, remaining) < 150:
        return True
    to_destination = bearing_deg(latitude, longitude, d.latitude, d.longitude)
    off = abs((track - to_destination + 180) % 360 - 180)
    return off <= 90


class _TtlCache:
    def __init__(self) -> None:
        self._data: dict[str, tuple[float, Any]] = {}

    def get(self, key: str) -> tuple[bool, Any]:
        hit = self._data.get(key)
        if hit is None or hit[0] < time.monotonic():
            return False, None
        return True, hit[1]

    def put(self, key: str, value: Any) -> None:
        if len(self._data) >= CACHE_SIZE:
            self._data.clear()
        ttl = CACHE_TTL_S if value is not None else NEGATIVE_TTL_S
        self._data[key] = (time.monotonic() + ttl, value)


class RouteLookup:
    """Cached adsbdb client (callsign -> route, icao24 -> aircraft)."""

    def __init__(
        self,
        base_url: str | None = None,
        enabled: bool | None = None,
        client: httpx.AsyncClient | None = None,
        timeout: float = 4.0,
    ) -> None:
        url = base_url or os.getenv("ADSBDB_URL") or "https://api.adsbdb.com/v0"
        self.base_url = url.rstrip("/")
        self.enabled = (
            enabled
            if enabled is not None
            else os.getenv("ROUTE_LOOKUP", "adsbdb").lower() not in {"off", "false", "0"}
        )
        self._client = client
        self._timeout = timeout
        self._routes = _TtlCache()
        self._aircraft = _TtlCache()
        self._lock = asyncio.Lock()

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()

    async def _get(self, path: str) -> dict[str, Any] | None:
        if self._client is None:
            async with self._lock:
                if self._client is None:
                    self._client = httpx.AsyncClient(
                        timeout=self._timeout, headers={"User-Agent": "SkyRadar"}
                    )
        try:
            response = await self._client.get(f"{self.base_url}/{path}")
        except httpx.HTTPError as exc:
            log.warning("route_lookup_failed", path=path, error=str(exc))
            raise
        if response.status_code == 404:
            return None
        response.raise_for_status()
        payload = response.json().get("response")
        return payload if isinstance(payload, dict) else None

    async def route(self, callsign: str | None) -> FlightRoute | None:
        callsign = (callsign or "").strip().upper()
        if not self.enabled or len(callsign) < 3:
            return None
        found, cached = self._routes.get(callsign)
        if found:
            return cached  # type: ignore[no-any-return]
        try:
            payload = await self._get(f"callsign/{callsign}")
        except (httpx.HTTPError, ValueError):
            return None  # not cached: a transient error is retried on the next click
        route = None
        data = (payload or {}).get("flightroute")
        if data and data.get("origin") and data.get("destination"):
            try:
                route = FlightRoute(
                    callsign=callsign,
                    origin=RouteAirport.from_adsbdb(data["origin"]),
                    destination=RouteAirport.from_adsbdb(data["destination"]),
                    airline=(data.get("airline") or {}).get("name"),
                )
            except (KeyError, TypeError, ValueError):
                route = None
        self._routes.put(callsign, route)
        return route

    async def aircraft(self, icao24: str) -> dict[str, Any] | None:
        """Type / registration / owner, used when the OpenSky database lacks the aircraft."""
        if not self.enabled:
            return None
        key = icao24.lower()
        found, cached = self._aircraft.get(key)
        if found:
            return cached  # type: ignore[no-any-return]
        try:
            payload = await self._get(f"aircraft/{key}")
        except (httpx.HTTPError, ValueError):
            return None
        data = (payload or {}).get("aircraft")
        info = None
        if data:
            info = {
                "registration": data.get("registration") or None,
                "typecode": data.get("icao_type") or None,
                "manufacturer": data.get("manufacturer") or None,
                "model": data.get("type") or None,
                "operator": data.get("registered_owner") or None,
                "operator_icao": data.get("registered_owner_operator_flag_code") or None,
            }
        self._aircraft.put(key, info)
        return info

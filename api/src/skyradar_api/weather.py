"""Current weather at an airport: its latest METAR (aviationweather.gov, NOAA).

The METAR is the observation pilots use, issued every 30 or 60 min by most airports with
scheduled traffic. The public API needs no key; results are cached ``CACHE_TTL_S`` per
airport so a busy demo sends at most a few requests per airport and hour. Small fields
without a weather station simply have no METAR. ``WEATHER_LOOKUP=off`` disables it.
"""

from __future__ import annotations

import math
import os
import time
from typing import Any

import httpx
import structlog

from skyradar_api.metrics import ROUTE_LOOKUPS

log = structlog.get_logger(__name__)

CACHE_TTL_S = 10 * 60
KT_TO_KMH = 1.852
SM_TO_KM = 1.609344
FT_TO_M = 0.3048

# Present weather (METAR codes) -> French; descriptors first, then phenomena.
DESCRIPTORS = {
    "SH": "averses de",
    "TS": "orage",
    "FZ": "verglaçant(e)",
    "BL": "chasse-neige",
    "DR": "chasse basse",
    "MI": "mince",
    "BC": "bancs de",
    "PR": "partiel",
}
PHENOMENA = {
    "RA": "pluie",
    "DZ": "bruine",
    "SN": "neige",
    "SG": "neige en grains",
    "GR": "grêle",
    "GS": "grésil",
    "PL": "granules de glace",
    "IC": "cristaux de glace",
    "UP": "précipitations",
    "FG": "brouillard",
    "BR": "brume",
    "HZ": "brume sèche",
    "FU": "fumée",
    "DU": "poussière",
    "SA": "sable",
    "VA": "cendres volcaniques",
    "SQ": "grains",
    "FC": "trombe",
    "SS": "tempête de sable",
    "DS": "tempête de poussière",
}
CLOUD_COVER = {
    "SKC": "ciel clair",
    "CLR": "ciel clair",
    "NSC": "pas de nuage significatif",
    "NCD": "pas de nuage détecté",
    "CAVOK": "ciel dégagé",
    "FEW": "quelques nuages",
    "SCT": "nuages épars",
    "BKN": "nuageux",
    "OVC": "couvert",
    "OVX": "ciel invisible",
}


def describe_weather(code: str | None) -> str | None:
    """``-SHRA BR`` -> ``averses de pluie faible, brume``."""
    if not code:
        return None
    parts = []
    for token in code.split():
        intensity = ""
        if token[0] in "+-":
            intensity = " forte" if token[0] == "+" else " faible"
            token = token[1:]
        if token.startswith("VC"):
            token, intensity = token[2:], intensity + " à proximité"
        words = []
        while token:
            chunk, token = token[:2], token[2:]
            words.append(DESCRIPTORS.get(chunk) or PHENOMENA.get(chunk) or chunk)
        text = " ".join(words).removesuffix(" de")  # "VCSH": averses, no phenomenon
        parts.append(text + intensity)
    return ", ".join(parts)


def humidity(temp: float | None, dewpoint: float | None) -> int | None:
    """Relative humidity from temperature and dew point (Magnus formula)."""
    if temp is None or dewpoint is None:
        return None
    a, b = 17.625, 243.04
    rh = 100 * math.exp(a * dewpoint / (b + dewpoint) - a * temp / (b + temp))
    return round(min(100.0, rh))


def parse_visibility(raw: Any) -> tuple[float | None, bool]:
    """aviationweather gives statute miles, ``"10+"`` meaning "10 or more"."""
    if raw is None:
        return None, False
    text = str(raw).strip()
    plus = text.endswith("+")
    try:
        value = float(text.rstrip("+"))
    except ValueError:
        return None, False
    return round(value * SM_TO_KM, 1), plus


def from_metar(data: dict[str, Any]) -> dict[str, Any]:
    raw = data.get("rawOb") or ""
    visibility, visibility_plus = parse_visibility(data.get("visib"))
    wind_dir = data.get("wdir")
    clouds = [c for c in data.get("clouds") or [] if c.get("cover")]
    lowest = next((c for c in clouds if c["cover"] in {"BKN", "OVC", "OVX"}), None)
    main = lowest or (clouds[-1] if clouds else None)
    tokens = raw.split()
    no_cloud = next((t for t in ("CAVOK", "NCD", "NSC") if t in tokens), None)
    sky = no_cloud or (main or {}).get("cover") or data.get("cover")
    base = (main or {}).get("base")
    temp, dew = data.get("temp"), data.get("dewp")
    wind = data.get("wspd")
    gust = data.get("wgst")
    altim = data.get("altim")
    return {
        "station": data.get("icaoId"),
        "observed_at": data.get("obsTime"),
        "temperature_c": temp,
        "dewpoint_c": dew,
        "humidity_pct": humidity(temp, dew),
        "wind_direction_deg": wind_dir if isinstance(wind_dir, int | float) else None,
        "wind_variable": wind_dir == "VRB",
        "wind_kmh": round(wind * KT_TO_KMH) if isinstance(wind, int | float) else None,
        "gust_kmh": round(gust * KT_TO_KMH) if isinstance(gust, int | float) else None,
        "visibility_km": visibility,
        "visibility_plus": visibility_plus,
        "pressure_hpa": round(altim) if isinstance(altim, int | float) else None,
        "conditions": describe_weather(data.get("wxString")),
        "sky": CLOUD_COVER.get(sky or "", sky),
        "cloud_base_m": round(base * FT_TO_M, -1) if isinstance(base, int | float) else None,
        "flight_category": data.get("fltCat"),
        "raw": raw or None,
    }


class AirportWeather:
    """Cached client of the aviationweather.gov METAR API."""

    def __init__(
        self,
        base_url: str | None = None,
        enabled: bool | None = None,
        client: httpx.AsyncClient | None = None,
        timeout: float = 5.0,
    ) -> None:
        url = base_url or os.getenv("METAR_URL") or "https://aviationweather.gov/api/data"
        self.base_url = url.rstrip("/")
        self.enabled = (
            enabled
            if enabled is not None
            else os.getenv("WEATHER_LOOKUP", "metar").lower() not in {"off", "false", "0"}
        )
        self._client = client
        self._timeout = timeout
        self._cache: dict[str, tuple[float, dict[str, Any] | None]] = {}

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()

    async def current(self, icao: str) -> dict[str, Any] | None:
        """Latest METAR of the airport, decoded; None without a station or when disabled."""
        if not self.enabled:
            return None
        icao = icao.upper()
        hit = self._cache.get(icao)
        if hit and hit[0] > time.monotonic():
            ROUTE_LOOKUPS.labels("metar", "cached").inc()
            return hit[1]
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=self._timeout, headers={"User-Agent": "SkyRadar"}
            )
        try:
            response = await self._client.get(
                f"{self.base_url}/metar", params={"ids": icao, "format": "json"}
            )
            response.raise_for_status()
            rows = response.json() if response.content else []
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("metar_lookup_failed", airport=icao, error=str(exc))
            ROUTE_LOOKUPS.labels("metar", "error").inc()
            return None  # not cached: retried on the next request
        weather = from_metar(rows[0]) if isinstance(rows, list) and rows else None
        if len(self._cache) > 5_000:
            self._cache.clear()
        self._cache[icao] = (time.monotonic() + CACHE_TTL_S, weather)
        ROUTE_LOOKUPS.labels("metar", "found" if weather else "unknown").inc()
        return weather

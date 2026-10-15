from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ApiSettings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    cors_origins: str = Field(
        "http://localhost:3000,http://localhost:5173", alias="API_CORS_ORIGINS"
    )
    trail_length: int = Field(120, alias="API_TRAIL_LENGTH")
    # Aircraft absent from snapshots for longer than this are removed from the map.
    aircraft_ttl_seconds: float = Field(300, alias="API_AIRCRAFT_TTL_SECONDS")
    alert_ttl_seconds: float = Field(900, alias="API_ALERT_TTL_SECONDS")
    ws_push_interval_seconds: float = Field(1.0, alias="API_WS_PUSH_INTERVAL_SECONDS")
    # On start, re-read this much history so the map is populated immediately
    # (must cover one polling interval: 960 s in anonymous OpenSky mode).
    bootstrap_lookback_seconds: int = Field(1200, alias="API_BOOTSTRAP_LOOKBACK_SECONDS")
    alerts_lookback_seconds: int = Field(6 * 3600, alias="API_ALERTS_LOOKBACK_SECONDS")
    # Marts written by dbt / the ML layer (read-only). Absent in the demo profile.
    marts_root: str = Field("/data/marts", alias="SKYRADAR_MARTS_ROOT")
    consume: bool = Field(True, alias="API_CONSUME")
    # Public deployment guards (see ratelimit.py); "off" disables a limit.
    client_ip_header: str = Field("X-Real-IP", alias="API_CLIENT_IP_HEADER")
    rate_aircraft: str = Field("60/minute", alias="API_RATE_AIRCRAFT")
    # Airport board: each request may trigger a few dozen route lookups.
    rate_airport: str = Field("20/minute", alias="API_RATE_AIRPORT")
    max_ws_per_client: int = Field(4, alias="API_MAX_WS_PER_CLIENT")

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]
